"""Правила контейнерного окружения — основа make container-check (пп. 12–14 ЛР4).

Запреты (п. 13 задания):
  1. latest и «плавающие» версии образов: только точная версия и дайджест sha256;
  2. запуск от root: USER в Dockerfile, user у сервисов с готовыми образами;
  3. секреты в образе: .dockerignore по белому списку, нет ENV/ARG/COPY с секретами,
     в собранном образе нет значений паролей и файлов настроек;
  4. отключение healthcheck: у каждого постоянно работающего сервиса есть проверка;
  5. публикация лишних портов: наружу открыт только порт Nginx.
Также проверяются: ограничения ресурсов, внутренние сети, порядок запуска (depends_on),
форма exec у CMD, файлы паролей (600, вне git), requirements.lock (хеши, версии из
pyproject.toml) и то, что make container-up начинается с make container-check.

    python3 tools/container_policy.py            правила файлов (до сборки образа)
    python3 tools/container_policy.py --image    проверка собранного образа приложения

Только стандартная библиотека: команда работает на сервере без виртуального окружения.
"""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess  # nosec B404  # запуск docker и git без оболочки
import sys
import tarfile
import tomllib
from pathlib import Path
from typing import IO, Any

ROOT = Path(__file__).resolve().parents[1]

#: Единственный сервис, публикующий порт, и порт внутри его контейнера
PUBLIC_SERVICE = "nginx"
PUBLIC_TARGET = 8080
#: Сервис приложения (образ собирается из Dockerfile) и минимум его экземпляров
APP_SERVICE = "app"
MIN_REPLICAS = 2
#: Ожидаемый порядок запуска: сервис → {зависимость: условие}
STARTUP_ORDER = {
    "migrate": {"db": "service_healthy"},
    "app": {"db": "service_healthy", "migrate": "service_completed_successfully"},
    "nginx": {"app": "service_healthy"},
}
#: Сервисы, которым нужна файловая система только для чтения
READ_ONLY = ("app", "migrate", "nginx")
#: Что может лежать в каталоге /app образа
IMAGE_APP_FILES = {"alembic.ini", "pyproject.toml", "app", "migrations"}

#: Файлы с паролями (создаёт make container-env) и какие сервисы какие файлы получают
ENV_DIR = "deploy/docker/env"
ENV_FILES = ("db.env", "app.env", "migrate.env")
#: Переменная роли-владельца базы: нужна только migrate, экземплярам app — нет
OWNER_VARIABLE = "ALEMBIC_DATABASE_URL"

IMAGE_NAME = r"^[a-z0-9][a-z0-9./_-]*"
PINNED_IMAGE = re.compile(IMAGE_NAME + r":(?P<tag>[A-Za-z0-9._-]+)@sha256:[0-9a-f]{64}$")
#: Свой образ: версия (0.1.0) или версия и начало хеша коммита (0.1.0-1a2b3c4d5e6f, ЛР5)
BUILT_IMAGE = re.compile(IMAGE_NAME + r":[0-9]+\.[0-9]+\.[0-9]+(-[0-9a-f]{7,40})?$")
VERSION_TAG = re.compile(r"[0-9]+\.[0-9]+")
SECRET_NAME = re.compile(r"PASSWORD|SECRET|TOKEN|PRIVATE|API_KEY|DATABASE_URL", re.IGNORECASE)
#: Похожи на секреты по имени, но это обычные настройки
NOT_SECRET = {"PASSWORD_MIN_LENGTH"}
PACKAGE = r"^(?P<name>[A-Za-z0-9._-]+)"
LOCK_ENTRY = re.compile(PACKAGE + r"==(?P<v>\S+)(\s+--hash=sha256:[0-9a-f]{64})+$")
REQUIREMENT = re.compile(PACKAGE + r"(\[(?P<extras>[A-Za-z0-9_,-]+)\])?==(?P<v>\S+)$")
ROOT_USERS = {"root", "0", "0:0", "root:root"}

problems: list[str] = []


def problem(where: str, text: str) -> None:
    problems.append(f"{where}: {text}")


def run(*args: str, text: bool = True) -> subprocess.CompletedProcess[Any]:
    # Безопасно: команды и аргументы формирует сама программа, оболочка не используется
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=text, check=False)  # nosec B603


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------------------
# Dockerfile и .dockerignore
# ---------------------------------------------------------------------------
def instructions(path: Path) -> list[tuple[int, str, str]]:
    """Инструкции Dockerfile: (номер строки, ИНСТРУКЦИЯ, аргументы); переносы «\\» склеены."""
    result: list[tuple[int, str, str]] = []
    buffer, start = "", 0
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not buffer and (not line or line.startswith("#")):
            continue
        if not buffer:
            start = number
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        buffer += line
        keyword, _, rest = buffer.partition(" ")
        result.append((start, keyword.upper(), rest.strip()))
        buffer = ""
    return result


def check_image_reference(where: str, image: str) -> None:
    match = PINNED_IMAGE.match(image)
    if not match:
        problem(where, f"образ «{image}» не закреплён: нужно имя:точная-версия@sha256:дайджест")
    elif match["tag"] == "latest" or not VERSION_TAG.search(match["tag"]):
        problem(where, f"тег «{match['tag']}» — не точная версия (latest запрещён)")


def check_dockerfile() -> None:
    path = ROOT / "Dockerfile"
    if not path.is_file():
        problem("Dockerfile", "файл не найден")
        return
    stages: set[str] = set()
    final: list[tuple[int, str, str]] = []
    for number, keyword, rest in instructions(path):
        where = f"Dockerfile:{number}"
        if keyword == "FROM":
            parts = rest.split()
            if parts[0] not in stages:
                check_image_reference(where, parts[0])
            if len(parts) == 3 and parts[1].upper() == "AS":
                stages.add(parts[2])
            final = []
        final.append((number, keyword, rest))
        if keyword in ("ENV", "ARG"):
            for name in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=", rest) or [rest.split()[0]]:
                if SECRET_NAME.search(name):
                    problem(where, f"{keyword} {name}: секреты в образе запрещены")
        if keyword in ("COPY", "ADD"):
            if re.search(r"(^|[\s/])(\.env|[^\s]*\.env|secrets?|backups?)(\s|/|$)", rest):
                problem(where, f"{keyword} копирует файлы настроек или секреты: {rest}")
            if keyword == "ADD" and "://" in rest:
                problem(where, "ADD с адресом в сети: результат сборки не воспроизводим")
    defaults = {
        name: value
        for _, keyword, rest in final
        if keyword in ("ARG", "ENV")
        for name, value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=(\S+)", rest)
    }
    users = [rest for _, keyword, rest in final if keyword == "USER"]
    user = re.sub(r"\$\{?(\w+)\}?", lambda m: defaults.get(m[1], m[0]), users[-1]) if users else ""
    if not user:
        problem("Dockerfile", "в итоговой стадии нет USER: процесс запустится от root")
    elif user in ROOT_USERS or user.split(":")[0] in ROOT_USERS:
        problem("Dockerfile", f"USER {user}: запуск от root запрещён")
    health = [rest for _, keyword, rest in final if keyword == "HEALTHCHECK"]
    if not health or health[-1].upper().startswith("NONE"):
        problem("Dockerfile", "нет HEALTHCHECK (или HEALTHCHECK NONE)")
    commands = [rest for _, keyword, rest in final if keyword in ("CMD", "ENTRYPOINT")]
    if not commands or not all(command.startswith("[") for command in commands):
        problem("Dockerfile", "CMD нужен в форме exec [...]: иначе сигнал получит оболочка")


def check_dockerignore() -> None:
    path = ROOT / ".dockerignore"
    if not path.is_file():
        problem(".dockerignore", "файл не найден: в контекст сборки попадёт всё, включая .env")
        return
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines or lines[0] != "*":
        problem(".dockerignore", "первая строка должна быть «*» (белый список)")
    for line in lines:
        if line.startswith("!") and re.search(r"\.env|secret|backup|\.git|deploy|tests", line):
            problem(".dockerignore", f"в контекст сборки разрешено лишнее: {line}")


# ---------------------------------------------------------------------------
# requirements.lock
# ---------------------------------------------------------------------------
def check_lock() -> None:
    path = ROOT / "requirements.lock"
    if not path.is_file():
        problem("requirements.lock", "файл не найден — выполните make lock")
        return
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    locked: dict[str, str] = {}
    for line in text.splitlines():
        line = " ".join(line.split())
        if not line or line.startswith("#"):
            continue
        match = LOCK_ENTRY.match(line)
        if not match:
            problem("requirements.lock", f"нет точной версии или хеша: {line[:60]}")
            continue
        locked[canonical(match["name"])] = match["v"]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for requirement in project["dependencies"]:
        match = REQUIREMENT.match(requirement.replace(" ", ""))
        if not match:
            continue  # точность версий в pyproject.toml проверяет make policy
        names = [canonical(match["name"])]
        names += [f"{names[0]}-{extra}" for extra in (match["extras"] or "").split(",") if extra]
        for name in names:
            if locked.get(name) != match["v"]:
                problem(
                    "requirements.lock",
                    f"{name}: {locked.get(name, 'нет')} вместо {match['v']} из pyproject.toml"
                    " — выполните make lock",
                )


# ---------------------------------------------------------------------------
# compose.yaml
# ---------------------------------------------------------------------------
def compose_config() -> dict[str, Any] | None:
    """Итоговая конфигурация: подставлены переменные, файлы env_file и значения по умолчанию."""
    result = run("docker", "compose", "config", "--format", "json")
    if result.returncode != 0:
        hint = " — выполните make container-env" if "env" in result.stderr else ""
        problem("compose.yaml", "docker compose config: " + result.stderr.strip() + hint)
        return None
    config: dict[str, Any] = json.loads(result.stdout)
    return config


def check_env_files() -> None:
    git = (ROOT / ".git").exists()
    tracked = run("git", "ls-files").stdout.split() if git else []
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for file in ENV_FILES:
        name = f"{ENV_DIR}/{file}"
        path = ROOT / name
        if not path.is_file():
            problem(name, "файл не найден — выполните make container-env")
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            problem(name, f"права {mode:o}: файл с паролями должен быть доступен только владельцу")
        if name in tracked:
            problem(name, "файл с паролями добавлен в git")
        if git:
            excluded = run("git", "check-ignore", "-q", name).returncode == 0
        else:  # копия проекта без .git (сервер): проверяем само правило
            excluded = f"{ENV_DIR}/*.env" in ignored
        if not excluded:
            problem(name, "не исключён в .gitignore")


def check_raw_compose() -> None:
    """Пароли не записываются в compose.yaml: только через файлы env_file."""
    path = ROOT / "compose.yaml"
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.match(r"^\s*-?\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(\S.*)?$", line)
        secret = match and SECRET_NAME.search(match[1]) and match[1] not in NOT_SECRET
        if secret and match[2] and not match[2].startswith("#"):
            problem(f"compose.yaml:{number}", f"{match[1]}: секрет в compose.yaml — в env_file")


def check_service(name: str, svc: dict[str, Any], networks: dict[str, Any]) -> None:
    where = f"compose.yaml: {name}"
    image = svc.get("image", "")
    if "build" in svc:
        if not BUILT_IMAGE.match(image):
            problem(where, f"образ «{image}» — нужен тег вида 0.1.0 или 0.1.0-<коммит>, не latest")
    else:
        check_image_reference(where, image)
        if not svc.get("user"):
            problem(where, "не задан user: готовый образ иначе работает от root")
    if str(svc.get("user", "")) in ROOT_USERS:
        problem(where, f"user {svc['user']}: запуск от root запрещён")
    for key in ("privileged", "cap_add"):
        if svc.get(key):
            problem(where, f"{key} запрещён")
    if svc.get("network_mode") == "host" or svc.get("pid") == "host":
        problem(where, "режим host запрещён: контейнер должен быть изолирован")
    if "no-new-privileges:true" not in svc.get("security_opt", []):
        problem(where, "нет security_opt no-new-privileges:true")
    if "ALL" not in svc.get("cap_drop", []):
        problem(where, "нет cap_drop: [ALL]")
    if name in READ_ONLY and not svc.get("read_only"):
        problem(where, "нужно read_only: true")
    environment = svc.get("environment") or {}
    if name == APP_SERVICE and OWNER_VARIABLE in environment:
        problem(where, f"{OWNER_VARIABLE} (пароль владельца базы) нужен только migrate")
    if name == PUBLIC_SERVICE and any(SECRET_NAME.search(key) for key in environment):
        problem(where, "Nginx не должен получать пароли")

    ports = svc.get("ports", [])
    if name == PUBLIC_SERVICE:
        if [port.get("target") for port in ports] != [PUBLIC_TARGET]:
            problem(where, f"должен публиковать ровно один порт → {PUBLIC_TARGET}")
    elif ports:
        published = ", ".join(str(port.get("published", port.get("target"))) for port in ports)
        problem(where, f"публикует порт {published}: наружу открыт только {PUBLIC_SERVICE}")

    long_running = svc.get("restart", "no") != "no"
    health = svc.get("healthcheck", {})
    if health.get("disable") or (health.get("test") or [""])[0] == "NONE":
        problem(where, "healthcheck отключён — это запрещено")
    elif long_running and not health.get("test"):
        problem(where, "нет healthcheck у постоянно работающего сервиса")
    if not long_running and name != "migrate":
        problem(where, "нет политики restart (unless-stopped)")

    limits = svc.get("deploy", {}).get("resources", {}).get("limits", {})
    for key in ("cpus", "memory", "pids"):
        if not limits.get(key):
            problem(where, f"нет ограничения ресурсов deploy.resources.limits.{key}")

    attached = list(svc.get("networks", {}))
    internal = [network for network in attached if networks.get(network, {}).get("internal")]
    if name == PUBLIC_SERVICE:
        if len(internal) == len(attached):
            problem(where, "Nginx должен быть во внешней сети (через неё публикуется порт)")
    elif internal != attached:
        problem(where, "должен быть только во внутренних сетях (internal: true)")

    expected = STARTUP_ORDER.get(name, {})
    actual = {dep: value.get("condition") for dep, value in svc.get("depends_on", {}).items()}
    for dependency, condition in expected.items():
        if actual.get(dependency) != condition:
            problem(where, f"depends_on {dependency} должен иметь condition: {condition}")


def check_compose() -> None:
    check_raw_compose()
    config = compose_config()
    if config is None:
        return
    services: dict[str, Any] = config.get("services", {})
    networks: dict[str, Any] = config.get("networks", {})
    for required in (APP_SERVICE, PUBLIC_SERVICE, "db", "migrate"):
        if required not in services:
            problem("compose.yaml", f"нет сервиса {required}")
    for name, svc in sorted(services.items()):
        check_service(name, svc, networks)
    replicas = services.get(APP_SERVICE, {}).get("deploy", {}).get("replicas", 1)
    if int(replicas) < MIN_REPLICAS:
        problem("compose.yaml: app", f"экземпляров {replicas}, нужно не менее {MIN_REPLICAS}")
    db_volumes = [item.get("type") for item in services.get("db", {}).get("volumes", [])]
    if "volume" not in db_volumes:
        problem("compose.yaml: db", "данные базы должны храниться в именованном volume")


def check_makefile() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    if not re.search(r"^container-up:[^\n#]*\bcontainer-check\b", text, re.MULTILINE):
        problem("Makefile", "make container-up должен выполняться только после container-check")


# ---------------------------------------------------------------------------
# Собранный образ (--image)
# ---------------------------------------------------------------------------
def secret_values() -> set[str]:
    """Значения из файлов env_file: ни одно не должно встречаться в образе."""
    values = set()
    for path in (ROOT / ENV_DIR).glob("*.env"):
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.startswith("#") and len(value) >= 8:
                values.add(value)
                values.update(re.findall(r"://[^:]+:([^@]{8,})@", value))  # пароль в адресе
    return values


def check_image() -> None:
    config = compose_config()
    if config is None:
        return
    image = config["services"][APP_SERVICE]["image"]
    inspect = run("docker", "image", "inspect", image)
    if inspect.returncode != 0:
        problem(image, "образ не собран — docker compose build")
        return
    info = json.loads(inspect.stdout)[0]["Config"]
    user = info.get("User") or "root"
    if user in ROOT_USERS or user.split(":")[0] in ROOT_USERS:
        problem(image, f"пользователь образа {user} — root")
    for variable in info.get("Env") or []:
        if SECRET_NAME.search(variable.partition("=")[0]):
            problem(image, f"переменная {variable.partition('=')[0]} зашита в образ")
    test = (info.get("Healthcheck") or {}).get("Test") or ["NONE"]
    if test[0] == "NONE":
        problem(image, "в образе нет HEALTHCHECK")
    getuid = "import os; print(os.getuid())"
    started = run("docker", "run", "--rm", "--network", "none", image, "python", "-c", getuid)
    uid = started.stdout.strip()
    if not uid.isdigit() or uid == "0":
        problem(image, f"процесс в контейнере работает с UID {uid or '?'} (нужен не 0)")

    history = run("docker", "history", "--no-trunc", "--format", "{{.CreatedBy}}", image).stdout
    secrets = secret_values()
    for value in secrets:
        if value in history or value in inspect.stdout:
            problem(image, "значение из deploy/docker/env/*.env найдено в истории образа")
    container = run("docker", "create", image).stdout.strip()
    try:
        command = ["docker", "export", container]
        # Безопасно: аргументы формирует программа, оболочка не используется
        with subprocess.Popen(command, stdout=subprocess.PIPE) as export:  # nosec B603
            check_image_files(image, export.stdout, secrets)
    finally:
        run("docker", "rm", container)


def check_image_files(image: str, stream: IO[bytes] | None, secrets: set[str]) -> None:
    """Файлы образа (поток docker export): в /app — только код, без настроек и паролей."""
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            name = member.name.lstrip("./")
            if not name.startswith("app/"):
                continue
            parts = name.split("/")
            if parts[1] not in IMAGE_APP_FILES:
                problem(image, f"лишний файл в образе: /{name}")
            if re.search(r"(^|/)(\.env|[^/]*\.env|[^/]*\.dump)$", name):
                problem(image, f"файл настроек или копия базы в образе: /{name}")
            if member.isfile() and member.size < 1_000_000:
                content = archive.extractfile(member)
                data = content.read().decode("utf-8", "ignore") if content else ""
                if any(value in data for value in secrets):
                    problem(image, f"значение пароля найдено в файле /{name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--image", action="store_true", help="проверить собранный образ")
    args = parser.parse_args(argv)
    if args.image:
        check_image()
        done = "образ: пользователь не root, HEALTHCHECK есть, секретов и лишних файлов нет"
    else:
        check_dockerfile()
        check_dockerignore()
        check_lock()
        check_env_files()
        check_compose()
        check_makefile()
        done = (
            "правила соблюдены: образы закреплены (без latest), не root, секретов нет, "
            "healthcheck включён, наружу только порт Nginx, ресурсы ограничены"
        )
    if problems:
        print("Нарушены правила контейнерного окружения (docs/CONTAINERS.md):")
        print("\n".join(f"  - {item}" for item in problems))
        return 1
    print(f"container-check: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
