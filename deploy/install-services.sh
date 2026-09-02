#!/usr/bin/env bash
#
# Install the supervisor unit for THIS checkout.
#
#   sudo ./deploy/install-services.sh
#
# Installs the supervisor program for THIS checkout, rewriting the shipped
# template to use its real path and owner instead of /srv/trulyverdant and
# www-data -- www-data cannot read a home directory, which is the usual
# reason a first install fails to start.
#
# This host runs the application only. nginx lives on the public VPS and
# reaches gunicorn over WireGuard; see deploy/nginx-vps.conf.

set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The user who owns the checkout -- not root, who is running this script.
APP_USER="$(stat -c '%U' "$APP_DIR")"
APP_GROUP="$(stat -c '%G' "$APP_DIR")"
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

echo "==> checkout : $APP_DIR"
echo "==> run as   : $APP_USER:$APP_GROUP"

[[ -x "$APP_DIR/venv/bin/gunicorn" ]] || { echo "No venv/bin/gunicorn in $APP_DIR" >&2; exit 1; }

# --- runtime directories -------------------------------------------------
# gunicorn binds loopback TCP, so no socket directory is needed. Clean up
# the socket-era artefacts if an earlier run created them.
install -d -o "$APP_USER" -g "$APP_GROUP" -m 0755 /var/log/trulyverdant
rm -f /etc/tmpfiles.d/trulyverdant.conf
rm -rf /run/trulyverdant
echo "  ok log dir (loopback TCP upstream; no socket dir needed)"

# --- supervisor ----------------------------------------------------------
sed -e "s#/srv/trulyverdant#$APP_DIR#g" \
    -e "s#^user=www-data#user=$APP_USER#" \
    "$APP_DIR/deploy/supervisor.conf" > /etc/supervisor/conf.d/trulyverdant.conf
echo "  ok /etc/supervisor/conf.d/trulyverdant.conf"

# --- apply ---------------------------------------------------------------
supervisorctl reread
supervisorctl update
# update only restarts a program whose supervisor config changed; restart
# explicitly so edits to deploy/gunicorn.conf.py take effect too.
supervisorctl restart trulyverdant || true
sleep 4
supervisorctl status trulyverdant || true

BIND="$(grep -oP '^GUNICORN_BIND=\K.*' "$APP_DIR/.env" 2>/dev/null || echo '127.0.0.1:8000 (default)')"
echo
echo "==> done. gunicorn is bound to: $BIND"
echo "    From the VPS:  curl -I http://<this-host-wg-ip>:8000/"
echo "    nginx config for the VPS is deploy/nginx-vps.conf"
