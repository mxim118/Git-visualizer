"""Domain, Application и Infrastructure не должны зависеть от PySide6 (ТЗ 7.1)."""

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
    "git_visualizer.application.services",
    "git_visualizer.infrastructure.git.git_runner",
    "git_visualizer.infrastructure.git.git_errors",
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


# Domain чист: никаких Qt, subprocess и других слоёв приложения (ТЗ 7.1, 7.3).
DOMAIN_FORBIDDEN_IMPORTS = (
    "PySide6",
    "subprocess",
    "git_visualizer.infrastructure",
    "git_visualizer.application",
    "git_visualizer.presentation",
)


def _imported_modules(source: str) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_domain_sources_do_not_import_forbidden_modules() -> None:
    domain_dir = SRC_DIR / "git_visualizer" / "domain"
    files = sorted(domain_dir.rglob("*.py"))
    assert files, "каталог domain не найден"

    violations = [
        f"{path.relative_to(SRC_DIR)} imports {name}"
        for path in files
        for name in _imported_modules(path.read_text(encoding="utf-8"))
        if any(name == bad or name.startswith(bad + ".") for bad in DOMAIN_FORBIDDEN_IMPORTS)
    ]

    assert violations == []
