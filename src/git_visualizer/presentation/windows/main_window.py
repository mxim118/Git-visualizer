"""Главное окно приложения (заглушка каркаса; реальный интерфейс — в итерации 4)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow


class MainWindow(QMainWindow):
    """Минимальное окно, подтверждающее, что PySide6-приложение запускается."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Git Visualizer")
        self.resize(900, 600)

        placeholder = QLabel("Git Visualizer — каркас проекта")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(placeholder)
