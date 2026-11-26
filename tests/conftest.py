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


@pytest.fixture(autouse=True)
def close_waits_without_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тесты не упираются в модальный вопрос при закрытии главного окна.

    По умолчанию выбирается «Дождаться»: так закрытие ведёт себя как раньше
    (ждало фоновые задачи), но тест не блокируется диалогом. Тесты закрытия в
    ``tests/unit/test_main_window.py`` переопределяют метод на экземпляре окна.
    """
    from git_visualizer.presentation.windows.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow,
        "_confirm_close_with_active_tasks",
        lambda self, tasks: True,
    )
