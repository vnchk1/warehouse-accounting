"""Мутационная проверка тестов (make mutation).

Программа по одному вносит в проверяемый файл (по умолчанию app/rules.py)
небольшие изменения логики — «мутации» — и для каждой запускает тесты:

    сравнение      <  ->  <=,   >= ->  >,   == -> !=,   in -> not in, ...
    логика         and -> or,   or -> and,  not x -> x,  if x -> if not x
    числа          10 -> 11 и 10 -> 9
    арифметика     + -> -,  * -> /,  % -> //
    True / False   True -> False

Если тесты после изменения не упали, мутация «выжила»: изменение логики
осталось незамеченным, значит, тестов не хватает. Доля обнаруженных мутаций
должна быть не ниже min_score из раздела [tool.warehouse.mutation] файла
pyproject.toml. Так проверяется требование «преподаватель изменяет одно
условие в основной логике, и тесты должны обнаружить изменение».

Не изменяются: тексты сообщений, аннотации типов, литералы словарей (это
справочные таблицы — данные, а не логика) и логические флаги в именованных
аргументах вызовов (например, strict=True).

Исходный файл не меняется: каждая мутация проверяется в копии проекта во
временном каталоге.

    python tools/mutation.py                                 # по pyproject.toml
    python tools/mutation.py --target app/services.py --tests tests --jobs 1
"""

from __future__ import annotations

import argparse
import ast
import os
import shutil
import subprocess  # nosec B404  # запуск pytest для каждой мутации, без оболочки
import sys
import tempfile
import time
import tomllib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from queue import Queue

ROOT = Path(__file__).resolve().parents[1]
#: Что копируется во временный каталог для прогона тестов
COPY = ["app", "tests", "migrations", "alembic.ini", "pyproject.toml", ".env"]
TIMEOUT = 300  # секунд на один прогон; зависание считается обнаруженной мутацией

COMPARE = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}
BINARY = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.Mod: ast.FloorDiv,
}
SYMBOL = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.In: "in",
    ast.NotIn: "not in",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.Mod: "%",
    ast.FloorDiv: "//",
    ast.And: "and",
    ast.Or: "or",
}
#: Поля узлов, внутрь которых мутации не вносятся (аннотации типов)
SKIP_FIELDS = {"annotation", "returns"}


@dataclass(frozen=True)
class Mutant:
    number: int
    line: int
    description: str


# ---------------------------------------------------------------------------
# Поиск мест для мутаций
# ---------------------------------------------------------------------------
def _walk(node: ast.AST, skip: set[int]) -> Iterator[ast.AST]:
    """Обход дерева с пропуском аннотаций, словарей и флагов в аргументах."""
    if id(node) in skip or isinstance(node, ast.Dict):
        return
    if (
        isinstance(node, ast.keyword)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, bool)
    ):
        skip.add(id(node.value))
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return  # строка документации
    yield node
    for field, value in ast.iter_fields(node):
        if field in SKIP_FIELDS:
            continue
        for child in value if isinstance(value, list) else [value]:
            if isinstance(child, ast.AST):
                yield from _walk(child, skip)


def _candidates(tree: ast.AST) -> Iterator[tuple[ast.AST, str, object]]:
    """Все возможные мутации в постоянном порядке: (узел, вид, параметр)."""
    for node in _walk(tree, set()):
        if isinstance(node, ast.Compare):
            for index, op in enumerate(node.ops):
                if type(op) in COMPARE:
                    yield node, "compare", index
        elif isinstance(node, ast.BoolOp):
            yield node, "bool", None
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            yield node, "not", None
        elif isinstance(node, ast.BinOp) and type(node.op) in BINARY:
            yield node, "binary", None
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            yield node, "flag", None
        elif isinstance(node, ast.Constant) and isinstance(node.value, int):
            yield node, "number", 1
            yield node, "number", -1
        if isinstance(node, (ast.If, ast.IfExp, ast.While)) and isinstance(
            node.test, (ast.Name, ast.Attribute, ast.Call)
        ):
            yield node, "condition", None


def _mutate(node: ast.AST, kind: str, parameter: object) -> str:
    """Вносит мутацию в узел и возвращает её описание."""
    if kind == "compare" and isinstance(node, ast.Compare) and isinstance(parameter, int):
        old = type(node.ops[parameter])
        node.ops[parameter] = COMPARE[old]()
        return f"«{SYMBOL[old]}» заменено на «{SYMBOL[COMPARE[old]]}»"
    if kind == "bool" and isinstance(node, ast.BoolOp):
        old_op = type(node.op)
        node.op = ast.Or() if old_op is ast.And else ast.And()
        return f"«{SYMBOL[old_op]}» заменено на «{SYMBOL[type(node.op)]}»"
    if kind == "not" and isinstance(node, ast.UnaryOp):
        node.operand = ast.UnaryOp(op=ast.Not(), operand=node.operand)
        return "удалено отрицание «not»"
    if kind == "binary" and isinstance(node, ast.BinOp):
        old_bin = type(node.op)
        node.op = BINARY[old_bin]()
        return f"«{SYMBOL[old_bin]}» заменено на «{SYMBOL[BINARY[old_bin]]}»"
    if kind == "flag" and isinstance(node, ast.Constant):
        node.value = not node.value
        return f"{not node.value} заменено на {node.value}"
    if kind == "number" and isinstance(node, ast.Constant) and isinstance(parameter, int):
        old_value = node.value
        node.value = old_value + parameter
        return f"число {old_value} заменено на {node.value}"
    if kind == "condition" and isinstance(node, (ast.If, ast.IfExp, ast.While)):
        node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
        return "условие заменено противоположным"
    raise ValueError(f"неизвестная мутация {kind}")


def _nth_candidate(source: str, number: int) -> tuple[ast.Module, ast.AST, str, object]:
    tree = ast.parse(source)
    node, kind, parameter = list(_candidates(tree))[number]
    return tree, node, kind, parameter


def find_mutants(source: str) -> list[Mutant]:
    """Перечень мутаций. Каждая описывается на свежей копии дерева разбора."""
    mutants = []
    for number in range(len(list(_candidates(ast.parse(source))))):
        _tree, node, kind, parameter = _nth_candidate(source, number)
        line = getattr(node, "lineno", 0)
        mutants.append(Mutant(number, line, _mutate(node, kind, parameter)))
    return mutants


def mutated_source(source: str, mutant: Mutant) -> str:
    tree, node, kind, parameter = _nth_candidate(source, mutant.number)
    _mutate(node, kind, parameter)
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


# ---------------------------------------------------------------------------
# Прогон тестов в копиях проекта
# ---------------------------------------------------------------------------
def _copy_project(destination: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
    for name in COPY:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, destination / name, ignore=ignore)
        elif source.is_file():
            shutil.copy2(source, destination / name)


def _run_tests(workdir: Path, tests: str) -> tuple[bool, str]:
    """True — тесты прошли. Аргументы формирует программа, оболочка не используется."""
    command = [sys.executable, "-m", "pytest", tests, "-x", "-q", "-p", "no:cacheprovider"]
    try:
        result = subprocess.run(  # nosec B603  # см. строку документации
            command, cwd=workdir, capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired:
        return False, "превышено время"
    lines = (result.stdout + result.stderr).strip().splitlines()
    return result.returncode == 0, lines[-1] if lines else ""


def _check_imports(workdir: Path) -> None:
    """Тесты должны импортировать код из копии, а не из рабочего каталога."""
    probe = "import app; print(app.__file__)"
    result = subprocess.run(  # nosec B603  # аргументы — константы программы
        [sys.executable, "-c", probe], cwd=workdir, capture_output=True, text=True, check=False
    )
    if not result.stdout.strip().startswith(str(workdir)):
        raise SystemExit(f"Код импортируется не из копии проекта: {result.stdout or result.stderr}")


def main(argv: list[str] | None = None) -> int:
    settings = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    config = settings["tool"]["warehouse"]["mutation"]
    parser = argparse.ArgumentParser(description="Мутационная проверка тестов")
    parser.add_argument("--target", default=config["target"], help="изменяемый файл")
    parser.add_argument("--tests", default=config["tests"], help="какие тесты запускать")
    parser.add_argument("--jobs", type=int, default=0, help="параллельных прогонов")
    parser.add_argument("--report", type=Path, help="куда записать отчёт")
    args = parser.parse_args(argv)
    min_score = float(config["min_score"])
    # Интеграционные тесты работают с одной тестовой базой — только по очереди.
    jobs = args.jobs or (1 if "integration" in args.tests or args.tests == "tests" else 0)
    jobs = jobs or min(os.cpu_count() or 1, 8)

    source = (ROOT / args.target).read_text(encoding="utf-8")
    source_lines = source.splitlines()
    mutants = find_mutants(source)
    print(f"Мутационная проверка {args.target}: мутаций {len(mutants)}, тесты {args.tests}")

    with tempfile.TemporaryDirectory(prefix="mutation-") as temp:
        workdirs: Queue[Path] = Queue()
        for index in range(jobs):
            workdir = Path(temp) / f"w{index}"
            _copy_project(workdir)
            workdirs.put(workdir)
        first = workdirs.get()
        _check_imports(first)
        passed, summary = _run_tests(first, args.tests)
        workdirs.put(first)
        if not passed:
            print(f"Тесты не проходят и без мутаций: {summary}")
            return 1

        def check(mutant: Mutant) -> tuple[Mutant, bool, str]:
            workdir = workdirs.get()
            target = workdir / args.target
            try:
                target.write_text(mutated_source(source, mutant), encoding="utf-8")
                survived, summary = _run_tests(workdir, args.tests)
            finally:
                target.write_text(source, encoding="utf-8")
                workdirs.put(workdir)
            return mutant, survived, summary

        started = time.monotonic()
        survivors: list[Mutant] = []
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for done, (mutant, survived, _summary) in enumerate(pool.map(check, mutants), 1):
                if survived:
                    survivors.append(mutant)
                mark = "ВЫЖИЛА" if survived else "поймана"
                print(
                    f"  [{done:3}/{len(mutants)}] {mark:7} строка {mutant.line:4}: "
                    f"{mutant.description}"
                )

    killed = len(mutants) - len(survivors)
    score = 100.0 * killed / len(mutants) if mutants else 100.0
    report = [
        f"Мутационная проверка {args.target} (тесты: {args.tests})",
        f"Мутаций: {len(mutants)}, обнаружено тестами: {killed}, выжило: {len(survivors)}",
        f"Доля обнаруженных: {score:.1f} % (порог {min_score:.0f} %), "
        f"время {time.monotonic() - started:.0f} с",
    ]
    for mutant in survivors:
        code = source_lines[mutant.line - 1].strip() if mutant.line else ""
        report.append(f"  выжила: строка {mutant.line}: {mutant.description}:  {code}")
    print("\n".join(report))
    if args.report:
        args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    if score < min_score:
        print("Тесты не обнаружили часть изменений логики — добавьте проверки (см. выше).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
