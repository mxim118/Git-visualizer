"""Главное окно: стартовый экран, рабочая область и координация с контроллером.

MainWindow не содержит бизнес-логики: открытие репозитория, проверка Git, загрузка
истории, списка файлов, diff, состояния рабочей директории, операции stage/unstage,
создание коммита и операции с ветками выполняются Application Services через
``RepositoryController``. Здесь — только диалоги, переключение экранов, фильтрация
устаревших результатов по выбранному коммиту/файлу, подтверждение области действия
массовой операции и передачи данных виджетам. Diff файла коммита показывается в
большой панели ``DiffOverlay`` поверх всего окна.

Локальные настройки (``AppSettings`` на ``QSettings``) окно читает при создании и
сохраняет при закрытии: недавние репозитории, геометрию, состояние сплиттеров,
точное положение графа (масштаб и центр) и лимит загрузки коммитов. Пароли,
токены и прочие учётные данные в настройки не попадают.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QStackedWidget,
    QWidget,
)

from git_visualizer.application.services.changes_service import ChangesService
from git_visualizer.application.services.remote_service import (
    RemoteConfigurationContext,
    RemoteConfigurationResult,
    RemoteOperationContext,
    RemoteOperationResult,
)
from git_visualizer.domain.models import (
    Branch,
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
from git_visualizer.presentation.dialogs.remote_settings_dialog import (
    RemoteSettingsDialog,
)
from git_visualizer.presentation.dialogs.settings_dialog import SettingsDialog
from git_visualizer.presentation.error_texts import (
    branch_user_message,
    commit_user_message,
    technical_details,
    user_message,
)
from git_visualizer.presentation.remote_texts import (
    remote_configuration_error_title,
    remote_configuration_progress_text,
    remote_configuration_result_text,
    remote_configuration_user_message,
    remote_error_title,
    remote_progress_text,
    remote_result_text,
    remote_user_message,
)
from git_visualizer.presentation.settings import AppSettings
from git_visualizer.presentation.widgets.changes_panel import (
    discard_confirmation,
    scope_details,
    scope_summary,
)
from git_visualizer.presentation.widgets.commit_overlay import CommitOverlay
from git_visualizer.presentation.widgets.diff_overlay import DiffOverlay
from git_visualizer.presentation.widgets.graph_view import GraphViewState
from git_visualizer.presentation.widgets.start_screen import StartScreen
from git_visualizer.presentation.widgets.workspace_page import WorkspacePage
from git_visualizer.presentation.workers.task_runner import TaskFailure

_WINDOW_TITLE = "Git Visualizer"

_OPERATION_PROGRESS: dict[str, str] = {
    "stage-file": "Добавление файла в индекс…",
    "unstage-file": "Удаление файла из индекса…",
    "stage-all": "Добавление всех изменений в индекс…",
    "unstage-all": "Удаление всех изменений из индекса…",
    "discard-file": "Отмена изменений файла…",
}
_OPERATION_ERROR_TITLES: dict[str, str] = {
    "stage-file": "Не удалось добавить файл в индекс",
    "unstage-file": "Не удалось убрать файл из индекса",
    "stage-all": "Не удалось добавить изменения в индекс",
    "unstage-all": "Не удалось убрать изменения из индекса",
    "discard-file": "Не удалось отменить изменения файла",
}
_BRANCH_PROGRESS: dict[str, str] = {
    "create": "Создание ветки…",
    "switch": "Переключение ветки…",
}
_BRANCH_ERROR_TITLES: dict[str, str] = {
    "create": "Не удалось создать ветку",
    "switch": "Не удалось переключить ветку",
}

# Имена фоновых задач (``TaskRunner.submit(name=...)``) → понятное описание операции.
# Используется диалогом закрытия: пользователь видит, что именно ещё выполняется.
_CLOSE_TASK_LABELS: dict[str, str] = {
    "git-environment": "проверка Git",
    "open-repository": "открытие репозитория",
    "load-workspace": "загрузка истории",
    "load-older-history": "загрузка старой истории",
    "changes": "обновление состояния рабочей директории",
    "commit-files": "загрузка списка файлов коммита",
    "file-diff": "загрузка diff коммита",
    "worktree-diff": "загрузка diff файла",
    "commit": "создание коммита",
    "branch-create": "создание ветки",
    "branch-switch": "переключение ветки",
    "stage-file": "добавление файла в индекс",
    "unstage-file": "удаление файла из индекса",
    "stage-all": "добавление всех изменений в индекс",
    "unstage-all": "удаление всех изменений из индекса",
    "discard-file": "отмена изменений файла",
    "remote-fetch": "Fetch",
    "remote-pull": "Pull",
    "remote-push": "Push",
    "remote-push-initial": "первый Push",
    "remote-config-add": "добавление remote",
    "remote-config-edit": "изменение remote",
    "remote-config-remove": "удаление remote",
}
_UNKNOWN_TASK_LABEL = "операция Git"
# Период проверки завершения задач при закрытии окна: событийный цикл остаётся
# живым, поэтому окно перерисовывается и кнопка «Не ждать» доступна.
_CLOSE_WAIT_POLL_MSEC = 100
# Короткие операции (проверка Git, обновление статуса) успевают завершиться за
# это время — тогда вопрос о закрытии не задаётся.
_CLOSE_GRACE_MSEC = 500


def describe_active_tasks(names: Sequence[str]) -> str:
    """Русское описание выполняющихся фоновых операций для диалога закрытия.

    Имена задач берутся из ``RepositoryController.active_task_names()``; повторы
    и неизвестные имена не создают дублей (неизвестное — «операция Git»).
    """
    labels: list[str] = []
    for name in names:
        label = _CLOSE_TASK_LABELS.get(name, _UNKNOWN_TASK_LABEL)
        if label not in labels:
            labels.append(label)
    return ", ".join(labels)


class MainWindow(QMainWindow):
    """Координирует стартовый экран и рабочую область репозитория."""

    commit_selected = Signal(str)

    def __init__(
        self,
        controller: RepositoryController,
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._settings = settings
        # Положение графа текущего репозитория; None — вписывать автоматически.
        self._current_repository_path: str | None = None
        self._preferred_graph_view = None

        self.setWindowTitle(_WINDOW_TITLE)
        self.resize(1280, 800)
        geometry = settings.window_geometry()
        if geometry is not None:
            self.restoreGeometry(geometry)

        self.start_screen = StartScreen(self)
        self.workspace_page = WorkspacePage(self)
        self.workspace_page.restore_panel_state(
            main_state=settings.main_splitter_state(),
            body_state=settings.body_splitter_state(),
        )
        # При запуске: переносим настройки прежней версии и убираем положения
        # графов репозиториев, которых больше нет в «Недавних».
        recent_repositories = settings.recent_repositories()
        settings.clean_up_graph_view_states(recent_repositories)
        settings.sync()
        self.start_screen.set_recent_repositories(recent_repositories)
        # Лимит загрузки коммитов — пользовательская настройка Application-сервиса.
        self._controller.set_history_limit(settings.history_limit())

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
        # "unstage-all"/"discard-file". Нужна для заголовка ошибки и статусной строки.
        self._changes_operation: str | None = None
        # Diff какого файла обновить после операции: (путь, секция после операции).
        self._pending_worktree_diff_refresh: tuple[str, bool] | None = None
        # Начальная точка создаваемой ветки для текста ошибки, если Git не нашёл коммит.
        self._branch_start_point: str | None = None
        # Сообщение, которое нужно показать после перезагрузки после смены HEAD.
        self._pending_workspace_status: str | None = None
        # Контекст и результат последней сетевой операции.
        self._remote_context: RemoteOperationContext | None = None
        self._pending_remote_result: RemoteOperationResult | None = None
        # Открытое окно настроек remote.
        self._remote_settings_dialog: RemoteSettingsDialog | None = None
        # После успешной настройки remote окно остаётся занятым до перезагрузки.
        self._pending_remote_configuration_refresh = False
        # Окно закрывается: повторные closeEvent игнорируются, а модальные
        # диалоги ошибок не открываются (они заблокировали бы завершение).
        self._closing = False

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
        """Закрывает окно, не блокируя интерфейс фоновыми задачами.

        Настройки сохраняются **до** любого ожидания: если пользователь закроет
        окно без ожидания (или снимет зависший процесс), геометрия, панели и
        положение графа останутся. Короткие операции успевают завершиться за
        ``_CLOSE_GRACE_MSEC`` и не вызывают вопрос; если задача ещё выполняется,
        пользователь выбирает «Дождаться» или «Закрыть без ожидания», а ожидание
        идёт с живым событийным циклом и индикатором — окно не замирает до
        сетевого таймаута (120 с у Fetch/Pull/Push).
        """
        if self._closing:
            # Повторный closeEvent во время ожидания (например, Alt+F4).
            event.accept()
            return
        self._closing = True
        self._save_settings()

        tasks = self._controller.active_task_names()
        if not tasks:
            super().closeEvent(event)
            return
        if self._wait_for_tasks_quietly(_CLOSE_GRACE_MSEC):
            super().closeEvent(event)
            return

        remaining = self._controller.active_task_names() or tasks
        if not self._confirm_close_with_active_tasks(remaining):
            super().closeEvent(event)
            return
        self._wait_for_active_tasks(remaining)
        super().closeEvent(event)

    def _wait_for_tasks_quietly(self, msecs: int) -> bool:
        """Ждёт завершения задач не дольше ``msecs``, не показывая диалогов.

        Нужна, чтобы обычное закрытие не спрашивало про короткие фоновые
        операции. Событийный цикл при этом живой (окно перерисовывается).

        :return: ``True``, если все задачи завершились за отведённое время.
        """
        if not self._controller.active_task_names():
            return True

        loop = QEventLoop(self)
        poll = QTimer(self)
        poll.setInterval(_CLOSE_WAIT_POLL_MSEC)
        deadline = QTimer(loop)
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        finished = False

        def check() -> None:
            nonlocal finished
            if not self._controller.wait_for_done(0):
                return
            if self._controller.active_task_names():
                # Пул свободен, но задача ещё не снята: ждём доставку finished.
                return
            finished = True
            loop.quit()

        poll.timeout.connect(check)
        poll.start()
        deadline.start(msecs)
        check()
        if not finished:
            loop.exec()
        poll.stop()
        return finished

    def _confirm_close_with_active_tasks(self, tasks: tuple[str, ...]) -> bool:
        """Спрашивает, дождаться ли фоновых операций перед закрытием.

        :return: ``True``, если выбран вариант «Дождаться».
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Закрытие Git Visualizer")
        box.setText(
            "Выполняются фоновые операции Git: "
            f"{describe_active_tasks(tasks)}."
        )
        box.setInformativeText(
            "Дождаться их завершения перед закрытием? Если закрыть без ожидания, "
            "операция может продолжиться в фоне, а её результат в приложении уже "
            "не отобразится."
        )
        wait_button = box.addButton(
            "Дождаться", QMessageBox.ButtonRole.AcceptRole
        )
        close_button = box.addButton(
            "Закрыть без ожидания", QMessageBox.ButtonRole.DestructiveRole
        )
        box.setDefaultButton(wait_button)
        box.exec()
        # Esc или крестик диалога не должны молча выбирать потерю результата:
        # всё, кроме явного «Закрыть без ожидания», означает ожидание.
        return box.clickedButton() is not close_button

    def _wait_for_active_tasks(self, tasks: tuple[str, ...]) -> bool:
        """Ждёт завершения фоновых задач, не блокируя событийный цикл.

        Показывается индикатор с кнопкой «Не ждать»: ожидание ограничено только
        самими операциями (у сетевых — их timeout), а не зависанием GUI.

        :return: ``True``, если все задачи завершились; ``False`` — пользователь
            отказался ждать.
        """
        dialog = QProgressDialog(self)
        dialog.setWindowTitle("Закрытие Git Visualizer")
        dialog.setLabelText(
            f"Завершение: {describe_active_tasks(tasks)}…"
        )
        dialog.setCancelButtonText("Не ждать")
        dialog.setRange(0, 0)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.show()

        loop = QEventLoop(self)
        timer = QTimer(self)
        timer.setInterval(_CLOSE_WAIT_POLL_MSEC)
        finished = False

        def check() -> None:
            nonlocal finished
            if not self._controller.wait_for_done(0):
                return
            if self._controller.active_task_names():
                # Пул свободен, но задача ещё не снята: ждём доставку finished.
                return
            finished = True
            loop.quit()

        timer.timeout.connect(check)
        dialog.canceled.connect(loop.quit)
        timer.start()
        check()
        if not finished:
            loop.exec()
        timer.stop()
        dialog.close()
        dialog.deleteLater()
        return finished

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        for name in ("diff_overlay", "commit_overlay"):
            overlay = getattr(self, name, None)
            if overlay is not None and overlay.isVisible():
                overlay.setGeometry(self.rect())

    def _connect_signals(self) -> None:
        self.start_screen.open_repository_requested.connect(self._choose_repository)
        self.start_screen.recent_repository_requested.connect(
            self._open_recent_repository
        )
        self.start_screen.settings_requested.connect(self._open_settings_dialog)
        self.workspace_page.refresh_requested.connect(self._refresh_history)
        self.workspace_page.close_repository_requested.connect(self._close_repository)
        self.workspace_page.fetch_requested.connect(self._on_fetch_requested)
        self.workspace_page.pull_requested.connect(self._on_pull_requested)
        self.workspace_page.push_requested.connect(self._on_push_requested)
        self.workspace_page.remote_settings_requested.connect(
            self._open_remote_settings_dialog
        )
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
        self.workspace_page.changes_discard_requested.connect(
            self._on_discard_requested
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
        self.workspace_page.older_history_requested.connect(
            self._on_load_older_history
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
        self._controller.older_history_loading.connect(
            self._on_older_history_loading
        )
        self._controller.older_history_loaded.connect(
            self._on_older_history_loaded
        )
        self._controller.older_history_failed.connect(
            self._on_older_history_failed
        )
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
        self._controller.remote_operation_started.connect(
            self._on_remote_operation_started
        )
        self._controller.remote_operation_succeeded.connect(
            self._on_remote_operation_succeeded
        )
        self._controller.remote_operation_failed.connect(
            self._on_remote_operation_failed
        )
        self._controller.remote_configuration_started.connect(
            self._on_remote_configuration_started
        )
        self._controller.remote_configuration_succeeded.connect(
            self._on_remote_configuration_succeeded
        )
        self._controller.remote_configuration_failed.connect(
            self._on_remote_configuration_failed
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

    def _open_recent_repository(self, path: str) -> None:
        """Открывает репозиторий из списка недавних, проверяя, что папка на месте.

        Перемещённый или удалённый репозиторий не должен приводить к падению:
        запись убирается из списка, показывается понятное сообщение, а
        приложение остаётся на стартовом экране.
        """
        if not _is_existing_directory(path):
            self._forget_recent_repository(path)
            self.statusBar().showMessage(
                "Репозиторий из списка недавних не найден."
            )
            self._show_notice(
                "Репозиторий недоступен",
                f"Папка не найдена: {path}\n\n"
                "Возможно, репозиторий перемещён или удалён. Запись удалена из "
                "списка недавних репозиториев.",
            )
            return
        self._controller.open_repository(path)

    def _remember_recent_repository(self, path: str) -> None:
        """Добавляет путь в недавние (первым) и сразу сохраняет на диск."""
        repositories = self._settings.add_recent_repository(path)
        self._settings.sync()
        self.start_screen.set_recent_repositories(repositories)

    def _forget_recent_repository(self, path: str) -> None:
        repositories = self._settings.remove_recent_repository(path)
        self._settings.sync()
        self.start_screen.set_recent_repositories(repositories)

    def _open_settings_dialog(self) -> None:
        """Открывает диалог настроек; изменения применяются и сохраняются."""
        dialog = SettingsDialog(history_limit=self._settings.history_limit(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_history_limit(dialog.history_limit())

    def _apply_history_limit(self, limit: int) -> None:
        """Сохраняет лимит загрузки коммитов и передаёт его контроллеру."""
        self._settings.set_history_limit(limit)
        self._settings.sync()
        self._controller.set_history_limit(limit)
        self.statusBar().showMessage(f"Лимит загрузки коммитов: {limit}.")

    # --- настройки remote ---

    def _open_remote_settings_dialog(self) -> None:
        """Открывает окно управления remote текущего репозитория."""
        if self._controller.repository is None:
            return
        existing = self._remote_settings_dialog
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        data = self.workspace_page.workspace_data
        if data is None:
            self.statusBar().showMessage(
                "Данные репозитория ещё загружаются: повторите через мгновение."
            )
            return

        dialog = RemoteSettingsDialog(remotes=data.remotes, parent=self)
        dialog.add_requested.connect(self._on_remote_add_requested)
        dialog.edit_requested.connect(self._on_remote_edit_requested)
        dialog.remove_requested.connect(self._on_remote_remove_requested)
        dialog.finished.connect(self._on_remote_settings_dialog_finished)
        self._remote_settings_dialog = dialog
        dialog.show()
        dialog.raise_()

    def _close_remote_settings_dialog(self) -> None:
        """Закрывает окно настроек remote (смена или закрытие репозитория)."""
        dialog = self._remote_settings_dialog
        self._remote_settings_dialog = None
        self._pending_remote_configuration_refresh = False
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()

    def _on_remote_settings_dialog_finished(self, _result: int) -> None:
        self._remote_settings_dialog = None

    def _on_remote_add_requested(self, name: str, url: str) -> None:
        if self._controller.repository is None:
            return
        if not self._controller.add_remote(name, url):
            self._announce_remote_configuration_busy()

    def _on_remote_edit_requested(
        self, name: str, new_name: str, url: str
    ) -> None:
        if self._controller.repository is None:
            return
        if not self._controller.edit_remote(
            name, new_name=new_name, url=url
        ):
            self._announce_remote_configuration_busy()

    def _on_remote_remove_requested(self, name: str) -> None:
        if self._controller.repository is None:
            return
        if not self._controller.remove_remote(name):
            self._announce_remote_configuration_busy()

    def _announce_remote_configuration_busy(self) -> None:
        message = "Другая операция Git уже выполняется…"
        dialog = self._remote_settings_dialog
        if dialog is not None:
            dialog.set_busy(False)
            dialog.show_status(message, error=True)
        self.statusBar().showMessage(message)

    def _on_remote_configuration_started(
        self, context: RemoteConfigurationContext
    ) -> None:
        self._pending_remote_configuration_refresh = False
        text = remote_configuration_progress_text(context)
        dialog = self._remote_settings_dialog
        if dialog is not None:
            dialog.set_busy(True, text)
        self.workspace_page.set_busy(True, text)
        self.statusBar().showMessage(text)

    def _on_remote_configuration_succeeded(
        self, result: RemoteConfigurationResult
    ) -> None:
        text = remote_configuration_result_text(result)
        dialog = self._remote_settings_dialog
        if dialog is not None:
            dialog.set_remotes(
                result.remotes,
                selected=result.new_name or result.remote_name,
            )
            dialog.show_status(text)
            # Кнопки вернутся после того, как контроллер перечитает репозиторий:
            # иначе можно запустить вторую правку параллельно с обновлением.
            self._pending_remote_configuration_refresh = True
        self.statusBar().showMessage(text)

    def _on_remote_configuration_failed(
        self, context: RemoteConfigurationContext, failure: TaskFailure
    ) -> None:
        self._pending_remote_configuration_refresh = False
        self.workspace_page.set_busy(False)
        title = remote_configuration_error_title(context)
        message = remote_configuration_user_message(
            failure.exception, context=context
        )
        dialog = self._remote_settings_dialog
        if dialog is not None:
            dialog.set_busy(False)
            dialog.show_status(f"{title}.", error=True)
        self.statusBar().showMessage(f"{title}.")
        self._show_error(title, failure.exception, message=message)

    # --- положение графа ---

    def _apply_preferred_graph_view(
        self, previous: GraphViewState | None = None
    ) -> None:
        """Восстанавливает положение графа после загрузки истории.

        ``previous`` — положение до перезагрузки данных в текущей сессии: оно
        важнее сохранённого при запуске, чтобы обновление истории, коммит или
        смена ветки не сбрасывали вид. При открытии нового репозитория
        ``previous`` нет, и применяется положение из прошлого запуска.
        """
        state = previous or self._preferred_graph_view
        if state is None:
            return
        if not self.workspace_page.graph_view.restore_view_state(state):
            return
        self._preferred_graph_view = self.workspace_page.graph_view.view_state()

    def _remember_graph_view(self) -> None:
        """Сохраняет положение графа текущего репозитория; без графа — ничего."""
        repository_path = self._current_repository_path
        if repository_path is None:
            return
        view_state = self.workspace_page.graph_view.view_state()
        if view_state is None:
            return
        self._preferred_graph_view = view_state
        self._settings.set_graph_view_state(repository_path, view_state)

    def _save_settings(self) -> None:
        """Сохраняет геометрию, панели и точное положение графа при закрытии."""
        self._settings.set_window_geometry(self.saveGeometry())
        main_state, body_state = self.workspace_page.panel_state()
        self._settings.set_main_splitter_state(main_state)
        self._settings.set_body_splitter_state(body_state)
        # Положение снимается при закрытии: панорамирование не испускает сигналов,
        # поэтому отдельное отслеживание каждого сдвига не нужно (и было бы шумным).
        self._remember_graph_view()
        self._settings.sync()

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
        self._close_remote_settings_dialog()
        self._pending_worktree_diff_refresh = None
        self._changes_operation = None
        self._reset_branch_operation()
        self._pending_workspace_status = None
        self._reset_remote_operation()
        # Запоминаем вид до очистки: пользователь мог закрыть/сменить репозиторий,
        # а затем выйти из приложения — положение графа не должно потеряться.
        self._remember_graph_view()
        self._settings.sync()
        self._current_repository_path = None
        self._preferred_graph_view = None
        self.workspace_page.clear()
        self.stack.setCurrentWidget(self.start_screen)
        self.start_screen.set_open_enabled(False)
        self.statusBar().showMessage(f"Открытие репозитория: {path}…")

    def _on_repository_opened(self, repository: Repository) -> None:
        self._current_repository_path = str(repository.path)
        self.setWindowTitle(f"{_WINDOW_TITLE} — {repository.name}")
        self.workspace_page.set_repository(repository)
        self.stack.setCurrentWidget(self.workspace_page)
        self.start_screen.set_open_enabled(True)
        # Недавние обновляются первыми: это заодно чистит положения графов,
        # выпавших из списка, после чего читаем положение текущего репозитория.
        self._remember_recent_repository(self._current_repository_path)
        self._preferred_graph_view = self._settings.graph_view_state(
            self._current_repository_path
        )
        self.statusBar().showMessage("Загрузка истории…")

    def _on_open_failed(self, failure: TaskFailure) -> None:
        self.stack.setCurrentWidget(self.start_screen)
        self.start_screen.set_open_enabled(True)
        self.statusBar().showMessage("Не удалось открыть репозиторий.")
        self._show_error("Не удалось открыть репозиторий", failure.exception)

    def _on_repository_closed(self) -> None:
        self._hide_diff_overlay()
        self.commit_overlay.hide_overlay()
        self._close_remote_settings_dialog()
        self._pending_worktree_diff_refresh = None
        self._changes_operation = None
        self._reset_branch_operation()
        self._pending_workspace_status = None
        self._reset_remote_operation()
        # Закрытие репозитория не должно стирать положение графа: при следующем
        # открытии картинка восстанавливается такой же, как была.
        self._remember_graph_view()
        self._settings.sync()
        self._current_repository_path = None
        self._preferred_graph_view = None
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
        # Положение графа до перезагрузки: сохраняем его, чтобы обновление истории
        # или смена HEAD не сбрасывали вид на «вписать заново».
        previous_view = self.workspace_page.graph_view.view_state()
        self.workspace_page.set_workspace_data(data)
        # Полная загрузка могла обогнать незавершённую догрузку страницы
        # (контроллер отбрасывает её результат по сессии): снимаем её индикацию.
        self.workspace_page.set_history_loading(False)
        self.workspace_page.set_busy(False)
        if self._remote_settings_dialog is not None:
            self._remote_settings_dialog.set_remotes(data.remotes)
        if self._pending_remote_configuration_refresh:
            self._pending_remote_configuration_refresh = False
            if self._remote_settings_dialog is not None:
                self._remote_settings_dialog.set_busy(False)
        self._apply_preferred_graph_view(previous_view)
        # Статус рабочей директории — отдельный запрос: он меняется независимо от HEAD.
        self._controller.load_changes()
        if self._pending_workspace_status is not None:
            self.statusBar().showMessage(self._pending_workspace_status)
            self._pending_workspace_status = None
        elif data.commits:
            message = (
                f"Коммитов загружено: {len(data.commits)}. "
                f"Веток: {len(data.branches)}."
            )
            if data.has_more_history:
                # Пользователь должен знать, что граф — не вся история (FR-HIST-07).
                message = (
                    f"Коммитов загружено: {len(data.commits)} — показаны последние, "
                    "есть более старая история. "
                    f"Веток: {len(data.branches)}."
                )
            self.statusBar().showMessage(message)
        else:
            self.statusBar().showMessage(
                "В репозитории пока нет коммитов. Создайте первый коммит в Git, "
                "затем обновите историю."
            )
        self._apply_pending_remote_result()

    def _on_load_failed(self, failure: TaskFailure) -> None:
        self._pending_workspace_status = None
        self._reset_branch_operation()
        if self._pending_remote_configuration_refresh:
            self._pending_remote_configuration_refresh = False
            dialog = self._remote_settings_dialog
            if dialog is not None:
                dialog.set_busy(False)
                dialog.show_status(
                    "Не удалось обновить список remote.", error=True
                )
        self.workspace_page.set_busy(False)
        self.workspace_page.set_history_loading(False)
        self.statusBar().showMessage("Не удалось загрузить историю.")
        self._show_error("Не удалось загрузить историю", failure.exception)

    def _on_load_older_history(self) -> None:
        """Догружает страницу более старой истории по кнопке уведомления."""
        if self._controller.repository is None:
            return
        if not self._controller.load_more_history():
            self.statusBar().showMessage(
                "История уже загружается или загружена полностью."
            )

    def _on_older_history_loading(self) -> None:
        self.workspace_page.set_history_loading(True)
        self.statusBar().showMessage("Загрузка более старой истории…")

    def _on_older_history_loaded(self, data: WorkspaceData) -> None:
        """Показывает догруженную историю, сохраняя выбор и вид графа.

        Новые коммиты дописываются вниз, поэтому координаты уже загруженных
        узлов не меняются: восстанавливается только масштаб и положение окна.
        """
        previous_view = self.workspace_page.graph_view.view_state()
        loaded_before = self._loaded_commit_count()
        self.workspace_page.set_workspace_data(data, preserve_selection=True)
        self.workspace_page.set_history_loading(False)
        self._apply_preferred_graph_view(previous_view)
        added = len(data.commits) - loaded_before
        if added == 0:
            # Вся страница уже была в графе: история изменилась снаружи, и окно
            # skip сдвинулось. Догружать нечего — нужен refresh.
            message = (
                "Новых коммитов не загружено: история изменилась снаружи. "
                "Обновите историю."
            )
        elif data.has_more_history:
            message = (
                f"Загружено более старой истории: {added}. "
                f"Всего коммитов: {len(data.commits)}."
            )
        else:
            message = (
                f"История загружена полностью: {len(data.commits)} коммитов."
            )
        self.statusBar().showMessage(message)

    def _on_older_history_failed(self, failure: TaskFailure) -> None:
        self.workspace_page.set_history_loading(False)
        self.statusBar().showMessage("Не удалось загрузить более старую историю.")
        self._show_error(
            "Не удалось загрузить более старую историю", failure.exception
        )

    def _loaded_commit_count(self) -> int:
        data = self.workspace_page.workspace_data
        return len(data.commits) if data is not None else 0

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

    def _on_discard_requested(self, changed_file: ChangedFile) -> None:
        """Подтверждает и запускает необратимую отмену изменений выбранного файла."""
        repository = self._controller.repository
        if repository is None or not changed_file.can_discard:
            return
        if not self._confirm_discard(
            changed_file, has_head=not repository.is_unborn
        ):
            return

        # Отменённый файл исчезнет из списков (или перестанет быть отслеживаемым):
        # открытый diff и запомненное обновление относятся к прежнему состоянию.
        self._hide_diff_overlay()
        self._pending_worktree_diff_refresh = None
        self.workspace_page.changes_panel.clear_selection()
        self._changes_operation = "discard-file"
        if not self._controller.discard_file(changed_file):
            self._changes_operation = None
            self.statusBar().showMessage(
                "Другая операция с индексом уже выполняется…"
            )

    def _confirm_discard(
        self, changed_file: ChangedFile, *, has_head: bool
    ) -> bool:
        """Показывает предупреждение о необратимости отмены изменений."""
        confirmation = discard_confirmation(
            changed_file, has_head=has_head
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(confirmation.title)
        box.setText(confirmation.question)
        box.setInformativeText(confirmation.warning)
        box.setDetailedText(confirmation.details)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        # По умолчанию — отмена: потерять изменения случайным Enter нельзя.
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

    # --- сетевые операции: Fetch, Pull, Push ---

    def _on_fetch_requested(self) -> None:
        """Fetch выбранного в верхней панели remote."""
        if self._controller.repository is None:
            return
        remote = self.workspace_page.top_bar.selected_remote
        if remote is None:
            self._show_notice(
                "Fetch невозможен",
                "В репозитории не настроен ни один remote. Добавьте remote в Git "
                "и обновите данные репозитория.",
            )
            return
        if not self._controller.fetch(remote):
            self._announce_remote_busy()

    def _on_pull_requested(self) -> None:
        """Pull текущей ветки только fast-forward (--ff-only)."""
        branch = self._current_branch()
        if not self._check_remote_operation_ready(branch, action="Pull"):
            return
        assert branch is not None
        if branch.upstream is None:
            message = (
                f"У ветки «{branch.name}» не настроен upstream. Pull только "
                "fast-forward невозможен. Настройте upstream (например, выполните "
                "первый Push с параметром -u) и повторите."
            )
            self.workspace_page.top_bar.show_remote_result(
                "Pull невозможен: upstream не настроен.", error=True, details=message
            )
            self._show_notice("Pull невозможен", message)
            return
        if not self._controller.pull():
            self._announce_remote_busy()

    def _on_push_requested(self) -> None:
        """Push текущей ветки в upstream или первый Push с явным выбором remote."""
        branch = self._current_branch()
        if not self._check_remote_operation_ready(branch, action="Push"):
            return
        assert branch is not None
        if branch.upstream is None:
            remote = self.workspace_page.top_bar.selected_remote
            if remote is None:
                self._show_notice(
                    "Push невозможен",
                    "В репозитории не настроен ни один remote. Добавьте remote, "
                    "чтобы отправить ветку.",
                )
                return
            if not self._confirm_initial_push(branch.name, remote):
                return
            if not self._controller.push_initial(remote):
                self._announce_remote_busy()
            return
        if not self._controller.push():
            self._announce_remote_busy()

    def _current_branch(self) -> Branch | None:
        """Текущая локальная ветка по загруженным данным."""
        data = self.workspace_page.workspace_data
        if data is None:
            return None
        return next(
            (
                branch
                for branch in data.branches
                if branch.is_current and not branch.is_remote
            ),
            None,
        )

    def _check_remote_operation_ready(
        self, branch: Branch | None, *, action: str
    ) -> bool:
        """Общие проверки перед сетевой операцией, не запуская Git."""
        data = self.workspace_page.workspace_data
        if data is None or self._controller.repository is None:
            return False
        if data.repository.is_unborn:
            self._show_notice(
                f"{action} невозможен",
                "В репозитории ещё нет коммитов: отправлять и получать нечего.",
            )
            return False
        if data.repository.is_detached or branch is None:
            self._show_notice(
                f"{action} невозможен",
                "HEAD не указывает на локальную ветку (detached HEAD). "
                "Переключитесь на локальную ветку и повторите.",
            )
            return False
        if not data.remotes:
            self._show_notice(
                f"{action} невозможен",
                "В репозитории не настроен ни один remote. Добавьте remote в Git "
                "и обновите данные репозитория.",
            )
            return False
        return True

    def _announce_remote_busy(self) -> None:
        self.statusBar().showMessage(
            "Другая сетевая операция уже выполняется…"
        )

    def _confirm_initial_push(self, branch_name: str, remote: str) -> bool:
        """Спрашивает подтверждение первого Push с настройкой upstream (-u)."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Первый Push")
        box.setText(f"У ветки «{branch_name}» не настроен upstream.")
        box.setInformativeText(
            f"Выполнить первый Push в remote «{remote}» с параметром -u?\n\n"
            f"git push --set-upstream {remote} {branch_name}\n\n"
            "После этого Pull и Push будут работать без аргументов. Force push "
            "не выполняется."
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_remote_operation_started(self, context: RemoteOperationContext) -> None:
        self._remote_context = context
        self._pending_remote_result = None
        # Pull меняет рабочую директорию, Fetch — remote-tracking refs: открытый diff
        # и незавершённый ввод коммита относятся к прежнему состоянию.
        self._hide_diff_overlay()
        self.commit_overlay.hide_overlay()
        self._pending_worktree_diff_refresh = None
        self._changes_operation = None
        text = remote_progress_text(context)
        self.workspace_page.set_busy(True, text)
        self.workspace_page.changes_panel.set_busy(True)
        self.workspace_page.top_bar.show_remote_progress(text)
        self.statusBar().showMessage(text)

    def _on_remote_operation_succeeded(
        self, result: RemoteOperationResult
    ) -> None:
        # Контроллер сразу перечитывает репозиторий; результат показываем после
        # загрузки, чтобы сообщение не затёрлось счётчиками коммитов.
        self._pending_remote_result = result

    def _on_remote_operation_failed(
        self, context: RemoteOperationContext, failure: TaskFailure
    ) -> None:
        self._remote_context = None
        self._pending_remote_result = None
        self.workspace_page.set_busy(False)
        self.workspace_page.changes_panel.set_busy(False)
        title = remote_error_title(context.operation)
        branch = self._current_branch()
        message = remote_user_message(
            failure.exception,
            operation=context.operation,
            remote_name=context.remote,
            remote_url=self._remote_url(
                context.remote, push=context.operation == "push"
            ),
            branch_name=branch.name if branch is not None else None,
            upstream=branch.upstream if branch is not None else None,
        )
        self.statusBar().showMessage(f"{title}.")
        self.workspace_page.top_bar.show_remote_result(
            f"{title}.", details=message, error=True
        )
        self._show_error(title, failure.exception, message=message)

    def _apply_pending_remote_result(self) -> None:
        """Показывает итог сетевой операции после перезагрузки данных."""
        result = self._pending_remote_result
        self._pending_remote_result = None
        self._remote_context = None
        if result is None:
            return
        text = remote_result_text(result)
        self.workspace_page.top_bar.show_remote_result(text, details=result.output)
        self.statusBar().showMessage(text)

    def _remote_url(self, remote_name: str | None, *, push: bool) -> str | None:
        data = self.workspace_page.workspace_data
        if data is None or remote_name is None:
            return None
        remote = next(
            (item for item in data.remotes if item.name == remote_name), None
        )
        if remote is None:
            return None
        if push:
            return remote.push_url or remote.fetch_url
        return remote.fetch_url or remote.push_url

    def _reset_remote_operation(self) -> None:
        self._remote_context = None
        self._pending_remote_result = None

    def _show_notice(self, title: str, message: str) -> None:
        """Информационное сообщение: действие не запущено."""
        if self._closing:
            # Окно закрывается: модальный диалог заблокировал бы завершение.
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(title)
        box.setText(message)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

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
        if self._closing:
            # Ошибка фоновой операции при закрытии: диалог не открывается, иначе
            # он остановил бы завершение приложения. Технические подробности
            # остаются в TaskFailure до конца жизни задачи.
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText(user_message(error) if message is None else message)
        box.setDetailedText(technical_details(error))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()


def _is_existing_directory(path: str) -> bool:
    """Существует ли путь и является ли он каталогом (без исключений ОС)."""
    try:
        return Path(path).is_dir()
    except (OSError, ValueError):
        # Некорректный для ОС путь — такой же несуществующий репозиторий.
        return False
