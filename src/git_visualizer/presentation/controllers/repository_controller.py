"""Координация Application Services для главного окна.

Контроллер выполняет открытие репозитория, проверку Git и загрузку данных истории
в фоновых задачах ``TaskRunner``. Он не знает о виджетах: результат сообщается
сигналами, которые MainWindow связывает с интерфейсом. Бизнес-логика остаётся
в Application Services.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import QObject, Signal

from git_visualizer.application.services.branch_service import BranchService, build_ref_labels
from git_visualizer.application.services.diff_service import DiffService
from git_visualizer.application.services.git_environment_service import (
    GitEnvironmentService,
)
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.application.services.history_service import HistoryService
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.domain.models import (
    Branch,
    Commit,
    Diff,
    FileChange,
    GraphLayout,
    RefLabel,
    Remote,
    Repository,
)
from git_visualizer.presentation.workers.task_runner import TaskFailure, TaskRunner


@dataclass(frozen=True, kw_only=True)
class WorkspaceData:
    """Всё, что нужно основному окну для отображения открытого репозитория."""

    repository: Repository
    commits: tuple[Commit, ...]
    branches: tuple[Branch, ...]
    remotes: tuple[Remote, ...]
    ref_labels: Mapping[str, tuple[RefLabel, ...]]
    layout: GraphLayout


class RepositoryController(QObject):
    """Фоновые операции открытия и обновления репозитория.

    Один экземпляр на главное окно. Ожидаемые отказы приходят сигналами
    ``open_failed``/``load_failed`` с ``TaskFailure`` (исходное исключение —
    ``TaskFailure.exception``), поэтому Presentation переводит их в русский текст.
    """

    git_environment_ready = Signal(object)  # GitEnvironment
    git_environment_failed = Signal(object)  # TaskFailure

    repository_opening = Signal(str)
    repository_opened = Signal(object)  # Repository
    open_failed = Signal(object)  # TaskFailure

    history_loading = Signal()
    repository_loaded = Signal(object)  # WorkspaceData
    load_failed = Signal(object)  # TaskFailure

    commit_files_loading = Signal(str)  # commit hash
    commit_files_loaded = Signal(str, object)  # commit hash, tuple[FileChange, ...]
    commit_files_failed = Signal(str, object)  # commit hash, TaskFailure

    file_diff_loading = Signal(str, str)  # commit hash, path
    file_diff_loaded = Signal(str, str, object)  # commit hash, path, Diff
    file_diff_failed = Signal(str, str, object)  # commit hash, path, TaskFailure

    repository_closed = Signal()

    def __init__(
        self,
        *,
        repository_service: RepositoryService,
        history_service: HistoryService,
        branch_service: BranchService,
        graph_service: GraphService,
        diff_service: DiffService,
        git_environment_service: GitEnvironmentService,
        task_runner: TaskRunner | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository_service = repository_service
        self._history_service = history_service
        self._branch_service = branch_service
        self._graph_service = graph_service
        self._diff_service = diff_service
        self._git_environment_service = git_environment_service
        self._runner = task_runner or TaskRunner(self)

        self._session = 0
        self._repository: Repository | None = None
        self._workspace: WorkspaceData | None = None

    @property
    def repository(self) -> Repository | None:
        return self._repository

    @property
    def workspace(self) -> WorkspaceData | None:
        return self._workspace

    @property
    def is_loading(self) -> bool:
        """Идёт ли загрузка данных текущего открытого репозитория."""
        return self._runner.is_running(self._load_key())

    def check_git_environment(self) -> None:
        """Проверяет наличие и версию Git в фоне."""
        task = self._runner.submit(
            self._git_environment_service.check,
            name="git-environment",
            key="git-environment",
        )
        if task is None:
            return
        task.succeeded.connect(self.git_environment_ready)
        task.failed.connect(self.git_environment_failed)

    def open_repository(self, path: str) -> None:
        """Открывает репозиторий по выбранному пути и загружает его данные."""
        self._session += 1
        session = self._session
        self._repository = None
        self._workspace = None
        self.repository_opening.emit(path)

        task = self._runner.submit(
            partial(self._repository_service.open_repository, path),
            name="open-repository",
            key=f"open:{session}",
        )
        if task is None:
            return
        task.succeeded.connect(partial(self._handle_repository_opened, session))
        task.failed.connect(partial(self._handle_open_failed, session))

    def refresh(self) -> bool:
        """Запускает повторную загрузку истории и веток.

        :return: ``True``, если задача поставлена; ``False``, если репозиторий
            не открыт или загрузка уже идёт.
        """
        return self._load_workspace()

    def close_repository(self) -> None:
        """Закрывает текущий репозиторий; результаты старых задач игнорируются."""
        self._session += 1
        self._repository = None
        self._workspace = None
        self.repository_closed.emit()

    def load_commit_files(self, commit_hash: str) -> bool:
        """Запускает фоновую загрузку файлов выбранного коммита.

        :return: ``True``, если задача поставлена. ``False`` — коммит не найден,
            репозиторий закрыт или такая загрузка уже идёт.
        """
        repository = self._repository
        commit = self._find_commit(commit_hash)
        if repository is None or commit is None:
            return False

        session = self._session
        task = self._runner.submit(
            partial(self._load_commit_files, repository, commit),
            name="commit-files",
            key=f"commit-files:{session}:{commit_hash}",
        )
        if task is None:
            return False
        self.commit_files_loading.emit(commit_hash)
        task.succeeded.connect(
            partial(self._handle_commit_files_loaded, session, commit_hash)
        )
        task.failed.connect(
            partial(self._handle_commit_files_failed, session, commit_hash)
        )
        return True

    def load_file_diff(self, commit_hash: str, file_change: FileChange) -> bool:
        """Запускает фоновую загрузку diff выбранного файла.

        :return: ``True``, если задача поставлена; ``False`` — коммит/файл не найден,
            репозиторий закрыт или diff этого файла уже загружается.
        """
        repository = self._repository
        commit = self._find_commit(commit_hash)
        if repository is None or commit is None:
            return False

        session = self._session
        task = self._runner.submit(
            partial(self._load_file_diff, repository, commit, file_change),
            name="file-diff",
            key=f"file-diff:{session}:{commit_hash}:{file_change.path}",
        )
        if task is None:
            return False
        self.file_diff_loading.emit(commit_hash, file_change.path)
        task.succeeded.connect(
            partial(
                self._handle_file_diff_loaded,
                session,
                commit_hash,
                file_change.path,
            )
        )
        task.failed.connect(
            partial(
                self._handle_file_diff_failed,
                session,
                commit_hash,
                file_change.path,
            )
        )
        return True

    def wait_for_done(self, msecs: int = -1) -> bool:
        """Дожидается завершения фоновых задач (используется при закрытии окна)."""
        return self._runner.wait_for_done(msecs)

    def _load_workspace(self) -> bool:
        repository = self._repository
        if repository is None:
            return False
        session = self._session
        task = self._runner.submit(
            partial(self._load_data, repository),
            name="load-workspace",
            key=self._load_key(),
        )
        if task is None:
            return False
        self.history_loading.emit()
        task.succeeded.connect(partial(self._handle_workspace_loaded, session))
        task.failed.connect(partial(self._handle_load_failed, session))
        return True

    def _load_data(self, repository: Repository) -> WorkspaceData:
        """Worker-функция: выполняется вне GUI-потока, без обращения к виджетам."""
        commits = self._history_service.get_history(repository)
        branches = self._branch_service.get_branches(repository)
        remotes = self._repository_service.get_remotes(repository)
        layout = self._graph_service.build_layout(commits)
        ref_labels = build_ref_labels(repository, branches)
        return WorkspaceData(
            repository=repository,
            commits=commits,
            branches=branches,
            remotes=remotes,
            ref_labels=ref_labels,
            layout=layout,
        )

    def _handle_repository_opened(
        self, session: int, repository: Repository
    ) -> None:
        if session != self._session:
            return
        self._repository = repository
        self.repository_opened.emit(repository)
        self._load_workspace()

    def _handle_open_failed(self, session: int, failure: TaskFailure) -> None:
        if session != self._session:
            return
        self._repository = None
        self.open_failed.emit(failure)

    def _handle_workspace_loaded(self, session: int, data: WorkspaceData) -> None:
        if session != self._session:
            return
        self._workspace = data
        self.repository_loaded.emit(data)

    def _handle_load_failed(self, session: int, failure: TaskFailure) -> None:
        if session != self._session:
            return
        self.load_failed.emit(failure)

    def _load_commit_files(
        self, repository: Repository, commit: Commit
    ) -> tuple[FileChange, ...]:
        """Worker-функция списка файлов; без обращения к Qt-виджетам."""
        return self._diff_service.get_commit_files(repository, commit)

    def _load_file_diff(
        self, repository: Repository, commit: Commit, file_change: FileChange
    ) -> Diff:
        """Worker-функция diff одного файла; без обращения к Qt-виджетам."""
        return self._diff_service.get_file_diff(repository, commit, file_change)

    def _handle_commit_files_loaded(
        self, session: int, commit_hash: str, files: tuple[FileChange, ...]
    ) -> None:
        if session != self._session:
            return
        self.commit_files_loaded.emit(commit_hash, files)

    def _handle_commit_files_failed(
        self, session: int, commit_hash: str, failure: TaskFailure
    ) -> None:
        if session != self._session:
            return
        self.commit_files_failed.emit(commit_hash, failure)

    def _handle_file_diff_loaded(
        self, session: int, commit_hash: str, path: str, diff: Diff
    ) -> None:
        if session != self._session:
            return
        self.file_diff_loaded.emit(commit_hash, path, diff)

    def _handle_file_diff_failed(
        self, session: int, commit_hash: str, path: str, failure: TaskFailure
    ) -> None:
        if session != self._session:
            return
        self.file_diff_failed.emit(commit_hash, path, failure)

    def _find_commit(self, commit_hash: str) -> Commit | None:
        if self._workspace is None:
            return None
        return next(
            (
                commit
                for commit in self._workspace.commits
                if commit.hash == commit_hash
            ),
            None,
        )

    def _load_key(self) -> str:
        return f"load:{self._session}"
