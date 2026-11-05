#!/usr/bin/env bash
# Опубликованные образы warehouse-app в локальном реестре (ЛР5, защита): на wa-app01
#   sudo bash deploy/ci/registry_tags.sh            все теги
#   sudo bash deploy/ci/registry_tags.sh <коммит>   есть ли образ этого коммита (код 0/1)
# Читает хранилище реестра /srv/registry/data напрямую: пароль не нужен, но нужен root.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Запустите через sudo" >&2; exit 1; }
dir=/srv/registry/data/docker/registry/v2/repositories/warehouse-app/_manifests/tags
tags=()
if [[ -d $dir ]]; then
    mapfile -t tags < <(find "$dir" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
fi

if [[ -n ${1:-} ]]; then
    commit=${1:0:12}
    for tag in "${tags[@]}"; do
        if [[ $tag == *-"$commit" ]]; then
            echo "Образ коммита $commit опубликован: 127.0.0.1:5000/warehouse-app:$tag"
            exit 0
        fi
    done
    echo "Образа коммита $commit в реестре НЕТ"
    exit 1
fi
if ((${#tags[@]} == 0)); then
    echo "В реестре ещё нет образов warehouse-app"
    exit 0
fi
echo "Образы в 127.0.0.1:5000/warehouse-app (${#tags[@]} шт.):"
for tag in "${tags[@]}"; do
    echo "  $tag   опубликован $(stat -c %y "$dir/$tag" | cut -d. -f1)"
done
