"""Резервное копирование и восстановление базы данных.

    python -m app.dbadmin backup              make backup
    python -m app.dbadmin restore [ФАЙЛ]      make restore [FILE=...] [YES=1]
    python -m app.dbadmin list                make backups
    python -m app.dbadmin psql [-c SQL]       make psql [SQL="..."]

Копия — архив pg_dump в собственном формате (-Fc): сжатый, проверяемый
командой pg_restore --list. Подключение берётся из DATABASE_URL; пароль
передаётся программам PostgreSQL через переменную окружения PGPASSWORD, а не
в командной строке, поэтому его не видно в списке процессов.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess  # nosec B404  # запуск программ PostgreSQL без оболочки
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.models import Material, Receipt, ReceiptItem, Supplier, Unit, User

BACKUP_DIR = Path("backups")
#: Обычные копии: warehouse-ГГГГММДД-ЧЧММСС.dump
PREFIX = "warehouse"
#: Копия текущего состояния, которую restore делает перед восстановлением
SAFETY_PREFIX = "before-restore"
#: Сколько ждать блокировку таблицы при восстановлении, если база занята
LOCK_TIMEOUT = "10s"
MODELS = (Unit, Supplier, Material, User, Receipt, ReceiptItem)


class BackupError(Exception):
    """Ошибка резервного копирования — сообщение для пользователя."""


def _database_url(database_url: str | None) -> str:
    return database_url or get_settings().database_url


def database_name(database_url: str | None = None) -> str:
    return make_url(_database_url(database_url)).database or ""


def connection_env(database_url: str | None = None) -> dict[str, str]:
    """Переменные окружения libpq для программ pg_dump, pg_restore и psql."""
    url = make_url(_database_url(database_url))
    env = dict(os.environ)
    env.update(
        PGHOST=url.host or "localhost",
        PGPORT=str(url.port or 5432),
        PGUSER=url.username or "",
        PGDATABASE=url.database or "",
    )
    if url.password:
        env["PGPASSWORD"] = url.password
    return env


def _program(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise BackupError(
            f"Не найдена программа {name}. Установите клиент PostgreSQL: "
            "sudo apt install postgresql-client"
        )
    return path


def _run(args: list[str], env: dict[str, str]) -> str:
    # Безопасно: список аргументов формирует сама программа, оболочка не используется.
    result = subprocess.run(args, env=env, capture_output=True, text=True, check=False)  # nosec B603
    if result.returncode != 0:
        raise BackupError(f"{Path(args[0]).name}: {result.stderr.strip() or 'ошибка'}")
    return result.stdout


def check_archive(path: Path) -> int:
    """Проверка, что файл — архив pg_dump. Возвращает число объектов в нём."""
    if not path.is_file():
        raise BackupError(f"Файл {path} не найден")
    listing = _run([_program("pg_restore"), "--list", str(path)], dict(os.environ))
    return len([line for line in listing.splitlines() if line and not line.startswith(";")])


def create_backup(
    directory: Path = BACKUP_DIR, *, prefix: str = PREFIX, database_url: str | None = None
) -> Path:
    """Создаёт копию базы данных и проверяет, что её можно прочитать."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{prefix}-{stamp}.dump"
    number = 1
    while path.exists():  # несколько копий в одну секунду
        path = directory / f"{prefix}-{stamp}-{number}.dump"
        number += 1
    env = connection_env(database_url)
    try:
        _run([_program("pg_dump"), "--format=custom", "--no-password", f"--file={path}"], env)
        check_archive(path)
    except BackupError:
        path.unlink(missing_ok=True)  # неполная копия хуже, чем никакой
        raise
    path.chmod(0o600)  # в копии все данные, включая хеши паролей
    return path


def list_backups(directory: Path = BACKUP_DIR, *, prefix: str = PREFIX) -> list[Path]:
    """Копии от старой к новой (имя содержит дату и время)."""
    return sorted(directory.glob(f"{prefix}-*.dump"))


def restore_backup(
    path: Path, *, directory: Path = BACKUP_DIR, database_url: str | None = None
) -> Path:
    """Заменяет содержимое базы данных содержимым копии.

    Сначала проверяется сам файл, затем сохраняется текущее состояние базы
    (копия before-restore-…), и только после этого выполняется восстановление
    одной транзакцией: при любой ошибке база остаётся в прежнем виде.
    """
    check_archive(path)
    safety = create_backup(directory, prefix=SAFETY_PREFIX, database_url=database_url)
    env = connection_env(database_url)
    env["PGOPTIONS"] = f"-c lock_timeout={LOCK_TIMEOUT}"
    _run(
        [
            _program("pg_restore"),
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--single-transaction",
            "--exit-on-error",
            "--no-password",
            f"--dbname={env['PGDATABASE']}",
            str(path),
        ],
        env,
    )
    return safety


def table_counts(database_url: str | None = None) -> dict[str, int]:
    """Число записей в таблицах — для контроля после восстановления."""
    engine = create_engine(_database_url(database_url))
    try:
        with engine.connect() as connection:
            return {
                model.__tablename__: int(
                    connection.execute(select(func.count()).select_from(model)).scalar_one()
                )
                for model in MODELS
            }
    finally:
        engine.dispose()


def _print_counts() -> None:
    counts = table_counts()
    print("Записей в таблицах: " + ", ".join(f"{name} {count}" for name, count in counts.items()))


def _confirm(path: Path) -> bool:
    name = database_name()
    print(f"База данных «{name}» будет заменена содержимым копии {path}.")
    print("Перед этим текущее состояние будет сохранено в копию before-restore-….")
    answer = input(f"Для подтверждения введите имя базы данных ({name}): ")
    return answer.strip() == name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.dbadmin", description=__doc__)
    parser.add_argument("--dir", type=Path, default=BACKUP_DIR, help="каталог копий")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup", help="создать резервную копию")
    restore = commands.add_parser("restore", help="восстановить базу из копии")
    restore.add_argument("file", nargs="?", type=Path, help="файл копии (по умолчанию — последний)")
    restore.add_argument("--yes", action="store_true", help="не спрашивать подтверждение")
    commands.add_parser("list", help="перечень копий")
    psql = commands.add_parser("psql", help="консоль psql к рабочей базе")
    psql.add_argument("-c", dest="sql", help="выполнить команду SQL и выйти")
    args = parser.parse_args(argv)

    try:
        if args.command == "backup":
            path = create_backup(args.dir)
            print(f"Резервная копия создана: {path} ({path.stat().st_size} байт)")
            _print_counts()
        elif args.command == "list":
            backups = list_backups(args.dir)
            for path in backups:
                print(f"{path}  {path.stat().st_size} байт")
            if not backups:
                print(f"В каталоге {args.dir} резервных копий нет")
        elif args.command == "restore":
            backups = list_backups(args.dir)
            path = args.file or (backups[-1] if backups else None)
            if path is None:
                raise BackupError(f"В каталоге {args.dir} нет копий. Сначала: make backup")
            if not args.yes and not _confirm(path):
                print("Восстановление отменено")
                return 1
            safety = restore_backup(path, directory=args.dir)
            print(f"База данных восстановлена из {path}")
            print(f"Состояние до восстановления сохранено в {safety}")
            _print_counts()
        else:  # psql
            command = [_program("psql"), "--set=ON_ERROR_STOP=1"]
            command += ["-c", args.sql] if args.sql else []
            env = connection_env()
            # Безопасно: аргументы формирует программа, оболочка не используется.
            completed = subprocess.run(command, env=env, check=False)  # nosec B603
            return completed.returncode
    except BackupError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
