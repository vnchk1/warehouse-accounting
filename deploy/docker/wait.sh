#!/usr/bin/env bash
# Ожидание готовности окружения после docker compose up (вызывает make container-up).
# Готово, когда все работающие контейнеры в состоянии healthy, а одноразовый migrate
# завершился с кодом 0. Затем — запрос /healthz через Nginx.
#   bash deploy/docker/wait.sh [СЕКУНД]      по умолчанию 180
set -euo pipefail
cd "$(dirname "$0")/../.."

limit=${1:-180}
port=${HTTP_PORT:-$(sed -n 's/^HTTP_PORT=//p' .env 2>/dev/null || true)}
port=${port:-80}

state() {
    local ids
    ids=$(docker compose ps -a -q)
    [[ -n $ids ]] || return 0
    # shellcheck disable=SC2086 # список ID разделяется пробелами намеренно
    docker inspect -f \
        '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}} {{.State.ExitCode}}' \
        $ids
}

for ((elapsed = 0; elapsed < limit; elapsed += 3)); do
    pending=$(state | awk '
        $2 == "exited" && $4 != 0                { print $1 " завершился с кодом " $4; bad = 1 }
        $2 == "running" && $3 != "healthy"       { print $1 " " $3 }
        $2 != "running" && $2 != "exited"        { print $1 " " $2 }
        END { exit bad }') || { echo "$pending" >&2; echo "Журнал: docker compose logs" >&2; exit 1; }
    if [[ -z $pending ]]; then
        echo "Все контейнеры готовы (${elapsed} с):"
        docker compose ps
        echo "Проверка через Nginx: $(curl -fsS -m 5 "http://127.0.0.1:${port}/healthz")"
        exit 0
    fi
    sleep 3
done
echo "За ${limit} с готовы не все контейнеры:" >&2
echo "$pending" >&2
echo "Журнал: docker compose logs --tail 50" >&2
exit 1
