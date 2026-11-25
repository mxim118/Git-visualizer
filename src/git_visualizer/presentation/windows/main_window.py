"""Главное окно: стартовый экран, рабочая область и координация с контроллером.

MainWindow не содержит бизнес-логики: открытие репозитория, проверка Git, загрузка
истории, списка файлов, diff, состояния рабочей директории, операции stage/unstage,
создание коммита и операции с ветками выполняются Application Services через
``RepositoryController``. Здесь — только диалоги, переключение экранов, фильтрация
устаревших результатов по выбранному коммиту/файлу, подтверждение области действия
массовой операции и передачи данных виджетам. Diff файла коммита показывается в
большой панели ``DiffOverlay`` поверх всего окна.
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

from git_visualizer.application.services.changes_service import ChangesService
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
from git_visualizer.presentation.error_texts import (
    branch_user_message,
    commit_user_message,
    technical_details,
    user_message,
)
from git_visualizer.presentation.widgets.changes_panel import (
    scope_details,
    scope_summary,
)
from git_visualizer.presentation.widgets.commit_overlay import CommitOverlay
from git_visualizer.presentation.widgets.diff_overlay import DiffOverlay
from git_visualizer.presentation.widgets.start_screen import StartScreen
from git_visualizer.presentation.widgets.workspace_page import WorkspacePage
from git_visualizer.presentation.workers.task_runner import TaskFailure

_WINDOW_TITLE = "Git Visualizer"

_OPERATION_PROGRESS: dict[str, str] = {
    "stage-file": "Добавление файла в индекс…",
    "unstage-file": "Удаление файла из индекса…",
    "stage-all": "Добавление всех изменений в индекс…",
    "unstage-all": "Удаление всех изменений из индекса…",
}
_OPERATION_ERROR_TITLES: dict[str, str] = {
    "stage-file": "Не удалось добавить файл в индекс",
    "unstage-file": "Не удалось убрать файл из индекса",
    "stage-all": "Не удалось добавить изменения в индекс",
    "unstage-all": "Не удалось убрать изменения из индекса",
}
_BRANCH_PROGRESS: dict[str, str] = {
    "create": "Создание ветки…",
    "switch": "Переключение ветки…",
}
_BRANCH_ERROR_TITLES: dict[str, str] = {
    "create": "Не удалось создать ветку",
    "switch": "Не удалось переключить ветку",
}


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
        self.commit_overlay = CommitOverlay(self)
        self.commit_overlay.hide()
        # Какая секция открыла панель: "commit" или "worktree".
        self._diff_overlay_kind: str | None = None
        # Текущая операция с индексом: "stage-file"/"unstage-file"/"stage-all"/
        # "unstage-all". Нужна для заголовка ошибки и статусной строки.
        self._changes_operation: str | None = None
        # Diff какого файла обновить после операции: (путь, секция после операции).
        self._pending_worktree_diff_refresh: tuple[str, bool] | None = None
        # Начальная точка создаваемой ветки для текста ошибки, если Git не нашёл коммит.
        self._branch_start_point: str | None = None
        # Сообщение, которое нужно показать после перезагрузки после смены HEAD.
        self._pending_workspace_status: str | None = None

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
        for name in ("diff_overlay", "commit_overlay"):
            overlay = getattr(self, name, None)
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
        self.workspace_page.worktree_diff_requested.connect(
            self._on_worktree_diff_requested
        )
        self.workspace_page.changes_refresh_requested.connect(self._refresh_changes)
        self.workspace_page.changes_stage_requested.connect(
            self._on_stage_requested
        )
        self.workspace_page.changes_unstage_requested.connect(
            self._on_unstage_requested
        )
        self.workspace_page.changes_stage_all_requested.connect(
            self._on_stage_all_requested
        )
        self.workspace_page.changes_unstage_all_requested.connect(
            self._on_unstage_all_requested
        )
        self.workspace_page.changes_commit_requested.connect(
            self._open_commit_overlay
        )
        self.workspace_page.branch_create_requested.connect(
            self._on_branch_create_requested
        )
        self.workspace_page.branch_switch_requested.connect(
            self._on_branch_switch_requested
        )
        self.commit_overlay.submitted.connect(self._on_commit_submitted)
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
        self._controller.changes_operation_started.connect(
            self._on_changes_operation_started
        )
        self._controller.changes_operation_succeeded.connect(
            self._on_changes_operation_succeeded
        )
        self._controller.changes_operation_failed.connect(
            self._on_changes_operation_failed
        )
        self._controller.commit_started.connect(self._on_commit_started)
        self._controller.commit_succeeded.connect(self._on_commit_succeeded)
        self._controller.commit_failed.connect(self._on_commit_failed)
        self._controller.branch_operation_started.connect(
            self._on_branch_operation_started
        )
        self._controller.branch_operation_succeeded.connect(
            self._on_branch_operation_succeeded
        )
        self._controller.branch_operation_failed.connect(
            self._on_branch_operation_failed
        )
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
        self.commit_overlay.hide_overlay()
        self._pending_worktree_diff_refresh = None
        self._changes_operation = None
        self._reset_branch_operation()
        self._pending_workspace_status = None
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
        self.commit_overlay.hide_overlay()
        self._pending_worktree_diff_refresh = None
        self._changes_operation = None
        self._reset_branch_operation()
        self._pending_workspace_status = None
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
        if self._pending_workspace_status is not None:
            self.statusBar().showMessage(self._pending_workspace_status)
            self._pending_workspace_status = None
        elif data.commits:
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
        self._pending_workspace_status = None
        self._reset_branch_operation()
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
        """Закрывает панель diff; выбор файла Changes сохраняется.

        С отдельной кнопкой ``Diff`` выбор больше не мешает открыть тот же файл
        снова, зато после закрытия панели Stage/Unstage остаются доступны сразу.
        """
        kind = self._diff_overlay_kind
        self._hide_diff_overlay()
        if kind != "worktree":
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
        panel = self.workspace_page.changes_panel
        panel.set_changes(changes)
        self.commit_overlay.update_staged_count(len(panel.staged_files))
        self._apply_pending_diff_refresh()
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
        self._pending_worktree_diff_refresh = None
        self._hide_diff_overlay()
        self.workspace_page.changes_panel.set_error(
            "Не удалось загрузить изменения."
        )
        self.statusBar().showMessage("Не удалось загрузить изменения.")
        self._show_error("Не удалось загрузить изменения", failure.exception)

    # --- stage/unstage: действия пользователя ---

    def _on_stage_requested(self, changed_file: ChangedFile) -> None:
        self._start_file_operation("stage", changed_file)

    def _on_unstage_requested(self, changed_file: ChangedFile) -> None:
        self._start_file_operation("unstage", changed_file)

    def _start_file_operation(
        self, operation: str, changed_file: ChangedFile
    ) -> None:
        """Запускает Application-операцию с одним файлом и запоминает refresh diff."""
        # После stage открытый unstaged-diff станет staged-diff (и наоборот):
        # ту же строку ищем в противоположной секции, когда придёт новый статус.
        target_staged = operation == "stage"
        current_staged = not target_staged
        pending: tuple[str, bool] | None = None
        if self._diff_overlay_kind == "worktree" and self._is_current_worktree_file(
            changed_file.path, current_staged
        ):
            pending = (changed_file.path, target_staged)
        self._pending_worktree_diff_refresh = pending
        self._changes_operation = f"{operation}-file"

        submit = (
            self._controller.stage_file
            if operation == "stage"
            else self._controller.unstage_file
        )
        if not submit(changed_file):
            self._changes_operation = None
            self._pending_worktree_diff_refresh = None
            self.statusBar().showMessage(
                "Другая операция с индексом уже выполняется…"
            )

    def _on_stage_all_requested(self) -> None:
        self._start_bulk_operation("stage")

    def _on_unstage_all_requested(self) -> None:
        self._start_bulk_operation("unstage")

    def _start_bulk_operation(self, operation: str) -> None:
        """Подтверждает область действия массовой операции и запускает её."""
        panel = self.workspace_page.changes_panel
        changes = panel.changes
        files = (
            ChangesService.stageable(changes)
            if operation == "stage"
            else ChangesService.staged(changes)
        )
        if not files:
            return
        conflicts = tuple(
            change for change in changes if change.is_conflicted
        )
        if not self._confirm_bulk_operation(operation, files, conflicts):
            return

        # Выбор строки после массовой операции непредсказуем: старый diff закрываем.
        self._hide_diff_overlay()
        panel.clear_selection()
        self._pending_worktree_diff_refresh = None
        self._changes_operation = f"{operation}-all"

        submit = (
            self._controller.stage_all
            if operation == "stage"
            else self._controller.unstage_all
        )
        if not submit():
            self._changes_operation = None
            self.statusBar().showMessage(
                "Другая операция с индексом уже выполняется…"
            )

    def _confirm_bulk_operation(
        self,
        operation: str,
        files: tuple[ChangedFile, ...],
        conflicts: tuple[ChangedFile, ...],
    ) -> bool:
        """Показывает файлы и категории, которые затронет массовое действие."""
        is_stage = operation == "stage"
        title = "Stage All" if is_stage else "Unstage All"
        if is_stage:
            question = "Добавить в индекс все изменения рабочей директории?"
        else:
            question = (
                "Убрать все изменения из индекса? Содержимое рабочей "
                "директории не изменится."
            )

        informative = scope_summary(files, staged=not is_stage)
        # Короткий предпросмотр: какие именно файлы затронет действие. Полный
        # список — в «Подробнее», чтобы диалог не разрастался на больших наборах.
        details = scope_details(files, staged=not is_stage)
        preview = details.splitlines()[:8]
        if preview:
            informative += "\n\n" + "\n".join(preview)
            if len(files) > len(preview):
                informative += f"\n…и ещё {len(files) - len(preview)}"
        if conflicts:
            informative += (
                f"\n\nКонфликтующие файлы ({len(conflicts)}) не затрагиваются: "
                "разрешение конфликтов не входит в MVP."
            )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(question)
        box.setInformativeText(informative)
        box.setDetailedText(details)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_changes_operation_started(self) -> None:
        self.workspace_page.changes_panel.set_busy(True)
        message = _OPERATION_PROGRESS.get(self._changes_operation)
        if message:
            self.statusBar().showMessage(message)

    def _on_changes_operation_succeeded(self) -> None:
        # Статус обновит контроллер; diff переключится в _on_changes_loaded.
        self._changes_operation = None

    def _on_changes_operation_failed(self, failure: TaskFailure) -> None:
        operation = self._changes_operation
        self._changes_operation = None
        self._pending_worktree_diff_refresh = None
        self.workspace_page.changes_panel.set_busy(False)
        # Панель diff показывала состояние до операции: не оставляем устаревший diff.
        self._hide_diff_overlay()
        title = _OPERATION_ERROR_TITLES.get(
            operation, "Не удалось изменить индекс"
        )
        self._show_error(title, failure.exception)

    # --- создание коммита ---

    def _open_commit_overlay(self) -> None:
        """Открывает окно ввода сообщения, если в индексе есть изменения."""
        if self._controller.repository is None:
            return
        panel = self.workspace_page.changes_panel
        if not panel.staged_files:
            return
        # Коммит меняет индекс и HEAD: открытый diff рабочей директории устареет.
        self._hide_diff_overlay()
        self._pending_worktree_diff_refresh = None
        self.commit_overlay.show_overlay(staged_count=len(panel.staged_files))

    def _on_commit_submitted(self, message: str) -> None:
        """Запускает коммит staged changes в фоне."""
        if self._controller.repository is None:
            self.commit_overlay.hide_overlay()
            return
        self._hide_diff_overlay()
        if not self._controller.commit(message):
            self.commit_overlay.set_busy(False)
            self.statusBar().showMessage(
                "Другая операция с индексом уже выполняется…"
            )

    def _on_commit_started(self) -> None:
        self.workspace_page.changes_panel.set_busy(True)
        self.commit_overlay.set_busy(True)
        self.statusBar().showMessage("Создание коммита…")

    def _on_commit_succeeded(self) -> None:
        # Сообщение больше не нужно; контроллер сразу переоткрывает репозиторий
        # и заново загружает историю, граф, refs и Changes.
        self.commit_overlay.hide_overlay()
        self.statusBar().showMessage("Коммит создан. Обновление данных…")

    def _on_commit_failed(self, failure: TaskFailure) -> None:
        panel = self.workspace_page.changes_panel
        panel.set_busy(False)
        # Окно не закрываем: сообщение сохраняется, чтобы можно было повторить.
        self.commit_overlay.set_busy(False)
        self.statusBar().showMessage("Не удалось создать коммит.")
        self._show_error(
            "Не удалось создать коммит",
            failure.exception,
            message=commit_user_message(failure.exception),
        )

    # --- создание и переключение веток ---

    def _on_branch_create_requested(
        self, name: str, start_point: object
    ) -> None:
        """Создаёт ветку от текущего HEAD или от выбранного коммита."""
        if self._controller.repository is None:
            return
        start_hash = start_point if isinstance(start_point, str) else None
        if start_hash is not None:
            commit = self._find_commit(start_hash)
            if commit is None:
                self.statusBar().showMessage(
                    "Выбранный коммит больше не найден: обновите историю."
                )
                return
            if not self._confirm_branch_head_change("create", name, commit):
                return

        self._reset_branch_operation()
        self._branch_start_point = start_hash
        if not self._controller.create_branch(name, start_point=start_hash):
            self._reset_branch_operation()
            self.statusBar().showMessage(
                "Другая операция с индексом или смена HEAD уже выполняется…"
            )

    def _on_branch_switch_requested(self, name: str) -> None:
        """Переключает на существующую локальную ветку."""
        data = self.workspace_page.workspace_data
        if data is None or self._controller.repository is None:
            return
        branch = next(
            (
                candidate
                for candidate in data.branches
                if candidate.name == name and not candidate.is_remote
            ),
            None,
        )
        if branch is None or branch.is_current:
            self.statusBar().showMessage(
                "Ветка не найдена или уже текущая: обновите список веток."
            )
            return
        if not self._confirm_branch_head_change("switch", name, None):
            return

        self._reset_branch_operation()
        if not self._controller.switch_branch(name):
            self._reset_branch_operation()
            self.statusBar().showMessage(
                "Другая операция с индексом или смена HEAD уже выполняется…"
            )

    def _confirm_branch_head_change(
        self, operation: str, name: str, commit: Commit | None
    ) -> bool:
        """Предупреждает о незакоммиченных изменениях перед сменой HEAD.

        Приложение не удаляет и не перезаписывает их: Git либо безопасно перенесёт
        изменения на другую ветку, либо откажет. Подтверждение показывает, что
        операция это учитывает, но не заменяет проверку Git.
        """
        changes = self.workspace_page.changes_panel.changes
        if not changes:
            return True
        title = "Создание ветки" if operation == "create" else "Переключение ветки"
        if operation == "create" and commit is not None:
            question = (
                f"Создать ветку «{name}» от коммита {commit.short_hash}?"
            )
        else:
            question = f"Переключиться на ветку «{name}»?"
        informative = (
            "В рабочей директории есть незакоммиченные изменения: "
            f"{len(changes)}. Git перенесёт их только если это безопасно, а при "
            "конфликте откажет в операции. Изменения не будут удалены или "
            "перезаписаны приложением."
        )
        details = "\n".join(change.path for change in changes)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(question)
        box.setInformativeText(informative)
        box.setDetailedText(details)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_branch_operation_started(self, operation: str, _name: str) -> None:
        self.workspace_page.set_busy(True)
        # Операции с индексом делят с ветками ключ в контроллере, но и в UI они
        # недоступны, пока Git меняет HEAD/рабочую директорию.
        self.workspace_page.changes_panel.set_busy(True)
        self._hide_diff_overlay()
        self._pending_worktree_diff_refresh = None
        self._changes_operation = None
        message = _BRANCH_PROGRESS.get(operation, "Операция с веткой…")
        self.statusBar().showMessage(message)

    def _on_branch_operation_succeeded(self, operation: str, name: str) -> None:
        if operation == "create":
            self.workspace_page.branches_panel.clear_branch_name()
            self._pending_workspace_status = (
                f"Ветка «{name}» создана и стала текущей."
            )
        else:
            self._pending_workspace_status = (
                f"Переключение на ветку «{name}» выполнено."
            )
        self._reset_branch_operation()

    def _on_branch_operation_failed(
        self, operation: str, name: str, failure: TaskFailure
    ) -> None:
        start_point = self._branch_start_point
        self._reset_branch_operation()
        self._pending_workspace_status = None
        self.workspace_page.set_busy(False)
        self.workspace_page.changes_panel.set_busy(False)
        title = _BRANCH_ERROR_TITLES.get(operation, "Не удалось изменить ветку")
        self.statusBar().showMessage(f"{title}.")
        self._show_error(
            title,
            failure.exception,
            message=branch_user_message(
                failure.exception,
                operation=operation,
                branch_name=name,
                start_point=start_point,
            ),
        )

    def _reset_branch_operation(self) -> None:
        self._branch_start_point = None

    def _apply_pending_diff_refresh(self) -> None:
        """После операции выбирает файл в новой секции и перезагружает его diff."""
        pending = self._pending_worktree_diff_refresh
        self._pending_worktree_diff_refresh = None
        if pending is None:
            return
        path, staged = pending
        if not self.workspace_page.changes_panel.select_file(path, staged=staged):
            self._hide_diff_overlay()
            return
        # select_file испускает file_selected, который закрывает прежний diff:
        # открываем diff заново уже для новой секции выбранного файла.
        changed_file = self.workspace_page.changes_panel.selected_file
        if changed_file is None:
            self._hide_diff_overlay()
            return
        self._on_worktree_diff_requested(changed_file, staged)

    def _on_worktree_file_selected(
        self, changed_file: ChangedFile, staged: bool
    ) -> None:
        """Клик по файлу только выбирает его: diff открывается кнопкой ``Diff``."""
        # Открытый diff относился к прежнему выбору; новый файл не должен
        # оставаться за устаревшей панелью. Невыполненное обновление diff после
        # операции тоже отменяется — пользователь выбрал другой файл.
        self._pending_worktree_diff_refresh = None
        if self._diff_overlay_kind == "worktree":
            self._hide_diff_overlay()

    def _on_worktree_diff_requested(
        self, changed_file: ChangedFile, staged: bool
    ) -> None:
        """Открывает diff выбранного файла по отдельной кнопке."""
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

    def _show_error(
        self,
        title: str,
        error: BaseException,
        *,
        message: str | None = None,
    ) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText(user_message(error) if message is None else message)
        box.setDetailedText(technical_details(error))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()
