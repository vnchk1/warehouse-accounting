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

.PHONY: help setup install run migrate migrate-down migrate-status seed demo_seed \
        backup restore backups psql lint policy format-check lint-check sast test \
        test-unit mutation verify hooks clean \
        container-env lock container-check container-up container-down container-ps \
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

backup:  ## резервная копия базы данных в backups/
	$(PY) -m app.dbadmin backup

restore:  ## восстановление: make restore [FILE=backups/....dump] [YES=1]
	$(PY) -m app.dbadmin restore $(FILE) $(if $(filter 1,$(YES)),--yes)

backups:  ## перечень резервных копий
	$(PY) -m app.dbadmin list

psql:  ## консоль рабочей базы: make psql [SQL="SELECT ..."]
	$(PY) -m app.dbadmin psql $(if $(SQL),-c "$(SQL)")

# ---------------------------------------------------------------------------
# Проверки. Каждая команда — отдельный шаг make verify.
# ---------------------------------------------------------------------------
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

test:  ## все тесты с покрытием; отчёты reports/junit.xml и reports/htmlcov
	@mkdir -p $(REPORTS)
	$(PY) -m pytest --cov --cov-report=term --cov-report=html --cov-report=xml \
		--junitxml=$(REPORTS)/junit.xml

test-unit:  ## только unit-тесты (база данных не нужна)
	$(PY) -m pytest tests/unit

mutation:  ## мутационная проверка: тесты должны поймать изменение правил
	@mkdir -p $(REPORTS)
	$(PY) tools/mutation.py --report $(REPORTS)/mutation.txt

verify:  ## ОБЯЗАТЕЛЬНЫЙ набор проверок перед запросом на слияние
	@echo "==> [1/6] policy: правила проекта"
	@$(MAKE) --no-print-directory policy
	@echo "==> [2/6] format-check: форматирование"
	@$(MAKE) --no-print-directory format-check
	@echo "==> [3/6] lint-check: линтер"
	@$(MAKE) --no-print-directory lint-check
	@echo "==> [4/6] sast: анализ безопасности"
	@$(MAKE) --no-print-directory sast
	@echo "==> [5/6] test: unit- и интеграционные тесты, покрытие"
	@$(MAKE) --no-print-directory test
	@echo "==> [6/6] mutation: мутационная проверка тестов"
	@$(MAKE) --no-print-directory mutation
	@echo "==> make verify: все проверки пройдены. Отчёты — в каталоге $(REPORTS)/"

# ---------------------------------------------------------------------------
# Контейнерное окружение (ЛР4): docs/CONTAINERS.md. Нужны Docker и Compose v2;
# на сервере команды выполняются через sudo. Виртуальное окружение .venv не нужно.
# ---------------------------------------------------------------------------
container-env:  ## файлы паролей deploy/docker/env/*.env (случайные значения, 600)
	bash deploy/docker/init-env.sh

lock:  ## requirements.lock: версии и хеши пакетов образа (нужен доступ к PyPI)
	bash deploy/docker/lock.sh

container-check:  ## ОБЯЗАТЕЛЬНАЯ проверка перед любым изменением контейнерного окружения
	@echo "==> [1/3] правила: Dockerfile, .dockerignore, compose.yaml, пароли, requirements.lock"
	@$(PYTHON) tools/container_policy.py
	@echo "==> [2/3] сборка образа приложения"
	$(COMPOSE) build app  # migrate использует тот же образ: параллельная сборка двух сервисов в один тег падает ("already exists")
	@echo "==> [3/3] собранный образ: пользователь, HEALTHCHECK, отсутствие секретов"
	@$(PYTHON) tools/container_policy.py --image
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

clean:  ## удалить кэши и отчёты
	rm -rf .pytest_cache .ruff_cache .coverage $(REPORTS)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
