#!/usr/bin/env bash
# make tools: gitleaks, trivy и actionlint точных версий из tools/tools.lock в .tools/bin.
# Архив скачивается с GitHub один раз (.tools/cache), проверяется по SHA-256 и только
# после этого распаковывается. Чужой или повреждённый файл — ошибка, а не предупреждение.
# Повторный запуск ничего не скачивает, если нужная версия уже установлена.
#   TOOLS_DIR — каталог установки (по умолчанию .tools)
set -euo pipefail
cd "$(dirname "$0")/.."

dest=${TOOLS_DIR:-.tools}
fail() { echo "Ошибка: $*" >&2; exit 1; }

[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] ||
    fail "в tools/tools.lock указаны сборки для Linux x86_64, а здесь $(uname -sm)"
for program in curl sha256sum tar; do
    command -v "$program" >/dev/null || fail "не найдена программа $program"
done
mkdir -p "$dest/bin" "$dest/cache"

while read -r name version sha url; do
    [[ -z $name || $name == \#* ]] && continue
    marker="$dest/bin/.$name-$version"
    if [[ -x $dest/bin/$name && -f $marker ]]; then
        echo "$name $version — уже установлен"
        continue
    fi
    archive="$dest/cache/${url##*/}"
    if [[ ! -f $archive ]]; then
        echo "$name $version — загрузка ${url}"
        curl -fsSL --retry 3 --retry-delay 2 -o "$archive.part" "$url" ||
            fail "не удалось скачать $url"
        mv "$archive.part" "$archive"
    fi
    if ! echo "$sha  $archive" | sha256sum --check --quiet - >/dev/null 2>&1; then
        rm -f "$archive"
        fail "SHA-256 архива $name $version не совпадает с tools/tools.lock — файл удалён"
    fi
    tar -xzf "$archive" -C "$dest/bin" "$name"
    chmod 755 "$dest/bin/$name"
    rm -f "$dest/bin/.$name-"*
    touch "$marker"
    echo "$name $version — установлен, SHA-256 совпадает"
done <tools/tools.lock
