"""Верхняя панель основного окна: репозиторий, ветка и основные действия."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from git_visualizer.domain.models import Repository

_PLACEHOLDER = "—"
_LATER_TOOLTIP = "Функциональность будет добавлена в следующих версиях"


class TopBar(QFrame):
    """Панель с названием приложения, именем репозитория, веткой и кнопками.

    Fetch, Pull и Push пока не реализованы: кнопки отключены и оставлены как место
    для следующих итераций. Refresh запускает ручное обновление истории.
    """

    refresh_requested = Signal()
    close_repository_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("top_bar")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._has_repository = False
        self._busy = False

        self.app_title_label = QLabel("Git Visualizer")
        self.app_title_label.setObjectName("app_title_label")
        self.app_title_label.setStyleSheet("font-size: 16px; font-weight: 600;")

        self.repository_name_label = QLabel(_PLACEHOLDER)
        self.repository_name_label.setObjectName("repository_name_label")
        self.repository_name_label.setStyleSheet("font-weight: 600;")

        self.branch_label = QLabel("Ветка: —")
        self.branch_label.setObjectName("current_branch_label")
        self.branch_label.setStyleSheet("color: #334155;")

        self.status_label = QLabel()
        self.status_label.setObjectName("operation_status_label")
        self.status_label.setStyleSheet("color: #2563eb;")
        self.status_label.hide()

        self.fetch_button = self._make_action_button(
            "Fetch", "fetch_button", enabled=False
        )
        self.pull_button = self._make_action_button("Pull", "pull_button", enabled=False)
        self.push_button = self._make_action_button("Push", "push_button", enabled=False)

        self.refresh_button = self._make_action_button(
            "Обновить", "refresh_button", enabled=False
        )
        self.refresh_button.setToolTip("Обновить историю репозитория")
        self.refresh_button.clicked.connect(self.refresh_requested)

        self.close_button = self._make_action_button(
            "Закрыть", "close_repository_button", enabled=False
        )
        self.close_button.setToolTip("Закрыть репозиторий и вернуться на стартовый экран")
        self.close_button.clicked.connect(self.close_repository_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(12)
        layout.addWidget(self.app_title_label)
        layout.addWidget(self.repository_name_label)
        layout.addWidget(self.branch_label)
        layout.addStretch(1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.fetch_button)
        layout.addWidget(self.pull_button)
        layout.addWidget(self.push_button)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.close_button)

    def set_repository(self, repository: Repository) -> None:
        """Показывает имя репозитория (путь — в подсказке) и состояние HEAD."""
        self._has_repository = True
        self.repository_name_label.setText(repository.name)
        self.repository_name_label.setToolTip(str(repository.path))
        self.branch_label.setText(f"Ветка: {_branch_text(repository)}")
        self.branch_label.setToolTip(_branch_tooltip(repository))
        self.refresh_button.setEnabled(not self._busy)
        self.close_button.setEnabled(True)

    def clear(self) -> None:
        self._has_repository = False
        self.repository_name_label.setText(_PLACEHOLDER)
        self.repository_name_label.setToolTip("")
        self.branch_label.setText("Ветка: —")
        self.branch_label.setToolTip("")
        self.refresh_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.set_busy(False)

    def set_busy(self, busy: bool) -> None:
        """Показывает индикатор «Загрузка…» и блокирует повторное обновление."""
        self._busy = busy
        self.status_label.setText("Загрузка…")
        self.status_label.setVisible(busy)
        self.refresh_button.setEnabled(self._has_repository and not busy)

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def has_repository(self) -> bool:
        return self._has_repository

    def _make_action_button(
        self, text: str, object_name: str, *, enabled: bool
    ) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setEnabled(enabled)
        if not enabled:
            button.setToolTip(_LATER_TOOLTIP)
        return button


def _branch_text(repository: Repository) -> str:
    if repository.is_unborn:
        branch = repository.current_branch or "новая ветка"
        return f"{branch} (коммитов нет)"
    if repository.is_detached:
        if repository.head_commit:
            return f"detached HEAD ({repository.head_commit[:7]})"
        return "detached HEAD"
    return repository.current_branch or _PLACEHOLDER


def _branch_tooltip(repository: Repository) -> str:
    if repository.is_unborn:
        return "В репозитории ещё нет коммитов"
    if repository.is_detached:
        return "HEAD не указывает на ветку (detached HEAD)"
    if repository.current_branch is None:
        return "Текущая ветка не определена"
    return f"Текущая ветка: {repository.current_branch}"
