#!/usr/bin/env bash
# Проверки CI в DinD (ЛР5, docs/CI.md): раннер только поднимает контейнер
# docker:dind — изолированный демон Docker — и управляет им. Каждая проверка
# (make …) выполняется в контейнере ci-work внутри этого демона; PostgreSQL для
# заданий tests/migrations/backup — контейнер в той же сети DinD, причём его
# образ берётся из compose.yaml (совпадение версий — по построению).
#
#   tools/ci_dind.sh up [--db] [--docker]  — DinD, образ проверок, контейнер ci-work;
#                                            --db: PostgreSQL и TEST_DATABASE_URL;
#                                            --docker: доступ из ci-work к демону DinD
#   tools/ci_dind.sh exec <команда…>       — выполнить команду в ci-work; reports/
#                                            выгружаются на раннер при любом исходе,
#                                            код возврата — код команды
#   tools/ci_dind.sh guard-from-base       — tools/ci_guard.py из основной ветки
#                                            (BASE_REF) → /tmp/ci_guard.py в ci-work
#   tools/ci_dind.sh cache-load trivy      — .trivy-cache раннера → кэш trivy в ci-work
#   tools/ci_dind.sh cache-save trivy      — кэш trivy из ci-work → .trivy-cache раннера
#
# Окружение: DIND_IMAGE — закреплённый образ docker:*-dind (задаётся в ci.yml,
# обязательна). CI_IMAGE_TAR — файл образа проверок из артефакта задания ci-image;
# без него образ собирается из Dockerfile.checks (локальный запуск, нужен Docker).
# С --db обязательна CI_DB_PASSWORD.
set -euo pipefail
cd "$(dirname "$0")/.."

DIND=ci-dind
WORK=ci-work
DB=ci-db
NET=ci
DIND_HOST=tcp://127.0.0.1:2375

fail() { echo "Ошибка: $*" >&2; exit 1; }

# docker на демоне раннера (до того, как DOCKER_HOST переключён на DinD)
runner_docker() { DOCKER_HOST= docker "$@"; }

wait_dind() {
    local n
    for n in $(seq 60); do
        if docker info >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    fail "демон DinD не ответил за 60 с"
}

up() {
    local with_db=0 with_docker=0
    while (($#)); do
        case $1 in
            --db) with_db=1 ;;
            --docker) with_docker=1 ;;
            *) fail "неизвестный ключ up: $1" ;;
        esac
        shift
    done
    [[ -n ${DIND_IMAGE:-} ]] ||
        fail "не задана DIND_IMAGE (закреплённый образ docker:*-dind из ci.yml)"

    # DinD: изолированный демон в контейнере на раннере; API — только на localhost
    if ! runner_docker ps -q -f "name=^/$DIND$" | grep -q .; then
        if runner_docker ps -aq -f "name=^/$DIND$" | grep -q .; then
            runner_docker rm -f "$DIND" >/dev/null
        fi
        runner_docker run -d --privileged --name "$DIND" \
            -p 127.0.0.1:2375:2375 -e DOCKER_TLS_CERTDIR= "$DIND_IMAGE" >/dev/null
    fi
    export DOCKER_HOST=$DIND_HOST
    echo "DOCKER_HOST=$DIND_HOST" >>"${GITHUB_ENV:-/dev/null}"
    wait_dind
    echo "DinD: docker $(docker version -f '{{.Server.Version}}')"

    # Образ проверок: из артефакта задания ci-image или локальная сборка
    local image
    if [[ -n ${CI_IMAGE_TAR:-} ]]; then
        (cd "$(dirname "$CI_IMAGE_TAR")" &&
            sha256sum --check --quiet "$(basename "$CI_IMAGE_TAR").sha256")
        image=$(gunzip -c "$CI_IMAGE_TAR" | docker load -q | sed -n 's/^Loaded image: //p')
    else
        echo "CI_IMAGE_TAR не задан — собираю образ проверок из Dockerfile.checks"
        docker build --quiet -f Dockerfile.checks -t warehouse-ci:local . >/dev/null
        image=warehouse-ci:local
    fi
    [[ -n $image ]] || fail "не удалось загрузить или собрать образ проверок"
    echo "Образ проверок: $image"

    if ! docker network inspect "$NET" >/dev/null 2>&1; then
        docker network create "$NET" >/dev/null
    fi

    # PostgreSQL — тот же образ, что сервис db в compose.yaml
    local db_url= db_image=
    if ((with_db)); then
        [[ -n ${CI_DB_PASSWORD:-} ]] || fail "с ключом --db обязательна CI_DB_PASSWORD"
        db_image=$(awk '/^  db:$/{f=1; next} f && /^    image:/{print $2; exit}
            f && /^  [^ ]/{f=0}' compose.yaml)
        [[ -n $db_image ]] || fail "не найден образ сервиса db в compose.yaml"
        docker run -d --name "$DB" --network "$NET" --network-alias db \
            -e POSTGRES_USER=warehouse \
            -e "POSTGRES_PASSWORD=$CI_DB_PASSWORD" \
            -e POSTGRES_DB=warehouse_test \
            --health-cmd "pg_isready -U warehouse -d warehouse_test" \
            --health-interval 5s --health-timeout 5s --health-retries 20 \
            "$db_image" >/dev/null
        db_url="postgresql+psycopg://warehouse:${CI_DB_PASSWORD}@db:5432/warehouse_test"
    fi

    # Контейнер проверок: код коммита копируется в /src — в образе там же
    # editable-установка, поэтому проверяется именно этот код
    local gateway
    gateway=$(docker network inspect "$NET" --format '{{(index .IPAM.Config 0).Gateway}}')
    local envs=(-e CI=true -e "BASE_REF=${BASE_REF:-origin/master}")
    if ((with_db)); then
        envs+=(-e "TEST_DATABASE_URL=$db_url")
    fi
    if ((with_docker)); then
        envs+=(-e "DOCKER_HOST=tcp://$gateway:2375")
    fi
    if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then
        envs+=(-e GITHUB_STEP_SUMMARY=/tmp/step-summary.md)
    fi
    if docker ps -aq -f "name=^/$WORK$" | grep -q .; then
        docker rm -f "$WORK" >/dev/null
    fi
    docker create --name "$WORK" --network "$NET" --workdir /src \
        "${envs[@]}" "$image" sleep infinity >/dev/null
    docker start "$WORK" >/dev/null
    tar -cf - . | docker exec -i "$WORK" tar -xf - -C /src
    docker exec "$WORK" git config --global --add safe.directory /src
    # Событие GitHub (метки запроса на слияние) читает make guard
    if [[ -n ${GITHUB_EVENT_PATH:-} && -f $GITHUB_EVENT_PATH ]]; then
        docker cp "$GITHUB_EVENT_PATH" "$WORK:/tmp/github-event.json" >/dev/null
    fi

    if ((with_db)); then
        local n status=
        for n in $(seq 30); do
            status=$(docker inspect -f '{{.State.Health.Status}}' "$DB")
            if [[ $status == healthy ]]; then
                break
            fi
            sleep 2
        done
        [[ $status == healthy ]] || fail "PostgreSQL в DinD не стартовал"
        echo "PostgreSQL готов: $db_image"
    fi
}

exec_in_work() {
    local extra=()
    if docker exec "$WORK" test -f /tmp/ci_guard.py; then
        extra+=(-e GUARD=/tmp/ci_guard.py)
    fi
    if docker exec "$WORK" test -f /tmp/github-event.json; then
        extra+=(-e GITHUB_EVENT_PATH=/tmp/github-event.json)
    fi
    local rc=0
    docker exec "${extra[@]}" "$WORK" "$@" || rc=$?
    # Отчёты — на раннер при любом исходе: их сохраняет шаг upload-artifact
    mkdir -p reports
    if docker exec "$WORK" test -d /src/reports; then
        docker cp "$WORK:/src/reports/." reports/
    fi
    if docker exec "$WORK" test -f /tmp/step-summary.md; then
        docker exec "$WORK" cat /tmp/step-summary.md >>"${GITHUB_STEP_SUMMARY:-/dev/null}"
        docker exec "$WORK" rm /tmp/step-summary.md
    fi
    return "$rc"
}

guard_from_base() {
    docker exec "$WORK" bash -euc '
        if git cat-file -e "$BASE_REF:tools/ci_guard.py" 2>/dev/null; then
            git show "$BASE_REF:tools/ci_guard.py" >/tmp/ci_guard.py
            echo "Используется tools/ci_guard.py из $BASE_REF"
        else
            echo "В $BASE_REF ещё нет tools/ci_guard.py — используется версия из этой ветки"
        fi
    '
}

cache_load() {
    [[ ${1:-} == trivy ]] || fail "cache-load: поддерживается только trivy"
    if [[ -d .trivy-cache ]] && [[ -n $(ls -A .trivy-cache) ]]; then
        docker exec "$WORK" mkdir -p /root/.cache/trivy
        docker cp .trivy-cache/. "$WORK:/root/.cache/trivy/"
    fi
}

cache_save() {
    [[ ${1:-} == trivy ]] || fail "cache-save: поддерживается только trivy"
    mkdir -p .trivy-cache
    if docker exec "$WORK" test -d /root/.cache/trivy; then
        docker cp "$WORK:/root/.cache/trivy/." .trivy-cache/
    fi
}

command=${1:-}
case $command in
    up) shift; up "$@" ;;
    exec) shift; exec_in_work "$@" ;;
    guard-from-base) guard_from_base ;;
    cache-load) shift; cache_load "$@" ;;
    cache-save) shift; cache_save "$@" ;;
    *) fail "команды: up [--db] [--docker] | exec <команда…> | guard-from-base | cache-load trivy | cache-save trivy" ;;
esac
