"""Проверка миграций и резервного копирования на отдельной временной базе (ЛР5, п. 5).

    python tools/db_check.py migrations    make migrations-check
    python tools/db_check.py backup        make backup-check

Выполняются те же команды make, что и вручную, но с DATABASE_URL временной базы
<тестовая база без _test>_<проверка>_check_test на сервере из TEST_DATABASE_URL.
Рабочая и основная тестовая базы не затрагиваются; временная база удаляется в конце.

migrations: пустая база → make migrate (последняя версия схемы) → make migrate-status →
            make migrate-down по одному шагу до пустой схемы → снова make migrate.
backup:     make migrate, seed, demo_seed → make backup → повреждение данных →
            make restore YES=1 → число записей и версия схемы как до повреждения.

Роли базы нужно право CREATEDB (как для тестов миграций, см. .env.example).
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess  # nosec B404  # запуск make без оболочки
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alembic.config import Config  # noqa: E402 — путь к проекту добавляется строкой выше
from alembic.script import ScriptDirectory  # noqa: E402 — то же
from sqlalchemy import create_engine, inspect, text  # noqa: E402 — то же
from sqlalchemy.engine import Connection, make_url  # noqa: E402 — то же
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402 — то же

from app.dbadmin import table_counts  # noqa: E402 — то же

#: Повреждение данных перед восстановлением: удаляются все поступления
DAMAGE = ("DELETE FROM receipt_item", "DELETE FROM receipt")

T = TypeVar("T")
lines: list[str] = []


class CheckError(Exception):
    """Проверка не пройдена — сообщение для пользователя."""


def say(message: str) -> None:
    print(message, flush=True)
    lines.append(message)


# ---------------------------------------------------------------------------
# Временная база
# ---------------------------------------------------------------------------
def _setting(name: str) -> str | None:
    """Значение из окружения или из файла .env проекта (как в tests/conftest.py)."""
    if os.environ.get(name):
        return os.environ[name]
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name and not key.lstrip().startswith("#"):
                return value.strip().strip("'\"")
    return None


def scratch_url(kind: str) -> str:
    """Адрес временной базы на сервере тестовой базы."""
    url = _setting("TEST_DATABASE_URL")
    if not url:
        working = _setting("DATABASE_URL")
        url = working + "_test" if working else None
    if not url:
        raise CheckError("не задан TEST_DATABASE_URL (в .env или в окружении)")
    parsed = make_url(url)
    name = parsed.database or ""
    if not name.endswith("_test"):
        raise CheckError(f"имя тестовой базы должно оканчиваться на _test, а указано «{name}»")
    scratch = f"{name.removesuffix('_test')}_{kind}_check_test"
    return parsed.set(database=scratch).render_as_string(hide_password=False)


def _on_server(url: str, *statements: str) -> None:
    """Команды в служебной базе postgres того же сервера (вне транзакции)."""
    server = make_url(url).set(database="postgres")
    engine = create_engine(server, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            for statement in statements:
                connection.execute(text(statement))
    finally:
        engine.dispose()


def create_database(url: str) -> None:
    name = make_url(url).database
    _on_server(url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', f'CREATE DATABASE "{name}"')


def drop_database(url: str) -> None:
    _on_server(url, f'DROP DATABASE IF EXISTS "{make_url(url).database}" WITH (FORCE)')


def in_database(url: str, action: Callable[[Connection], T]) -> T:
    """Действие в одной транзакции временной базы; соединение сразу закрывается."""
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            return action(connection)
    finally:
        engine.dispose()


def _revision(connection: Connection) -> str | None:
    if not inspect(connection).has_table("alembic_version"):
        return None
    value = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return str(value) if value is not None else None


def _tables(connection: Connection) -> list[str]:
    return sorted(set(inspect(connection).get_table_names()) - {"alembic_version"})


def _damage(connection: Connection) -> None:
    for statement in DAMAGE:
        connection.execute(text(statement))


def current_revision(url: str) -> str | None:
    return in_database(url, _revision)


def revisions() -> list[str]:
    """Ревизии миграций от первой к последней."""
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    return [revision.revision for revision in reversed(list(script.walk_revisions()))]


# ---------------------------------------------------------------------------
# Команды make с адресом временной базы
# ---------------------------------------------------------------------------
def make(url: str, *args: str) -> None:
    env = dict(os.environ, DATABASE_URL=url)
    env.pop("ALEMBIC_DATABASE_URL", None)  # миграции — тоже во временную базу
    # Параметры приложения для временной базы: случайные, нигде не сохраняются
    env.update(
        SECRET_KEY=secrets.token_urlsafe(32),
        ADMIN_LOGIN="admin",
        ADMIN_PASSWORD=secrets.token_urlsafe(12),
    )
    program = shutil.which("make")
    if program is None:
        raise CheckError("не найдена программа make")
    say(f"$ make {' '.join(args)}")
    # Безопасно: аргументы задаёт сама программа, оболочка не используется.
    result = subprocess.run(  # nosec B603
        [program, "--no-print-directory", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    for line in (result.stdout + result.stderr).splitlines():
        say(f"    {line}")
    if result.returncode != 0:
        raise CheckError(f"make {args[0]} завершилась с кодом {result.returncode}")


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckError(message)
    say(f"[ОК] {message}")


# ---------------------------------------------------------------------------
# Проверки
# ---------------------------------------------------------------------------
def check_migrations(url: str) -> None:
    chain = revisions()
    head = chain[-1]
    make(url, "migrate")
    expect(current_revision(url) == head, f"схема пустой базы приведена к последней версии {head}")
    make(url, "migrate-status")
    for expected in reversed([None, *chain[:-1]]):
        make(url, "migrate-down")
        expect(current_revision(url) == expected, f"откат на один шаг: версия {expected or 'base'}")
    leftovers = in_database(url, _tables)
    expect(not leftovers, f"после отката всех миграций таблиц проекта нет {leftovers or ''}")
    make(url, "migrate")
    expect(current_revision(url) == head, f"повторное применение миграций: версия {head}")
    say(f"Миграции ({len(chain)} шт.): применение, поэтапный откат, повторное применение — успешно")


def check_backup(url: str) -> None:
    head = revisions()[-1]
    make(url, "migrate")
    make(url, "seed")
    make(url, "demo_seed")
    before = table_counts(url)
    say(f"Данные до копии: {before}")
    expect(before["receipt"] > 0 and before["receipt_item"] > 0, "в базе есть поступления")
    with tempfile.TemporaryDirectory(prefix="backup-check-") as directory:
        make(url, "backup", f"BACKUP_DIR={directory}")
        copies = sorted(Path(directory).glob("warehouse-*.dump"))
        expect(len(copies) == 1, f"создана одна резервная копия {[p.name for p in copies]}")

        in_database(url, _damage)
        damaged = table_counts(url)
        say(f"Данные после повреждения: {damaged}")
        expect(damaged != before, "повреждение изменило данные")

        make(url, "restore", f"FILE={copies[0]}", "YES=1", f"BACKUP_DIR={directory}")
        after = table_counts(url)
        say(f"Данные после восстановления: {after}")
        expect(after == before, "число записей во всех таблицах как до повреждения")
        expect(current_revision(url) == head, f"версия схемы после восстановления {head}")
        safety = sorted(Path(directory).glob("before-restore-*.dump"))
        expect(len(safety) == 1, "перед восстановлением сохранена копия текущего состояния")
    say("Резервное копирование и восстановление — успешно")


CHECKS = {"migrations": check_migrations, "backup": check_backup}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("check", choices=sorted(CHECKS))
    parser.add_argument("--report", type=Path, help="сохранить ход проверки в файл")
    args = parser.parse_args(argv)
    status = 1
    try:
        url = scratch_url(args.check)
        say(f"Временная база: {make_url(url).database}")
        create_database(url)
        try:
            CHECKS[args.check](url)
            status = 0
        finally:
            drop_database(url)
    except CheckError as error:
        say(f"Ошибка: {error}")
    except SQLAlchemyError as error:
        say(f"Ошибка базы данных: {getattr(error, 'orig', None) or error}")
        say("Проверьте TEST_DATABASE_URL и право CREATEDB у роли (см. .env.example)")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status


if __name__ == "__main__":
    sys.exit(main())
