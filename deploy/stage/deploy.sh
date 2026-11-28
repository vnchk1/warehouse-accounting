#!/bin/bash
# Развертывание образа из реестра на предэксплуатационном окружении (ЛР6, пп. 3, 5).
# Запуск (пользователь deploy): bash /srv/warehouse-stage/deploy.sh <тег>
# Образ только загружается из реестра — на сервере ничего не собирается.
set -euo pipefail
cd "$(dirname "$0")"

TAG=${1:-}
if [[ ! $TAG =~ ^[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}$ ]]; then
    echo "Ошибка: укажите тег образа вида 0.1.0-0123456789ab (получено: «$TAG»)" >&2
    exit 2
fi

for f in app db migrate registry; do
    [[ -f env/$f.env ]] || { echo "Ошибка: нет env/$f.env (образцы — env/*.example)" >&2; exit 2; }
done

set -a; . ./env/registry.env; set +a
REGISTRY=${REGISTRY:-192.168.253.130:5000}
trap 'docker logout "$REGISTRY" >/dev/null 2>&1 || true' EXIT

echo "== Вход в реестр $REGISTRY"
printf '%s' "$REGISTRY_PASSWORD" | docker login "$REGISTRY" -u "$REGISTRY_USER" --password-stdin

export APP_IMAGE_TAG=$TAG
echo "== Загрузка образа warehouse-app:$TAG из реестра (без сборки на сервере)"
docker compose pull

echo "== Перезапуск окружения"
docker compose up -d

echo "== Ожидание готовности контейнеров (до 120 с)"
deadline=$((SECONDS + 120))
while :; do
    pending=$(docker compose ps --format '{{.Service}} {{.Status}}' |
        grep -vE 'healthy|Exited \(0\)' || true)
    [[ -z $pending ]] && break
    if (( SECONDS >= deadline )); then
        echo "Ошибка: контейнеры не стали готовы за 120 с:" >&2
        docker compose ps >&2
        exit 1
    fi
    sleep 3
done
docker compose ps
echo "Развёрнут образ warehouse-app:$TAG — дальше проверка: verify.sh $TAG"
