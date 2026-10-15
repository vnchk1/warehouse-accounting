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

.PHONY: help setup install run migrate migrate-down migrate-status seed demo_seed \
        backup restore backups psql lint policy format-check lint-check sast test \
        test-unit mutation verify hooks clean

help:  ## список команд
	@grep -E '^[a-z_-]+:.*##' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*## "}{printf "  %-15s %s\n", $$1, $$2}'

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

hooks:  ## git-хук: make verify перед каждым git push
	git config core.hooksPath .githooks
	@echo "Хук pre-push включён: git push выполнится только после успешного make verify"

clean:  ## удалить кэши и отчёты
	rm -rf .pytest_cache .ruff_cache .coverage $(REPORTS)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
