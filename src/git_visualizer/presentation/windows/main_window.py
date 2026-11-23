"""Главное окно: стартовый экран, рабочая область и координация с контроллером.

MainWindow не содержит бизнес-логики: открытие репозитория, проверка Git и загрузка
истории выполняются Application Services через ``RepositoryController``. Здесь —
только диалоги, переключение экранов и передача данных виджетам.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from git_visualizer.domain.models import Repository
from git_visualizer.presentation.controllers.repository_controller import (
    RepositoryController,
    WorkspaceData,
)
from git_visualizer.presentation.error_texts import technical_details, user_message
from git_visualizer.presentation.widgets.start_screen import StartScreen
from git_visualizer.presentation.widgets.workspace_page import WorkspacePage
from git_visualizer.presentation.workers.task_runner import TaskFailure

_WINDOW_TITLE = "Git Visualizer"


class MainWindow(QMainWindow):
    """Координирует стартовый экран и рабочую область репозитория."""

    commit_selected = Signal(str)

    def __init__(
        self,
        controller: RepositoryController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller

        self.setWindowTitle(_WINDOW_TITLE)
        self.resize(1280, 800)

        self.start_screen = StartScreen(self)
        self.workspace_page = WorkspacePage(self)

        self.stack = QStackedWidget(self)
        self.stack.setObjectName("main_stack")
        self.stack.addWidget(self.start_screen)
        self.stack.addWidget(self.workspace_page)
        self.setCentralWidget(self.stack)

        self._connect_signals()
        self.statusBar().showMessage("Проверка Git…")
        self.stack.setCurrentWidget(self.start_screen)
        self._controller.check_git_environment()

    @property
    def selected_commit_hash(self) -> str | None:
        """Hash выбранного коммита; сохраняется рабочей областью."""
        return self.workspace_page.selected_commit_hash

    @property
    def controller(self) -> RepositoryController:
        return self._controller

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        # Фоновые задачи должны завершиться раньше окна: их слоты меняют виджеты.
        self._controller.wait_for_done()
        super().closeEvent(event)

    def _connect_signals(self) -> None:
        self.start_screen.open_repository_requested.connect(self._choose_repository)
        self.workspace_page.refresh_requested.connect(self._refresh_history)
        self.workspace_page.close_repository_requested.connect(self._close_repository)
        self.workspace_page.commit_selected.connect(self.commit_selected.emit)

        self._controller.git_environment_ready.connect(self._on_git_environment_ready)
        self._controller.git_environment_failed.connect(self._on_git_environment_failed)
        self._controller.repository_opening.connect(self._on_repository_opening)
        self._controller.repository_opened.connect(self._on_repository_opened)
        self._controller.open_failed.connect(self._on_open_failed)
        self._controller.history_loading.connect(self._on_history_loading)
        self._controller.repository_loaded.connect(self._on_workspace_loaded)
        self._controller.load_failed.connect(self._on_load_failed)
        self._controller.repository_closed.connect(self._on_repository_closed)

    # --- действия пользователя ---

    def _choose_repository(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку Git-репозитория",
            str(Path.home()),
        )
        if path:
            self._controller.open_repository(path)

    def _refresh_history(self) -> None:
        if self._controller.repository is None:
            return
        if not self._controller.refresh():
            self.statusBar().showMessage("История уже загружается…")

    def _close_repository(self) -> None:
        self._controller.close_repository()

    # --- состояние окружения Git ---

    def _on_git_environment_ready(self, environment) -> None:
        self.start_screen.show_git_environment(environment)
        if not self._start_screen_is_idle():
            return
        if environment.is_supported:
            self.statusBar().showMessage(
                f"Git {environment.version_text} — версия поддерживается."
            )
        else:
            self.statusBar().showMessage(
                f"Версия Git {environment.version_text} ниже минимальной "
                f"({environment.minimum_text})."
            )

    def _on_git_environment_failed(self, failure: TaskFailure) -> None:
        self.start_screen.show_git_error(user_message(failure.exception))
        if self._start_screen_is_idle():
            self.statusBar().showMessage("Не удалось проверить Git.")

    def _start_screen_is_idle(self) -> bool:
        """Стартовый экран виден и не занят открытием репозитория."""
        return (
            self.stack.currentWidget() is self.start_screen
            and self._controller.repository is None
            and self.start_screen.open_button.isEnabled()
        )

    # --- открытие и закрытие репозитория ---

    def _on_repository_opening(self, path: str) -> None:
        self.workspace_page.clear()
        self.stack.setCurrentWidget(self.start_screen)
        self.start_screen.set_open_enabled(False)
        self.statusBar().showMessage(f"Открытие репозитория: {path}…")

    def _on_repository_opened(self, repository: Repository) -> None:
        self.setWindowTitle(f"{_WINDOW_TITLE} — {repository.name}")
        self.workspace_page.set_repository(repository)
        self.stack.setCurrentWidget(self.workspace_page)
        self.start_screen.set_open_enabled(True)
        self.statusBar().showMessage("Загрузка истории…")

    def _on_open_failed(self, failure: TaskFailure) -> None:
        self.stack.setCurrentWidget(self.start_screen)
        self.start_screen.set_open_enabled(True)
        self.statusBar().showMessage("Не удалось открыть репозиторий.")
        self._show_error("Не удалось открыть репозиторий", failure.exception)

    def _on_repository_closed(self) -> None:
        self.workspace_page.clear()
        self.stack.setCurrentWidget(self.start_screen)
        self.start_screen.set_open_enabled(True)
        self.setWindowTitle(_WINDOW_TITLE)
        self.statusBar().showMessage("Репозиторий закрыт.")

    # --- загрузка истории ---

    def _on_history_loading(self) -> None:
        self.workspace_page.set_busy(True)
        self.statusBar().showMessage("Загрузка истории…")

    def _on_workspace_loaded(self, data: WorkspaceData) -> None:
        self.workspace_page.set_workspace_data(data)
        self.workspace_page.set_busy(False)
        if data.commits:
            self.statusBar().showMessage(
                f"Коммитов загружено: {len(data.commits)}. "
                f"Веток: {len(data.branches)}."
            )
        else:
            self.statusBar().showMessage(
                "В репозитории пока нет коммитов. Создайте первый коммит в Git, "
                "затем обновите историю."
            )

    def _on_load_failed(self, failure: TaskFailure) -> None:
        self.workspace_page.set_busy(False)
        self.statusBar().showMessage("Не удалось загрузить историю.")
        self._show_error("Не удалось загрузить историю", failure.exception)

    # --- диалоги ---

    def _show_error(self, title: str, error: BaseException) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText(user_message(error))
        box.setDetailedText(technical_details(error))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()
