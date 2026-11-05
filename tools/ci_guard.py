"""Охрана приёмки: make guard и задание guard в CI (ЛР5, пп. 4, 13–15).

Правила текущего состояния (проверяются всегда):
  1. Pipeline .github/workflows/ci.yml: события push, pull_request и workflow_dispatch;
     все обязательные задания на месте; публикация зависит от всех проверок и не имеет
     условий, которые позволили бы ей выполниться после ошибки; нет continue-on-error,
     «|| true», «set +e», --no-verify; always() — только у шагов сохранения отчётов;
     сторонние actions закреплены полным хешем коммита; PostgreSQL в CI — тот же образ,
     что в compose.yaml.
  2. Локальные и серверные проверки совпадают: каждый шаг make verify и make
     container-check выполняется в CI, а CI не запускает проверок, которых нет локально.
  3. .github/CODEOWNERS назначает владельцев всем критическим файлам приёмки.
  4. В репозитории нет файлов, которым там не место: .env, копии базы, закрытые ключи.

Сравнение с основной веткой (BASE_REF, точнее — с точкой ответвления от неё):
  5. Не удалены файлы тестов и тестовые функции, не стало меньше проверок (assert,
     pytest.raises) — так нельзя «выключить» тест, оставив его имя.
  6. Не ослаблены правила: нижние границы и списки tools/policy.py, пороги и исключения
     в pyproject.toml, правила .gitleaks.toml, исключения .trivyignore.
  7. Перечень изменённых критических файлов — в отчёт: их обязаны одобрить владельцы
     (CODEOWNERS + правило защиты ветки «Require review from Code Owners»).

Ослабление может быть оправдано (например, тест заменён более точным). Тогда команда
ставит на запрос на слияние метку acceptance-change (локально — GUARD_APPROVED=1):
найденное печатается как предупреждение, а слияние всё равно ждёт одобрения владельцев.

В CI задание guard запускает версию этого файла из основной ветки: изменение самой
охраны вступает в силу только после проверки и слияния.

Работает в корне проекта (текущий каталог); нужны git и PyYAML.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess  # nosec B404  # запуск git без оболочки
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path.cwd()
WORKFLOW = ".github/workflows/ci.yml"
LOCAL_ACTIONS = ".github/actions"
CODEOWNERS = ".github/CODEOWNERS"

#: Задания pipeline; publish выполняется только после успеха всех остальных
CHECK_JOBS = (
    "guard",
    "style",
    "tests",
    "migrations",
    "backup",
    "security",
    "container",
    "build",
    "image-scan",
)
PUBLISH_JOB = "publish"
#: Окружение GitHub с секретами реестра (доступно только основной ветке)
PUBLISH_ENVIRONMENT = "registry"
#: Цели make, которые CI вызывает для подготовки, а не для проверки
HELPER_TARGETS = {"install", "tools", "container-env", "image-tag"}
#: Метка запроса на слияние: ослабление приёмки согласовано командой
APPROVAL_LABEL = "acceptance-change"

#: Критические файлы приёмки: тесты, Makefile, настройки анализаторов и pipeline
CRITICAL = (
    "/.github/",
    "/.githooks/",
    "/tests/",
    "/tools/",
    "/migrations/",
    "/Makefile",
    "/pyproject.toml",
    "/Dockerfile",
    "/.dockerignore",
    "/compose.yaml",
    "/requirements.lock",
    "/.gitleaks.toml",
    "/.trivyignore",
    "/CONTRIBUTING.md",
)

FORBIDDEN_RUN = {
    r"\|\|\s*(true|:)(\s|;|$)": "ошибка команды подавлена («|| true»)",
    r"\bset\s+\+e\b": "остановка при ошибке выключена («set +e»)",
    r"--no-verify": "обход проверок git (--no-verify)",
    r"\bexit\s+0\b": "принудительный успешный выход («exit 0»)",
    r"PYTEST_ADDOPTS": "ключи pytest в обход pyproject.toml (PYTEST_ADDOPTS)",
    r"--exit-zero|--exit-code[ =]0\b": "анализатор не может завершиться ошибкой",
}
STATUS_FUNCTION = re.compile(r"\b(always|failure|cancelled|success)\s*\(")
PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
MAKE_CALL = re.compile(r"(?:^|[\s;&|(])make\s+(?:-\S+\s+)*([a-z][a-z0-9_-]*)")
FORBIDDEN_FILE = re.compile(
    r"(^|/)\.env$|^deploy/docker/env/[^/]+\.env$|(^|/)backups/|\.dump$|\.pem$|\.key$"
    r"|(^|/)id_(rsa|ecdsa|ed25519)$"
)
TEST_FILE = re.compile(r"^tests/(.+/)?test_[^/]+\.py$")

problems: list[str] = []
weakenings: list[str] = []
notes: list[str] = []


def problem(where: str, text: str) -> None:
    problems.append(f"{where}: {text}")


def weakening(where: str, text: str) -> None:
    weakenings.append(f"{where}: {text}")


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------
def git(*args: str) -> subprocess.CompletedProcess[str]:
    program = shutil.which("git")
    if program is None:
        raise SystemExit("make guard: не найдена программа git")
    # Безопасно: аргументы формирует сама программа, оболочка не используется.
    return subprocess.run(  # nosec B603
        [program, *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def in_repository() -> bool:
    return git("rev-parse", "--is-inside-work-tree").returncode == 0


def merge_base(base: str) -> str | None:
    """Точка ответвления текущей ветки от основной; None — сравнивать не с чем."""
    if git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode != 0:
        return None
    result = git("merge-base", base, "HEAD")
    return result.stdout.strip() or None


def base_text(commit: str, path: str) -> str | None:
    result = git("show", f"{commit}:{path}")
    return result.stdout if result.returncode == 0 else None


def base_paths(commit: str, prefix: str) -> list[str]:
    return git("ls-tree", "-r", "--name-only", commit, "--", prefix).stdout.split()


def head_text(path: str) -> str | None:
    file = ROOT / path
    return file.read_text(encoding="utf-8") if file.is_file() else None


def changed_paths(commit: str) -> list[str]:
    """Изменения ветки, включая ещё не закоммиченные и новые файлы."""
    changed = git("diff", "--name-only", commit).stdout.split()
    changed += git("ls-files", "--others", "--exclude-standard").stdout.split()
    return sorted(set(changed))


# ---------------------------------------------------------------------------
# 1–2. Pipeline и совпадение с локальными проверками
# ---------------------------------------------------------------------------
def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in job.get("steps") or [] if isinstance(step, dict)]


def _run_blocks(document: dict[str, Any]) -> list[tuple[str, str]]:
    """(где, текст run) для заданий workflow и шагов составных actions."""
    blocks = []
    for name, job in (document.get("jobs") or {}).items():
        for number, step in enumerate(_steps(job), 1):
            if "run" in step:
                blocks.append((f"{name}, шаг {step.get('name', number)}", str(step["run"])))
    for number, step in enumerate((document.get("runs") or {}).get("steps") or [], 1):
        if isinstance(step, dict) and "run" in step:
            blocks.append((f"шаг {step.get('name', number)}", str(step["run"])))
    return blocks


def _recipes() -> dict[str, list[str]]:
    recipes: dict[str, list[str]] = {}
    current = None
    for line in (ROOT / "Makefile").read_text(encoding="utf-8").splitlines():
        target = re.match(r"^([a-z_-]+):", line)
        if target:
            current = target.group(1)
            recipes[current] = []
        elif line.startswith("\t") and current:
            recipes[current].append(line.strip())
    return recipes


def local_steps() -> dict[str, list[str]]:
    """Шаги make verify и make container-check в порядке выполнения."""
    recipes = _recipes()
    return {
        target: re.findall(r"\$\(MAKE\) --no-print-directory ([a-z_-]+)", " ".join(recipes[target]))
        for target in ("verify", "container-check")
        if target in recipes
    }


def compose_db_image() -> str | None:
    path = ROOT / "compose.yaml"
    if not path.is_file():
        return None
    services = load_yaml(path).get("services") or {}
    image = (services.get("db") or {}).get("image")
    return str(image) if image else None


def check_triggers(workflow: dict[str, Any]) -> None:
    # PyYAML читает ключ on как логическое True (YAML 1.1)
    triggers = workflow.get("on", workflow.get(True)) or {}
    if isinstance(triggers, str | list):
        triggers = {name: None for name in ([triggers] if isinstance(triggers, str) else triggers)}
    for event in ("push", "pull_request", "workflow_dispatch"):
        if event not in triggers:
            problem(WORKFLOW, f"pipeline не запускается по событию {event}")
    pull_request = triggers.get("pull_request") or {}
    branches = pull_request.get("branches") if isinstance(pull_request, dict) else None
    if branches and "master" not in branches:
        problem(WORKFLOW, "запросы на слияние в master не проверяются")
    push = triggers.get("push") or {}
    if isinstance(push, dict) and (push.get("branches-ignore") or push.get("paths-ignore")):
        problem(WORKFLOW, "часть отправок исключена из проверки (branches-ignore/paths-ignore)")
    if isinstance(pull_request, dict) and pull_request.get("paths-ignore"):
        problem(WORKFLOW, "часть изменений исключена из проверки (paths-ignore)")


def check_jobs(workflow: dict[str, Any]) -> None:
    jobs = workflow.get("jobs") or {}
    for name in (*CHECK_JOBS, PUBLISH_JOB):
        if name not in jobs:
            problem(WORKFLOW, f"нет обязательного задания {name}")
    for name, job in jobs.items():
        where = f"{WORKFLOW}: {name}"
        if "continue-on-error" in job:
            problem(where, "continue-on-error: ошибка задания не остановит pipeline")
        condition = str(job.get("if", ""))
        if STATUS_FUNCTION.search(condition):
            problem(where, f"условие задания отменяет зависимость от проверок: {condition}")
        for step in _steps(job):
            label = f"{where}, шаг {step.get('name', step.get('uses', step.get('run', '')))}"
            if "continue-on-error" in step:
                problem(label, "continue-on-error: ошибка шага будет проигнорирована")
            uses = str(step.get("uses", ""))
            step_if = str(step.get("if", ""))
            if STATUS_FUNCTION.search(step_if) and not uses.startswith("actions/upload-artifact@"):
                problem(
                    label, f"always()/failure() допустимы только для сохранения отчётов: {step_if}"
                )
            if uses and not uses.startswith("./") and not PINNED_ACTION.match(uses):
                problem(label, f"action «{uses}» не закреплён полным хешем коммита")
        for service, settings in (job.get("services") or {}).items():
            image = (settings or {}).get("image")
            expected = compose_db_image()
            if expected and image != expected:
                problem(f"{where}: {service}", f"образ {image}, а в compose.yaml — {expected}")
    publish = jobs.get(PUBLISH_JOB) or {}
    needs = publish.get("needs") or []
    needs = [needs] if isinstance(needs, str) else needs
    for name in CHECK_JOBS:
        if name not in needs:
            problem(f"{WORKFLOW}: {PUBLISH_JOB}", f"публикация не ждёт задания {name}")
    where = f"{WORKFLOW}: {PUBLISH_JOB}"
    environment = publish.get("environment")
    if isinstance(environment, dict):
        environment = environment.get("name")
    if environment != PUBLISH_ENVIRONMENT:
        problem(where, f"публикация — только в окружении {PUBLISH_ENVIRONMENT}")
    if "pull_request" in str(publish.get("if", "")):
        problem(where, "образ публикуется из непроверенного запроса на слияние")


def check_workflow() -> None:
    path = ROOT / WORKFLOW
    if not path.is_file():
        problem(WORKFLOW, "файл pipeline не найден")
        return
    workflow = load_yaml(path)
    check_triggers(workflow)
    check_jobs(workflow)
    permissions = workflow.get("permissions")
    if not isinstance(permissions, dict) or permissions.get("contents") != "read":
        problem(WORKFLOW, "нужно permissions: contents: read (минимальные права токена)")

    documents = [(WORKFLOW, workflow)]
    documents += [
        (str(action.relative_to(ROOT)), load_yaml(action))
        for action in sorted((ROOT / LOCAL_ACTIONS).glob("*/action.y*ml"))
    ]
    called: dict[str, str] = {}
    for source, document in documents:
        for where, run in _run_blocks(document):
            for pattern, text in FORBIDDEN_RUN.items():
                if re.search(pattern, run, re.MULTILINE):
                    problem(f"{source}: {where}", text)
            for target in MAKE_CALL.findall(run):
                called.setdefault(target, f"{source}: {where}")

    steps = local_steps()
    if "verify" not in steps or "container-check" not in steps:
        problem("Makefile", "нет целей verify и container-check")
        return
    expected = set(steps["verify"]) | set(steps["container-check"])
    for target in [*steps["verify"], *steps["container-check"]]:
        if target not in called:
            problem(WORKFLOW, f"локальная проверка make {target} не выполняется в CI")
    for target, where in sorted(called.items()):
        if target not in expected | HELPER_TARGETS:
            problem(where, f"CI запускает make {target} — такой проверки нет в make verify")
    notes.append(
        f"Совпадение с локальными проверками: в CI выполняются все {len(expected)} шагов "
        "make verify и make container-check"
    )


# ---------------------------------------------------------------------------
# 3–4. CODEOWNERS и файлы, которым не место в репозитории
# ---------------------------------------------------------------------------
def check_codeowners() -> None:
    path = ROOT / CODEOWNERS
    if not path.is_file():
        problem(
            CODEOWNERS, "файл не найден: изменения критических файлов никто не обязан проверять"
        )
        return
    owners: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("#", 1)[0].split()
        if parts:
            owners[parts[0]] = parts[1:]
    for pattern, names in owners.items():
        if not names:
            problem(CODEOWNERS, f"«{pattern}» без владельцев снимает защиту с этих файлов")
    if any("@TEAMMATE" in names for names in owners.values()):
        notes.append(f"В {CODEOWNERS} осталась заглушка @TEAMMATE — замените логином участника")
    for critical in CRITICAL:
        names = owners.get(critical)
        if not names:
            problem(CODEOWNERS, f"нет владельцев для {critical}")
        elif len(names) < 2:
            notes.append(
                f"У {critical} один владелец ({names[0]}): свой запрос на слияние он одобрить "
                "не может — добавьте второго участника команды"
            )


def check_tracked_files() -> None:
    for path in git("ls-files").stdout.splitlines():
        if FORBIDDEN_FILE.search(path):
            problem(path, "файл с секретами или данными не должен быть в репозитории")


# ---------------------------------------------------------------------------
# 5–7. Сравнение с основной веткой
# ---------------------------------------------------------------------------
def _test_facts(text: str) -> tuple[set[str], int]:
    """Имена тестовых функций и число проверок (assert и pytest.raises) в файле."""
    tree = ast.parse(text)
    names = set()
    checks = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            names.add(node.name)
        elif isinstance(node, ast.Assert) or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("raises", "warns")
        ):
            checks += 1
    return names, checks


def compare_tests(commit: str) -> None:
    base_files = [path for path in base_paths(commit, "tests") if TEST_FILE.match(path)]
    head_files = sorted(
        str(path.relative_to(ROOT)).replace(os.sep, "/")
        for path in (ROOT / "tests").rglob("test_*.py")
    )
    base_total = head_total = base_checks = head_checks = 0
    for path in head_files:
        names, checks = _test_facts(head_text(path) or "")
        head_total += len(names)
        head_checks += checks
    for path in base_files:
        names, checks = _test_facts(base_text(commit, path) or "")
        base_total += len(names)
        base_checks += checks
        if path not in head_files:
            weakening(path, f"удалён файл тестов ({len(names)} тестов)")
            continue
        head_names, _ = _test_facts(head_text(path) or "")
        for name in sorted(names - head_names):
            weakening(path, f"удалён тест {name}")
    if head_checks < base_checks:
        weakening(
            "tests/", f"проверок в тестах стало меньше: было {base_checks}, стало {head_checks}"
        )
    notes.append(
        f"Тесты: было {base_total} функций и {base_checks} проверок, "
        f"стало {head_total} и {head_checks}"
    )


def _constants(text: str | None) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if not text:
        return values
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.isupper():
                try:
                    values[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    continue
    return values


def compare_policy(commit: str) -> None:
    base = _constants(base_text(commit, "tools/policy.py"))
    head = _constants(head_text("tools/policy.py"))
    for name in ("COVERAGE_FLOOR", "MUTATION_FLOOR"):
        if name in base and float(head.get(name, 0)) < float(base[name]):
            weakening("tools/policy.py", f"{name} снижен: {base[name]} → {head.get(name)}")
    if "MUTATION_TARGET" in base and head.get("MUTATION_TARGET") != base["MUTATION_TARGET"]:
        weakening("tools/policy.py", "изменён охват мутационной проверки (MUTATION_TARGET)")
    for name in ("VERIFY_STEPS", "CONTAINER_STEPS", "WEAKENING"):
        removed = set(base.get(name, ())) - set(head.get(name, ()))
        if removed:
            weakening("tools/policy.py", f"из {name} убрано: {', '.join(sorted(removed))}")


def _get(data: dict[str, Any], dotted: str, default: Any = None) -> Any:
    for key in dotted.split("."):
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return data


def _as_set(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {f"{key}:{item}" for key, items in value.items() for item in items}
    if isinstance(value, str):
        return set(value.split())
    return {str(item) for item in value or ()}


def compare_pyproject(commit: str) -> None:
    text = base_text(commit, "pyproject.toml")
    current = head_text("pyproject.toml")
    if not text or not current:
        return
    base, head = tomllib.loads(text), tomllib.loads(current)
    for key in ("tool.coverage.report.fail_under", "tool.warehouse.mutation.min_score"):
        old, new = _get(base, key), _get(head, key)
        if old is not None and (new is None or float(new) < float(old)):
            weakening("pyproject.toml", f"{key} снижен: {old} → {new}")
    fewer = {
        "tool.ruff.lint.select": "правила линтера",
        "tool.pytest.ini_options.addopts": "обязательные ключи pytest",
    }
    for key, title in fewer.items():
        removed = _as_set(_get(base, key)) - _as_set(_get(head, key))
        if removed:
            weakening("pyproject.toml", f"убраны {title}: {', '.join(sorted(removed))}")
    more = {
        "tool.ruff.lint.ignore": "исключения линтера",
        "tool.ruff.lint.per-file-ignores": "исключения линтера для файлов",
        "tool.bandit.skips": "исключения bandit",
        "tool.bandit.exclude_dirs": "каталоги без анализа bandit",
        "tool.coverage.run.omit": "файлы без учёта покрытия",
        "tool.coverage.report.exclude_also": "строки без учёта покрытия",
        "tool.pytest.ini_options.filterwarnings": "подавленные предупреждения",
    }
    for key, title in more.items():
        added = _as_set(_get(head, key)) - _as_set(_get(base, key))
        if added:
            weakening("pyproject.toml", f"добавлены {title}: {', '.join(sorted(added))}")


def _entries(text: str | None) -> set[str]:
    return {
        line.split("#", 1)[0].strip()
        for line in (text or "").splitlines()
        if line.split("#", 1)[0].strip()
    }


def compare_scanners(commit: str) -> None:
    added = _entries(head_text(".trivyignore")) - _entries(base_text(commit, ".trivyignore"))
    if added:
        weakening(".trivyignore", f"добавлены исключения уязвимостей: {', '.join(sorted(added))}")
    base_config, head_config = base_text(commit, ".gitleaks.toml"), head_text(".gitleaks.toml")
    if base_config and head_config:
        base_rules = {rule["id"] for rule in tomllib.loads(base_config).get("rules", [])}
        head_rules = {rule["id"] for rule in tomllib.loads(head_config).get("rules", [])}
        if base_rules - head_rules:
            weakening(
                ".gitleaks.toml", f"убраны правила: {', '.join(sorted(base_rules - head_rules))}"
            )


def check_gitleaks_config() -> None:
    text = head_text(".gitleaks.toml")
    if text is None:
        problem(".gitleaks.toml", "файл не найден")
        return
    config = tomllib.loads(text)
    if _get(config, "extend.useDefault") is not True:
        problem(".gitleaks.toml", "стандартные правила gitleaks должны быть включены (useDefault)")
    if "allowlist" in config or "allowlists" in config:
        problem(".gitleaks.toml", "общие исключения запрещены — только «gitleaks:allow — причина»")
    for rule in config.get("rules", []):
        if "allowlist" in rule or "allowlists" in rule:
            problem(".gitleaks.toml", f"исключения в правиле {rule.get('id')} запрещены")


def critical_changes(changed: list[str]) -> list[str]:
    return [
        path
        for path in changed
        if any(
            f"/{path}" == pattern or (pattern.endswith("/") and f"/{path}".startswith(pattern))
            for pattern in CRITICAL
        )
    ]


# ---------------------------------------------------------------------------
# Итог
# ---------------------------------------------------------------------------
def approved() -> bool:
    if os.environ.get("GUARD_APPROVED") == "1":
        return True
    event = os.environ.get("GITHUB_EVENT_PATH")
    if not event or not Path(event).is_file():
        return False
    payload = json.loads(Path(event).read_text(encoding="utf-8"))
    labels = (payload.get("pull_request") or {}).get("labels") or []
    return any(label.get("name") == APPROVAL_LABEL for label in labels)


def report(critical: list[str], is_approved: bool, base: str) -> str:
    lines = ["## Охрана приёмки (make guard)", ""]
    if problems:
        lines += ["### Нарушены правила", "", *[f"- ❌ {item}" for item in problems], ""]
    if weakenings:
        title = (
            f"### Ослабление проверок — согласовано меткой «{APPROVAL_LABEL}»"
            if is_approved
            else "### Ослабление проверок — не принимается"
        )
        mark = "⚠️" if is_approved else "❌"
        lines += [title, "", *[f"- {mark} {item}" for item in weakenings], ""]
        if not is_approved:
            lines += [
                f"Если ослабление оправдано, поставьте на запрос на слияние метку "
                f"`{APPROVAL_LABEL}` и объясните причину; слияние всё равно потребует "
                "одобрения владельцев из CODEOWNERS.",
                "",
            ]
    if critical:
        lines += [
            f"### Изменены критические файлы приёмки (относительно {base})",
            "",
            "Слияние — только после одобрения владельцев из `.github/CODEOWNERS`.",
            "",
            *[f"- `{path}`" for path in critical],
            "",
        ]
    lines += ["### Сведения", "", *[f"- {item}" for item in notes], ""]
    if not problems and (not weakenings or is_approved):
        lines.append("**Итог: пройдено.**")
    else:
        lines.append("**Итог: не пройдено.**")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Охрана приёмки (make guard)")
    parser.add_argument("--base", default="origin/master", help="основная ветка")
    parser.add_argument("--report", type=Path, help="отчёт в формате Markdown")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="корень проекта")
    args = parser.parse_args(argv)
    global ROOT
    ROOT = args.root.resolve()
    for found in (problems, weakenings, notes):
        found.clear()

    check_workflow()
    check_codeowners()
    check_gitleaks_config()
    critical: list[str] = []
    if in_repository():
        check_tracked_files()
        commit = merge_base(args.base)
        if commit:
            compare_tests(commit)
            compare_policy(commit)
            compare_pyproject(commit)
            compare_scanners(commit)
            critical = critical_changes(changed_paths(commit))
        elif os.environ.get("CI") == "true":
            problem(args.base, "нет основной ветки для сравнения — нужен fetch-depth: 0")
        else:
            notes.append(f"Ветки {args.base} нет — сравнение пропущено (git fetch origin)")
    elif os.environ.get("CI") == "true":
        problem("git", "каталог не является репозиторием git")
    else:
        notes.append("Каталог не является репозиторием git — сравнение пропущено")

    is_approved = approved()
    text = report(critical, is_approved, args.base)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as file:
            file.write(text)
    return 1 if problems or (weakenings and not is_approved) else 0


if __name__ == "__main__":
    sys.exit(main())
