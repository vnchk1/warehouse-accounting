#!/usr/bin/env bash
# make secrets: поиск секретов (gitleaks) — шаг make verify и задание security в CI.
#
#   1. Файлы, которые попадут или уже попали в репозиторий: отслеживаемые git и новые,
#      не исключённые .gitignore. Локальные .env, .venv, backups/ не проверяются — они
#      и так никогда не коммитятся (это проверяет tools/ci_guard.py).
#   2. Коммиты ветки, которых ещё нет в основной ветке (BASE_REF..HEAD): секрет,
#      добавленный и затем удалённый следующим коммитом, остаётся в истории — и тоже
#      ловится.
#
# Отчёты — reports/gitleaks-files.json, reports/gitleaks-commits.json; значения
# секретов в отчётах и на экране скрыты (--redact): отчёт CI доступен всем участникам.
# Ложное срабатывание — комментарий «gitleaks:allow — причина» в строке (CONTRIBUTING.md).
set -euo pipefail
cd "$(dirname "$0")/.."

absolute() { if [[ $1 == /* ]]; then echo "$1"; else echo "$PWD/$1"; fi; }
gitleaks=$(absolute "${TOOLS_DIR:-.tools}")/bin/gitleaks
base=${BASE_REF:-origin/master}
reports=$(absolute "${REPORTS:-reports}")
options=(--config "$PWD/.gitleaks.toml" --no-banner --no-color --verbose --redact --exit-code 1
    --report-format json)

[[ -x $gitleaks ]] || { echo "Нет $gitleaks — выполните make tools" >&2; exit 1; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
    { echo "make secrets работает в репозитории git (git init или git clone)" >&2; exit 1; }
mkdir -p "$reports"

snapshot=$(mktemp -d)
trap 'rm -rf "$snapshot"' EXIT
# -z: имена с пробелами и кириллицей передаются без искажений
git ls-files -z --cached --others --exclude-standard |
    while IFS= read -r -d '' file; do [[ -f $file ]] && printf '%s\0' "$file"; done |
    tar --null -T - -cf - | tar -xf - -C "$snapshot"

status=0
echo "gitleaks: файлы репозитория ($(find "$snapshot" -type f | wc -l) шт.)"
# из каталога копии: в отчёте пути вида app/config.py, а не /tmp/…/app/config.py
(cd "$snapshot" && "$gitleaks" dir . "${options[@]}" --report-path "$reports/gitleaks-files.json") ||
    status=1

if git rev-parse --verify --quiet "$base^{commit}" >/dev/null; then
    count=$(git rev-list --count "$base..HEAD")
    if ((count > 0)); then
        echo "gitleaks: коммиты $base..HEAD ($count шт.)"
        "$gitleaks" git . "${options[@]}" --log-opts="$base..HEAD" \
            --report-path "$reports/gitleaks-commits.json" || status=1
    else
        echo "gitleaks: новых коммитов относительно $base нет"
    fi
elif [[ ${CI:-} == true ]]; then
    echo "gitleaks: в CI нет $base — нужен actions/checkout с fetch-depth: 0" >&2
    status=1
else
    echo "gitleaks: ветки $base нет (git fetch origin) — проверены только файлы"
fi

if ((status)); then
    echo "Найдены секреты (подробности выше и в $reports/gitleaks-*.json)." >&2
    echo "Удалите секрет, смените его (он уже мог утечь) и перепишите коммиты ветки." >&2
fi
exit "$status"
