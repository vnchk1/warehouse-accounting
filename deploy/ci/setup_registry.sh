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
LAN_IP=""
ALLOW_IP=""
NEW_PASSWORD=""
fail() { echo "Ошибка: $*" >&2; exit 1; }

# ЛР6: --lan <адрес wa-app01> --allow <адрес wa-stage01> — доступ к реестру с
# предэксплуатационного окружения (вход по-прежнему обязателен)
while [[ $# -gt 0 ]]; do
    case $1 in
        --lan) LAN_IP=$2; shift 2 ;;
        --allow) ALLOW_IP=$2; shift 2 ;;
        --new-password) NEW_PASSWORD=1; shift ;;
        *) fail "неизвестный аргумент: $1" ;;
    esac
done
[[ -z $LAN_IP || -n $ALLOW_IP ]] || fail "--lan требует --allow <адрес окружения>"

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
if [[ ! -s $BASE/auth/htpasswd || -n $NEW_PASSWORD ]]; then
    password=$(openssl rand -hex 24)
    # bcrypt (-B) — единственный формат, который понимает реестр
    htpasswd -Bbn "$USER_NAME" "$password" >"$BASE/auth/htpasswd"
fi
chown root:5000 "$BASE/auth/htpasswd"
chmod 640 "$BASE/auth/htpasswd"

if [[ -n $LAN_IP ]]; then
    # Доступ из подсети стенда: порт слушается и на адресе wa-app01, но правило
    # iptables DOCKER-USER пропускает обращения только с адреса окружения (ufw
    # трафик опубликованных портов Docker не фильтрует — поэтому DOCKER-USER)
    cat >"$BASE/compose.lan.yaml" <<EOF
# Сгенерировано setup_registry.sh --lan $LAN_IP --allow $ALLOW_IP (ЛР6).
services:
  registry:
    ports:
      - "127.0.0.1:5000:5000"
      - "$LAN_IP:5000:5000"
EOF
    cat >/usr/local/sbin/registry-lan-guard <<EOF
#!/bin/bash
# К реестру в подсети обращается только предэксплуатационное окружение (ЛР6).
set -euo pipefail
iptables -C DOCKER-USER -p tcp -d $LAN_IP --dport 5000 ! -s $ALLOW_IP -j DROP 2>/dev/null || \\
    iptables -I DOCKER-USER -p tcp -d $LAN_IP --dport 5000 ! -s $ALLOW_IP -j DROP
EOF
    chmod 755 /usr/local/sbin/registry-lan-guard
    cat >/etc/systemd/system/registry-lan-guard.service <<'EOF'
[Unit]
Description=Restrict registry LAN access to the staging host
After=docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/registry-lan-guard
[Install]
WantedBy=multi-user.target
EOF
    systemctl enable --now registry-lan-guard.service
    /usr/local/sbin/registry-lan-guard
    echo "Реестр в подсети: $LAN_IP:5000, обращения только с $ALLOW_IP"
fi

compose_files=(-f registry/compose.yaml)
[[ -f $BASE/compose.lan.yaml ]] && compose_files+=(-f "$BASE/compose.lan.yaml")
docker compose "${compose_files[@]}" up -d
for _ in $(seq 1 20); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://$REGISTRY/v2/" || true)
    [[ $code == 401 ]] && break
    sleep 1
done
[[ ${code:-} == 401 ]] || fail "реестр не отвечает на http://$REGISTRY/v2/ (docker compose -f deploy/ci/registry/compose.yaml logs)"
echo "Реестр работает: без пароля — 401 (вход обязателен)"

if [[ -n $LAN_IP ]]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://$LAN_IP:5000/v2/" || true)
    [[ $code == 401 ]] || fail "реестр не отвечает на http://$LAN_IP:5000/v2/ (код ${code:-нет})"
    echo "Из подсети ($LAN_IP:5000): тоже 401 — вход обязателен и там"
fi

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