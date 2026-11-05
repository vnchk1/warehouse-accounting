#!/usr/bin/env bash
# Резервное копирование и восстановление базы в контейнерной среде (п. 10 ЛР4).
# Запуск из корня проекта (обычно через make, см. make help):
#   bash deploy/docker/dbctl.sh backup                    make container-backup
#   bash deploy/docker/dbctl.sh list                      make container-backups
#   bash deploy/docker/dbctl.sh counts                    make container-counts
#   bash deploy/docker/dbctl.sh restore [ФАЙЛ] [--yes]    make container-restore [FILE=…] [YES=1]
#   bash deploy/docker/dbctl.sh restore-new ФАЙЛ VOLUME   make container-restore-new FILE=… VOLUME=…
#   bash deploy/docker/dbctl.sh use-volume VOLUME         make container-use-volume VOLUME=…
#
# pg_dump и pg_restore запускаются внутри контейнера db: их версия всегда совпадает
# с версией сервера, а на сервере не нужен клиент PostgreSQL. Копия — архив pg_dump
# в собственном формате (-Fc), как make backup из ЛР3; файлы — в backups/ (права 600).
set -euo pipefail
cd "$(dirname "$0")/../.."

BACKUP_DIR=backups
DB_NAME=warehouse
OWNER=wh_owner
RESTORE_PROJECT=warehouse-restore   # временный проект Compose для восстановления в новый volume
TABLES="'unit','supplier','material','app_user','receipt','receipt_item'"

die() { echo "Ошибка: $*" >&2; exit 1; }

# docker compose основного проекта или временного (-p), команда — внутри контейнера db
compose() { docker compose "$@"; }
db_exec() { compose "${PROJECT[@]}" exec -T db "$@"; }
PROJECT=()

# Число записей в таблицах — для контроля до и после восстановления
counts() {
    db_exec psql -U postgres -d "$DB_NAME" -AtF ' ' -v ON_ERROR_STOP=1 -c "
        SELECT table_name, (xpath('/row/c/text()', query_to_xml(
                   format('SELECT count(*) AS c FROM public.%I', table_name), false, true, '')))[1]
          FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name IN ($TABLES)
         ORDER BY table_name" | paste -sd ',' | sed 's/,/, /g; s/^/Записей в таблицах: /'
}

# Число объектов в архиве по его оглавлению (pg_restore --list); 0 — не архив pg_dump
objects_in() {
    db_exec pg_restore --list <"$1" 2>/dev/null | grep -vc '^;' || true
}

check_archive() {
    local objects
    [[ -f $1 ]] || die "файл $1 не найден"
    objects=$(objects_in "$1")
    ((objects > 0)) || die "$1 — не архив pg_dump или он пуст"
    echo "Архив $1: объектов $objects"
}

# backup [ПРЕФИКС]: копия текущей базы; печатает путь к файлу
backup() {
    local prefix=${1:-warehouse} file part
    install -d -m 700 "$BACKUP_DIR"
    file="$BACKUP_DIR/$prefix-$(date +%Y%m%d-%H%M%S).dump"
    part="$file.part"
    # неполная копия хуже, чем никакой: при любой ошибке временный файл удаляется
    if ! (umask 077 && db_exec pg_dump -U postgres -d "$DB_NAME" --format=custom >"$part"); then
        rm -f "$part"
        die "pg_dump завершился с ошибкой"
    fi
    if (($(objects_in "$part") == 0)); then
        rm -f "$part"
        die "созданная копия не читается командой pg_restore --list"
    fi
    mv "$part" "$file"
    chmod 600 "$file"
    echo "$file"
}

# Загрузка архива в базу проекта PROJECT от имени владельца wh_owner, одной транзакцией:
# при ошибке база остаётся прежней. --no-owner/--no-privileges: владельцем объектов
# становится wh_owner, права wh_app выдаются автоматически (ALTER DEFAULT PRIVILEGES).
load() {
    db_exec env PGOPTIONS="-c lock_timeout=10s" pg_restore -U "$OWNER" -d "$DB_NAME" \
        --clean --if-exists --no-owner --no-privileges --single-transaction --exit-on-error <"$1"
}

latest() { find "$BACKUP_DIR" -maxdepth 1 -name 'warehouse-*.dump' 2>/dev/null | sort | tail -n 1; }

command=${1:-}
shift || true
case $command in
backup)
    file=$(backup)
    echo "Резервная копия создана: $file ($(stat -c %s "$file") байт)"
    counts
    ;;
list)
    find "$BACKUP_DIR" -maxdepth 1 -name '*.dump' -printf '%p  %s байт\n' 2>/dev/null | sort ||
        true
    ;;
counts)
    counts
    ;;
restore)
    file="" yes=""
    for arg in "$@"; do
        if [[ $arg == --yes ]]; then yes=$arg; elif [[ -n $arg ]]; then file=$arg; fi
    done
    file=${file:-$(latest)}
    [[ -n $file ]] || die "в $BACKUP_DIR нет копий. Сначала: make container-backup"
    check_archive "$file"
    if [[ $yes != --yes ]]; then
        echo "База данных «$DB_NAME» в контейнере db будет заменена содержимым $file."
        read -rp "Для подтверждения введите имя базы данных ($DB_NAME): " answer
        [[ $answer == "$DB_NAME" ]] || { echo "Восстановление отменено"; exit 1; }
    fi
    safety=$(backup before-restore)
    echo "Текущее состояние сохранено в $safety"
    # приложение останавливается, чтобы не держать блокировки таблиц; запускается в любом случае
    compose stop app
    trap 'compose start app >/dev/null' EXIT
    load "$file"
    echo "База данных восстановлена из $file"
    counts
    ;;
restore-new)
    file=${1:-}
    volume=${2:-}
    [[ -n $file && -n $volume ]] || die "нужно: restore-new ФАЙЛ VOLUME"
    [[ $volume =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]+$ ]] || die "недопустимое имя volume: $volume"
    if docker volume inspect "$volume" &>/dev/null; then
        die "volume $volume уже существует — выберите новое имя"
    fi
    [[ -f $file ]] || die "файл $file не найден"
    # Отдельный проект Compose: та же конфигурация db (образ, роли, ограничения), но новый
    # volume и своя сеть. Работающее окружение не затрагивается и может быть даже остановлено.
    export PG_VOLUME=$volume
    PROJECT=(-p "$RESTORE_PROJECT")
    trap 'echo "Не удалось. Временный проект и новый volume удаляются так:" \
        "docker compose -p $RESTORE_PROJECT down -v (с PG_VOLUME=$volume)" >&2' ERR
    echo "== Новый volume $volume: запуск временного контейнера базы"
    compose "${PROJECT[@]}" up -d --wait db
    check_archive "$file"
    echo "== Восстановление"
    load "$file"
    counts
    compose "${PROJECT[@]}" down
    echo "Данные восстановлены в volume $volume (временный контейнер удалён, volume сохранён)."
    echo "Переключить окружение на него: make container-use-volume VOLUME=$volume"
    ;;
use-volume)
    volume=${1:-}
    [[ -n $volume ]] || die "нужно: use-volume VOLUME"
    docker volume inspect "$volume" &>/dev/null || die "volume $volume не найден"
    # .env рядом с compose.yaml: Compose читает его сам при каждой команде
    touch .env
    if grep -q '^PG_VOLUME=' .env; then
        sed -i "s/^PG_VOLUME=.*/PG_VOLUME=$volume/" .env
    else
        echo "PG_VOLUME=$volume" >>.env
    fi
    echo "В .env записано PG_VOLUME=$volume. Применение — make container-up."
    ;;
*)
    sed -n '2,10p' "$0"
    exit 2
    ;;
esac
