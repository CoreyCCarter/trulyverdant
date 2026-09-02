#!/usr/bin/env bash
#
# Install the nginx + supervisor units for THIS checkout.
#
#   sudo ./deploy/install-services.sh --local        # http on localhost, dev
#   sudo ./deploy/install-services.sh example.com    # with TLS placeholders
#
# Rewrites the shipped templates to use this checkout's real path and owner
# instead of /srv/trulyverdant and www-data, which is the usual reason a
# first install fails: www-data cannot read a home directory.

set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The user who owns the checkout -- not root, who is running this script.
APP_USER="$(stat -c '%U' "$APP_DIR")"
APP_GROUP="$(stat -c '%G' "$APP_DIR")"
MODE="${1:-}"

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
[[ -n "$MODE" ]] || { sed -n '3,8p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

if [[ "$MODE" == "--local" ]]; then SERVER_NAME=localhost; LOCAL=1
else SERVER_NAME="$MODE"; LOCAL=0; fi

echo "==> checkout : $APP_DIR"
echo "==> run as   : $APP_USER:$APP_GROUP"
echo "==> server   : $SERVER_NAME"

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

# --- nginx ---------------------------------------------------------------
NGINX_TMP="$(mktemp)"

if [[ $LOCAL -eq 1 ]]; then
  # A purpose-built http-only vhost. The shipped template's `listen 443 ssl`
  # cannot pass `nginx -t` without a certificate, so locally we do not ship
  # a TLS block at all rather than comment one out.
  cat > "$NGINX_TMP" <<EOF
upstream trulyverdant_app { server 127.0.0.1:8000 fail_timeout=0; }

server {
    listen 80;
    listen [::]:80;
    server_name $SERVER_NAME;
    root $APP_DIR;
    client_max_body_size 12m;

    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    gzip on; gzip_vary on; gzip_min_length 512;
    gzip_types text/plain text/css application/json application/javascript
               application/rss+xml application/xml image/svg+xml;

    location /static/ {
        alias $APP_DIR/app/static/;
        access_log off;
        add_header Cache-Control "public, max-age=3600";
        try_files \$uri =404;
    }

    location / {
        proxy_pass http://trulyverdant_app;
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host  \$host;
        proxy_redirect off;
    }
}
EOF
else
  # Production: keep the full template, TLS block included. The certificate
  # lines stay commented until certbot fills them in, so run certbot before
  # reloading nginx.
  sed -e "s#/srv/trulyverdant#$APP_DIR#g" \
      -e "s#SERVER_NAME_HERE#$SERVER_NAME#g" \
      "$APP_DIR/deploy/nginx.conf" > "$NGINX_TMP"
fi

install -m 0644 "$NGINX_TMP" /etc/nginx/sites-available/trulyverdant
rm -f "$NGINX_TMP"
ln -sfn /etc/nginx/sites-available/trulyverdant /etc/nginx/sites-enabled/trulyverdant

# The stock default vhost owns port 80 and shadows this one for other hosts.
if [[ -e /etc/nginx/sites-enabled/default ]]; then
  rm -f /etc/nginx/sites-enabled/default
  echo "  ok removed nginx default site"
fi
echo "  ok /etc/nginx/sites-available/trulyverdant"

# --- apply ---------------------------------------------------------------
nginx -t
systemctl reload nginx
supervisorctl reread
supervisorctl update
# update only restarts a program whose supervisor config changed; restart
# explicitly so edits to deploy/gunicorn.conf.py take effect too.
supervisorctl restart trulyverdant || true
sleep 4
supervisorctl status trulyverdant || true

echo
echo "==> done. Check:  curl -I http://$SERVER_NAME/"
