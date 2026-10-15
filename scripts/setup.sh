#!/usr/bin/env bash
# Первоначальная настройка проекта на машине разработчика (Linux, WSL2): make setup.
# Один сценарий от чистой копии репозитория до работающего приложения и тестов.
# Повторный запуск безопасен: уже выполненные шаги пропускаются.
#
#   1. Проверка программ: Python не ниже 3.11, модуль venv, клиент PostgreSQL.
#   2. Виртуальное окружение .venv и зафиксированные версии зависимостей.
#   3. Файл .env из .env.example со случайными паролями (готовый .env не меняется).
#   4. PostgreSQL: роль, рабочая и тестовая базы по адресам из .env.
#   5. Миграции схемы и начальные данные (make migrate, make seed).
#   6. git-хук pre-push: make verify перед каждой отправкой изменений.
#
# Переменные: PYTHON (по умолчанию python3), VENV (.venv),
#             SKIP_DB=1 — не трогать PostgreSQL (шаги 4–5).
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-python3}
VENV=${VENV:-.venv}
PY="$VENV/bin/python"

step() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nОшибка: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
step "1/6 Проверка программ"
command -v "$PYTHON" >/dev/null || fail "не найден $PYTHON. Установите: sudo apt install python3 python3-venv"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' ||
    fail "нужен Python 3.11 или новее, найден $("$PYTHON" --version)"
"$PYTHON" -c 'import ensurepip, venv' 2>/dev/null ||
    fail "нет модуля venv. Установите: sudo apt install python3-venv"
for program in psql pg_dump pg_restore pg_isready; do
    command -v "$program" >/dev/null ||
        fail "не найдена программа $program. Установите: sudo apt install postgresql postgresql-client"
done
echo "$("$PYTHON" --version), $(pg_dump --version)"

# ---------------------------------------------------------------------------
step "2/6 Виртуальное окружение $VENV и зафиксированные версии зависимостей"
[[ -x $PY ]] || "$PYTHON" -m venv "$VENV"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -e ".[dev]"
"$PY" -m pip check

# ---------------------------------------------------------------------------
step "3/6 Файл настроек .env"
if [[ -f .env ]]; then
    echo ".env уже есть — не изменяется"
else
    "$PY" - <<'PY'
import secrets
from pathlib import Path

db_password = secrets.token_hex(16)  # только [0-9a-f]: безопасно внутри адреса базы
values = {
    "DATABASE_URL": f"postgresql+psycopg://warehouse:{db_password}@localhost:5432/warehouse",
    "TEST_DATABASE_URL": f"postgresql+psycopg://warehouse:{db_password}@localhost:5432/warehouse_test",
    "SECRET_KEY": secrets.token_urlsafe(32),
    "ADMIN_PASSWORD": secrets.token_urlsafe(9),
}
lines = []
for line in Path(".env.example").read_text(encoding="utf-8").splitlines():
    key = line.partition("=")[0]
    lines.append(f"{key}={values[key]}" if key in values else line)
Path(".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Создан .env. Вход в приложение: admin / {values['ADMIN_PASSWORD']}")
PY
    chmod 600 .env
fi

# ---------------------------------------------------------------------------
# Параметры подключения из .env: печатает строки DB_HOST=… для eval
db_params() {
    "$PY" - <<'PY'
import shlex
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

values = {}
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    key, sep, value = line.partition("=")
    if sep and not key.lstrip().startswith("#"):
        values[key.strip()] = value.strip().strip("'\"")
url = make_url(values["DATABASE_URL"])
test = make_url(values.get("TEST_DATABASE_URL") or f"{values['DATABASE_URL']}_test")
for name, value in {
    "DB_HOST": url.host or "localhost",
    "DB_PORT": url.port or 5432,
    "DB_USER": url.username or "",
    "DB_PASSWORD": url.password or "",
    "DB_NAME": url.database or "",
    "TEST_NAME": test.database or "",
}.items():
    print(f"{name}={shlex.quote(str(value))}")
PY
}

as_postgres() { (cd /tmp && sudo -u postgres psql -v ON_ERROR_STOP=1 -qtA "$@"); }
can_connect() {
    PGPASSWORD=$DB_PASSWORD psql -w -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$1" \
        -tAc 'SELECT 1' >/dev/null 2>&1
}

step "4/6 PostgreSQL: роль и базы данных"
if [[ ${SKIP_DB:-0} == 1 ]]; then
    echo "пропущено (SKIP_DB=1)"
else
    eval "$(db_params)"
    [[ $TEST_NAME == *_test ]] || fail "имя тестовой базы должно оканчиваться на _test: $TEST_NAME"
    local_db=0
    [[ $DB_HOST == localhost || $DB_HOST == 127.0.0.1 ]] && local_db=1
    if ! pg_isready -q -h "$DB_HOST" -p "$DB_PORT"; then
        ((local_db)) || fail "PostgreSQL на $DB_HOST:$DB_PORT не отвечает"
        echo "PostgreSQL не запущен — запуск: sudo service postgresql start"
        sudo service postgresql start
        sleep 2
        pg_isready -q -h "$DB_HOST" -p "$DB_PORT" || fail "PostgreSQL не отвечает на $DB_HOST:$DB_PORT"
    fi
    if can_connect "$DB_NAME" && can_connect "$TEST_NAME"; then
        echo "Роль $DB_USER и базы $DB_NAME, $TEST_NAME уже есть"
    else
        ((local_db)) || fail "нет доступа к базам на $DB_HOST — создайте роль и базы вручную"
        echo "Создание роли и баз через sudo -u postgres (может быть запрошен пароль sudo)"
        if [[ -z $(as_postgres -c "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'") ]]; then
            # пароль передаётся через stdin, а не аргументом — его не видно в списке процессов
            printf "CREATE ROLE \"%s\" LOGIN CREATEDB PASSWORD '%s';\n" "$DB_USER" "$DB_PASSWORD" |
                as_postgres
            echo "  создана роль $DB_USER (с правом CREATEDB для тестов миграций)"
        elif ! can_connect postgres; then
            fail "роль $DB_USER уже есть, но пароль в .env не подходит. Исправьте пароль в
       DATABASE_URL и TEST_DATABASE_URL или задайте его роли:
       sudo -u postgres psql -c \"ALTER ROLE $DB_USER PASSWORD '<пароль из .env>'\""
        fi
        as_postgres -c "ALTER ROLE \"$DB_USER\" CREATEDB"
        for database in "$DB_NAME" "$TEST_NAME"; do
            if [[ -z $(as_postgres -c "SELECT 1 FROM pg_database WHERE datname = '$database'") ]]; then
                as_postgres -c "CREATE DATABASE \"$database\" OWNER \"$DB_USER\""
                echo "  создана база $database"
            fi
        done
    fi
fi

# ---------------------------------------------------------------------------
step "5/6 Миграции и начальные данные"
if [[ ${SKIP_DB:-0} == 1 ]]; then
    echo "пропущено (SKIP_DB=1)"
else
    "$VENV/bin/alembic" upgrade head
    "$VENV/bin/alembic" current
    "$PY" -m app.seed
fi

# ---------------------------------------------------------------------------
step "6/6 git-хук pre-push"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git config core.hooksPath .githooks
    echo "включён: git push выполняется только после успешного make verify"
else
    echo "пропущено: каталог не является репозиторием git (включить позже: make hooks)"
fi

cat <<'EOF'

Готово. Дальше:
  make run         запуск приложения, http://127.0.0.1:8000
  make demo_seed   демонстрационные данные (необязательно)
  make verify      обязательный набор проверок перед запросом на слияние
  make help        все команды
EOF
