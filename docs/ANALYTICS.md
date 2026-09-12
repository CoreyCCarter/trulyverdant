# Analytics

Self-hosted [Umami](https://umami.is) on the VPS. Cookieless, so it needs no
consent banner and reports on **all** traffic rather than only the share who
accept cookies — and served from your own domain, so ad blockers don't strip
it.

```
browser ──▶ trulyverdant.com/stats.js   ─┐
            trulyverdant.com/api/send   ─┴─▶ nginx ──▶ umami (127.0.0.1:3000)
```

## What goes on the VPS

Only two files from this repo, plus one you create:

| File | Purpose |
| --- | --- |
| `deploy/nginx-vps.conf` | installed to `/etc/nginx/sites-available/trulyverdant` |
| `deploy/umami/docker-compose.yml` | run in place with `docker compose` |
| `deploy/umami/.env` | you create it from `.env.example`; two secrets |

No virtualenv, no Python, no migrations, and **not** the app's own `.env`.
`deploy.sh` is never run here — that is the app server's job.

Umami runs on the **same single VPS** you already have — the one holding the
WireGuard endpoint and terminating TLS to make the site reachable. It is not
a second server: it is one more container on that box, bound to
`127.0.0.1:3000`, with the nginx already running there proxying two paths to
it.

Keeping it there rather than on the app server means tracker traffic never
crosses the WireGuard tunnel or touches your home network.

---

## 1. Start Umami on the VPS

Requires Docker with the compose plugin.

```bash
cd ~/trulyverdant/deploy/umami
cp .env.example .env
# Fill both values:  openssl rand -base64 36
docker compose up -d
docker compose ps          # both services healthy
```

It binds `127.0.0.1:3000` only — nothing is reachable from the internet yet.

## 2. Enable the two public paths

The locations are already in `deploy/nginx-vps.conf`, commented out. Umami
needs exactly two paths served from your domain — uncomment both blocks in
the installed config:

```bash
sudoedit /etc/nginx/sites-available/trulyverdant
#   find "--- Umami analytics (optional) ---" and uncomment
#   the two location blocks beneath it
sudo nginx -t && sudo systemctl reload nginx
curl -sI https://yourdomain.com/stats.js | head -1      # expect 200
```

`X-Forwarded-For` is in there deliberately: without it every visitor appears
to come from the VPS itself and you lose all geography.

## 3. Reach the dashboard

Deliberately **not** published. Reach it over the tunnel from your own
machine:

```bash
ssh -L 3000:127.0.0.1:3000 you@your-vps
# then open http://localhost:3000
```

Default login is `admin` / `umami` — **change it immediately**.

Add a website in the UI with your real domain. It gives you a website ID;
that goes in the app's `.env`.

## 4. Point the app at it

On the **app server**, in `/home/verdant/trulyverdant/.env`:

```ini
UMAMI_SCRIPT_URL=/stats.js
UMAMI_WEBSITE_ID=paste-the-id-from-the-dashboard
```

```bash
sudo supervisorctl restart trulyverdant
./venv/bin/flask preflight            # analytics should read "ok"
```

## 5. Verify

```bash
curl -s https://yourdomain.com/ | grep stats.js
```

Then load the site in a browser **while signed out** and check the dashboard
shows the visit.

Signed-out visitors only is deliberate: the script is suppressed for
signed-in staff and across `/admin` and `/auth`. At low traffic your own
visits would otherwise dominate the numbers you are trying to read.

## Multiple sites on one instance

One Umami instance handles as many sites as you want — that is what it is
built for. There is no second server, container or database per site.

Add each site under **Settings → Websites** in the dashboard. Each gets its
own website ID and its own reports, all sharing the one container and one
database.

The only per-site work is the first-party script path. Each domain needs its
own pair of locations, in **that domain's** `server` block, both pointing at
the same Umami:

```nginx
# in the server block for the other domain
location = /stats.js  { proxy_pass http://127.0.0.1:3000/script.js; }
location = /api/send  { proxy_pass http://127.0.0.1:3000/api/send; }
```

Then set that site's own `UMAMI_WEBSITE_ID` in its own `.env`.

A site hosted on a different machine entirely works the same way: its
tracker talks to this VPS over the public internet via its own domain, so
nothing needs to change here.

## Backups

Umami's data is in the `umami-db` docker volume, separate from the site's
database, and `deploy.sh` does **not** back it up:

```bash
docker compose exec -T db pg_dump -U umami umami | gzip > umami-$(date +%F).sql.gz
```

Worth a cron job once you have history worth keeping.

## What to actually look at

- **Search Console** tells you which queries you nearly rank for. That is
  the highest-yield signal for deciding what to write next, and Umami
  cannot tell you it.
- **Umami** tells you which articles hold attention and where people land.
  Use it to decide what to expand, not what to write from scratch.
