"""Правила проекта, которые проверяются автоматически (make policy, первый шаг make verify).

1. Версии зависимостей зафиксированы точно (==) и установлены именно они.
2. Тесты не пропускаются: skip, skipif, xfail, pytest.skip() и подобное запрещены.
3. Пороги не снижаются: покрытие и мутационная проверка не ниже нижних границ ниже.
4. Набор make verify полный, а его команды не ослаблены ключами вроде -k,
   --deselect, --ignore, --exit-zero.
5. Исключения анализаторов только с обоснованием:
   - `# noqa: КОД — причина` (конкретное правило и пояснение);
   - `# nosec BXXX` с пояснением: комментарий «# Безопасно: …» строкой выше
     или `# причина` в той же строке;
   - каждое исключение в настройках pyproject.toml (ignore, per-file-ignores,
     skips, exclude_dirs, exclude_also, omit, filterwarnings) с комментарием.

Нижние границы порогов ниже меняются только отдельной задачей с обоснованием
и проверкой двумя участниками (CONTRIBUTING.md).
"""

from __future__ import annotations

import ast
import re
import sys
import tokenize
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: Нижние границы порогов. Снижать запрещено.
COVERAGE_FLOOR = 98
MUTATION_FLOOR = 100
MUTATION_TARGET = "app/rules.py"

CODE_DIRS = ("app", "tests", "tools", "migrations")
VERIFY_STEPS = ("policy", "format-check", "lint-check", "sast", "test", "mutation")
#: Ключи, ослабляющие проверки в командах make verify
WEAKENING = (
    "--deselect",
    "--ignore",
    " -k ",
    "--cov-fail-under",
    "--exit-zero",
    "--no-cov",
    "-p no:",
    "--skip",
    "--extend-ignore",
    "--lf",
)
SKIP_CALLS = {"skip", "xfail", "importorskip"}
SKIP_MARKS = {"skip", "skipif", "xfail"}
#: Разделы pyproject.toml, где каждое исключение требует комментария-обоснования
GUARDED = {
    "tool.ruff.lint": {"ignore", "extend-ignore"},
    "tool.ruff.lint.per-file-ignores": {"*"},
    "tool.bandit": {"skips", "exclude_dirs"},
    "tool.coverage.run": {"omit"},
    "tool.coverage.report": {"exclude_also", "exclude_lines", "omit"},
    "tool.pytest.ini_options": {"filterwarnings"},
}
PIN = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)(\[[A-Za-z0-9_,-]+\])?==(?P<version>[0-9][^;\s]*)$")
NOQA = re.compile(r"#\s*noqa\b(?P<rest>.*)")
NOQA_OK = re.compile(r":\s*[A-Z]+[0-9]+(\s*,\s*[A-Z]+[0-9]+)*\s+(—|--|#)\s*\S")
NOSEC = re.compile(r"#\s*nosec\b(?P<rest>.*)")
NOSEC_OK = re.compile(r"^\s+B[0-9]{3}(\s*,\s*B[0-9]{3})*(\s+#\s*\S.*)?\s*$")

problems: list[str] = []


def problem(where: str, text: str) -> None:
    problems.append(f"{where}: {text}")


def python_files() -> list[Path]:
    return sorted(path for name in CODE_DIRS for path in (ROOT / name).rglob("*.py"))


# ---------------------------------------------------------------------------
# 1. Зафиксированные версии
# ---------------------------------------------------------------------------
def check_pins(project: dict[str, Any]) -> None:
    requirements = list(project["dependencies"])
    for group in project.get("optional-dependencies", {}).values():
        requirements += group
    for requirement in requirements:
        match = PIN.match(requirement.replace(" ", ""))
        if not match:
            problem("pyproject.toml", f"версия «{requirement}» не зафиксирована точно (==)")
            continue
        name, version = match["name"], match["version"]
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            problem("окружение", f"пакет {name} не установлен — выполните make install")
            continue
        if installed != version:
            problem(
                "окружение",
                f"{name} {installed} вместо зафиксированной {version} — выполните make install",
            )


# ---------------------------------------------------------------------------
# 2. Запрет пропуска тестов
# ---------------------------------------------------------------------------
def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return node.id if isinstance(node, ast.Name) else ""


def check_no_skips() -> None:
    found: set[tuple[str, str]] = set()
    for path in sorted((ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = _dotted(node)
            parts = name.split(".")
            skipped = (
                (parts[:1] == ["pytest"] and parts[-1] in SKIP_CALLS and len(parts) == 2)
                or (parts[:2] == ["pytest", "mark"] and parts[-1] in SKIP_MARKS)
                or (parts[:1] == ["unittest"] and parts[-1].startswith("skip"))
            )
            if skipped:
                found.add((f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 0)}", name))
    for where, name in sorted(found):
        problem(where, f"пропуск теста запрещён ({name})")


# ---------------------------------------------------------------------------
# 3–4. Пороги и полнота make verify
# ---------------------------------------------------------------------------
def check_thresholds(settings: dict[str, Any]) -> None:
    tool = settings["tool"]
    coverage = float(tool["coverage"]["report"].get("fail_under", 0))
    if coverage < COVERAGE_FLOOR:
        problem("pyproject.toml", f"порог покрытия {coverage} ниже {COVERAGE_FLOOR}")
    mutation = tool["warehouse"]["mutation"]
    if float(mutation["min_score"]) < MUTATION_FLOOR:
        problem("pyproject.toml", f"порог мутационной проверки ниже {MUTATION_FLOOR}")
    if mutation["target"] != MUTATION_TARGET:
        problem("pyproject.toml", f"мутационная проверка должна охватывать {MUTATION_TARGET}")
    pytest_options = tool["pytest"]["ini_options"]
    addopts = f" {pytest_options.get('addopts', '')} "
    for flag in ("--strict-markers", "--strict-config"):
        if flag not in addopts:
            problem("pyproject.toml", f"в addopts pytest нет обязательного {flag}")
    for flag in WEAKENING:
        if flag in addopts:
            problem("pyproject.toml", f"addopts pytest ослабляет проверки: {flag.strip()}")
    if pytest_options.get("xfail_strict") is not True:
        problem("pyproject.toml", "нужно xfail_strict = true")


def _recipes(makefile: str) -> dict[str, list[str]]:
    recipes: dict[str, list[str]] = {}
    current = None
    for line in makefile.splitlines():
        target = re.match(r"^([a-z_-]+):", line)
        if target:
            current = target.group(1)
            recipes[current] = []
        elif line.startswith("\t") and current:
            recipes[current].append(line.strip())
    return recipes


def check_makefile() -> None:
    recipes = _recipes((ROOT / "Makefile").read_text(encoding="utf-8"))
    verify = " ".join(recipes.get("verify", []))
    for step in VERIFY_STEPS:
        if f"$(MAKE) --no-print-directory {step}" not in verify:
            problem("Makefile", f"make verify не запускает шаг {step}")
    for step in VERIFY_STEPS:
        lines = recipes.get(step, [])
        checking = [line for line in lines if "--exit-zero" not in line or "bandit" not in line]
        for line in checking:
            for flag in WEAKENING:
                if flag in f" {line} ":
                    problem("Makefile", f"шаг {step} ослаблен ключом {flag.strip()}: {line}")
    if not any("bandit" in line and "--exit-zero" not in line for line in recipes.get("sast", [])):
        problem("Makefile", "шаг sast должен завершаться ошибкой при найденных уязвимостях")


# ---------------------------------------------------------------------------
# 5. Исключения анализаторов — только с обоснованием
# ---------------------------------------------------------------------------
def _comments(path: Path) -> list[tuple[int, str]]:
    """Комментарии файла (строки документации и тексты в кавычках не учитываются)."""
    with path.open("rb") as file:
        return [
            (token.start[0], token.string)
            for token in tokenize.tokenize(file.readline)
            if token.type == tokenize.COMMENT
        ]


def check_inline_exceptions() -> None:
    for path in python_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, comment in _comments(path):
            where = f"{path.relative_to(ROOT)}:{number}"
            noqa = NOQA.search(comment)
            if noqa and not NOQA_OK.match(noqa["rest"]):
                problem(where, "нужно «# noqa: КОД — причина»")
            nosec = NOSEC.search(comment)
            if nosec and not lines[number - 1].strip().startswith("#"):
                previous = lines[number - 2].strip() if number > 1 else ""
                justified = "#" in nosec["rest"] or previous.startswith("# Безопасно:")
                if not NOSEC_OK.match(nosec["rest"]) or not justified:
                    problem(where, "нужно «# nosec BXXX» и пояснение «# Безопасно: …» строкой выше")


def check_config_exceptions() -> None:
    section = ""
    key = None  # имя ключа открытого многострочного списка
    commented = False  # над строкой есть комментарий-обоснование
    for number, raw in enumerate((ROOT / "pyproject.toml").read_text().splitlines(), 1):
        line = raw.strip()
        header = re.match(r"^\[([^\]]+)\]$", line)
        if header:
            section, key, commented = header.group(1), None, False
            continue
        if line.startswith("#"):
            commented = True
            continue
        if not line:
            commented = False
            continue
        guarded = GUARDED.get(section, set())
        assignment = re.match(r'^"?([A-Za-z0-9_.*/-]+)"?\s*=\s*(.*)$', line)
        if key is None and assignment:
            name, value = assignment.groups()
            if "*" in guarded or name in guarded:
                # комментарий над группой строк обосновывает всю группу
                entries = re.findall(r'"[^"]*"|\'[^\']*\'', value.split("#", 1)[0])
                if (entries or "*" in guarded) and "#" not in value and not commented:
                    problem(f"pyproject.toml:{number}", f"исключение «{name}» без обоснования")
                if value.startswith("[") and "]" not in value:
                    key = name
                continue
            commented = False
            continue
        if key is not None:
            if line.startswith("]"):
                key, commented = None, False
            elif "#" not in line and not commented:
                problem(f"pyproject.toml:{number}", f"исключение в «{key}» без обоснования")


def main() -> int:
    settings = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    check_pins(settings["project"])
    check_no_skips()
    check_thresholds(settings)
    check_makefile()
    check_inline_exceptions()
    check_config_exceptions()
    if problems:
        print("Нарушены правила проекта (CONTRIBUTING.md):")
        print("\n".join(f"  - {item}" for item in problems))
        return 1
    print(
        "Правила проекта соблюдены: версии зафиксированы, пропусков тестов нет, "
        f"пороги не ниже {COVERAGE_FLOOR} % (покрытие) и {MUTATION_FLOOR} % (мутации), "
        "исключения анализаторов обоснованы"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
