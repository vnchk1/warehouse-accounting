# Контейнерное окружение (ЛР4)

Приложение, база данных и Nginx работают в контейнерах Docker под управлением
Docker Compose (`compose.yaml`). Окружение меняется только командой
`make container-up`, которая сначала выполняет `make container-check`.

```
                 HTTP_PORT (80)
                       │  единственный опубликованный порт
                 ┌─────▼─────┐   сеть frontend (обычная)
                 │   nginx   │   UID 101, порт 8080, только чтение
                 └─────┬─────┘
                       │ round robin       сеть backend (internal)
            ┌──────────┴──────────┐
       ┌────▼────┐           ┌────▼────┐
       │  app-1  │    …      │  app-N  │   UID 10001, порт 8000 не публикуется
       └────┬────┘           └────┬────┘
            └──────────┬──────────┘         сеть data (internal)
       ┌─────────┐ ┌───▼───┐
       │ migrate │►│  db   │◄── volume warehouse_pgdata
       └─────────┘ └───────┘   PostgreSQL 16, UID postgres, порт 5432 не публикуется
```

## Файлы

| Файл | Назначение |
|---|---|
| `Dockerfile` | Образ приложения: две стадии, базовый образ по дайджесту, пакеты по `requirements.lock`, пользователь 10001, `HEALTHCHECK`, CMD в форме exec |
| `.dockerignore` | Белый список контекста сборки: в образ может попасть только `app/`, `migrations/`, `alembic.ini`, `pyproject.toml`, `requirements.lock` |
| `requirements.lock` | Точные версии и SHA-256 всех пакетов образа, включая транзитивные (`make lock`) |
| `compose.yaml` | Сервисы db, migrate, app (≥ 2 экземпляра), nginx; сети, volume, проверки, порядок запуска, ограничения |
| `deploy/docker/nginx/nginx.conf` | Балансировка между экземплярами, повтор запроса на другом экземпляре при отказе |
| `deploy/docker/db/initdb/10-roles.sh` | Роли `wh_owner` (миграции) и `wh_app` (только DML) при создании нового volume |
| `deploy/docker/env/*.env.example` | Образцы файлов паролей; настоящие файлы создаёт `make container-env` |
| `deploy/docker/init-env.sh` | Создание `db.env`, `app.env`, `migrate.env` со случайными паролями (600) |
| `deploy/docker/dbctl.sh` | Резервная копия, восстановление, восстановление в новый volume, переключение volume |
| `deploy/docker/wait.sh` | Ожидание готовности: все контейнеры healthy, migrate завершился с кодом 0 |
| `deploy/docker/balance.sh` | Сколько ответов дал каждый экземпляр (заголовок `X-Instance-Id`) |
| `deploy/docker/lock.sh`, `tools/lock.py` | Создание `requirements.lock` в том же базовом образе |
| `tools/container_policy.py` | Правила контейнерного окружения — основа `make container-check` |

## Команды

| Команда | Назначение |
|---|---|
| `make container-env` | Файлы паролей (один раз; существующие не меняются) |
| `make lock` | `requirements.lock` после изменения версий в `pyproject.toml` или базового образа |
| **`make container-check`** | **Правила → сборка образа → проверка образа. Обязательна перед изменением окружения** |
| `make container-up` | `container-check`, затем `docker compose up -d` и ожидание готовности |
| `make container-down` | Остановить и удалить контейнеры (volume с данными сохраняется) |
| `make container-ps`, `make container-logs [S=app]` | Состояние и журналы |
| `make container-migrate` | Миграции и начальные данные (`alembic upgrade head`, `app.seed`) |
| `make container-balance [N=20]` | Распределение запросов между экземплярами |
| `make container-backup`, `make container-backups` | Резервная копия в `backups/`, перечень копий |
| `make container-restore [FILE=…] [YES=1]` | Восстановление в текущую базу (с копией `before-restore-…`) |
| `make container-restore-new FILE=… VOLUME=…` | Восстановление в новый volume, работающее окружение не затрагивается |
| `make container-use-volume VOLUME=…` | Переключение базы на другой volume (через `container-check`) |
| `make container-counts` | Число записей в таблицах |

Необязательные параметры — в файле `.env` рядом с `compose.yaml` или в окружении:
`HTTP_PORT` (80), `APP_REPLICAS` (2), `APP_IMAGE_TAG` (0.1.0), `PG_VOLUME` (`warehouse_pgdata`).

## Как выполнены требования

| Пункт | Решение |
|---|---|
| 1. Dockerfile и .dockerignore | Две стадии: `build` (виртуальное окружение) и `runtime` (интерпретатор, окружение, код). `.dockerignore` — белый список |
| 2. Воспроизводимая сборка | Базовые образы — `имя:точная-версия@sha256:…`; Python-пакеты — `pip install --require-hashes --no-deps --only-binary=:all: -r requirements.lock`; `pip check` после установки. Одни и те же входные данные дают тот же набор файлов |
| 3. Непривилегированный пользователь | `USER 10001:10001` в образе; `user: postgres` для db и `user: "101:101"` для nginx; у всех `cap_drop: [ALL]` и `no-new-privileges`; у app, migrate, nginx файловая система только для чтения |
| 4. Настройки через переменные окружения | `env_file` (`deploy/docker/env/*.env`) и `environment` в `compose.yaml`; приложение читает их через `app/config.py` |
| 5. Корректное завершение | CMD в форме exec — uvicorn является PID 1 и получает SIGTERM; `--timeout-graceful-shutdown 15` < `stop_grace_period: 20s`; при остановке закрываются соединения с базой (`lifespan`); код выхода 0. PostgreSQL получает SIGINT (быстрая остановка с контрольной точкой), Nginx — SIGQUIT |
| 6–7. Compose, сети, volume, healthcheck, порядок | Сети `backend` и `data` — `internal: true`; volume `warehouse_pgdata`; проверки у db, app, nginx; `db (healthy) → migrate (завершился успешно) → app (healthy) → nginx` |
| 8. Порт БД | Не публикуется: базу видят только контейнеры сети `data` |
| 9. Сохранность данных | Данные — в именованном volume с постоянным именем; `down`, `--force-recreate` и новый образ (`APP_IMAGE_TAG`) его не затрагивают |
| 10. Миграции, копия, восстановление | Сервис `migrate` при каждом запуске; `dbctl.sh` — `pg_dump`/`pg_restore` внутри контейнера db (версия совпадает с сервером) |
| 11. Ограничения ресурсов | Таблица ниже |
| 12. make container-check | Три шага: правила файлов → сборка → проверка образа |
| 13. Запреты | Проверяются `tools/container_policy.py` (раздел ниже) |
| 14. Изменение только после проверки | `container-up: container-check` — make не выполнит `up`, если проверка не прошла; это правило само проверяется в `container_policy.py` |
| Доп. 1–2. Nginx, нет прямого доступа | Nginx — отдельный контейнер; у app нет `ports`, он только во внутренних сетях |
| Доп. 3–4. ≥ 2 экземпляра, распределение | `deploy.replicas: 2`; `upstream` с `resolve` (адреса экземпляров из DNS Docker каждые 5 с), round robin, `proxy_next_upstream` |
| Доп. 5. Идентификатор экземпляра | Заголовок `X-Instance-Id` в каждом ответе, поле `instance` в `/healthz`, подвал страниц; Nginx добавляет `X-Upstream-Addr` |

## Ограничения ресурсов

| Сервис | CPU | Память | Процессы | Обоснование |
|---|---|---|---|---|
| db | 1.0 | 512M (резерв 256M) | 200 | `shared_buffers` по умолчанию 128 МБ + рабочая память запросов и до ~30 соединений приложений (2 × пул 5 + 10); 200 процессов — с запасом на служебные процессы и соединения |
| app (каждый) | 1.0 | 256M (резерв 128M) | 100 | Процесс uvicorn ≈ 80–120 МБ; хеш пароля argon2 занимает 64 МБ на время входа — лимит допускает два входа одновременно; 1 CPU — хеширование argon2 ≈ 50 мс; потоки обработчиков (до 40) укладываются в 100 |
| migrate | 0.5 | 256M | 50 | Одноразовый процесс alembic и seed |
| nginx | 0.5 | 64M | 32 | Один рабочий процесс (`worker_processes 1`), буферы проксирования в tmpfs |

Итого не более ≈ 1,4 ГБ памяти при двух экземплярах — на сервере с 2 ГБ остаётся
место для ОС и Docker. Журналы контейнеров ограничены: 3 файла по 10 МБ.

## Правила make container-check

`tools/container_policy.py` (без аргументов — до сборки):

- **образы**: в `FROM` и `image` — точная версия и `@sha256:`; `latest` и теги без
  версии запрещены; собранный образ приложения — тег вида `0.1.0`;
- **не root**: `USER` итоговой стадии не root/0; у готовых образов задан `user`, не root;
  нет `privileged`, `cap_add`, `network_mode: host`; есть `cap_drop: [ALL]` и `no-new-privileges`;
- **секреты**: в Dockerfile нет `ENV`/`ARG` с паролями и `COPY` файлов настроек;
  `.dockerignore` начинается с `*`; в `compose.yaml` нет значений паролей; файлы
  `deploy/docker/env/*.env` существуют, права 600, исключены в `.gitignore` и не в git;
  пароль владельца базы получает только migrate, Nginx паролей не получает;
- **healthcheck**: у db, app, nginx есть проверка; `disable: true` и `NONE` запрещены;
  в образе есть `HEALTHCHECK`;
- **порты**: публикует только nginx и ровно один порт; app и db — только во внутренних сетях;
- **ресурсы**: у каждого сервиса `cpus`, `memory`, `pids`; экземпляров app не меньше двух;
- **порядок запуска** (`depends_on` с условиями), политика `restart`, именованный volume у db;
- **requirements.lock**: каждая строка с `==` и `--hash`, версии прямых зависимостей
  совпадают с `pyproject.toml`;
- **Makefile**: `container-up` зависит от `container-check`.

С ключом `--image` — проверка собранного образа: пользователь не root (в том числе
фактический UID процесса при запуске), есть `HEALTHCHECK`, в переменных образа нет
паролей, ни одно значение из `*.env` не встречается в истории слоёв и в файлах `/app`,
в `/app` только код (`app/`, `migrations/`, `alembic.ini`, `pyproject.toml`).

## Безопасность сети

Docker публикует порты в обход правил ufw (цепочки iptables DOCKER), поэтому защита
строится на том, что публикуется: наружу открыт только порт Nginx. Порт 8000
приложения и 5432 базы не публикуются вообще, контейнеры app и db находятся только
во внутренних сетях без выхода наружу.
