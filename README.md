# TrulyVerdant

A plant-blog platform: public, crawlable articles written by an invited team,
built to satisfy Google AdSense's requirements.

Flask 3 · SQLAlchemy 2 · PostgreSQL · gunicorn behind nginx · no frontend
build step.

## What it does

- **Public, indexable articles.** Nothing readers need is behind a login —
  the single hardest AdSense requirement to retrofit.
- **Invite-only authorship.** No public sign-up route exists. Admins issue
  single-use invitation links; recipients set their own password.
- **Markdown editing** with server-rendered live preview. Article HTML is
  sanitised on save, so stored content is safe by construction.
- **Responsive images.** Uploads are re-encoded to WebP at several widths,
  EXIF stripped, and served via `srcset`.
- **SEO built in:** canonical URLs, meta descriptions, Open Graph, JSON-LD
  `Article` data, `sitemap.xml`, `robots.txt`, and an RSS feed.
- **Ad slots that respect consent.** No Google script is emitted until a
  publisher ID is configured *and* the visitor accepts cookies.
- **Light / dark / system theme**, chosen by the reader and remembered. The
  saved choice is applied before first paint, so there is no flash of the
  wrong palette.
- **Mobile first.** 16px minimum inputs (so iOS does not zoom on focus),
  44px touch targets, safe-area padding on notched phones, self-scrolling
  tables, and a print stylesheet.

## Setup

Requires PostgreSQL. `postgresql-client` is also needed for `deploy.sh`
backups (`pg_dump`/`pg_restore`).

```bash
sudo apt install postgresql-client nginx supervisor

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # SECRET_KEY, SITE_URL and DATABASE_URL are required
flask db upgrade
flask create-admin        # prompts for username, email, password
```

For day-to-day work `flask run` is fine. To run the way production does —
gunicorn behind nginx, managed by supervisor — see **Serving** below.

Add sample content to see the layout with something in it:

```bash
flask seed-demo
```

## Commands

| Command | Purpose |
| --- | --- |
| `flask create-admin` | Create the first administrator. |
| `flask invite EMAIL [--role admin\|author]` | Print an invitation link. |
| `flask seed-demo` | Add sample categories and articles. |
| `flask db upgrade` | Apply database migrations. |
| `pytest` | Run the test suite. |

Invitations can also be issued from **Admin → People**.

## Roles

- **admin** — everything, including categories, accounts and invitations.
- **author** — writes and publishes their own articles, and nothing else.

Authors cannot view, edit or delete another author's work; both are enforced
server-side and covered by tests.

## Going live with AdSense

The code side is done. The rest is account setup and content, in this order:

1. **Deploy to a real domain over HTTPS** and set `SITE_URL` to it. AdSense
   will not approve a site on localhost or a temporary host.
2. **Publish substantial original content first.** This is the most common
   rejection reason by a wide margin. Aim for 15–25 genuine articles before
   applying, not three.
3. **Fill in the placeholder pages.** `/about`, `/contact` and `/privacy`
   render real, reviewable text — but the privacy policy is a *starting
   template*. Have someone confirm it matches what you actually do before you
   rely on it.
4. **Apply to AdSense**, then set `ADSENSE_CLIENT_ID` and `ADS_TXT`.
   Until `ADS_TXT` is set, `/ads.txt` returns 404 by design.
5. **Create ad units** in the AdSense dashboard and put their slot IDs in
   `ADSENSE_SLOT_HEADER`, `ADSENSE_SLOT_IN_ARTICLE`, `ADSENSE_SLOT_SIDEBAR`.
   Any slot left blank simply renders nothing.
6. **Submit `sitemap.xml`** to Google Search Console.

### On cookie consent

The built-in banner blocks ad and analytics scripts until the visitor
accepts, which is the correct default. It is **not** a Google-certified CMP.
If you expect meaningful EEA or UK traffic, Google requires a certified CMP
(their own "Privacy & messaging" tool is free) — set `REQUIRE_COOKIE_CONSENT=false`
and let the CMP take over gating once one is installed.

## Serving

The Flask development server is single-process and not built for real
traffic. The application runs under gunicorn, supervised by supervisor, on a
private host. A separate public VPS terminates TLS and proxies to it over a
WireGuard tunnel.

```
                    public internet
                          │ https
                    ┌─────▼─────┐
                    │ VPS nginx │   TLS, gzip, security headers
                    └─────┬─────┘
                          │ http over wg0
                    ┌─────▼──────────────────┐
                    │ app server             │
                    │  gunicorn (gthread)    │  supervised by supervisor
                    │  Flask app             │
                    │  app/static + uploads  │
                    └────────────────────────┘
```

The app server runs **no nginx**. Two files live there, one on the VPS:

| File | Host | Install to |
| --- | --- | --- |
| `deploy/gunicorn.conf.py` | app server | used in place, via `-c` |
| `deploy/supervisor.conf` | app server | `/etc/supervisor/conf.d/trulyverdant.conf` |
| `deploy/nginx-vps.conf` | **VPS** | `/etc/nginx/sites-available/trulyverdant` |

### App server

```bash
sudo ./deploy/install-services.sh
```

Set `GUNICORN_BIND` in `.env` to this host's **WireGuard** address, e.g.
`10.8.0.2:8000`. Never `0.0.0.0`: nothing authenticates in front of the app,
and it trusts `X-Forwarded-*`, so anything that can reach the port can forge
its own client IP and scheme. Binding the tunnel makes the VPS the only way
in.

### VPS

Edit `deploy/nginx-vps.conf`, replacing `SERVER_NAME_HERE` with the domain
and `APP_WG_IP` with the app server's WireGuard address, then:

```bash
sudo cp nginx-vps.conf /etc/nginx/sites-available/trulyverdant
sudo ln -s /etc/nginx/sites-available/trulyverdant /etc/nginx/sites-enabled/
sudo certbot --nginx -d yourdomain.com
sudo nginx -t && sudo systemctl reload nginx
```

Because nginx is on a different machine it cannot read the app's disk, so
`/static/` — including uploaded images — is proxied like everything else.
Every asset therefore crosses the tunnel and occupies a gunicorn thread. If
that becomes slow once there are real images, add a `proxy_cache` to the
`location /` block on the VPS; nothing in the app needs to change.

The config returns a plain 503 page rather than a raw 502 when the tunnel is
down.

### Wiring up nginx + supervisor

Nothing is served until the units are installed — supervisor with no program
config manages nothing, and nginx keeps showing its default page. One script
adapts the templates to this checkout's real path and owner and installs
them:

```bash
sudo ./deploy/install-services.sh --local        # http on localhost, for dev
sudo ./deploy/install-services.sh example.com    # production, TLS placeholders
```

It creates `/run/trulyverdant` and `/var/log/trulyverdant`, adds a
`tmpfiles.d` entry so the socket directory survives a reboot (`/run` is
tmpfs), installs both configs, removes nginx's default site, then runs
`nginx -t` and `supervisorctl update`.

The templates ship with `/srv/trulyverdant` and `user=www-data`. The script
rewrites both to the checkout's actual path and owner — `www-data` cannot
read a home directory, which is the usual reason a hand-rolled first install
fails to start.

Afterwards:

```bash
sudo supervisorctl status trulyverdant
curl -I http://localhost/
```

### Letting deploy.sh restart the app without sudo

Supervisor's control socket is `0700 root:root` by default, so
`supervisorctl` — and therefore `deploy.sh`'s restart step — only works as
root. Running the whole deploy as root is worse: `git pull` and `pip
install` would leave root-owned files in the checkout. Grant just socket
access instead:

```bash
sudo sed -i 's|^chmod=0700.*|chmod=0770\nchown=root:YOUR_USER|' \
    /etc/supervisor/supervisord.conf
sudo systemctl restart supervisor
supervisorctl status trulyverdant     # now works unprivileged
```

`deploy.sh` falls back to `sudo -n supervisorctl` if that is permitted, and
otherwise says so plainly and **exits non-zero** rather than reporting a
success it did not achieve — the new code is not live until the app
restarts.

For production, run certbot and uncomment the two `ssl_certificate` lines in
`/etc/nginx/sites-available/trulyverdant` before reloading.

nginx serves `/static` itself, so the app never handles an asset request.
Uploads are real files under `app/static/uploads/` — put them on persistent
storage, because a rebuild without a mounted volume loses every image.

Set `PROXY_FIX_HOPS=1` so `request.scheme` and the client IP reflect the
browser's real request rather than the tunnel hop. Raise it only if you add
another proxy in front; trusting more hops than exist lets a client forge
its own IP and scheme.

TLS terminates at the VPS, so the browser's connection **is** https and
`SESSION_COOKIE_SECURE` must be `true`. It is only ever `false` for serving
the app over plain http directly, which is a development arrangement. The
app logs a warning at startup if it is false outside debug, because the
consequence — session cookies in the clear — is otherwise invisible.

## Deploying

Standing up production for the first time: **[docs/PRODUCTION.md](docs/PRODUCTION.md)**.

```bash
./deploy.sh                # backup, pull, deps, migrate, restart, health check
./deploy.sh --dry-run      # show what would happen, change nothing
./deploy.sh --backup       # backup only
./deploy.sh --no-pull      # deploy the working tree as-is
./deploy.sh --rollback     # restore the most recent backup (asks to confirm)
```

The ordering is deliberate: the database is dumped **before** migrations
run, and the app is only restarted **after** they succeed. Any failure
aborts with the previous release still serving. A pull is refused outright
if the working tree has uncommitted changes.

Backups land in `backups/` (override with `BACKUP_DIR`), keeping the last 10
(`KEEP_BACKUPS`). Uploaded media is archived alongside the database, since
it lives on disk and is not in the dump. `backups/` is gitignored — the
dumps contain real user data including password hashes.

Set `HEALTH_URL` to have the script confirm the site actually answers 200
after restarting, and fail loudly if it does not.

## Tests

```bash
pytest                                    # in-memory SQLite, fast
TEST_DATABASE_URL=postgresql://... pytest # against real Postgres
```

48 tests covering content sanitisation, the authorisation boundaries between
authors, the invite lifecycle, SEO endpoint output, theme and mobile markup,
and ad gating in every configuration/consent combination.

CI (`.github/workflows/ci.yml`) runs the suite against PostgreSQL 16, applies
the migrations from scratch, and runs `flask db check` — which fails the
build if a model changed without a matching migration.
