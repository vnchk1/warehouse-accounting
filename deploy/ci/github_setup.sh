#!/usr/bin/env bash
# Настройки репозитория на GitHub для CI (ЛР5, пп. 3, 10, 11, 14).
#   bash deploy/ci/github_setup.sh secrets   шаги 1–3: до первой отправки ветки с CI
#   bash deploy/ci/github_setup.sh protect   шаг 4: после слияния ветки с CI в master
#   bash deploy/ci/github_setup.sh           всё сразу
# Выполняется на машине разработчика (WSL) с GitHub CLI: sudo apt install gh && gh auth login.
#
#   1. Секрет CI_DB_PASSWORD — пароль одноразовой тестовой базы в заданиях CI (случайный).
#   2. Окружение registry: публиковать может только ветка master; секреты
#      REGISTRY_USER и REGISTRY_PASSWORD (значения печатает deploy/ci/setup_registry.sh).
#   3. Метка acceptance-change — согласованное командой ослабление приёмки (make guard).
#   4. Защита master: только через запрос на слияние, обязательные проверки CI (все
#      задания, кроме publish; среди них «Охрана приёмки» — обязательная проверка изменений
#      критических файлов, п. 14), правила действуют и для администраторов, запрет force
#      push и удаления ветки.
#
# Переменные: REPO (по умолчанию — из git remote origin), APPROVALS — число одобрений людей:
# 0 (по умолчанию) — одобрения не требуются, изменения критических файлов останавливает
# обязательная проверка «Охрана приёмки»; N > 0 — нужно N одобрений любого запроса и
# одобрение владельцев критических файлов из .github/CODEOWNERS (повторное — после новых коммитов).
# Шаги 1–4 можно выполнить и вручную в веб-интерфейсе: docs/CI.md, раздел «Настройка».
set -euo pipefail
cd "$(dirname "$0")/../.."

fail() { echo "Ошибка: $*" >&2; exit 1; }
command -v gh >/dev/null || fail "нет GitHub CLI: sudo apt install gh && gh auth login"
gh auth status >/dev/null 2>&1 || fail "выполните gh auth login"
REPO=${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}
APPROVALS=${APPROVALS:-0}
[[ $APPROVALS =~ ^[0-9]+$ ]] || fail "APPROVALS — число"
STEP=${1:-all}
[[ $STEP == all || $STEP == secrets || $STEP == protect ]] || fail "шаг: secrets, protect или пусто"
echo "Репозиторий: $REPO"

# Названия обязательных проверок — поля name: заданий в .github/workflows/ci.yml
CHECKS=("Охрана приёмки" "Стиль и правила проекта" "Тесты" "Миграции" "Резервное копирование"
    "Безопасность" "Контейнерная проверка" "Сборка образа" "Анализ образа")

if [[ $STEP != protect ]]; then
echo "==> 1/4 Секрет CI_DB_PASSWORD (тестовая база в CI)"
if gh secret list --repo "$REPO" | grep -q '^CI_DB_PASSWORD\b'; then
    echo "уже задан — не меняется"
else
    openssl rand -hex 24 | gh secret set CI_DB_PASSWORD --repo "$REPO"
fi

echo "==> 2/4 Окружение registry: только ветка master, секреты реестра"
gh api -X PUT "repos/$REPO/environments/registry" --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
policies=$(gh api "repos/$REPO/environments/registry/deployment-branch-policies" \
    --jq '.branch_policies[].name')
if ! grep -qx master <<<"$policies"; then
    gh api -X POST "repos/$REPO/environments/registry/deployment-branch-policies" \
        -f name=master -f type=branch >/dev/null
fi
echo "в окружение registry допускается только ветка master"
for name in REGISTRY_USER REGISTRY_PASSWORD; do
    if gh secret list --repo "$REPO" --env registry | grep -q "^$name\b"; then
        echo "$name уже задан — не меняется (заменить: gh secret set $name --env registry)"
    else
        read -rsp "$name (из вывода deploy/ci/setup_registry.sh): " value
        echo
        [[ -n $value ]] || fail "пустое значение $name"
        printf '%s' "$value" | gh secret set "$name" --repo "$REPO" --env registry
    fi
done

echo "==> 3/4 Метка acceptance-change"
gh label create acceptance-change --repo "$REPO" --force --color B60205 \
    --description "Ослабление приёмки согласовано командой (make guard)" >/dev/null

fi
[[ $STEP == secrets ]] && { echo "Готово (секреты, окружение, метка)."; exit 0; }

echo "==> 4/4 Защита ветки master"
if ((APPROVALS > 0)); then
    reviews='{"required_approving_review_count": '"$APPROVALS"', "require_code_owner_reviews": true,
    "dismiss_stale_reviews": true, "require_last_push_approval": true}'
else
    reviews='{"required_approving_review_count": 0, "require_code_owner_reviews": false,
    "dismiss_stale_reviews": false, "require_last_push_approval": false}'
fi
checks_json=$(printf '%s\n' "${CHECKS[@]}" | python3 -c \
    'import json, sys; print(json.dumps([{"context": line.strip()} for line in sys.stdin]))')
gh api -X PUT "repos/$REPO/branches/master/protection" --input - >/dev/null <<JSON
{
  "required_status_checks": {"strict": true, "checks": $checks_json},
  "enforce_admins": true,
  "required_pull_request_reviews": $reviews,
  "restrictions": null,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
gh api "repos/$REPO/branches/master/protection" --jq \
    '"обязательные проверки: \(.required_status_checks.checks | map(.context) | join(", "))\nодобрение владельцев (CODEOWNERS): \(.required_pull_request_reviews.require_code_owner_reviews), для администраторов: \(.enforce_admins.enabled)"'
echo
echo "Готово. Осталось вручную: Settings → Actions → General → «Fork pull request workflows»"
echo "— Require approval for all external contributors (для публичного репозитория)."
