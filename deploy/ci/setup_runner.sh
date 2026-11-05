#!/usr/bin/env bash
# Self-hosted runner GitHub Actions на wa-app01 (ЛР5): публикует образ в локальный реестр.
#
#   sudo bash deploy/ci/setup_runner.sh <ВЕРСИЯ> <SHA256> <АДРЕС РЕПОЗИТОРИЯ>
#   пример: sudo bash deploy/ci/setup_runner.sh 2.338.0 6f1c…e2 https://github.com/vnchk1/warehouse-accounting
#
# Версию и SHA-256 возьмите со страницы Settings → Actions → Runners → New self-hosted
# runner (Linux, x64): там строки «curl -o actions-runner-linux-x64-<ВЕРСИЯ>.tar.gz» и
# «echo "<SHA256>  actions-runner-…" | shasum -a 256 -c». Код регистрации (token) с той же
# страницы скрипт спросит сам — в командной строке и в истории он не остаётся.
#
# Что делает: пользователь ghrunner (без пароля, в группе docker — нужна для docker push),
# runner в /opt/actions-runner с метками self-hosted, linux, warehouse-registry, служба
# systemd. Задание publish не запускает код репозитория: только docker load/tag/push
# готового образа, который собрали и проверили задания на серверах GitHub.
set -euo pipefail

fail() { echo "Ошибка: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "запустите через sudo"
[[ $# -eq 3 ]] || fail "нужно: <ВЕРСИЯ> <SHA256> <АДРЕС РЕПОЗИТОРИЯ> (см. начало файла)"
version=$1 sha=$2 url=$3
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "версия вида 2.338.0, получено «$version»"
[[ $sha =~ ^[0-9a-f]{64}$ ]] || fail "SHA-256 — 64 шестнадцатеричных символа"
[[ $url =~ ^https://github\.com/[^/]+/[^/]+$ ]] || fail "адрес вида https://github.com/<владелец>/<репозиторий>"

RUNNER_USER=ghrunner
DIR=/opt/actions-runner
archive=actions-runner-linux-x64-$version.tar.gz

if ! id "$RUNNER_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/home/$RUNNER_USER" --shell /bin/bash "$RUNNER_USER"
    passwd -l "$RUNNER_USER" >/dev/null
fi
usermod -aG docker "$RUNNER_USER"

if [[ -f $DIR/.runner ]]; then
    echo "Runner уже настроен в $DIR. Перенастроить: sudo $DIR/svc.sh stop && sudo $DIR/svc.sh uninstall,"
    echo "затем sudo -u $RUNNER_USER $DIR/config.sh remove --token <код удаления> и снова этот скрипт."
    exit 0
fi

mkdir -p "$DIR"
cd "$DIR"
if [[ ! -f $archive ]]; then
    curl -fsSL --retry 3 -o "$archive.part" \
        "https://github.com/actions/runner/releases/download/v$version/$archive"
    mv "$archive.part" "$archive"
fi
echo "$sha  $archive" | sha256sum --check --quiet - || { rm -f "$archive"; fail "SHA-256 не совпал"; }
tar -xzf "$archive"
chown -R "$RUNNER_USER:$RUNNER_USER" "$DIR"
./bin/installdependencies.sh >/dev/null

read -rsp "Код регистрации (token) со страницы New self-hosted runner: " token
echo
sudo -u "$RUNNER_USER" ./config.sh --unattended --url "$url" --token "$token" \
    --name "$(hostname)" --labels warehouse-registry --work _work --replace
./svc.sh install "$RUNNER_USER"
./svc.sh start
./svc.sh status | head -5
echo
echo "Готово: runner $(hostname) с меткой warehouse-registry. На GitHub: Settings → Actions → Runners — Idle."
