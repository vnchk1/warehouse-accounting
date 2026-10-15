#!/usr/bin/env bash
# wa-app01, однократная подготовка: пакеты, служебный пользователь, каталоги и права,
# файлы настроек вне кода, unit-файл systemd, firewall (пп. 3, 4, 5, 8, 10, 11).
#   sudo bash deploy/app/setup_app.sh
# Спросит пароли ролей wh_app и wh_owner — их выводит deploy/db/setup_db.sh.
# Существующие файлы /etc/warehouse/*.env не перезаписываются.
set -euo pipefail

APP_USER=warehouse
APP_DIR=/opt/warehouse
CONF_DIR=/etc/warehouse
DB_HOST=${DB_HOST:-192.168.253.131}
DB_NAME=${DB_NAME:-warehouse}
APP_PORT=${APP_PORT:-8000}
SSH_SUBNET=${SSH_SUBNET:-192.168.253.0/24}
HERE=$(cd "$(dirname "$0")" && pwd)

[[ $EUID -eq 0 ]] || { echo "Запустите через sudo" >&2; exit 1; }

# 1. Среда выполнения: Python 3.12 из Ubuntu 24.04, venv, клиент PostgreSQL для диагностики
dpkg -s python3 python3-venv rsync curl postgresql-client ufw &>/dev/null ||
    { apt-get update && apt-get install -y python3 python3-venv rsync curl postgresql-client ufw; }

# 2. Служебный пользователь: системный, без пароля и без оболочки
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --user-group --home-dir "$APP_DIR" --no-create-home \
        --shell /usr/sbin/nologin "$APP_USER"
fi

# 3. Каталоги: код читает служба, но не может изменить; настройки видит только root
install -d -o root -g "$APP_USER" -m 750 "$APP_DIR"
install -d -o root -g root -m 700 "$CONF_DIR"

# 4. Настройки и пароли — отдельно от кода (п. 11)
umask 077
if [[ ! -f $CONF_DIR/warehouse.env ]]; then
    read -rsp "Пароль роли wh_app: " app_pw && echo
    admin_pw=$(openssl rand -hex 8)
    cat >"$CONF_DIR/warehouse.env" <<EOF
DATABASE_URL=postgresql+psycopg://wh_app:${app_pw}@${DB_HOST}:5432/${DB_NAME}
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_LOGIN=admin
ADMIN_PASSWORD=${admin_pw}
APP_HOST=0.0.0.0
APP_PORT=${APP_PORT}
SESSION_HOURS=12
PASSWORD_MIN_LENGTH=8
EOF
    echo "Создан $CONF_DIR/warehouse.env. Вход в приложение: admin / ${admin_pw}"
fi
if [[ ! -f $CONF_DIR/migrate.env ]]; then
    read -rsp "Пароль роли wh_owner: " owner_pw && echo
    echo "ALEMBIC_DATABASE_URL=postgresql+psycopg://wh_owner:${owner_pw}@${DB_HOST}:5432/${DB_NAME}" \
        >"$CONF_DIR/migrate.env"
    echo "Создан $CONF_DIR/migrate.env"
fi
chown root:root "$CONF_DIR"/*.env
chmod 600 "$CONF_DIR"/*.env
umask 022

# 5. Unit-файл службы (запускается после первого deploy_app.sh)
install -m 644 "$HERE/warehouse.service" /etc/systemd/system/warehouse.service
systemctl daemon-reload
systemctl enable warehouse

# 6. Firewall: SSH из подсети, приложение — клиентам
ufw default deny incoming
ufw default allow outgoing
ufw allow from "$SSH_SUBNET" to any port 22 proto tcp comment 'SSH admin'
ufw allow "$APP_PORT/tcp" comment 'warehouse app'
ufw --force enable
ufw status verbose

echo "Готово. Следующий шаг: sudo bash deploy/app/deploy_app.sh"
