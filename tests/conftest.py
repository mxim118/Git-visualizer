import os

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
