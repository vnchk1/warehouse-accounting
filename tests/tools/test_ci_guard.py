"""Охрана приёмки (tools/ci_guard.py): попытка удалить тест, отключить проверку или
ослабить правило не может пройти незаметно (ЛР5, п. 15).

Каждый тест создаёт временный репозиторий git с копией pipeline, Makefile, настроек
и тестов проекта: ветка master — как сейчас, затем в рабочей ветке вносится одно
«вредное» изменение, и make guard обязан его найти.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import ROOT

#: Что копируется во временный репозиторий: всё, что читает ci_guard.py
FILES = (
    ".github",
    "tests",
    "Makefile",
    "compose.yaml",
    "pyproject.toml",
    "tools/policy.py",
    ".gitleaks.toml",
    ".trivyignore",
)
WORKFLOW = ".github/workflows/ci.yml"


def load_guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_guard", ROOT / "tools" / "ci_guard.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard_module = load_guard()


def git(repo: Path, *args: str) -> str:
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.com",
    )
    result = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in FILES:
        source, target = ROOT / name, tmp_path / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    git(tmp_path, "init", "-q", "-b", "master")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "master")
    git(tmp_path, "update-ref", "refs/remotes/origin/master", "HEAD")
    git(tmp_path, "switch", "-q", "-c", "feature/1-change")
    # прогон тестов в CI не должен влиять на проверку временного репозитория
    for name in ("CI", "GITHUB_EVENT_PATH", "GITHUB_STEP_SUMMARY", "GUARD_APPROVED"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def run_guard(repo: Path) -> tuple[int, str]:
    code = guard_module.main(["--root", str(repo), "--base", "origin/master"])
    found = "\n".join([*guard_module.problems, *guard_module.weakenings])
    return code, found


def edit(repo: Path, path: str, old: str, new: str, *, regex: bool = False) -> None:
    file = repo / path
    text = file.read_text(encoding="utf-8")
    changed = (
        re.sub(old, new, text, count=1, flags=re.MULTILINE) if regex else text.replace(old, new, 1)
    )
    assert changed != text, f"в {path} не найдено: {old}"
    file.write_text(changed, encoding="utf-8")


def test_unchanged_project_passes(repo: Path) -> None:
    code, found = run_guard(repo)
    assert (code, found) == (0, "")


# ---------------------------------------------------------------------------
# Удалить тест или ослабить его
# ---------------------------------------------------------------------------
def test_deleted_test_file(repo: Path) -> None:
    (repo / "tests/unit/test_rules.py").unlink()
    code, found = run_guard(repo)
    assert code == 1
    assert "tests/unit/test_rules.py: удалён файл тестов" in found


def test_disabled_test_function(repo: Path) -> None:
    # переименованная функция не запускается pytest — тест «выключен», хотя код остался
    edit(
        repo, "tests/unit/test_rules.py", "def test_bp04_valid_date(", "def check_bp04_valid_date("
    )
    code, found = run_guard(repo)
    assert code == 1
    assert "удалён тест test_bp04_valid_date" in found


def test_removed_assertion(repo: Path) -> None:
    edit(repo, "tests/unit/test_rules.py", r"^(\s+)assert ", r"\1_ = ", regex=True)
    code, found = run_guard(repo)
    assert code == 1
    assert "проверок в тестах стало меньше" in found


# ---------------------------------------------------------------------------
# Ослабить правило
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("path", "old", "new", "message"),
    [
        ("pyproject.toml", "fail_under = 98", "fail_under = 90", "fail_under снижен"),
        ("pyproject.toml", "min_score = 100", "min_score = 90", "min_score снижен"),
        ("pyproject.toml", "skips = []", 'skips = ["B603"]  # мешает', "исключения bandit"),
        ("pyproject.toml", '"SIM", ', "", "убраны правила линтера: SIM"),
        ("tools/policy.py", "COVERAGE_FLOOR = 98", "COVERAGE_FLOOR = 80", "COVERAGE_FLOOR"),
        ("tools/policy.py", '    "mutation",\n', "", "из VERIFY_STEPS убрано: mutation"),
        (".trivyignore", "\n", "\nCVE-2026-0001\n", "исключения уязвимостей"),
    ],
)
def test_weakened_rule(repo: Path, path: str, old: str, new: str, message: str) -> None:
    edit(repo, path, old, new)
    code, found = run_guard(repo)
    assert code == 1
    assert message in found


# ---------------------------------------------------------------------------
# Отключить проверку в pipeline
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("run: make mutation", "run: echo mutation", "make mutation не выполняется в CI"),
        ("run: make test", "run: make test || true", "ошибка команды подавлена"),
        (
            "      - name: make secrets\n",
            "      - name: make secrets\n        continue-on-error: true\n",
            "continue-on-error",
        ),
        (
            "needs: [guard, style, tests, migrations,",
            "needs: [guard, style, migrations,",
            "публикация не ждёт задания tests",
        ),
        (
            "      (github.event_name == 'push'",
            "      always() && (github.event_name == 'push'",
            "условие задания отменяет зависимость от проверок",
        ),
        (
            "      || github.event_name == 'workflow_dispatch'",
            "      || github.event_name == 'pull_request'",
            "из непроверенного запроса на слияние",
        ),
        ("    branches: [master]\n", "    branches: [develop]\n", "в master не проверяются"),
        (
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/checkout@v7",
            "не закреплён полным хешем",
        ),
        ("run: make policy", "run: make policy && make test-unit", "make test-unit"),
        ("image: postgres:16.15-trixie", "image: postgres:17.6-trixie", "в compose.yaml"),
        ("  contents: read\n", "  contents: write\n", "permissions"),
    ],
)
def test_disabled_check_in_pipeline(repo: Path, old: str, new: str, message: str) -> None:
    edit(repo, WORKFLOW, old, new)
    code, found = run_guard(repo)
    assert code == 1
    assert message in found


def test_removed_job(repo: Path) -> None:
    text = (repo / WORKFLOW).read_text(encoding="utf-8")
    start = text.index("  backup:\n")
    end = text.index("  security:\n")
    (repo / WORKFLOW).write_text(text[:start] + text[end:], encoding="utf-8")
    code, found = run_guard(repo)
    assert code == 1
    assert "нет обязательного задания backup" in found
    assert "make backup-check не выполняется в CI" in found


# ---------------------------------------------------------------------------
# Защита критических файлов и секреты
# ---------------------------------------------------------------------------
def test_critical_file_without_owner(repo: Path) -> None:
    edit(repo, ".github/CODEOWNERS", r"^/tests/.*\n", "", regex=True)
    code, found = run_guard(repo)
    assert code == 1
    assert "нет владельцев для /tests/" in found


def test_owner_removed_by_later_rule(repo: Path) -> None:
    path = repo / ".github/CODEOWNERS"
    path.write_text(path.read_text(encoding="utf-8") + "/tests/unit/\n", encoding="utf-8")
    code, found = run_guard(repo)
    assert code == 1
    assert "без владельцев" in found


def test_committed_env_file(repo: Path) -> None:
    (repo / ".env").write_text("SECRET_KEY=x\n", encoding="utf-8")
    git(repo, "add", "-f", ".env")
    code, found = run_guard(repo)
    assert code == 1
    assert ".env: файл с секретами" in found


def test_gitleaks_allowlist_forbidden(repo: Path) -> None:
    path = repo / ".gitleaks.toml"
    path.write_text(path.read_text() + '\n[allowlist]\npaths = ["app/"]\n', encoding="utf-8")
    code, found = run_guard(repo)
    assert code == 1
    assert "общие исключения запрещены" in found


def test_critical_changes_are_listed(repo: Path) -> None:
    edit(repo, "Makefile", "# Команды проекта.", "# Команды проекта (правка).")
    edit(repo, "compose.yaml", "name: warehouse", "name: warehouse  # правка")
    assert guard_module.main(["--root", str(repo), "--base", "origin/master"]) == 0
    report = guard_module.report(
        guard_module.critical_changes(["Makefile", "compose.yaml", "app/web.py"]), False, "x"
    )
    assert "`Makefile`" in report and "`compose.yaml`" in report
    assert "app/web.py" not in report


# ---------------------------------------------------------------------------
# Согласованное ослабление: видно в отчёте, но не блокирует
# ---------------------------------------------------------------------------
def test_weakening_approved_by_label(
    repo: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    edit(repo, "pyproject.toml", "fail_under = 98", "fail_under = 90")
    assert run_guard(repo)[0] == 1
    event = tmp_path_factory.mktemp("event") / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"labels": [{"name": "acceptance-change"}]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    code, found = run_guard(repo)
    assert code == 0
    assert "fail_under снижен" in found


def test_approval_does_not_cover_pipeline_rules(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GUARD_APPROVED", "1")
    edit(repo, WORKFLOW, "run: make test", "run: make test || true")
    assert run_guard(repo)[0] == 1
