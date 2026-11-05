# Непрерывная интеграция (ЛР5)

CI — независимый обязательный барьер перед созданием и публикацией образа
приложения. Система — **GitHub Actions**: репозиторий уже на GitHub, а защита
ветки, обязательные проверки, `CODEOWNERS`, секреты и окружения в нём
встроенные. Проверки выполняются на серверах GitHub (чистая машина на каждый
запуск, не зависит от компьютера разработчика), публикация — на self-hosted
runner стенда `wa-app01` в локальный реестр образов (локальный «Docker Hub»).

```
 разработчик (WSL)            GitHub Actions (ubuntu-24.04)                 wa-app01
 ─────────────────            ───────────────────────────────               ─────────────────────────
 make verify (хук pre-push)   guard ─┐                                      runner ghrunner
 git push ──────────────────► style ─┤                                      (метка warehouse-registry)
 запрос на слияние ─────────► tests ─┤  (своя тестовая база PostgreSQL)          │
                              migrations ─┤ (временная база)                     ▼
                              backup ─┼──────────────────────► publish ──► 127.0.0.1:5000
                              security ─┤                        (только master) реестр registry:3
                              container ─► build ─► image-scan ─┘                warehouse-app:<версия>-<коммит>
```

## Когда запускается (п. 3)

| Событие | Что происходит |
|---|---|
| `push` в любую ветку | все проверки; публикации нет |
| `pull_request` в `master` (открытие, новые коммиты, метки) | все проверки; их успех обязателен для слияния (защита ветки) |
| `push` в `master` (после слияния) | все проверки и **публикация образа** |
| `workflow_dispatch` (кнопка Run workflow) | все проверки для выбранной ветки; публикация — только если это `master` (окружение `registry`) |

## Задания и локальные команды (пп. 4, 5)

Каждое задание выполняет **те же команды make**, что разработчик у себя
(`make verify`, `make container-check`), с теми же версиями: Python-пакеты —
из `pyproject.toml` (`==`), gitleaks/trivy/actionlint — из `tools/tools.lock`
(SHA-256), PostgreSQL — тот же образ, что в `compose.yaml`.

| Этап задания (п. 5) | Задание CI | Команды | Отчёты (артефакт прогона) |
|---|---|---|---|
| охрана приёмки | `guard` — Охрана приёмки | `make guard` | `reports-guard`: `guard.md` |
| проверки стиля | `style` — Стиль и правила проекта | `make policy`, `make format-check`, `make lint-check` | журнал задания |
| тесты | `tests` — Тесты | `make test`, `make mutation` | `reports-tests`: `junit.xml`, `coverage.xml`, `htmlcov/`, `mutation.txt` |
| миграции | `migrations` — Миграции | `make migrations-check` | `reports-migrations`: `migrations-check.txt` |
| резервное копирование | `backup` — Резервное копирование | `make backup-check` | `reports-backup`: `backup-check.txt` |
| безопасность | `security` — Безопасность | `make sast`, `make secrets`, `make deps-audit` | `reports-security`: `bandit.html`, `gitleaks-*.json`, `deps-*.md` |
| контейнерная проверка | `container` — Контейнерная проверка | `make container-policy` | журнал задания |
| сборка | `build` — Сборка образа | `make container-build`, `make container-image-check` | артефакт `image` (сам образ) |
| безопасность образа | `image-scan` — Анализ образа | `make image-scan` | `reports-image-scan`: `trivy-image.json` |
| публикация | `publish` — Публикация образа | `docker push` в `127.0.0.1:5000` | итог задания: тег и дайджест |

Отчёты сохраняются и при ошибке (`if: always()` — только у шагов
`upload-artifact`): по ним разбирают, что именно упало.

**Почему локальные и серверные проверки должны совпадать.** Если CI проверяет
больше, чем `make verify`, разработчик узнаёт об ошибке только после отправки
и чинит её вслепую, «коммитами в CI». Если меньше — то, что разработчик
считал обязательным, на сервере не проверяется, и барьер можно обойти,
отключив хук (`git push --no-verify`). Одинаковые команды и версии дают один
результат в обоих местах: зелёный `make verify` почти гарантирует зелёный CI,
а красный CI воспроизводится локально одной командой. Совпадение не держится
на честном слове — его проверяет `make guard`: каждый шаг `make verify` и
`make container-check` обязан встречаться в CI, а CI не может запускать
проверок, которых нет локально.

## Отдельная тестовая база (п. 6)

Задания `tests`, `migrations`, `backup` получают свой контейнер
`postgres:16.15-trixie@sha256:…` (тот же, что `db` в `compose.yaml`) с базой
`warehouse_test`; пароль — секрет `CI_DB_PASSWORD`. Тесты работают только с
базой `*_test` (`tests/conftest.py`), проверки миграций и копий — с временными
базами `warehouse_migrations_check_test`, `warehouse_backup_check_test`,
которые создаются и удаляются `tools/db_check.py`. Локально те же команды
используют `TEST_DATABASE_URL` из `.env`; рабочая база не затрагивается.

## Кэширование (п. 8)

- загрузки pip — `actions/setup-python` с `cache: pip`, ключ — хеш `pyproject.toml`;
- gitleaks, trivy, actionlint — `actions/cache`, ключ — хеш `tools/tools.lock`;
- база уязвимостей trivy — `actions/cache`, ключ — дата (обновляется раз в сутки).

## Тег образа (п. 9)

`make image-tag` → `<версия из pyproject.toml>-<12 символов хеша коммита>`,
например `warehouse-app:0.1.0-9cc9c1965767`; полный хеш — в метке образа
`org.opencontainers.image.revision`. Тег вычисляет `Makefile` из `git rev-parse`
— одинаково у разработчика и в CI; задание `build` дополнительно сверяет его
с `GITHUB_SHA`. Вне репозитория git (сервер ЛР4) тег остаётся `0.1.0`.

## Запрет публикации при ошибке (п. 10)

- `publish` указан с `needs:` на **все** задания проверки; условие `if:` не
  содержит `always()`/`failure()`, поэтому GitHub добавляет к нему `success()`:
  упало или пропущено любое задание — `publish` получает статус *skipped*;
- публикуется ровно тот файл образа, который собрал `build` и проверили
  `build` и `image-scan` (сверка ID образа после `docker load`);
- секреты реестра — в окружении `registry`, доступном только ветке `master`;
- реестр слушает только `127.0.0.1` на `wa-app01`: опубликовать в обход CI
  можно только с самой ВМ и только с паролем;
- `make guard` не даст удалить зависимость или добавить `always()`.

## Секреты (п. 11)

| Секрет | Где | Для чего |
|---|---|---|
| `CI_DB_PASSWORD` | Settings → Secrets and variables → Actions → Repository secrets | пароль одноразовой тестовой базы в CI |
| `REGISTRY_USER`, `REGISTRY_PASSWORD` | Settings → Environments → `registry` → Environment secrets | вход в реестр; доступны только заданию `publish` в `master` |

В файлах репозитория, журналах и образе паролей нет: GitHub маскирует
значения секретов в журнале, `docker login` получает пароль через stdin,
учётные данные Docker пишутся во временный каталог задания. На сервере
хранится только хеш bcrypt (`/srv/registry/auth/htpasswd`).

## Секреты, зависимости, образ (п. 12)

| Проверка | Инструмент | Что считается ошибкой |
|---|---|---|
| `make secrets` | gitleaks 8.30.1: стандартные правила + пароль в адресе PostgreSQL (`.gitleaks.toml`) | любой секрет в файлах репозитория **и в коммитах ветки** (удалённый следующим коммитом тоже) |
| `make deps-audit` | pip-audit 2.10.0 (база PyPI/OSV) | известная уязвимость в пакетах образа (`requirements.lock`, хеши сверяются с PyPI) или окружения проверок |
| `make image-scan` | trivy 0.76.1 | уязвимость HIGH или CRITICAL, для которой есть исправление, в пакетах ОС и Python внутри образа |

Плюс `make container-image-check` (ЛР4): в образе нет значений паролей,
файлов `.env`, процесса от root.

Сторонние actions закреплены полным хешем коммита, а trivy ставится
бинарным файлом с проверкой SHA-256, без `trivy-action`: в марте 2026 года
теги `aquasecurity/trivy-action` и выпуск trivy 0.69.4 были подменены
злоумышленником (CVE-2026-33634) — плавающие теги и непроверенные загрузки в
CI недопустимы.

## Защита критических файлов (пп. 13–15)

Критические файлы приёмки — тесты, `Makefile`, `pyproject.toml`,
`tools/`, `.github/` (pipeline, `CODEOWNERS`), `.githooks/`, `migrations/`,
`.gitleaks.toml`, `.trivyignore`, `Dockerfile`, `.dockerignore`,
`compose.yaml`, `requirements.lock`, `CONTRIBUTING.md`. Три уровня защиты:

1. **`.github/CODEOWNERS` + защита ветки `master`** (`deploy/ci/github_setup.sh protect`):
   слияние только через запрос; все задания CI, кроме `publish`, —
   обязательные проверки; изменение критического файла требует одобрения
   владельца (*Require review from Code Owners*); новый коммит сбрасывает
   одобрение; правила действуют и для администраторов (*Do not allow
   bypassing*); `force push` и удаление ветки запрещены.
2. **`make guard`** (задание «Охрана приёмки») находит то, что легко
   пропустить глазами, и **останавливает** запрос на слияние:
   - удалён файл тестов или тестовая функция (в том числе переименована так,
     что pytest её не видит), уменьшилось число проверок `assert`/`pytest.raises`;
   - снижены пороги или нижние границы, сокращён набор `make verify`, правил
     ruff, ключей pytest; добавлены исключения ruff/bandit/coverage, правило
     в `.trivyignore`, общие исключения gitleaks;
   - из pipeline убрано задание или шаг, добавлены `continue-on-error`,
     `|| true`, `always()`, публикация не ждёт какой-то проверки, action не
     закреплён хешем, PostgreSQL в CI отличается от `compose.yaml`;
   - у критического файла нет владельца в `CODEOWNERS`; в репозитории `.env`,
     копия базы или закрытый ключ.

   Перечень изменённых критических файлов печатается в итоге задания.
   Оправданное ослабление проходит `guard` только с меткой
   `acceptance-change` и всё равно ждёт одобрения владельцев.
3. **Охрана берётся из `master`.** Задание `guard` запускает
   `tools/ci_guard.py` из основной ветки, а не из проверяемой: в запросе на
   слияние нельзя заодно «подправить» саму охрану. А правка
   `.github/workflows/ci.yml`, которая убирает задание `guard`, оставляет
   обязательную проверку «Охрана приёмки» без результата — слияние
   заблокировано.

Каждый сценарий обхода из п. 15 проверяется автоматически:
`tests/tools/test_ci_guard.py` (30 случаев) создаёт временный репозиторий,
вносит «вредное» изменение и проверяет, что `make guard` его находит.

## Настройка (один раз)

1. **Секреты, окружение, метка** — `bash deploy/ci/github_setup.sh secrets`
   (нужен `gh auth login`) или вручную:
   - Settings → Secrets and variables → Actions → *New repository secret*
     `CI_DB_PASSWORD` = `openssl rand -hex 24`;
   - Settings → Environments → *New environment* `registry` → Deployment
     branches and tags: *Selected branches and tags* → `master`; секреты
     `REGISTRY_USER`, `REGISTRY_PASSWORD`;
   - Issues → Labels → `acceptance-change`.
2. **Реестр на wa-app01** — `sudo bash deploy/ci/setup_registry.sh` (печатает
   пароль один раз).
3. **Runner на wa-app01** — `sudo bash deploy/ci/setup_runner.sh <версия> <sha256> <адрес>`
   (значения — со страницы Settings → Actions → Runners → *New self-hosted runner*).
4. **Защита master** — после слияния ветки с CI:
   `bash deploy/ci/github_setup.sh protect` (`APPROVALS=0` — одобрение нужно
   только для критических файлов). Для публичного репозитория также Settings →
   Actions → General → *Require approval for all external contributors*.

Защита веток, `CODEOWNERS` с обязательным одобрением и окружения с
ограничением веток в **приватном** репозитории доступны только на тарифах
GitHub Pro/Team (для студентов — бесплатно через GitHub Education); в
публичном — на любом тарифе.

## Безопасность self-hosted runner

Runner работает от пользователя `ghrunner` в группе `docker` — это права,
равные root на `wa-app01`, поэтому:
- на нём выполняется только `publish`, а `publish` не запускает код
  репозитория — только `docker load`/`tag`/`push` уже проверенного образа;
- `publish` не выполняется для `pull_request`, а секреты реестра доступны
  только `master`;
- в публичном репозитории запуск workflow из чужих форков — только после
  одобрения (Settings → Actions → General).

## Обслуживание

- опубликованные образы: `sudo bash deploy/ci/registry_tags.sh [коммит]`;
- обновить инструмент: новая строка в `tools/tools.lock` (версия, SHA-256 из
  `*_checksums.txt` релиза) — отдельная задача и запрос на слияние;
- уязвимость в образе: обновить базовый образ (дайджест — github.com/docker-library/repo-info)
  или пакет в `pyproject.toml` + `make lock`; если исправления нет — строка в
  `.trivyignore` с причиной и сроком, метка `acceptance-change`.
