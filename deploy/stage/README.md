# Предэксплуатационное окружение (ЛР6) — ВМ wa-stage01 (192.168.253.132)

Отдельное окружение для проверки релизов перед эксплуатацией. Образ не собирается
на сервере — только загружается из реестра на wa-app01 (`192.168.253.130:5000`),
настройки окружения лежат на сервере и не попадают ни в образ, ни в репозиторий.

Развертывание и проверку выполняет задание «Развертывание и проверка релиза»
workflow CD (`.github/workflows/cd.yml`) — автоматически после успешной
публикации образа из `master` или вручную с указанием тега.

## Разметка сервера

| Путь | Что |
|---|---|
| `/srv/warehouse-stage/compose.yaml` | окружение без секции `build`: образ только из реестра |
| `/srv/warehouse-stage/env/{app,db,migrate}.env` | настройки и пароли окружения (root:deploy, 640) |
| `/srv/warehouse-stage/env/registry.env` | вход в реестр для pull |
| `/srv/warehouse-stage/deploy.sh` | развертывание тега: login → pull → up → ожидание готовности |
| `/srv/warehouse-stage/verify.sh` | проверка релиза: контейнеры → /healthz → /version → сценарий |
| `/srv/warehouse-stage/db/initdb`, `nginx.conf` | роли БД и конфигурация Nginx (копии из `deploy/docker/`) |

Пользователь **deploy** — отдельная учётная запись для pipeline (п. 2): вход
только по ключу (ключ — секрет окружения `staging` на GitHub), без sudo,
членство в группе `docker` даёт ровно права на pull/compose. Администрирование
остаётся за waadmin.

## Первоначальная настройка (один раз, выполняется при сборке стенда)

1. Docker и Compose v2; в `/etc/docker/daemon.json` реестр объявлен
   `insecure-registries` (изолированная подсеть стенда, аутентификация htpasswd
   сохраняется; на wa-app01 обращения к порту 5000 ограничены адресом wa-stage01 правилом iptables DOCKER-USER (ufw опубликованные порты Docker не фильтрует)).
2. Пользователь `deploy`, каталог `/srv/warehouse-stage` с файлами этого
   каталога; `env/*.env` со случайными паролями (из образцов `*.example`).
3. Проверка: `sudo -u deploy bash /srv/warehouse-stage/deploy.sh <тег>` и
   `… verify.sh <тег>` с уже опубликованным образом.

## Ручные операции

```bash
# развернуть конкретный тег (в т.ч. вернуть предыдущую версию — п. 10)
sudo -u deploy bash /srv/warehouse-stage/deploy.sh 0.1.0-0123456789ab
sudo -u deploy bash /srv/warehouse-stage/verify.sh  0.1.0-0123456789ab
# перечень образов в реестре (на wa-app01):
sudo bash /srv/warehouse/deploy/ci/registry_tags.sh
```
