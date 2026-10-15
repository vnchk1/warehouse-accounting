#!/usr/bin/env bash
# Проверка требований п. 13 и состояния машины. Запуск на любой из двух машин:
#   sudo bash deploy/check.sh
# Строки «[НЕТ]» — нарушения, их нужно исправить.
set -uo pipefail

[[ $EUID -eq 0 ]] || { echo "Запустите через sudo" >&2; exit 1; }
ok() { echo "[ОК]  $*"; }
bad() { echo "[НЕТ] $*"; FAILED=1; }
FAILED=0

echo "== $(hostname) $(hostname -I)"

echo "-- SSH"
cfg=$(sshd -T 2>/dev/null)
grep -qx 'permitrootlogin no' <<<"$cfg" && ok "вход root по SSH запрещён" || bad "PermitRootLogin не no"
grep -qx 'passwordauthentication no' <<<"$cfg" && ok "вход по паролю запрещён" || bad "PasswordAuthentication не no"
root_cfg=$(sshd -T -C user=root,host=check,addr=192.0.2.1 2>/dev/null)
grep -qx 'maxauthtries 0' <<<"$root_cfg" && ok "для root соединение разрывается до проверки ключа" ||
    bad "для root не задан MaxAuthTries 0"

echo "-- Firewall"
ufw status | grep -q '^Status: active' && ok "ufw включён" || bad "ufw выключен"
ufw status | sed -n '/^To/,$p'

echo "-- Слушающие порты (кроме localhost)"
ss -tlnpH | awk '$4 !~ /^(127\.|\[::1\])/ {print "      " $4, $6}'

if [[ -f /etc/systemd/system/warehouse.service ]]; then
    echo "-- Служба warehouse"
    systemctl is-enabled --quiet warehouse && ok "автозапуск включён" || bad "автозапуск выключен"
    systemctl is-active --quiet warehouse && ok "служба работает" || bad "служба не работает"
    pid=$(systemctl show -p MainPID --value warehouse)
    if [[ $pid != 0 ]]; then
        user=$(ps -o user= -p "$pid")
        [[ $user != root ]] && ok "процесс $pid работает от $user" || bad "процесс работает от root"
    fi
    echo "      Restart=$(systemctl show -p Restart --value warehouse)" \
        "RestartSec=$(systemctl show -p RestartUSec --value warehouse)" \
        "NRestarts=$(systemctl show -p NRestarts --value warehouse)"
    port=$(sed -n 's/^APP_PORT=//p' /etc/warehouse/warehouse.env)
    echo "      /healthz: $(curl -s -m 5 "http://127.0.0.1:${port:-8000}/healthz")"
    echo "-- Права"
    stat -c '      %A %U:%G %n' /opt/warehouse /etc/warehouse /etc/warehouse/*.env
    [[ $(stat -c %a /etc/warehouse/warehouse.env) == 600 ]] && ok "файл с паролями 600" ||
        bad "права на warehouse.env не 600"
    [[ -n $(find /opt/warehouse -user warehouse -print -quit) ]] &&
        bad "в /opt/warehouse есть файлы, принадлежащие warehouse" ||
        ok "служба не владеет своим кодом"
fi

if [[ -d /etc/postgresql/16/main ]]; then
    echo "-- PostgreSQL"
    systemctl is-active --quiet postgresql && ok "PostgreSQL работает" || bad "PostgreSQL не работает"
    systemctl is-enabled --quiet postgresql && ok "автозапуск включён" || bad "автозапуск выключен"
    sed -n '/warehouse-begin/,/warehouse-end/p' /etc/postgresql/16/main/pg_hba.conf
fi

exit $FAILED
