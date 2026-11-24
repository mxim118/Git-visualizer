"""Главное окно: стартовый экран, рабочая область и координация с контроллером.

MainWindow не содержит бизнес-логики: открытие репозитория, проверка Git, загрузка
истории, списка файлов, diff и состояния рабочей директории выполняются Application
Services через ``RepositoryController``. Здесь — только диалоги, переключение экранов,
фильтрация устаревших результатов по выбранному коммиту/файлу и передача данных
виджетам. Diff файла коммита показывается в большой панели ``DiffOverlay`` поверх
всего окна.
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

from git_visualizer.domain.models import (
    ChangedFile,
    Commit,
    Diff,
    FileChange,
    Repository,
)
from git_visualizer.presentation.controllers.repository_controller import (
    RepositoryController,
    WorkspaceData,
)
from git_visualizer.presentation.error_texts import technical_details, user_message
from git_visualizer.presentation.widgets.diff_overlay import DiffOverlay
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

        self.diff_overlay = DiffOverlay(self)
        self.diff_overlay.hide()
        # Какая секция открыла панель: "commit" или "worktree".
        self._diff_overlay_kind: str | None = None

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

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        overlay = getattr(self, "diff_overlay", None)
        if overlay is not None and overlay.isVisible():
            overlay.setGeometry(self.rect())

    def _connect_signals(self) -> None:
        self.start_screen.open_repository_requested.connect(self._choose_repository)
        self.workspace_page.refresh_requested.connect(self._refresh_history)
        self.workspace_page.close_repository_requested.connect(self._close_repository)
        self.workspace_page.commit_selected.connect(self._on_commit_selected)
        self.workspace_page.file_selected.connect(self._on_file_selected)
        self.workspace_page.worktree_file_selected.connect(
            self._on_worktree_file_selected
        )
        self.workspace_page.changes_refresh_requested.connect(self._refresh_changes)
        self.diff_overlay.close_requested.connect(self._close_file_diff)

        self._controller.git_environment_ready.connect(self._on_git_environment_ready)
        self._controller.git_environment_failed.connect(self._on_git_environment_failed)
        self._controller.repository_opening.connect(self._on_repository_opening)
        self._controller.repository_opened.connect(self._on_repository_opened)
        self._controller.open_failed.connect(self._on_open_failed)
        self._controller.history_loading.connect(self._on_history_loading)
        self._controller.repository_loaded.connect(self._on_workspace_loaded)
        self._controller.load_failed.connect(self._on_load_failed)
        self._controller.repository_closed.connect(self._on_repository_closed)
        self._controller.commit_files_loading.connect(self._on_commit_files_loading)
        self._controller.commit_files_loaded.connect(self._on_commit_files_loaded)
        self._controller.commit_files_failed.connect(self._on_commit_files_failed)
        self._controller.file_diff_loading.connect(self._on_file_diff_loading)
        self._controller.file_diff_loaded.connect(self._on_file_diff_loaded)
        self._controller.file_diff_failed.connect(self._on_file_diff_failed)
        self._controller.changes_loading.connect(self._on_changes_loading)
        self._controller.changes_loaded.connect(self._on_changes_loaded)
        self._controller.changes_failed.connect(self._on_changes_failed)
        self._controller.worktree_diff_loading.connect(
            self._on_worktree_diff_loading
        )
        self._controller.worktree_diff_loaded.connect(
            self._on_worktree_diff_loaded
        )
        self._controller.worktree_diff_failed.connect(
            self._on_worktree_diff_failed
        )

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

    def _refresh_changes(self) -> None:
        """Ручное обновление состояния рабочей директории (кнопка панели Changes)."""
        if self._controller.repository is None:
            return
        if not self._controller.load_changes():
            self.statusBar().showMessage("Изменения уже загружаются…")

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
        self._hide_diff_overlay()
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
        self._hide_diff_overlay()
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
        # Статус рабочей директории — отдельный запрос: он меняется независимо от HEAD.
        self._controller.load_changes()
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

    # --- детали выбранного коммита и diff ---

    def _on_commit_selected(self, commit_hash: str) -> None:
        self._hide_diff_overlay()
        self.workspace_page.clear_file_selection()
        self.commit_selected.emit(commit_hash)
        self._controller.load_commit_files(commit_hash)

    def _on_commit_files_loading(self, commit_hash: str) -> None:
        if self._is_current_commit(commit_hash):
            self.workspace_page.details_panel.set_files_loading()

    def _on_commit_files_loaded(
        self, commit_hash: str, files: tuple[FileChange, ...]
    ) -> None:
        if self._is_current_commit(commit_hash):
            self.workspace_page.details_panel.set_files(files)

    def _on_commit_files_failed(self, commit_hash: str, failure: TaskFailure) -> None:
        if not self._is_current_commit(commit_hash):
            return
        self.workspace_page.details_panel.set_files_error(
            "Не удалось загрузить список файлов."
        )
        self._show_error("Не удалось загрузить файлы коммита", failure.exception)

    def _on_file_selected(
        self, commit_hash: str, file_change: FileChange
    ) -> None:
        """Открывает diff файла коммита в панели поверх окна."""
        if self._find_commit(commit_hash) is None:
            return
        if not self._controller.load_file_diff(commit_hash, file_change):
            self.statusBar().showMessage("Diff этого файла уже загружается…")

    def _on_file_diff_loading(self, commit_hash: str, path: str) -> None:
        if not self._is_current_file(commit_hash, path):
            return
        context = self._current_file_context(commit_hash)
        if context is None:
            return
        commit, file_change = context
        self._diff_overlay_kind = "commit"
        self.diff_overlay.show_commit_loading(commit, file_change)

    def _on_file_diff_loaded(self, commit_hash: str, path: str, diff: Diff) -> None:
        if not self._is_current_file(commit_hash, path):
            return
        context = self._current_file_context(commit_hash)
        if context is None:
            return
        commit, file_change = context
        self._diff_overlay_kind = "commit"
        self.diff_overlay.show_commit_diff(commit, file_change, diff)

    def _on_file_diff_failed(
        self, commit_hash: str, path: str, failure: TaskFailure
    ) -> None:
        if not self._is_current_file(commit_hash, path):
            return
        context = self._current_file_context(commit_hash)
        if context is not None:
            commit, file_change = context
            self._diff_overlay_kind = "commit"
            self.diff_overlay.show_commit_error(
                commit, file_change, "Не удалось загрузить diff файла."
            )
        self._show_error("Не удалось загрузить diff", failure.exception)

    def _close_file_diff(self) -> None:
        """Закрывает панель diff и снимает выбор, чтобы файл можно было открыть снова."""
        kind = self._diff_overlay_kind
        self._hide_diff_overlay()
        if kind == "worktree":
            self.workspace_page.changes_panel.clear_selection()
        else:
            self.workspace_page.clear_file_selection()

    def _hide_diff_overlay(self) -> None:
        self._diff_overlay_kind = None
        self.diff_overlay.hide_overlay()

    def _find_commit(self, commit_hash: str) -> Commit | None:
        data = self.workspace_page.workspace_data
        if data is None:
            return None
        return next(
            (commit for commit in data.commits if commit.hash == commit_hash),
            None,
        )

    def _current_file_context(
        self, commit_hash: str
    ) -> tuple[Commit, FileChange] | None:
        commit = self._find_commit(commit_hash)
        file_change = self.workspace_page.selected_file
        if commit is None or file_change is None:
            return None
        return commit, file_change

    def _is_current_commit(self, commit_hash: str) -> bool:
        return self.workspace_page.selected_commit_hash == commit_hash

    def _is_current_file(self, commit_hash: str, path: str) -> bool:
        file_change = self.workspace_page.selected_file
        return (
            self._is_current_commit(commit_hash)
            and file_change is not None
            and file_change.path == path
        )

    # --- Changes: состояние рабочей директории и diff ---

    def _on_changes_loading(self) -> None:
        self.workspace_page.changes_panel.set_loading()
        self.statusBar().showMessage("Загрузка изменений…")

    def _on_changes_loaded(self, changes: tuple[ChangedFile, ...]) -> None:
        self.workspace_page.changes_panel.set_changes(changes)
        if changes:
            self.statusBar().showMessage(f"Изменений: {len(changes)}.")
        else:
            workspace = self.workspace_page.workspace_data
            # У пустого репозитория важнее сообщение об отсутствии коммитов.
            if workspace is not None and workspace.commits:
                self.statusBar().showMessage(
                    "Изменений нет — рабочая директория чиста."
                )

    def _on_changes_failed(self, failure: TaskFailure) -> None:
        self.workspace_page.changes_panel.set_error(
            "Не удалось загрузить изменения."
        )
        self.statusBar().showMessage("Не удалось загрузить изменения.")
        self._show_error("Не удалось загрузить изменения", failure.exception)

    def _on_worktree_file_selected(
        self, changed_file: ChangedFile, staged: bool
    ) -> None:
        self._diff_overlay_kind = "worktree"
        # У неотслеживаемого файла diff нет: Git не знает о нём до `git add`.
        if changed_file.is_untracked:
            self.diff_overlay.show_untracked(changed_file, staged)
            return
        if not self._controller.load_worktree_diff(changed_file, staged=staged):
            self.statusBar().showMessage("Diff этого файла уже загружается…")

    def _on_worktree_diff_loading(self, path: str, staged: bool) -> None:
        if not self._is_current_worktree_file(path, staged):
            return
        changed_file = self.workspace_page.changes_panel.selected_file
        if changed_file is None:
            return
        self._diff_overlay_kind = "worktree"
        self.diff_overlay.show_worktree_loading(changed_file, staged)

    def _on_worktree_diff_loaded(self, path: str, staged: bool, diff: Diff) -> None:
        if not self._is_current_worktree_file(path, staged):
            return
        changed_file = self.workspace_page.changes_panel.selected_file
        if changed_file is None:
            return
        self._diff_overlay_kind = "worktree"
        self.diff_overlay.show_worktree_diff(changed_file, staged, diff)

    def _on_worktree_diff_failed(
        self, path: str, staged: bool, failure: TaskFailure
    ) -> None:
        if not self._is_current_worktree_file(path, staged):
            return
        changed_file = self.workspace_page.changes_panel.selected_file
        if changed_file is not None:
            self._diff_overlay_kind = "worktree"
            self.diff_overlay.show_worktree_error(
                changed_file, staged, "Не удалось загрузить diff файла."
            )
        self._show_error("Не удалось загрузить diff", failure.exception)

    def _is_current_worktree_file(self, path: str, staged: bool) -> bool:
        panel = self.workspace_page.changes_panel
        selected = panel.selected_file
        return (
            selected is not None
            and selected.path == path
            and panel.selected_is_staged == staged
        )

    # --- диалоги ---

    def _show_error(self, title: str, error: BaseException) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText(user_message(error))
        box.setDetailedText(technical_details(error))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()
