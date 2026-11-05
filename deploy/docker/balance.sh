#!/usr/bin/env bash
# Распределение запросов между экземплярами (дополнительное задание): N запросов к /healthz
# через Nginx и число ответов от каждого экземпляра (заголовок X-Instance-Id).
#   bash deploy/docker/balance.sh [N] [АДРЕС]      make container-balance
# По умолчанию 20 запросов к http://127.0.0.1:HTTP_PORT.
set -euo pipefail
cd "$(dirname "$0")/../.."

count=${1:-20}
port=${HTTP_PORT:-$(sed -n 's/^HTTP_PORT=//p' .env 2>/dev/null || true)}
url=${2:-http://127.0.0.1:${port:-80}}

answers=()
failed=0
for ((i = 1; i <= count; i++)); do
    # -D - выводит заголовки ответа; тело не нужно
    if ! headers=$(curl -s -m 5 -o /dev/null -D - "$url/healthz"); then
        failed=$((failed + 1))
        continue
    fi
    headers=$(tr -d '\r' <<<"$headers")
    status=$(awk 'NR == 1 { print $2 }' <<<"$headers")
    instance=$(awk -F': ' 'tolower($1) == "x-instance-id" { print $2 }' <<<"$headers")
    answers+=("${instance:-нет_заголовка} $status")
done
echo "Запросов к $url/healthz: $count"
if ((${#answers[@]})); then
    printf '%s\n' "${answers[@]}" | sort | uniq -c |
        awk '{ printf "  экземпляр %-14s код %s — ответов: %s\n", $2, $3, $1 }'
fi
((failed == 0)) || echo "  без ответа: $failed"
echo "Экземпляры: $(docker compose ps -q app | cut -c1-12 | tr '\n' ' ')"
