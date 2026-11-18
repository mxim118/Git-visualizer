"""Точка входа приложения Git Visualizer."""

import sys

from PySide6.QtWidgets import QApplication

from git_visualizer.presentation.windows.main_window import MainWindow


def main() -> int:
    """Создаёт QApplication, показывает главное окно и запускает цикл событий."""
    app = QApplication(sys.argv)
    app.setApplicationName("Git Visualizer")

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
