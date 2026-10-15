#!/usr/bin/env bash
# Администратор с входом по SSH-ключам, запрет входа root и входа по паролю (пп. 2–3).
# Выполняется на обеих машинах. После имени пользователя — один или несколько ключей:
# файл .pub (в нём может быть и несколько ключей) или сама строка ключа в кавычках.
#   sudo bash deploy/common/setup_admin_ssh.sh waadmin /tmp/win.pub /tmp/wsl.pub
#   sudo bash deploy/common/setup_admin_ssh.sh waadmin "ssh-ed25519 AAAA... ivan@win"
# Ключи только добавляются: уже разрешённые остаются. Повторный запуск безопасен.
# Скрипт спросит пароль администратора — он нужен только для sudo, не для SSH.
set -euo pipefail

ADMIN_USER=${1:?Укажите имя администратора, например waadmin}
shift
(($#)) || { echo "Укажите хотя бы один открытый ключ: файл .pub или строку ключа" >&2; exit 1; }

[[ $EUID -eq 0 ]] || { echo "Запустите через sudo" >&2; exit 1; }

# 0. Ключи из аргументов. Каждый проверяется до любых изменений в системе.
KEYS=()
for src in "$@"; do
    if [[ -f $src ]]; then
        if grep -q 'PRIVATE KEY' "$src"; then
            echo "$src — это ЗАКРЫТЫЙ ключ. Нужен файл с расширением .pub" >&2
            exit 1
        fi
        content=$(<"$src")
    else
        content=$src
    fi
    while IFS= read -r line; do
        line=${line%$'\r'}                # конец строки в стиле Windows
        line=${line#$'\xEF\xBB\xBF'}       # метка BOM от Блокнота
        [[ -z $line || $line == \#* ]] && continue
        if ! ssh-keygen -l -f <(printf '%s\n' "$line") >/dev/null 2>&1; then
            echo "Не открытый ключ SSH и не файл: ${line:0:50}" >&2
            exit 1
        fi
        KEYS+=("$line")
    done <<<"$content"
done
((${#KEYS[@]})) || { echo "В аргументах не найдено ни одного ключа" >&2; exit 1; }

# 1. Пользователь-администратор (sudo — права root, adm — чтение журналов)
if ! id "$ADMIN_USER" &>/dev/null; then
    useradd --create-home --shell /bin/bash --groups sudo,adm "$ADMIN_USER"
    echo "Задайте пароль $ADMIN_USER (нужен для sudo):"
    passwd "$ADMIN_USER"
else
    usermod -aG sudo,adm "$ADMIN_USER"
fi

# 2. Открытые ключи: добавляются только те, которых ещё нет
SSH_DIR="/home/$ADMIN_USER/.ssh"
install -d -m 700 -o "$ADMIN_USER" -g "$ADMIN_USER" "$SSH_DIR"
touch "$SSH_DIR/authorized_keys"
for key in "${KEYS[@]}"; do
    grep -qxF "$key" "$SSH_DIR/authorized_keys" || echo "$key" >>"$SSH_DIR/authorized_keys"
done
chown "$ADMIN_USER:$ADMIN_USER" "$SSH_DIR/authorized_keys"
chmod 600 "$SSH_DIR/authorized_keys"

# 3. Настройки sshd. Файл 10-* читается раньше 50-cloud-init.conf,
#    а в sshd побеждает первое найденное значение параметра.
#    Для root соединение разрывается на первом же запросе входа: ни один ключ
#    и ни один пароль не проверяются (MaxAuthTries 0 в блоке Match User root).
echo "Root login is not available on this host." >/etc/ssh/root-denied.txt
chmod 644 /etc/ssh/root-denied.txt
cat >/etc/ssh/sshd_config.d/10-hardening.conf <<EOF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AllowUsers $ADMIN_USER

# Блок Match действует до конца этого файла, поэтому он последний.
Match User root
    Banner /etc/ssh/root-denied.txt
    MaxAuthTries 0
EOF
chmod 644 /etc/ssh/sshd_config.d/10-hardening.conf
sshd -t
systemctl restart ssh

# 4. Пароль root заблокирован (в Ubuntu так по умолчанию — закрепляем явно)
passwd -l root >/dev/null

echo "--- Действующие настройки sshd:"
sshd -T | grep -E '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication|allowusers) '
echo "--- Для пользователя root (блок Match):"
sshd -T -C user=root,host=check,addr=192.0.2.1 | grep -E '^(maxauthtries|banner) '
echo "--- Разрешённые ключи $ADMIN_USER (отпечаток и комментарий):"
ssh-keygen -l -f "$SSH_DIR/authorized_keys"
echo "Не закрывайте текущий сеанс, пока не проверите вход: ssh $ADMIN_USER@<адрес>"
