import importlib

import pytest

PACKAGES = [
    "git_visualizer",
    "git_visualizer.domain",
    "git_visualizer.domain.models",
    "git_visualizer.application",
    "git_visualizer.application.services",
    "git_visualizer.infrastructure",
    "git_visualizer.infrastructure.git",
    "git_visualizer.presentation",
    "git_visualizer.presentation.windows",
    "git_visualizer.presentation.widgets",
    "git_visualizer.presentation.dialogs",
    "git_visualizer.presentation.controllers",
    "git_visualizer.presentation.workers",
]


@pytest.mark.parametrize("package", PACKAGES)
def test_package_is_importable(package: str) -> None:
    importlib.import_module(package)
