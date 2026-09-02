#!/usr/bin/env bash
#
# TrulyVerdant deployment.
#
#   ./deploy.sh              full deploy: backup, pull, deps, migrate, restart
#   ./deploy.sh --backup     take a database backup and stop
#   ./deploy.sh --no-pull    deploy the working tree as-is (no git pull)
#   ./deploy.sh --rollback   restore the most recent backup
#   ./deploy.sh --dry-run    print what would happen, change nothing
#
# The ordering matters: the database is backed up BEFORE migrations run, and
# the app is only restarted once migrations have succeeded. A failure at any
# step aborts rather than leaving a half-deployed site.

set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"
VENV="${VENV:-$APP_DIR/venv}"
SUPERVISOR_PROGRAM="${SUPERVISOR_PROGRAM:-trulyverdant}"
BRANCH="${BRANCH:-main}"
HEALTH_URL="${HEALTH_URL:-}"

DO_PULL=1; DO_ALL=1; DRY_RUN=0; ROLLBACK=0; RESTARTED=0

for arg in "$@"; do
  case "$arg" in
    --backup)   DO_ALL=0 ;;
    --no-pull)  DO_PULL=0 ;;
    --rollback) ROLLBACK=1 ;;
    --dry-run)  DRY_RUN=1 ;;
    -h|--help)  sed -n '3,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------- utilities

BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
log()  { printf '%s==>%s %s\n' "$BOLD" "$OFF" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$GREEN" "$OFF" "$*"; }
warn() { printf '%s  !!%s %s\n' "$YELLOW" "$OFF" "$*"; }
die()  { printf '%s ERR%s %s\n' "$RED" "$OFF" "$*" >&2; exit 1; }

run() {
  if [[ $DRY_RUN -eq 1 ]]; then printf '   would run: %s\n' "$*"; else "$@"; fi
}

trap 'die "failed at line $LINENO. The site was NOT restarted; it is still running the previous release."' ERR

# ------------------------------------------------------------------- config

[[ -f .env ]] || die ".env not found. Copy .env.example and fill it in."

# Read DATABASE_URL without sourcing .env (which would execute its contents).
DATABASE_URL="$(grep -E '^[[:space:]]*DATABASE_URL[[:space:]]*=' .env \
                | tail -1 | cut -d= -f2- | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//' || true)"

is_postgres() { [[ "$DATABASE_URL" == postgres* ]]; }

# Export libpq environment variables parsed from DATABASE_URL.
#
# The credentials deliberately never appear as command-line arguments: those
# are visible to every user on the host via `ps`, and would be echoed by
# --dry-run and captured in CI logs. PGPASSWORD is read from the environment
# by pg_dump/pg_restore instead.
pg_env() {
  local parsed
  parsed="$("$VENV/bin/python" - "$DATABASE_URL" <<'PYPARSE'
import sys
from urllib.parse import urlsplit, unquote
u = urlsplit(sys.argv[1])
def esc(v):
    return "" if v is None else str(v).replace("\\", "\\\\").replace('"', '\\"')
print(f'export PGHOST="{esc(u.hostname)}"')
print(f'export PGPORT="{esc(u.port or 5432)}"')
print(f'export PGUSER="{esc(unquote(u.username or ""))}"')
print(f'export PGPASSWORD="{esc(unquote(u.password or ""))}"')
print(f'export PGDATABASE="{esc((u.path or "").lstrip("/"))}"')
PYPARSE
)" || die "Could not parse DATABASE_URL."
  eval "$parsed"
  [[ -n "${PGDATABASE:-}" ]] || die "DATABASE_URL has no database name."
}

# ------------------------------------------------------------------ backups

backup_db() {
  mkdir -p "$BACKUP_DIR"
  local stamp file
  stamp="$(date +%Y%m%d-%H%M%S)"

  if is_postgres; then
    command -v pg_dump >/dev/null 2>&1 || die "pg_dump not found. Install postgresql-client."
    pg_env
    file="$BACKUP_DIR/db-$stamp.dump"
    log "Backing up Postgres $PGDATABASE@$PGHOST to $(basename "$file")"
    # Custom format: compressed, and restorable selectively with pg_restore.
    run pg_dump --format=custom --no-owner --no-privileges --file="$file"
  elif [[ "$DATABASE_URL" == sqlite* ]]; then
    local src="${DATABASE_URL#sqlite:///}"
    [[ -f "$src" ]] || { warn "SQLite file $src not found; nothing to back up"; return 0; }
    file="$BACKUP_DIR/db-$stamp.sqlite"
    log "Backing up SQLite to $(basename "$file")"
    # .backup is safe against a live database; cp is not.
    run sqlite3 "$src" ".backup '$file'"
  else
    die "Could not determine the database type from DATABASE_URL."
  fi

  if [[ $DRY_RUN -eq 0 ]]; then
    [[ -s "$file" ]] || die "Backup file is empty -- aborting before any migration."
    ok "$(du -h "$file" | cut -f1) written"
    printf '%s' "$file" > "$BACKUP_DIR/.latest"
  fi

  # Uploaded images live on disk, not in the database, and are irreplaceable.
  if [[ -d app/static/uploads ]] && \
     [[ -n "$(find app/static/uploads -type f ! -name '.gitkeep' -print -quit)" ]]; then
    log "Backing up uploaded media"
    run tar -czf "$BACKUP_DIR/uploads-$stamp.tar.gz" -C app/static uploads
    ok "media archived"
  fi

  prune_backups
}

prune_backups() {
  if [[ $DRY_RUN -eq 1 ]]; then return 0; fi
  local removed=0 old
  # find (not ls) so a pattern matching nothing is not an error, and
  # -printf gives a sortable mtime without parsing ls output.
  for pattern in 'db-*' 'uploads-*'; do
    while IFS= read -r old; do
      [[ -n "$old" ]] || continue
      rm -f "$old" && removed=$((removed + 1))
    done < <(
      find "$BACKUP_DIR" -maxdepth 1 -type f -name "$pattern" \
           -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | tail -n +$((KEEP_BACKUPS + 1)) | cut -d' ' -f2-
    )
  done
  if [[ $removed -gt 0 ]]; then
    ok "pruned $removed old backup file(s), keeping $KEEP_BACKUPS"
  fi
  return 0
}

rollback() {
  local latest
  [[ -f "$BACKUP_DIR/.latest" ]] || die "No recorded backup to roll back to."
  latest="$(cat "$BACKUP_DIR/.latest")"
  [[ -f "$latest" ]] || die "Recorded backup $latest is missing."

  is_postgres && pg_env
  local dbname="${PGDATABASE:-${DATABASE_URL##*/}}"
  warn "This REPLACES database '$dbname' with $(basename "$latest")."
  read -r -p "Type the database name to confirm: " confirm
  [[ "$confirm" == "$dbname" ]] || die "Confirmation did not match. Nothing changed."

  stop_app
  if is_postgres; then
    pg_env
    log "Restoring $(basename "$latest") into $PGDATABASE"
    run pg_restore --clean --if-exists --no-owner --no-privileges \
                   --dbname="$PGDATABASE" "$latest"
  else
    local dest="${DATABASE_URL#sqlite:///}"
    run cp "$latest" "$dest"
  fi
  start_app
  ok "Rollback complete."
}

# ------------------------------------------------------------ process control

have_supervisor() { command -v supervisorctl >/dev/null 2>&1; }

# supervisorctl, escalating only if the socket is not readable as this user.
# Supervisor's socket defaults to 0700 root:root, so an unprivileged deploy
# gets PermissionError -- which must NOT be reported as "program missing".
SUPERVISORCTL=""
resolve_supervisorctl() {
  [[ -n "$SUPERVISORCTL" ]] && return 0
  have_supervisor || return 1
  local out
  if out="$(supervisorctl status "$SUPERVISOR_PROGRAM" 2>&1)"; then
    SUPERVISORCTL="supervisorctl"; return 0
  fi
  if [[ "$out" == *PermissionError* || "$out" == *"Permission denied"* ]]; then
    if sudo -n supervisorctl status "$SUPERVISOR_PROGRAM" >/dev/null 2>&1; then
      SUPERVISORCTL="sudo -n supervisorctl"; return 0
    fi
    warn "supervisorctl needs root: supervisor's socket is not readable by $USER."
    warn "Grant access once (see README), or run this script with sudo."
    return 1
  fi
  # Reached supervisor fine; it simply does not know this program.
  if [[ "$out" == *"no such process"* || "$out" == *ERROR* ]]; then
    warn "supervisor is reachable but has no program '$SUPERVISOR_PROGRAM'."
    warn "Install it with: sudo ./deploy/install-services.sh --local"
    return 1
  fi
  warn "supervisorctl failed: $out"
  return 1
}

supervisor_has_program() { resolve_supervisorctl; }

stop_app() {
  if supervisor_has_program; then
    log "Stopping $SUPERVISOR_PROGRAM"
    run $SUPERVISORCTL stop "$SUPERVISOR_PROGRAM"
  else
    warn "supervisor program '$SUPERVISOR_PROGRAM' not found; skipping stop"
  fi
}

start_app() {
  if supervisor_has_program; then
    log "Starting $SUPERVISOR_PROGRAM"
    run $SUPERVISORCTL start "$SUPERVISOR_PROGRAM"
  fi
}

restart_app() {
  if supervisor_has_program; then
    log "Restarting $SUPERVISOR_PROGRAM"
    # Graceful: existing workers finish their request before exiting.
    run $SUPERVISORCTL restart "$SUPERVISOR_PROGRAM"
    sleep 3
    if [[ $DRY_RUN -eq 0 ]]; then
      $SUPERVISORCTL status "$SUPERVISOR_PROGRAM" | grep -q RUNNING \
        || die "$SUPERVISOR_PROGRAM did not come back up. Check its logs."
      ok "running"
    fi
    RESTARTED=1
  else
    warn "Skipping restart -- the new code is NOT live yet."
  fi
}

# ------------------------------------------------------------------- deploy

if [[ $ROLLBACK -eq 1 ]]; then rollback; exit 0; fi

log "Deploying from $APP_DIR"
[[ $DRY_RUN -eq 1 ]] && warn "dry run -- nothing will actually change"

# 1. Backup first, always, before anything can modify the database.
backup_db
if [[ $DO_ALL -eq 0 ]]; then ok "Backup only; stopping here."; exit 0; fi

# 2. Refuse to clobber uncommitted work.
if [[ $DO_PULL -eq 1 ]]; then
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    die "Working tree has uncommitted changes. Commit, stash, or use --no-pull."
  fi
  BEFORE="$(git rev-parse HEAD)"
  log "Pulling origin/$BRANCH"
  run git fetch --prune origin
  run git checkout "$BRANCH"
  run git merge --ff-only "origin/$BRANCH"
  AFTER="$(git rev-parse HEAD)"
  if [[ "$BEFORE" == "$AFTER" ]]; then
    ok "already up to date ($(git rev-parse --short HEAD))"
  else
    ok "$(git rev-parse --short "$BEFORE") -> $(git rev-parse --short "$AFTER")"
    git --no-pager log --oneline "$BEFORE..$AFTER" | sed 's/^/     /'
  fi
fi

# 3. Dependencies.
[[ -x "$VENV/bin/python" ]] || die "No virtualenv at $VENV."
log "Installing dependencies"
run "$VENV/bin/pip" install --quiet --upgrade pip
run "$VENV/bin/pip" install --quiet -r requirements.txt
ok "dependencies in sync"

# 4. Migrations. The backup above is the safety net for this step.
log "Applying database migrations"
CURRENT="$("$VENV/bin/flask" db current 2>/dev/null | tail -1 || echo unknown)"
run "$VENV/bin/flask" db upgrade
ok "schema at $("$VENV/bin/flask" db current 2>/dev/null | tail -1 || echo unknown) (was ${CURRENT:-unknown})"

# 5. Restart.
restart_app

# 6. Confirm the site actually answers.
if [[ -n "$HEALTH_URL" && $DRY_RUN -eq 0 ]]; then
  log "Health check: $HEALTH_URL"
  for attempt in 1 2 3 4 5; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$HEALTH_URL" || echo 000)"
    if [[ "$code" == "200" ]]; then ok "HTTP $code"; break; fi
    [[ $attempt -eq 5 ]] && die "Health check failed (last status $code). Roll back with: ./deploy.sh --rollback"
    sleep 3
  done
fi

if [[ $RESTARTED -eq 1 || $DRY_RUN -eq 1 ]]; then
  log "Deployed successfully."
else
  warn "Finished, but the app was NOT restarted -- it is still running the"
  warn "previous code. Restart it, then re-check the site."
  exit 1
fi
