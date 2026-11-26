"""Границы слоёв (ТЗ 7.1): зависимости направлены внутрь.

* Domain не знает ни о Qt, ни о subprocess, ни о других слоях;
* Application не знает о Qt, subprocess и Presentation и обращается к Infrastructure
  только за типами ошибок (``git_errors``);
* Infrastructure не знает о Qt, Application и Presentation.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import git_visualizer

SRC_DIR = Path(git_visualizer.__file__).resolve().parent.parent

MODULES = [
    "git_visualizer.domain.enums",
    "git_visualizer.domain.models",
    "git_visualizer.application.errors",
    "git_visualizer.application.ports",
    "git_visualizer.application.services.repository_service",
    "git_visualizer.application.services.history_service",
    "git_visualizer.application.services.branch_service",
    "git_visualizer.application.services.graph_service",
    "git_visualizer.application.services.diff_service",
    "git_visualizer.application.services.changes_service",
    "git_visualizer.application.services.commit_service",
    "git_visualizer.application.services.git_environment_service",
    "git_visualizer.infrastructure.git.git_runner",
    "git_visualizer.infrastructure.git.git_logging",
    "git_visualizer.infrastructure.git.git_errors",
    "git_visualizer.infrastructure.git.git_parser",
    "git_visualizer.infrastructure.git.git_repository",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_does_not_import_qt(module: str) -> None:
    # Отдельный процесс: в текущем PySide6 уже может быть загружен другими тестами.
    code = f"import sys, {module}; sys.exit(1 if 'PySide6' in sys.modules else 0)"

    completed = subprocess.run(
        [sys.executable, "-c", code],
        env={"PYTHONPATH": str(SRC_DIR), "PATH": ""},
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def _imported_names(source: str) -> list[str]:
    """Полные имена всего, что импортируется: ``from a.b import c`` даёт ``a.b.c``."""
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _matches(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _violations(layer: str, forbidden: tuple[str, ...], allowed: tuple[str, ...] = ()) -> list[str]:
    layer_dir = SRC_DIR / "git_visualizer" / layer
    files = sorted(layer_dir.rglob("*.py"))
    assert files, f"каталог {layer} не найден"

    return [
        f"{path.relative_to(SRC_DIR)} imports {name}"
        for path in files
        for name in _imported_names(path.read_text(encoding="utf-8"))
        if any(_matches(name, bad) for bad in forbidden)
        and not any(_matches(name, ok) for ok in allowed)
    ]


def test_domain_sources_do_not_import_forbidden_modules() -> None:
    assert _violations(
        "domain",
        forbidden=(
            "PySide6",
            "subprocess",
            "git_visualizer.infrastructure",
            "git_visualizer.application",
            "git_visualizer.presentation",
        ),
    ) == []


def test_application_sources_depend_only_on_allowed_modules() -> None:
    assert _violations(
        "application",
        forbidden=(
            "PySide6",
            "subprocess",
            "git_visualizer.infrastructure",
            "git_visualizer.presentation",
        ),
        allowed=("git_visualizer.infrastructure.git.git_errors",),
    ) == []


def test_infrastructure_sources_do_not_import_upper_layers() -> None:
    assert _violations(
        "infrastructure",
        forbidden=("PySide6", "git_visualizer.application", "git_visualizer.presentation"),
    ) == []
