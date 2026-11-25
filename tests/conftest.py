import os
from pathlib import Path

# Тесты не должны открывать реальные окна: без дисплея (CI, Linux без X11)
# Qt иначе не запустится. Значение можно переопределить переменной окружения.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qt_app():
    """Единственный QApplication на всю тестовую сессию."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def app_settings(tmp_path: Path):
    """Настройки во временном INI-файле: тесты не трогают настройки пользователя."""
    from PySide6.QtCore import QSettings

    from git_visualizer.presentation.settings import AppSettings

    storage = QSettings(
        str(tmp_path / "git-visualizer-settings.ini"),
        QSettings.Format.IniFormat,
    )
    return AppSettings(storage)
