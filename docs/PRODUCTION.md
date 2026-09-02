# Production runbook

Both machines run the app under the **`verdant`** user, from
`/home/verdant/trulyverdant`. Run every command below as `verdant` unless it
is prefixed with `sudo`.

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

If the `verdant` user does not exist yet:

```bash
sudo adduser --disabled-password --gecos "" verdant
sudo -u verdant -i          # run the rest of this section as verdant
```

`verdant` needs its own way to reach GitHub, since it does not share your
SSH agent. Either add a **deploy key** — generate `ssh-keygen -t ed25519` as
`verdant` and add the public key at *Settings → Deploy keys* on the repo —
or clone over HTTPS with a token. A deploy key is preferable: it grants
access to this one repository rather than your whole account, and read-only
is enough because deploys only pull.

`postgresql-client` is not optional: `deploy.sh` refuses to migrate without
`pg_dump`, which is the behaviour you want.

### 2.2 Database

Create a **new, empty** database. Do not reuse the development one.

```sql
CREATE DATABASE tvprod;
CREATE USER trulyverdant WITH PASSWORD '<generate one>';
GRANT ALL PRIVILEGES ON DATABASE trulyverdant TO trulyverdant;
```

### 2.3 Checkout

As `verdant`:

```bash
git clone git@github.com:CoreyCCarter/trulyverdant.git /home/verdant/trulyverdant
cd /home/verdant/trulyverdant
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

#### If the clone fails with "Could not read from remote repository"

Almost always one of these. Diagnose as `verdant`, not root:

```bash
whoami                 # must be verdant
echo $HOME             # must be /home/verdant
ssh -T git@github.com  # success prints "Hi <repo>! You've successfully authenticated"
```

| Cause | Check | Fix |
| --- | --- | --- |
| Cloned as the wrong user | `whoami` says `root` | `sudo git clone` reads `/root/.ssh`, never `/home/verdant/.ssh`. Clone as `verdant`: `sudo -u verdant -i` first. |
| Key permissions too open | `stat -c '%a' ~/.ssh ~/.ssh/id_ed25519` | ssh silently ignores loose keys. `chmod 700 ~/.ssh && chmod 600 ~/.ssh/id_ed25519` |
| HTTPS URL | the clone URL starts `https://` | Deploy keys only work over SSH. Use `git@github.com:CoreyCCarter/trulyverdant.git` |
| Key not offered | `ssh -vT git@github.com 2>&1 \| grep -i offering` lists nothing | A non-default filename is not tried automatically. Add to `~/.ssh/config`:<br>`Host github.com`<br>`  IdentityFile ~/.ssh/<name>`<br>`  IdentitiesOnly yes` |
| Public key not actually installed | GitHub → repo → Settings → Deploy keys | Paste the contents of the `.pub` file. Pasting the private key by mistake looks similar at a glance and never works. |
| Key already used elsewhere | GitHub rejected it when adding | A deploy key may belong to only one repository. Generate a fresh keypair for this repo. |

Generate a key as `verdant` if you need a new one:

```bash
ssh-keygen -t ed25519 -C "verdant@$(hostname)" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub      # add this at Settings -> Deploy keys
```

Read-only access is sufficient; deploys only pull.

The checkout must be **owned by `verdant`** — `install-services.sh` reads
its owner to decide which user gunicorn runs as. If you cloned as root by
mistake, fix it before continuing:

```bash
sudo chown -R verdant:verdant /home/verdant/trulyverdant
```

No permission changes are needed beyond that. Supervisor runs as root and
can read the checkout whatever its mode, and it launches gunicorn as
`verdant`, which reads its own files as itself. Nothing else needs access,
because nginx is on the VPS and never touches this disk.

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

### 2.6 Preflight

Before starting anything, check the configuration:

```bash
./venv/bin/flask preflight
```

It verifies the settings whose failure mode is silence — a development
`SECRET_KEY`, a `SITE_URL` still pointing at localhost, `SESSION_COOKIE_SECURE`
mismatched to the scheme, `SEO_INDEXABLE` left false, a bind address that
either exposes the app to every interface or cannot be reached from the VPS
— plus database reachability, schema at head, and an admin account existing.

It exits non-zero on any failure. Fix everything it reports before
continuing.

### 2.7 Firewall

Gunicorn has no authentication in front of it and trusts `X-Forwarded-*`
headers, so anything that can reach port 8000 can forge a client IP and
scheme. Binding the tunnel address is the primary control; a firewall is the
backstop.

```bash
sudo ufw allow in on wg0 to any port 8000 proto tcp
sudo ufw deny 8000
```

Verify from a third machine that `http://<app-lan-ip>:8000/` is refused.

### 2.8 Supervisor

```bash
sudo ./deploy/install-services.sh
sudo supervisorctl status trulyverdant     # expect RUNNING
```

Then, so `deploy.sh` can restart the app without running the whole deploy as
root (which would leave root-owned files in the checkout):

```bash
sudo sed -i 's|^chmod=0700.*|chmod=0770\nchown=root:verdant|' \
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
git clone git@github.com:CoreyCCarter/trulyverdant.git /home/verdant/trulyverdant
cd /home/verdant/trulyverdant
```

The VPS only needs the repo for the nginx config — no venv, no `.env`, no
database. Fill in both placeholders, then install:

```bash
sed -e 's/SERVER_NAME_HERE/yourdomain.com/g' \
    -e 's/APP_WG_IP/10.8.0.2/g' \
    deploy/nginx-vps.conf | sudo tee /etc/nginx/sites-available/trulyverdant

sudo ln -s /etc/nginx/sites-available/trulyverdant /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
```

Keeping the checkout means `git pull` picks up future changes to the nginx
config; re-run the `sed` above to reinstall it.

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
deploy. Add a daily job to `verdant`'s crontab (`crontab -e` as `verdant`, or
`sudo crontab -u verdant -e`):

```cron
17 3 * * * cd /home/verdant/trulyverdant && ./deploy.sh --backup >> /var/log/trulyverdant/backup.log 2>&1
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
cd /home/verdant/trulyverdant
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
