# Production runbook

Two machines:

- **VPS** — public. Terminates TLS, runs nginx, holds the WireGuard endpoint.
- **App server** — private. Runs gunicorn, supervisor, PostgreSQL, and holds
  uploaded images on disk. Reachable only through the tunnel.

Work through this top to bottom. Nothing here is optional unless marked so.

---

## 1. Before you start

- A domain, with DNS **A/AAAA pointing at the VPS**, not the app server.
- Python 3.11+ on the app server (3.12 is the CI target; 3.14 also works).
- PostgreSQL 14+ reachable from the app server.
- A WireGuard tunnel between the two, already up and surviving reboot.

Decide the app server's WireGuard address now — it appears in three places
below. This document uses `10.8.0.2`.

---

## 2. App server

### 2.1 Packages

```bash
sudo apt install python3-venv postgresql-client supervisor
```

`postgresql-client` is not optional: `deploy.sh` refuses to migrate without
`pg_dump`, which is the behaviour you want.

### 2.2 Database

Create a **new, empty** database. Do not reuse the development one.

```sql
CREATE DATABASE trulyverdant;
CREATE USER trulyverdant WITH PASSWORD '<generate one>';
GRANT ALL PRIVILEGES ON DATABASE trulyverdant TO trulyverdant;
```

### 2.3 Checkout

```bash
sudo install -d -o "$USER" -g "$USER" /srv/trulyverdant
git clone git@github.com:CoreyCCarter/trulyverdant.git /srv/trulyverdant
cd /srv/trulyverdant
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

`/srv` avoids the home-directory permission problem entirely. If you deploy
into a home directory instead, whichever user runs gunicorn must be able to
read the checkout.

### 2.4 Configuration

```bash
cp .env.example .env
chmod 600 .env          # it holds the database password and secret key
```

Fill it in. These **must** differ from development:

| Variable | Value | Why |
| --- | --- | --- |
| `SECRET_KEY` | freshly generated, unique | Signs session cookies. Reusing the dev key means anyone with it can forge a logged-in session. |
| `DATABASE_URL` | the production database | |
| `SITE_URL` | `https://yourdomain.com` | Canonical tags, sitemap and RSS are built from this. Wrong value = wrong URLs submitted to Google. |
| `SESSION_COOKIE_SECURE` | `true` | TLS terminates at the VPS, so the browser's connection is https. Dev sets this false for plain http. |
| `GUNICORN_BIND` | `10.8.0.2:8000` | The WireGuard address. **Never `0.0.0.0`** — see §2.6. |
| `PROXY_FIX_HOPS` | `1` | The VPS nginx is the single proxy. |
| `SEO_INDEXABLE` | `true` | `false` emits noindex and blocks crawlers — correct for staging, fatal for production. |

Generate the secret with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Leave every `ADSENSE_*` value and `ADS_TXT` blank for now. While blank the
app emits no Google script at all and `/ads.txt` returns 404, which is what
you want before approval.

### 2.5 Schema and first account

```bash
./venv/bin/flask db upgrade
./venv/bin/flask create-admin       # prompts for username, email, password
```

Do **not** run `flask seed-demo` on production; it inserts placeholder
articles.

### 2.6 Firewall

Gunicorn has no authentication in front of it and trusts `X-Forwarded-*`
headers, so anything that can reach port 8000 can forge a client IP and
scheme. Binding the tunnel address is the primary control; a firewall is the
backstop.

```bash
sudo ufw allow in on wg0 to any port 8000 proto tcp
sudo ufw deny 8000
```

Verify from a third machine that `http://<app-lan-ip>:8000/` is refused.

### 2.7 Supervisor

```bash
sudo ./deploy/install-services.sh
sudo supervisorctl status trulyverdant     # expect RUNNING
```

Then, so `deploy.sh` can restart the app without running the whole deploy as
root (which would leave root-owned files in the checkout):

```bash
sudo sed -i 's|^chmod=0700.*|chmod=0770\nchown=root:YOUR_USER|' \
    /etc/supervisor/supervisord.conf
sudo systemctl restart supervisor
supervisorctl status trulyverdant          # must work unprivileged
```

Confirm the app answers locally before involving the VPS:

```bash
curl -I http://10.8.0.2:8000/
```

---

## 3. VPS

### 3.1 nginx

```bash
sudo apt install nginx certbot python3-certbot-nginx
```

Copy `deploy/nginx-vps.conf` across, replacing both placeholders:

- `SERVER_NAME_HERE` → your domain
- `APP_WG_IP` → `10.8.0.2`

```bash
sudo cp nginx-vps.conf /etc/nginx/sites-available/trulyverdant
sudo ln -s /etc/nginx/sites-available/trulyverdant /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
```

### 3.2 TLS

```bash
sudo certbot --nginx -d yourdomain.com
sudo systemctl reload nginx
systemctl list-timers | grep certbot     # confirm auto-renewal is armed
```

Certbot uncomments and fills the two `ssl_certificate` lines.

---

## 4. Verify before announcing

```bash
curl -I https://yourdomain.com/                    # 200
curl -s https://yourdomain.com/robots.txt          # names your real sitemap URL
curl -s https://yourdomain.com/sitemap.xml | head  # absolute https URLs
curl -sI https://yourdomain.com/static/css/style.css   # 200
curl -sI http://yourdomain.com/                    # 301 to https
```

Then in a browser:

- Sign in at `/auth/login`. **If sign-in silently fails, `SESSION_COOKIE_SECURE`
  is wrong for your scheme.**
- Publish a test article with an image; confirm it renders and the image loads.
- Check the page source: `<link rel="canonical">` must show your real domain,
  and `<meta name="robots">` must not say `noindex`.

Check the startup log for the insecure-cookie warning:

```bash
grep SESSION_COOKIE_SECURE /var/log/trulyverdant/gunicorn-error.log
```

Nothing there is correct. A hit means fix `.env` and restart.

---

## 5. Backups

`deploy.sh` takes one before every migration, but that only covers days you
deploy. Add a daily job:

```cron
17 3 * * * cd /srv/trulyverdant && ./deploy.sh --backup >> /var/log/trulyverdant/backup.log 2>&1
```

Backups land in `backups/` (last 10 kept, `KEEP_BACKUPS` to change) and
include the uploaded images, which are on disk and not in the database dump.

**Copy them off the machine.** A backup on the server it protects is not a
backup. Any of rsync, restic or rclone to object storage will do.

**Do a restore drill once, before you need it.** `./deploy.sh --rollback`
restores the most recent dump and asks you to type the database name to
confirm. Prove it works while nothing is on fire.

---

## 6. Deploying changes

```bash
cd /srv/trulyverdant
HEALTH_URL=https://yourdomain.com/ ./deploy.sh
```

Backs up, pulls, installs dependencies, migrates, restarts, then confirms
the site returns 200. It refuses to pull over uncommitted changes, and exits
non-zero if it could not restart — a deploy that did not restart has not
deployed anything.

---

## 7. AdSense

Only after the site is live with real content.

1. **Publish 15–25 substantial original articles.** Thin content is the most
   common rejection reason by a wide margin. This is the step people skip.
2. Fill in `/about` and `/contact`, and **have someone review `/privacy`** —
   it is a working template, not legal advice reviewed for your situation.
3. Submit `https://yourdomain.com/sitemap.xml` to Google Search Console and
   confirm pages are actually being indexed.
4. Apply to AdSense. Once approved, set in `.env`:
   - `ADSENSE_CLIENT_ID` — `ca-pub-…`
   - `ADS_TXT` — the exact line AdSense gives you. Until this is set,
     `/ads.txt` returns 404 and Google will not serve ads.
5. Create ad units in the AdSense dashboard and set `ADSENSE_SLOT_HEADER`,
   `ADSENSE_SLOT_IN_ARTICLE`, `ADSENSE_SLOT_SIDEBAR`. Any left blank simply
   render nothing.
6. Restart, then confirm `https://yourdomain.com/ads.txt` returns your line.

### Cookie consent

The built-in banner blocks ad scripts until a visitor accepts, which is the
correct default. It is **not** a Google-certified CMP. If you expect
meaningful EEA or UK traffic, Google requires one — their "Privacy &
messaging" tool is free. Once a CMP is installed, set
`REQUIRE_COOKIE_CONSENT=false` and let it handle gating.

---

## 8. Adding writers

There is no public sign-up. From **Admin → People**, or:

```bash
./venv/bin/flask invite someone@example.com --role author
```

Send them the printed link. It works once and expires in 14 days. Authors
can only touch their own articles; admins can touch any.
