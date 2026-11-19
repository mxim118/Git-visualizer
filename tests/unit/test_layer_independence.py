"""Domain, Application и Infrastructure не должны зависеть от PySide6 (ТЗ 7.1)."""

import subprocess
import sys
from pathlib import Path

import pytest

import git_visualizer

SRC_DIR = Path(git_visualizer.__file__).resolve().parent.parent

MODULES = [
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
