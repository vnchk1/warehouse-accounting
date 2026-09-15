# Обязательные команды проекта. Подробности — в README.md и CONTRIBUTING.md.
SHELL := /bin/bash
PYTHON ?= python3
VENV ?= .venv
PY := $(VENV)/bin/python

.PHONY: help setup run migrate seed demo_seed test lint verify clean

help:  ## список команд
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-10s %s\n", $$1, $$2}'

setup:  ## окружение, зависимости и .env
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"
	@test -f .env || { cp .env.example .env; echo "Создан .env — заполните значения"; }

run:  ## запуск приложения
	$(PY) -m app.main

migrate:  ## применить миграции
	$(VENV)/bin/alembic upgrade head

seed:  ## заполнить справочник ОКЕИ и создать администратора
	$(PY) -m app.seed

demo_seed:  ## добавить демо-данные для демонстрации (опционально)
	$(PY) -m app.demo_seed

test:  ## автоматические тесты
	$(PY) -m pytest

lint:  ## форматирование и статический анализ
	$(VENV)/bin/ruff format app tests
	$(VENV)/bin/ruff check --fix app tests

verify:  ## проверки перед запросом на слияние
	$(VENV)/bin/ruff format --check app tests
	$(VENV)/bin/ruff check app tests
	$(MAKE) test

clean:  ## удалить кэши
	rm -rf .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
