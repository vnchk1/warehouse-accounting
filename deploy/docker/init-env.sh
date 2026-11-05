#!/usr/bin/env bash
# Файлы настроек контейнеров со случайными паролями: make container-env.
#   deploy/docker/env/db.env       — контейнер db: пароли postgres, wh_owner, wh_app
#   deploy/docker/env/app.env      — app и migrate: DATABASE_URL (wh_app), SECRET_KEY, ADMIN_*
#   deploy/docker/env/migrate.env  — только migrate: ALEMBIC_DATABASE_URL (wh_owner)
# Как /etc/warehouse/*.env в ЛР2: владелец — тот, кто запустил (через sudo — root), права 600,
# в git и в образ не попадают (.gitignore, .dockerignore). Существующие файлы не меняются:
# пароли ролей уже записаны в базе, и новые с ними не совпали бы.
set -euo pipefail
cd "$(dirname "$0")/env"

DB_NAME=warehouse
random() { openssl rand -hex "$1"; }   # только [0-9a-f]: безопасно внутри адреса базы

if [[ -f db.env || -f app.env || -f migrate.env ]]; then
    for file in db.env app.env migrate.env; do
        [[ -f $file ]] || { echo "Нет $PWD/$file при наличии остальных — восстановите его" >&2; exit 1; }
    done
    echo "Файлы настроек уже есть — не изменяются: $PWD/{db,app,migrate}.env"
    exit 0
fi

owner_pw=$(random 24)
app_pw=$(random 24)
admin_pw=$(random 8)
umask 077

cat >db.env <<EOF
POSTGRES_PASSWORD=$(random 24)
WH_OWNER_PASSWORD=$owner_pw
WH_APP_PASSWORD=$app_pw
EOF

cat >app.env <<EOF
DATABASE_URL=postgresql+psycopg://wh_app:$app_pw@db:5432/$DB_NAME
SECRET_KEY=$(random 32)
ADMIN_LOGIN=admin
ADMIN_PASSWORD=$admin_pw
EOF

cat >migrate.env <<EOF
ALEMBIC_DATABASE_URL=postgresql+psycopg://wh_owner:$owner_pw@db:5432/$DB_NAME
EOF

chmod 600 db.env app.env migrate.env
echo "Созданы $PWD/{db,app,migrate}.env (права 600)"
echo "Вход в приложение на новой базе: admin / $admin_pw"
