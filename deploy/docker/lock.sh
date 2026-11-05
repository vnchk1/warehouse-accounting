#!/usr/bin/env bash
# make lock: requirements.lock в том же базовом образе, что и стадия build в Dockerfile.
# Нужен доступ к PyPI. Выполняется после изменения версий в pyproject.toml или базового
# образа; файл сохраняется в каталоге проекта и проверяется make container-check.
set -euo pipefail
cd "$(dirname "$0")/../.."

base=$(sed -n 's/^FROM \([^ ]*\) AS build$/\1/p' Dockerfile)
[[ -n $base ]] || { echo "В Dockerfile не найдена строка «FROM … AS build»" >&2; exit 1; }
echo "Базовый образ: $base"
# Контейнер одноразовый, от текущего пользователя; в нём виден только каталог проекта
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$PWD:/src" -w /src "$base" python -I tools/lock.py
