"""requirements.lock — точные версии и хеши SHA-256 всех пакетов образа (make lock).

В pyproject.toml закреплены только прямые зависимости; их зависимости (anyio,
pydantic-core и др.) без файла блокировки выбирались бы заново при каждой сборке.
Скрипт скачивает колёса (wheel) прямых зависимостей вместе со всеми транзитивными
и записывает для каждого файла версию и хеш. Dockerfile ставит пакеты с ключами
--require-hashes --no-deps: другая версия или подменённый файл — ошибка сборки.

Запускается внутри базового образа (deploy/docker/lock.sh): тот же Python и та же
платформа, что при docker build, поэтому pip выберет при сборке именно эти файлы.
"""

from __future__ import annotations

import hashlib
import re
import subprocess  # nosec B404  # запуск pip без оболочки
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements.lock"
#: имя и версия из имени файла колеса: name-version-pyX-abi-platform.whl
WHEEL = re.compile(r"^(?P<name>[^-]+)-(?P<version>[^-]+)-.+\.whl$")
HEADER = """\
# Файл создан командой make lock (tools/lock.py) — вручную не редактировать.
# Точные версии и SHA-256 всех пакетов образа приложения для {python} ({platform}).
# Источник версий прямых зависимостей — [project].dependencies в pyproject.toml.
"""


def canonical(name: str) -> str:
    """Имя пакета в нормальной форме: строчные буквы, разделитель «-»."""
    return re.sub(r"[-_.]+", "-", name).lower()


def main() -> int:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    with tempfile.TemporaryDirectory() as folder:
        command = [sys.executable, "-m", "pip", "download", "--quiet", "--no-cache-dir"]
        command += ["--only-binary=:all:", "--dest", folder, *project["dependencies"]]
        # Безопасно: аргументы — зафиксированные зависимости из pyproject.toml, без оболочки
        subprocess.run(command, check=True)  # nosec B603
        entries = []
        for path in sorted(Path(folder).iterdir()):
            match = WHEEL.match(path.name)
            if not match:
                print(f"Не колесо (wheel): {path.name} — сборка без компилятора невозможна")
                return 1
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append(
                f"{canonical(match['name'])}=={match['version']} \\\n    --hash=sha256:{digest}"
            )
    python = f"Python {sys.version_info.major}.{sys.version_info.minor}"
    header = HEADER.format(python=python, platform=sys.platform)
    LOCK.write_text(header + "\n".join(sorted(entries)) + "\n", encoding="utf-8")
    print(f"Записан {LOCK.name}: пакетов {len(entries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
