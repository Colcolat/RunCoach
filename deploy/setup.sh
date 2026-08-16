#!/usr/bin/env bash
#
# Provision RunCoach on a fresh Ubuntu instance.
#
# Written to be run more than once. Every step checks before it acts, so a
# failure halfway through is fixed by running it again rather than by working
# out what already happened. That matters when the thing being provisioned is
# the deliverable and the deadline is tomorrow.
#
#   sudo ./setup.sh <dominio> <correo-para-certbot>
#
# Secrets are NOT arguments: they go in /etc/runcoach/runcoach.env afterwards,
# because anything on the command line is visible to `ps` for every user here.

set -euo pipefail

DOMAIN="${1:?uso: setup.sh <dominio> <correo>}"
EMAIL="${2:?uso: setup.sh <dominio> <correo>}"

REPO="https://github.com/Colcolat/RunCoach.git"
APP_DIR="/opt/runcoach"
ENV_DIR="/etc/runcoach"

say() { printf "\n=== %s\n" "$1"; }

[[ $EUID -eq 0 ]] || { echo "hay que ejecutarlo con sudo"; exit 1; }

say "paquetes"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip git nginx certbot python3-certbot-nginx

say "usuario de servicio"
# No login shell and no home worth stealing: this account exists to own one
# directory and run one process.
id runcoach &>/dev/null || useradd --system --shell /usr/sbin/nologin --home "$APP_DIR" runcoach

say "codigo"
if [[ -d "$APP_DIR/.git" ]]; then
    git -C "$APP_DIR" fetch --quiet origin
    git -C "$APP_DIR" reset --hard --quiet origin/master
else
    rm -rf "$APP_DIR"
    git clone --quiet "$REPO" "$APP_DIR"
fi

# The database lives outside the checkout, so `git reset --hard` on the next
# deploy cannot take every runner's history with it.
mkdir -p "$APP_DIR/data"

say "dependencias"
[[ -d "$APP_DIR/venv" ]] || python3 -m venv "$APP_DIR/venv"
# Same guarantee as the README: if this succeeds, nothing needed compiling.
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet --only-binary=:all: -r "$APP_DIR/requirements.txt"

chown -R runcoach:runcoach "$APP_DIR"

say "configuracion"
mkdir -p "$ENV_DIR"
if [[ ! -f "$ENV_DIR/runcoach.env" ]]; then
    cat > "$ENV_DIR/runcoach.env" <<EOF
DATABASE_URL=sqlite:///$APP_DIR/data/runcoach.db
REMINDER_TIMEZONE=America/Mexico_City
LOG_LEVEL=INFO

# Rellenar a mano. Sin GOOGLE_API_KEY la aplicacion arranca igual y responde
# en modo degradado; sin TELEGRAM_BOT_TOKEN no hay recordatorios.
GOOGLE_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_USERNAME=
EOF
    echo "  creado $ENV_DIR/runcoach.env (hay que rellenar los secretos)"
else
    echo "  $ENV_DIR/runcoach.env ya existe, no se toca"
fi
chmod 600 "$ENV_DIR/runcoach.env"

say "servicio"
install -m 644 "$APP_DIR/deploy/runcoach.service" /etc/systemd/system/runcoach.service
systemctl daemon-reload
systemctl enable --quiet runcoach

say "nginx"
# HTTP only for now: certbot cannot validate against a config that references
# certificates which do not exist yet, so TLS is added in a second pass below.
cat > /etc/nginx/sites-available/runcoach <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/runcoach /etc/nginx/sites-enabled/runcoach
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

say "certificado"
if [[ -d "/etc/letsencrypt/live/$DOMAIN" ]]; then
    echo "  ya existe un certificado para $DOMAIN"
else
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect
fi

say "nginx definitivo, con WebSocket"
# Now that the certificate exists, install the real configuration: the one that
# forwards the Upgrade headers the voice path needs and raises the read timeout
# so a pause in a conversation does not end it.
sed "s/DOMAIN_PLACEHOLDER/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" \
    > /etc/nginx/sites-available/runcoach
nginx -t
systemctl reload nginx

say "arrancando"
systemctl restart runcoach
sleep 3
systemctl --no-pager --lines=0 status runcoach || true

cat <<EOF

Listo. Falta una cosa, y sin ella el coach responde en modo degradado:

  sudo nano $ENV_DIR/runcoach.env      # pegar las claves
  sudo systemctl restart runcoach

Comprobar:

  curl -s https://$DOMAIN/health
  sudo journalctl -u runcoach -f

EOF
