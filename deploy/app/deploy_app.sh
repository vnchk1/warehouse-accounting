#!/usr/bin/env bash
# wa-app01, развертывание версии приложения без контейнеров (п. 7). Повторяется при
# каждом обновлении: копирование кода, зависимости, права, миграции, начальные данные,
# перезапуск службы и проверка /healthz.
#   sudo bash deploy/app/deploy_app.sh            # из корня распакованного проекта
#   sudo DEMO=1 bash deploy/app/deploy_app.sh     # плюс демо-данные
set -euo pipefail

APP_USER=warehouse
APP_DIR=/opt/warehouse
CONF_DIR=/etc/warehouse
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(cd "$HERE/../.." && pwd)

[[ $EUID -eq 0 ]] || { echo "Запустите через sudo" >&2; exit 1; }
[[ -f $CONF_DIR/warehouse.env && -f $CONF_DIR/migrate.env ]] ||
    { echo "Нет файлов $CONF_DIR/*.env — сначала setup_app.sh" >&2; exit 1; }
[[ $SRC != "$APP_DIR" ]] || { echo "Запускайте из копии проекта, а не из $APP_DIR" >&2; exit 1; }

echo "== 1. Код: $SRC -> $APP_DIR"
rsync -a --delete --exclude-from=- "$SRC/" "$APP_DIR/" <<'EOF'
/.venv/
/.git/
/.githooks/
/.env
/tests/
/tools/
/scripts/
/deploy/
/reports/
/backups/
__pycache__/
.pytest_cache/
.ruff_cache/
*.egg-info/
EOF

echo "== 2. Виртуальное окружение и зависимости из pyproject.toml"
[[ -x $APP_DIR/.venv/bin/python ]] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
requirements=$(mktemp)
python3 - "$APP_DIR/pyproject.toml" >"$requirements" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as file:
    print("\n".join(tomllib.load(file)["project"]["dependencies"]))
PY
"$APP_DIR/.venv/bin/pip" install --quiet -r "$requirements"
rm -f "$requirements"
"$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/app" "$APP_DIR/migrations"

echo "== 3. Права: владелец root, группа $APP_USER только читает (п. 4)"
chown -R root:"$APP_USER" "$APP_DIR"
chmod -R u=rwX,g=rX,o= "$APP_DIR"

# Команда от имени служебного пользователя с переменными из указанных файлов
as_app() {
    local files=() arg
    while [[ $1 == *.env ]]; do files+=("$1"); shift; done
    (
        set -a
        for arg in "${files[@]}"; do . "$arg"; done
        set +a
        cd "$APP_DIR"
        runuser -u "$APP_USER" -- "$@"
    )
}

echo "== 4. Миграции (роль-владелец wh_owner)"
as_app "$CONF_DIR/warehouse.env" "$CONF_DIR/migrate.env" .venv/bin/alembic upgrade head

echo "== 5. Справочник ОКЕИ и администратор (роль приложения wh_app)"
as_app "$CONF_DIR/warehouse.env" .venv/bin/python -m app.seed
if [[ ${DEMO:-0} == 1 ]]; then
    as_app "$CONF_DIR/warehouse.env" .venv/bin/python -m app.demo_seed
fi

echo "== 6. Перезапуск службы"
systemctl restart warehouse
port=$(sed -n 's/^APP_PORT=//p' "$CONF_DIR/warehouse.env")
for _ in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:${port:-8000}/healthz"; then
        echo && systemctl --no-pager --lines=0 status warehouse
        exit 0
    fi
    sleep 1
done
echo "Служба не ответила за 20 с. Журнал: journalctl -u warehouse -n 50" >&2
exit 1
