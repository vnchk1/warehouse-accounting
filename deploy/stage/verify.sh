#!/bin/bash
# Проверка работающего релиза (ЛР6, пп. 7–9): контейнеры, служебный адрес,
# версия и основной пользовательский сценарий. Падение любого шага = релиз НЕ принят.
# Запуск (пользователь deploy): bash /srv/warehouse-stage/verify.sh <развёрнутый тег>
set -euo pipefail
cd "$(dirname "$0")"

TAG=${1:-}
[[ -n $TAG ]] || { echo "Ошибка: укажите развёрнутый тег образа" >&2; exit 2; }
export APP_IMAGE_TAG=$TAG  # для подстановки образа в compose.yaml

BASE=http://127.0.0.1
V1=$BASE/api/v1
fail() { echo "[НЕТ] $*" >&2; exit 1; }
ok() { echo "[ОК] $*"; }
json_value() { python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }

echo "== 1. Контейнеры"
status=$(docker compose ps -a --format '{{.Service}} {{.Status}}')
echo "$status"
grep -qE '^db .*(healthy)' <<<"$status" || fail "контейнер db не готов"
[[ $(grep -cE '^app .*(healthy)' <<<"$status") -ge 2 ]] || fail "готовы не оба экземпляра app"
grep -qE '^migrate .*Exited \(0\)' <<<"$status" || fail "миграции не завершились успешно"
grep -qE '^nginx ' <<<"$status" || fail "нет контейнера nginx"
ok "контейнеры готовы, миграции применены"

echo "== 2. Служебный адрес /healthz"
health=$(curl -fsS "$BASE/healthz") || fail "/healthz недоступен"
[[ $(json_value status <<<"$health") == ok ]] || fail "/healthz: $health"
ok "/healthz: $(json_value status <<<"$health"), база $(json_value database <<<"$health")"

echo "== 3. Версия работающего образа (/version)"
version=$(curl -fsS "$BASE/version") || fail "/version недоступен"
actual=$(json_value version <<<"$version")
[[ $actual == "$TAG" ]] || fail "/version сообщает «$actual», а развёрнут «$TAG»"
ok "/version: $actual (коммит $(json_value revision <<<"$version"))"

echo "== 4. Основной пользовательский сценарий (вход → поставщик → материал → поступление → отчёт)"
set -a; . ./env/app.env; set +a
jar=$(mktemp)
supplier_id=""; material_id=""; receipt_id=""
cleanup() {  # записи проверки удаляются, чтобы повторный запуск не упирался в уникальность
    [[ -n $receipt_id ]] && curl -fsS -b "$jar" -X DELETE "$V1/receipts/$receipt_id" -o /dev/null || true
    [[ -n $material_id ]] && curl -fsS -b "$jar" -X DELETE "$V1/materials/$material_id" -o /dev/null || true
    [[ -n $supplier_id ]] && curl -fsS -b "$jar" -X DELETE "$V1/suppliers/$supplier_id" -o /dev/null || true
    rm -f "$jar"
}
trap cleanup EXIT

post() { # post <путь> <json> — тело ответа; падает при коде не 2xx
    local path=$1 data=$2 code
    code=$(curl -fsS -b "$jar" -c "$jar" -o "$jar.out" -w '%{http_code}' \
        -H 'Content-Type: application/json' -X POST "$V1$path" -d "$data") \
        || fail "POST $path: сбой запроса"
    [[ $code == 2* ]] || fail "POST $path: код $code — $(cat "$jar.out")"
    cat "$jar.out"
}

stamp=$(date +%s)
post /auth/login "{\"login\": \"$ADMIN_LOGIN\", \"password\": \"$ADMIN_PASSWORD\"}" >/dev/null \
    || exit 1
ok "вход выполнен ($ADMIN_LOGIN)"

for inn in 500100732259 7707083893 6371292619 7129666880 225821585766; do
    result=$(post /suppliers "{\"name\": \"CD-проверка $stamp\", \"inn\": \"$inn\"}" 2>/dev/null) \
        && { supplier_id=$(json_value id <<<"$result"); break; }
done
[[ -n $supplier_id ]] || fail "не удалось создать поставщика (все ИНН заняты)"
ok "поставщик создан (id $supplier_id)"

unit_id=$(curl -fsS -b "$jar" "$V1/units" |
    python3 -c 'import json,sys; print(next(u["id"] for u in json.load(sys.stdin) if u["code"] == "шт"))')
result=$(post /materials "{\"sku\": \"CD-$stamp\", \"name\": \"CD-проверка\", \"unit_id\": $unit_id}")
material_id=$(json_value id <<<"$result")
ok "материал создан (id $material_id)"

result=$(post /receipts \
    "{\"receipt_date\": \"$(date +%F)\", \"supplier_id\": $supplier_id, \"invoice_number\": \"CD-$stamp\"}")
receipt_id=$(json_value id <<<"$result")
post "/receipts/$receipt_id/items" "{\"material_id\": $material_id, \"quantity\": \"1\", \"price\": \"100\"}" >/dev/null
ok "поступление оприходовано (id $receipt_id, накладная CD-$stamp)"

report=$(curl -fsS -b "$jar" "$V1/reports/receipts?supplier_id=$supplier_id")
[[ $(python3 -c 'import json,sys; print(json.load(sys.stdin)["rows"][0]["receipts"])' <<<"$report") == 1 ]] \
    || fail "отчёт по поставкам не содержит поступление: $report"
ok "отчёт по поставкам содержит поступление поставщика"

echo ""
echo "Релиз warehouse-app:$TAG ПРИНЯТ: контейнеры, /healthz, /version и сценарий в порядке"
