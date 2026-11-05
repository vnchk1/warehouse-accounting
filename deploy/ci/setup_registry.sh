#!/usr/bin/env bash
# Локальный реестр образов на wa-app01 (ЛР5, п. 2): sudo bash deploy/ci/setup_registry.sh
#
#   1. Каталоги /srv/registry/data (образы) и /srv/registry/auth (htpasswd), владелец 5000.
#   2. Учётная запись публикации ci-publisher: пароль генерируется и печатается ОДИН раз —
#      его нужно сразу сохранить в секрет REGISTRY_PASSWORD окружения registry на GitHub.
#      На сервере хранится только хеш bcrypt. Новый пароль: --new-password.
#   3. Запуск реестра (deploy/ci/registry/compose.yaml), проверка входа и списка образов.
#
# Повторный запуск безопасен: пароль не меняется, контейнер обновляется до версии из файла.
set -euo pipefail
cd "$(dirname "$0")"

USER_NAME=ci-publisher
BASE=/srv/registry
REGISTRY=127.0.0.1:5000
fail() { echo "Ошибка: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "запустите через sudo"
command -v docker >/dev/null || fail "нет Docker (ЛР4: apt install docker.io docker-compose-v2)"
if ! command -v htpasswd >/dev/null; then
    echo "Установка htpasswd (apache2-utils)"
    apt-get install -y -qq apache2-utils >/dev/null
fi

mkdir -p "$BASE/data" "$BASE/auth"
chown 5000:5000 "$BASE/data"
chmod 700 "$BASE/data"

password=""
if [[ ! -s $BASE/auth/htpasswd || ${1:-} == --new-password ]]; then
    password=$(openssl rand -hex 24)
    # bcrypt (-B) — единственный формат, который понимает реестр
    htpasswd -Bbn "$USER_NAME" "$password" >"$BASE/auth/htpasswd"
fi
chown root:5000 "$BASE/auth/htpasswd"
chmod 640 "$BASE/auth/htpasswd"

docker compose -f registry/compose.yaml up -d
for _ in $(seq 1 20); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://$REGISTRY/v2/" || true)
    [[ $code == 401 ]] && break
    sleep 1
done
[[ ${code:-} == 401 ]] || fail "реестр не отвечает на http://$REGISTRY/v2/ (docker compose -f deploy/ci/registry/compose.yaml logs)"
echo "Реестр работает: без пароля — 401 (вход обязателен)"

if [[ -n $password ]]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' -u "$USER_NAME:$password" "http://$REGISTRY/v2/")
    [[ $code == 200 ]] || fail "вход с новым паролем не прошёл (код $code)"
    cat <<EOF

Вход с паролем проверен. Сохраните значения в секреты окружения registry на GitHub
(Settings → Environments → registry → Environment secrets) и закройте окно:

  REGISTRY_USER=$USER_NAME
  REGISTRY_PASSWORD=$password

Больше пароль нигде не показывается; потерян — sudo bash deploy/ci/setup_registry.sh --new-password
EOF
else
    echo "Пароль ci-publisher не менялся (новый: --new-password)"
fi
echo
echo "Образы в реестре: sudo bash deploy/ci/registry_tags.sh"
