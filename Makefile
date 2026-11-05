# Команды проекта. Подробности — README.md, CONTRIBUTING.md и docs/TESTING.md.
SHELL := /bin/bash
.SHELLFLAGS := -e -o pipefail -c
PYTHON ?= python3
VENV ?= .venv
PY := $(VENV)/bin/python
# Каталоги с кодом на Python, которые проверяют линтер и форматирование
CODE := app tests tools migrations
# Каталоги, которые проверяет анализатор безопасности (тесты исключены в pyproject.toml)
SAST := app migrations tools
REPORTS := reports
COMPOSE ?= docker compose
BACKUP_DIR ?= backups
# Инструменты не из PyPI (gitleaks, trivy, actionlint): версии и SHA-256 — tools/tools.lock
TOOLS_DIR ?= .tools
TOOLS := $(TOOLS_DIR)/bin
# Основная ветка: с ней make guard и make secrets сравнивают текущую ветку
BASE_REF ?= origin/master
# Программа охраны приёмки; CI подставляет её версию из основной ветки (docs/CI.md)
GUARD ?= tools/ci_guard.py
# Тег образа приложения (ЛР5, п. 9): версия из pyproject.toml и начало хеша коммита,
# например 0.1.0-1a2b3c4d5e6f; вне репозитория git — только версия, как в ЛР4.
VERSION := $(shell sed -n 's/^version = "\(.*\)"$$/\1/p' pyproject.toml)
GIT_SHA := $(shell git rev-parse HEAD 2>/dev/null)
APP_IMAGE_TAG ?= $(VERSION)$(if $(GIT_SHA),-$(shell git rev-parse --short=12 HEAD))
APP_REVISION ?= $(or $(GIT_SHA),unknown)
export APP_IMAGE_TAG APP_REVISION BASE_REF TOOLS_DIR REPORTS

.PHONY: help setup install run migrate migrate-down migrate-status seed demo_seed \
        backup restore backups psql lint policy format-check lint-check sast test \
        test-unit mutation verify hooks clean tools guard secrets deps-audit \
        migrations-check backup-check image-tag \
        container-env lock container-check container-up container-down container-ps \
        container-policy container-build container-image-check image-scan \
        container-logs container-migrate container-balance container-backup \
        container-backups container-counts container-restore container-restore-new \
        container-use-volume

help:  ## список команд
	@grep -E '^[a-z_-]+:.*##' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*## "}{printf "  %-22s %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Установка и запуск
# ---------------------------------------------------------------------------
setup:  ## первоначальная настройка: Python, зависимости, .env, база, миграции
	PYTHON=$(PYTHON) VENV=$(VENV) bash scripts/setup.sh

install:  ## установить зафиксированные версии зависимостей в .venv
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install -e ".[dev]"

run:  ## запуск приложения (http://127.0.0.1:8000)
	$(PY) -m app.main

# ---------------------------------------------------------------------------
# База данных: миграции, начальные данные, резервные копии
# ---------------------------------------------------------------------------
migrate:  ## применить миграции до последней версии схемы
	$(VENV)/bin/alembic upgrade head

migrate-down:  ## откатить последнюю миграцию
	$(VENV)/bin/alembic downgrade -1

migrate-status:  ## текущая версия схемы и список миграций
	$(VENV)/bin/alembic current
	$(VENV)/bin/alembic history --indicate-current

seed:  ## справочник ОКЕИ и администратор
	$(PY) -m app.seed

demo_seed:  ## демонстрационные данные (необязательно)
	$(PY) -m app.demo_seed

backup:  ## резервная копия базы данных в backups/ (каталог: BACKUP_DIR=…)
	$(PY) -m app.dbadmin --dir $(BACKUP_DIR) backup

restore:  ## восстановление: make restore [FILE=backups/....dump] [YES=1]
	$(PY) -m app.dbadmin --dir $(BACKUP_DIR) restore $(FILE) $(if $(filter 1,$(YES)),--yes)

backups:  ## перечень резервных копий
	$(PY) -m app.dbadmin --dir $(BACKUP_DIR) list

psql:  ## консоль рабочей базы: make psql [SQL="SELECT ..."]
	$(PY) -m app.dbadmin psql $(if $(SQL),-c "$(SQL)")

# ---------------------------------------------------------------------------
# Проверки. Каждая команда — отдельный шаг make verify и задание (или шаг) CI
# в .github/workflows/ci.yml: локально и на сервере выполняются одни и те же
# команды с одними и теми же версиями (docs/CI.md). Совпадение проверяет make guard.
# ---------------------------------------------------------------------------
tools:  ## gitleaks, trivy, actionlint точных версий в .tools/bin (проверка SHA-256)
	bash tools/install_tools.sh

# actionlint без shellcheck и pyflakes: они есть не на каждой машине, а результат
# должен быть одинаковым у разработчика и в CI.
guard: tools  ## охрана приёмки: pipeline, CODEOWNERS, ослабление проверок относительно BASE_REF
	@mkdir -p $(REPORTS)
	$(TOOLS)/actionlint -no-color -shellcheck= -pyflakes= -config-file .github/actionlint.yaml \
		.github/workflows/ci.yml
	$(PY) $(GUARD) --base $(BASE_REF) --report $(REPORTS)/guard.md

lint:  ## исправить форматирование и простые замечания линтера
	$(VENV)/bin/ruff format $(CODE)
	$(VENV)/bin/ruff check --fix $(CODE)

policy:  ## правила проекта: версии, запрет пропуска тестов и исключений без причины
	$(PY) tools/policy.py

format-check:  ## проверка форматирования кода (ruff format)
	$(VENV)/bin/ruff format --check $(CODE)

lint-check:  ## линтер: ошибки и стиль кода (ruff check)
	$(VENV)/bin/ruff check $(CODE)

sast:  ## анализ безопасности кода (bandit), отчёт reports/bandit.html
	@mkdir -p $(REPORTS)
	$(VENV)/bin/bandit -q -c pyproject.toml -r $(SAST) --exit-zero -f html -o $(REPORTS)/bandit.html
	$(VENV)/bin/bandit -q -c pyproject.toml -r $(SAST)

secrets: tools  ## поиск секретов (gitleaks): файлы репозитория и коммиты ветки; reports/gitleaks-*.json
	bash tools/secrets.sh

deps-audit:  ## известные уязвимости зависимостей (pip-audit): образ и окружение; reports/deps-*.md
	@mkdir -p $(REPORTS)
	@test -f requirements.lock || { echo "Нет requirements.lock — выполните make lock" >&2; exit 1; }
	@rm -f $(REPORTS)/deps-image.md $(REPORTS)/deps-dev.md
	@echo "pip-audit: пакеты образа (requirements.lock, хеши сверяются с PyPI)"
	@status=0; $(VENV)/bin/pip-audit --strict --disable-pip --require-hashes \
		-r requirements.lock --desc -f markdown -o $(REPORTS)/deps-image.md || status=$$?; \
		if [ -s $(REPORTS)/deps-image.md ]; then cat $(REPORTS)/deps-image.md; fi; exit $$status
	@echo "pip-audit: окружение разработки и проверок ($(VENV); сам проект не проверяется)"
	@status=0; $(VENV)/bin/pip-audit --skip-editable --desc -f markdown \
		-o $(REPORTS)/deps-dev.md || status=$$?; \
		if [ -s $(REPORTS)/deps-dev.md ]; then cat $(REPORTS)/deps-dev.md; fi; exit $$status

test:  ## все тесты с покрытием; отчёты reports/junit.xml и reports/htmlcov
	@mkdir -p $(REPORTS)
	$(PY) -m pytest --cov --cov-report=term --cov-report=html --cov-report=xml \
		--junitxml=$(REPORTS)/junit.xml

test-unit:  ## только unit-тесты (база данных не нужна)
	$(PY) -m pytest tests/unit

mutation:  ## мутационная проверка: тесты должны поймать изменение правил
	@mkdir -p $(REPORTS)
	$(PY) tools/mutation.py --report $(REPORTS)/mutation.txt

migrations-check:  ## миграции на отдельной временной базе: upgrade → откат по шагу → upgrade
	@mkdir -p $(REPORTS)
	$(PY) tools/db_check.py migrations --report $(REPORTS)/migrations-check.txt

backup-check:  ## копия → повреждение → восстановление на отдельной временной базе
	@mkdir -p $(REPORTS)
	$(PY) tools/db_check.py backup --report $(REPORTS)/backup-check.txt

verify:  ## ОБЯЗАТЕЛЬНЫЙ набор проверок перед запросом на слияние (те же, что в CI)
	@echo "==> [1/11] policy: правила проекта"
	@$(MAKE) --no-print-directory policy
	@echo "==> [2/11] guard: охрана приёмки (pipeline, CODEOWNERS, ослабление проверок)"
	@$(MAKE) --no-print-directory guard
	@echo "==> [3/11] format-check: форматирование"
	@$(MAKE) --no-print-directory format-check
	@echo "==> [4/11] lint-check: линтер"
	@$(MAKE) --no-print-directory lint-check
	@echo "==> [5/11] sast: анализ безопасности кода"
	@$(MAKE) --no-print-directory sast
	@echo "==> [6/11] secrets: поиск секретов"
	@$(MAKE) --no-print-directory secrets
	@echo "==> [7/11] deps-audit: уязвимости зависимостей"
	@$(MAKE) --no-print-directory deps-audit
	@echo "==> [8/11] test: unit- и интеграционные тесты, покрытие"
	@$(MAKE) --no-print-directory test
	@echo "==> [9/11] mutation: мутационная проверка тестов"
	@$(MAKE) --no-print-directory mutation
	@echo "==> [10/11] migrations-check: миграции на временной базе"
	@$(MAKE) --no-print-directory migrations-check
	@echo "==> [11/11] backup-check: резервная копия и восстановление на временной базе"
	@$(MAKE) --no-print-directory backup-check
	@echo "==> make verify: все проверки пройдены. Отчёты — в каталоге $(REPORTS)/"

# ---------------------------------------------------------------------------
# Контейнерное окружение (ЛР4): docs/CONTAINERS.md. Нужны Docker и Compose v2;
# на сервере команды выполняются через sudo. Виртуальное окружение .venv не нужно.
# ---------------------------------------------------------------------------
container-env:  ## файлы паролей deploy/docker/env/*.env (случайные значения, 600)
	bash deploy/docker/init-env.sh

lock:  ## requirements.lock: версии и хеши пакетов образа (нужен доступ к PyPI)
	bash deploy/docker/lock.sh

container-policy:  ## правила файлов: Dockerfile, .dockerignore, compose.yaml, пароли, lock
	@$(PYTHON) tools/container_policy.py

container-build:  ## сборка образа приложения warehouse-app:<версия>-<коммит>
	$(COMPOSE) build app  # migrate использует тот же образ: параллельная сборка двух сервисов в один тег падает ("already exists")
	@echo "Собран образ warehouse-app:$(APP_IMAGE_TAG) (коммит $(APP_REVISION))"

container-image-check:  ## собранный образ: пользователь, HEALTHCHECK, отсутствие секретов
	@$(PYTHON) tools/container_policy.py --image

image-scan: tools  ## уязвимости образа (trivy): HIGH и CRITICAL с исправлением — ошибка
	@mkdir -p $(REPORTS)
	@status=0; $(TOOLS)/trivy image --quiet --scanners vuln --severity HIGH,CRITICAL \
		--ignore-unfixed --ignorefile .trivyignore --exit-code 1 \
		--format json --output $(REPORTS)/trivy-image.json warehouse-app:$(APP_IMAGE_TAG) \
		|| status=$$?; \
		$(TOOLS)/trivy convert --format table $(REPORTS)/trivy-image.json; exit $$status

image-tag:  ## тег образа приложения для текущего коммита
	@echo $(APP_IMAGE_TAG)

container-check:  ## ОБЯЗАТЕЛЬНАЯ проверка перед любым изменением контейнерного окружения
	@echo "==> [1/4] container-policy: Dockerfile, .dockerignore, compose.yaml, пароли, lock"
	@$(MAKE) --no-print-directory container-policy
	@echo "==> [2/4] container-build: сборка образа приложения"
	@$(MAKE) --no-print-directory container-build
	@echo "==> [3/4] container-image-check: пользователь, HEALTHCHECK, отсутствие секретов"
	@$(MAKE) --no-print-directory container-image-check
	@echo "==> [4/4] image-scan: уязвимости образа"
	@$(MAKE) --no-print-directory image-scan
	@echo "==> make container-check: пройдено, окружение можно изменять"

container-up: container-check  ## развернуть или обновить окружение (только после container-check)
	$(COMPOSE) up -d --remove-orphans
	bash deploy/docker/wait.sh

container-down:  ## остановить и удалить контейнеры (volume с данными сохраняется)
	$(COMPOSE) down

container-ps:  ## состояние контейнеров и их проверок
	$(COMPOSE) ps -a

container-logs:  ## журналы: make container-logs [S=app]
	$(COMPOSE) logs --tail 100 $(S)

container-migrate:  ## миграции и начальные данные в контейнере migrate
	$(COMPOSE) run --rm migrate

container-balance:  ## распределение запросов между экземплярами: [N=20]
	bash deploy/docker/balance.sh $(or $(N),20)

container-backup:  ## резервная копия базы из контейнера db в backups/
	bash deploy/docker/dbctl.sh backup

container-backups:  ## перечень резервных копий
	bash deploy/docker/dbctl.sh list

container-counts:  ## число записей в таблицах базы контейнера db
	bash deploy/docker/dbctl.sh counts

container-restore:  ## восстановление в текущую базу: [FILE=backups/….dump] [YES=1]
	bash deploy/docker/dbctl.sh restore $(FILE) $(if $(filter 1,$(YES)),--yes)

container-restore-new:  ## восстановление в новый volume: FILE=… VOLUME=…
	bash deploy/docker/dbctl.sh restore-new "$(FILE)" "$(VOLUME)"

container-use-volume:  ## переключить базу на другой volume: VOLUME=… (через container-check)
	bash deploy/docker/dbctl.sh use-volume "$(VOLUME)"
	$(MAKE) --no-print-directory container-up

hooks:  ## git-хук: make verify перед каждым git push
	git config core.hooksPath .githooks
	@echo "Хук pre-push включён: git push выполнится только после успешного make verify"

clean:  ## удалить кэши и отчёты (.tools с инструментами сохраняется)
	rm -rf .pytest_cache .ruff_cache .coverage $(REPORTS)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
