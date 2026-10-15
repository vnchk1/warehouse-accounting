#!/usr/bin/env bash
# wa-db01: PostgreSQL 16, база warehouse, роли wh_owner и wh_app, firewall (пп. 5, 6, 10).
#   sudo bash deploy/db/setup_db.sh
# Пароли ролей генерируются только при создании роли и выводятся один раз —
# перенесите их в /etc/warehouse/*.env на wa-app01. Повторный запуск безопасен.
set -euo pipefail

APP_IP=${APP_IP:-192.168.253.130}        # единственный узел, которому доступна БД
SSH_SUBNET=${SSH_SUBNET:-192.168.253.0/24}
DB_NAME=${DB_NAME:-warehouse}
PG_VER=16
PG_DIR=/etc/postgresql/$PG_VER/main

[[ $EUID -eq 0 ]] || { echo "Запустите через sudo" >&2; exit 1; }

# 1. Установка — только если пакетов ещё нет (при обновлении интернет не нужен)
dpkg -s "postgresql-$PG_VER" ufw &>/dev/null ||
    { apt-get update && apt-get install -y "postgresql-$PG_VER" ufw; }

# 2. Сетевые настройки сервера. listen_addresses='*' не открывает БД наружу:
#    доступ ограничивают pg_hba.conf (ниже) и firewall (п. 5 скрипта).
cat >"$PG_DIR/conf.d/10-warehouse.conf" <<EOF
listen_addresses = '*'
password_encryption = 'scram-sha-256'
EOF
chmod 644 "$PG_DIR/conf.d/10-warehouse.conf"

if ! grep -q '^# warehouse-begin' "$PG_DIR/pg_hba.conf"; then
    cat >>"$PG_DIR/pg_hba.conf" <<EOF
# warehouse-begin: база доступна только с сервера приложения
host    $DB_NAME    wh_app      $APP_IP/32    scram-sha-256
host    $DB_NAME    wh_owner    $APP_IP/32    scram-sha-256
# warehouse-end
EOF
fi
systemctl enable postgresql
systemctl restart postgresql

# 3. Роли. Пароль передаётся через stdin, а не аргументом, — его не видно в ps.
psql_admin() { sudo -u postgres psql -v ON_ERROR_STOP=1 -q "$@"; }
declare -A CREATED=()
for role in wh_owner wh_app; do
    if [[ -z $(psql_admin -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$role'") ]]; then
        password=$(openssl rand -hex 24)
        printf "CREATE ROLE %s LOGIN PASSWORD '%s';\n" "$role" "$password" | psql_admin
        CREATED[$role]=$password
    fi
done

# 4. База и минимальные права
psql_admin <<SQL
SELECT 'CREATE DATABASE $DB_NAME OWNER wh_owner'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$DB_NAME')\gexec
ALTER ROLE wh_owner NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE wh_app   NOSUPERUSER NOCREATEDB NOCREATEROLE;
REVOKE ALL ON DATABASE $DB_NAME FROM PUBLIC;
GRANT CONNECT ON DATABASE $DB_NAME TO wh_app;
\connect $DB_NAME
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO wh_app;
-- уже существующие объекты
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO wh_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO wh_app;
-- объекты, которые создадут будущие миграции от имени wh_owner
ALTER DEFAULT PRIVILEGES FOR ROLE wh_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO wh_app;
ALTER DEFAULT PRIVILEGES FOR ROLE wh_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO wh_app;
SQL

# 5. Firewall: SSH из подсети, PostgreSQL только с сервера приложения
ufw default deny incoming
ufw default allow outgoing
ufw allow from "$SSH_SUBNET" to any port 22 proto tcp comment 'SSH admin'
ufw allow from "$APP_IP" to any port 5432 proto tcp comment 'PostgreSQL from wa-app01'
ufw --force enable
ufw status verbose

# 6. Итог
echo "--- Порты:"
ss -tlnp | grep -E ':(22|5432) '
if ((${#CREATED[@]})); then
    echo "--- СОХРАНИТЕ пароли, больше они не будут показаны:"
    for role in "${!CREATED[@]}"; do echo "  $role: ${CREATED[$role]}"; done
else
    echo "Роли уже существовали, пароли не менялись."
fi
