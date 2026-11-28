#!/usr/bin/env bash
# Роли базы данных — как на wa-db01 в ЛР2 (deploy/db/setup_db.sh):
#   wh_owner — владелец базы, выполняет миграции (сервис migrate);
#   wh_app   — роль приложения, только SELECT/INSERT/UPDATE/DELETE.
# Образ postgres выполняет файлы /docker-entrypoint-initdb.d только при создании
# нового volume (пустой каталог данных); на существующих данных скрипт не запускается.
# Пароли — из deploy/docker/env/db.env. psql читает их из окружения командой \getenv,
# а не из аргументов, поэтому в списке процессов (ps) их не видно.
set -eo pipefail   # без -u: если файл не исполняемый, образ postgres выполняет его через source

: "${WH_OWNER_PASSWORD:?не задан WH_OWNER_PASSWORD (deploy/docker/env/db.env)}"
: "${WH_APP_PASSWORD:?не задан WH_APP_PASSWORD (deploy/docker/env/db.env)}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v db="$POSTGRES_DB" <<'SQL'
\getenv owner_pw WH_OWNER_PASSWORD
\getenv app_pw WH_APP_PASSWORD
CREATE ROLE wh_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'owner_pw';
CREATE ROLE wh_app   LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_pw';
ALTER DATABASE :"db" OWNER TO wh_owner;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO wh_app;

\connect :"db"
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO wh_app;
-- таблицы и последовательности, которые создадут миграции (и восстановление копии) от wh_owner
ALTER DEFAULT PRIVILEGES FOR ROLE wh_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO wh_app;
ALTER DEFAULT PRIVILEGES FOR ROLE wh_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO wh_app;
SQL

echo "10-roles.sh: созданы роли wh_owner (владелец базы $POSTGRES_DB) и wh_app"
