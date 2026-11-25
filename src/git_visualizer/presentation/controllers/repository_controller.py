"""Координация Application Services для главного окна.

Контроллер выполняет открытие репозитория, проверку Git, загрузку данных истории,
состояния рабочей директории, diff файла, создание коммита и операции с ветками в
фоновых задачах ``TaskRunner``. Он не знает о виджетах: результат сообщается
сигналами, которые MainWindow связывает с интерфейсом. Бизнес-логика остаётся в
Application Services.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from git_visualizer.application.services.branch_service import BranchService, build_ref_labels
from git_visualizer.application.services.changes_service import ChangesService
from git_visualizer.application.services.commit_service import CommitService
from git_visualizer.application.services.diff_service import DiffService
from git_visualizer.application.services.git_environment_service import (
    GitEnvironmentService,
)
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.application.services.history_service import (
    DEFAULT_HISTORY_LIMIT,
    HistoryService,
)
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.application.services.remote_service import (
    RemoteOperationContext,
    RemoteOperationResult,
    RemoteService,
)
from git_visualizer.domain.models import (
    Branch,
    ChangedFile,
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

    changes_loading = Signal()
    changes_loaded = Signal(object)  # tuple[ChangedFile, ...]
    changes_failed = Signal(object)  # TaskFailure

    changes_operation_started = Signal()
    changes_operation_succeeded = Signal()
    changes_operation_failed = Signal(object)  # TaskFailure

    commit_started = Signal()
    commit_succeeded = Signal()
    commit_failed = Signal(object)  # TaskFailure

    branch_operation_started = Signal(str, str)  # операция, имя ветки
    branch_operation_succeeded = Signal(str, str)  # операция, имя ветки
    branch_operation_failed = Signal(str, str, object)  # операция, имя, TaskFailure

    remote_operation_started = Signal(object)  # RemoteOperationContext
    remote_operation_succeeded = Signal(object)  # RemoteOperationResult
    remote_operation_failed = Signal(object, object)  # контекст, TaskFailure

    worktree_diff_loading = Signal(str, bool)  # path, staged
    worktree_diff_loaded = Signal(str, bool, object)  # path, staged, Diff
    worktree_diff_failed = Signal(str, bool, object)  # path, staged, TaskFailure

    repository_closed = Signal()

    def __init__(
        self,
        *,
        repository_service: RepositoryService,
        history_service: HistoryService,
        branch_service: BranchService,
        graph_service: GraphService,
        diff_service: DiffService,
        changes_service: ChangesService,
        commit_service: CommitService,
        remote_service: RemoteService,
        git_environment_service: GitEnvironmentService,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        task_runner: TaskRunner | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if history_limit < 1:
            raise ValueError(f"History limit must be positive, got {history_limit}")
        self._repository_service = repository_service
        self._history_service = history_service
        self._branch_service = branch_service
        self._graph_service = graph_service
        self._diff_service = diff_service
        self._changes_service = changes_service
        self._commit_service = commit_service
        self._remote_service = remote_service
        self._git_environment_service = git_environment_service
        self._history_limit = history_limit
        self._runner = task_runner or TaskRunner(self)

        self._session = 0
        self._repository: Repository | None = None
        self._workspace: WorkspaceData | None = None
        self._changes: tuple[ChangedFile, ...] = ()

    @property
    def repository(self) -> Repository | None:
        return self._repository

    @property
    def workspace(self) -> WorkspaceData | None:
        return self._workspace

    @property
    def changes(self) -> tuple[ChangedFile, ...]:
        """Последний загруженный статус рабочей директории."""
        return self._changes

    @property
    def is_loading(self) -> bool:
        """Идёт ли загрузка данных текущего открытого репозитория."""
        return self._runner.is_running(self._load_key())

    @property
    def history_limit(self) -> int:
        """Лимит коммитов, который применяется к следующим загрузкам истории."""
        return self._history_limit

    def set_history_limit(self, limit: int) -> None:
        """Меняет лимит загрузки коммитов (пользовательская настройка).

        Уже загруженная история не перезагружается: новое значение действует при
        следующем открытии репозитория или ручном обновлении.

        :raises ValueError: ``limit`` меньше 1.
        """
        if limit < 1:
            raise ValueError(f"History limit must be positive, got {limit}")
        self._history_limit = limit

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
        self._changes = ()
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
        self._changes = ()
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

    def load_changes(self) -> bool:
        """Запускает фоновое обновление состояния рабочей директории.

        :return: ``True``, если задача поставлена; ``False`` — репозиторий не
            открыт или статус уже загружается.
        """
        repository = self._repository
        if repository is None:
            return False

        session = self._session
        task = self._runner.submit(
            partial(self._changes_service.get_status, repository),
            name="changes",
            key=f"changes:{session}",
        )
        if task is None:
            return False
        self.changes_loading.emit()
        task.succeeded.connect(partial(self._handle_changes_loaded, session))
        task.failed.connect(partial(self._handle_changes_failed, session))
        return True

    def stage_file(self, changed_file: ChangedFile) -> bool:
        """Добавляет выбранный файл в индекс в фоновой задаче.

        :return: ``True``, если задача поставлена; ``False`` — репозиторий не
            открыт или другая операция с индексом уже выполняется.
        """
        return self._submit_changes_operation(
            "stage-file",
            self._changes_service.stage_file,
            changed_file,
        )

    def unstage_file(self, changed_file: ChangedFile) -> bool:
        """Убирает выбранный файл из индекса в фоновой задаче."""
        return self._submit_changes_operation(
            "unstage-file",
            self._changes_service.unstage_file,
            changed_file,
        )

    def stage_all(self) -> bool:
        """Добавляет в индекс все изменения в фоновой задаче."""
        return self._submit_changes_operation(
            "stage-all", self._changes_service.stage_all
        )

    def unstage_all(self) -> bool:
        """Убирает из индекса все файлы в фоновой задаче."""
        return self._submit_changes_operation(
            "unstage-all", self._changes_service.unstage_all
        )

    def discard_file(self, changed_file: ChangedFile) -> bool:
        """Отменяет незакоммиченные изменения выбранного файла в фоновой задаче.

        Операция необратима; подтверждение запрашивает MainWindow. Делит ключ с
        stage/unstage, коммитом и сменой HEAD, поэтому не выполняется параллельно
        с ними. После успеха или ошибки статус запрашивается заново.

        :return: ``True``, если задача поставлена; ``False`` — репозиторий не
            открыт или другая операция уже выполняется.
        """
        return self._submit_changes_operation(
            "discard-file", self._changes_service.discard_file, changed_file
        )

    def commit(self, message: str) -> bool:
        """Создаёт коммит из staged changes в фоновой задаче.

        Используется тот же ключ, что у операций с индексом: коммит и stage/unstage
        не должны выполняться одновременно — обе операции меняют индекс, а коммит
        дополнительно создаёт объект и сдвигает HEAD. После успеха контроллер
        переоткрывает репозиторий (снимок ``Repository`` описывает HEAD на момент
        открытия) и заново загружает историю, граф, ветки и refs; статус рабочей
        директории обновляет MainWindow по сигналу ``repository_loaded``.

        :return: ``True``, если задача поставлена; ``False`` — репозиторий не открыт
            или другая операция с индексом уже выполняется.
        """
        repository = self._repository
        if repository is None:
            return False

        session = self._session
        task = self._runner.submit(
            partial(self._commit_service.commit, repository, message),
            name="commit",
            key=f"changes-operation:{session}",
        )
        if task is None:
            return False
        self.commit_started.emit()
        task.succeeded.connect(partial(self._handle_commit_succeeded, session))
        task.failed.connect(partial(self._handle_commit_failed, session))
        return True

    def create_branch(self, name: str, *, start_point: str | None = None) -> bool:
        """Создаёт локальную ветку в фоновой задаче.

        ``start_point=None`` — от текущего HEAD, иначе от выбранного коммита.
        Делит ключ с операциями с индексом и коммитом: смена HEAD/рабочей
        директории не должна происходить параллельно с ними. После успеха
        контроллер переоткрывает репозиторий и заново загружает историю, граф,
        ветки/refs и remote; Changes обновляет MainWindow по ``repository_loaded``.

        :return: ``True``, если задача поставлена; ``False`` — репозиторий не
            открыт или другая операция уже выполняется.
        """
        repository = self._repository
        if repository is None:
            return False
        return self._submit_branch_operation(
            "create",
            name,
            partial(
                self._branch_service.create_branch,
                repository,
                name,
                start_point=start_point,
            ),
        )

    def switch_branch(self, name: str) -> bool:
        """Переключается на существующую локальную ветку в фоновой задаче.

        :return: ``True``, если задача поставлена; ``False`` — репозиторий не
            открыт или другая операция уже выполняется.
        """
        repository = self._repository
        if repository is None:
            return False
        return self._submit_branch_operation(
            "switch",
            name,
            partial(self._branch_service.switch_branch, repository, name),
        )

    def fetch(self, remote_name: str) -> bool:
        """Запускает Fetch выбранного remote в фоновой задаче.

        :return: ``True``, если задача поставлена; ``False`` — репозиторий не
            открыт или другая сетевая операция уже выполняется.
        """
        repository = self._repository
        if repository is None:
            return False
        return self._submit_remote_operation(
            RemoteOperationContext(
                operation="fetch",
                remote=remote_name,
                branch=repository.current_branch,
            ),
            partial(self._remote_service.fetch, repository, remote_name),
        )

    def pull(self) -> bool:
        """Запускает Pull текущей ветки только с ``--ff-only`` в фоновой задаче.

        :return: ``True``, если задача поставлена; ``False`` — репозиторий не
            открыт или другая сетевая операция уже выполняется.
        """
        repository = self._repository
        if repository is None:
            return False
        return self._submit_remote_operation(
            RemoteOperationContext(
                operation="pull", branch=repository.current_branch
            ),
            partial(self._remote_service.pull, repository),
        )

    def push(self) -> bool:
        """Запускает Push текущей ветки в настроенный upstream в фоновой задаче.

        :return: ``True``, если задача поставлена; ``False`` — репозиторий не
            открыт или другая сетевая операция уже выполняется.
        """
        repository = self._repository
        if repository is None:
            return False
        return self._submit_remote_operation(
            RemoteOperationContext(
                operation="push", branch=repository.current_branch
            ),
            partial(self._remote_service.push, repository),
        )

    def push_initial(self, remote_name: str) -> bool:
        """Запускает первый Push с ``-u`` и явным remote в фоновой задаче.

        Вызывается только после явного подтверждения пользователя: когда upstream
        отсутствует, приложение не выбирает remote молча.

        :return: ``True``, если задача поставлена; ``False`` — репозиторий не
            открыт или другая сетевая операция уже выполняется.
        """
        repository = self._repository
        if repository is None:
            return False
        return self._submit_remote_operation(
            RemoteOperationContext(
                operation="push",
                remote=remote_name,
                branch=repository.current_branch,
            ),
            partial(self._remote_service.push_initial, repository, remote_name),
        )

    def _submit_remote_operation(
        self,
        context: RemoteOperationContext,
        work: Callable[[], object],
    ) -> bool:
        """Запускает одну сетевую операцию; повторные запуски блокируются ключом.

        Ключ общий с операциями с индексом, коммитом и ветками: Fetch мог бы
        обновить refs, а Pull — HEAD и рабочую директорию параллельно с ними.
        Пока сетевая операция выполняется, конфликтующие действия не запускаются.
        """
        session = self._session
        task = self._runner.submit(
            work,
            name=f"remote-{context.operation}",
            key=f"changes-operation:{session}",
        )
        if task is None:
            return False
        self.remote_operation_started.emit(context)
        task.succeeded.connect(
            partial(self._handle_remote_operation_succeeded, session)
        )
        task.failed.connect(
            partial(self._handle_remote_operation_failed, session, context)
        )
        return True

    def _submit_branch_operation(
        self,
        operation: str,
        name: str,
        work: Callable[[], object],
    ) -> bool:
        """Запускает операцию с ветками; не параллелит её с индексом и коммитом."""
        session = self._session
        task = self._runner.submit(
            work,
            name=f"branch-{operation}",
            key=f"changes-operation:{session}",
        )
        if task is None:
            return False
        self.branch_operation_started.emit(operation, name)
        task.succeeded.connect(
            partial(
                self._handle_branch_operation_succeeded,
                session,
                operation,
                name,
            )
        )
        task.failed.connect(
            partial(
                self._handle_branch_operation_failed,
                session,
                operation,
                name,
            )
        )
        return True

    def _submit_changes_operation(
        self, name: str, operation: Callable[..., object], *args
    ) -> bool:
        """Запускает операцию с индексом; статус обновляется при любом исходе.

        Операции stage/unstage не параллелятся: две одновременные правки индекса
        дали бы непредсказуемое состояние. После успеха или ошибки статус
        запрашивается заново — панель показывает фактическое состояние.
        """
        repository = self._repository
        if repository is None:
            return False

        session = self._session
        key = f"changes-operation:{session}"
        task = self._runner.submit(
            partial(operation, repository, *args),
            name=name,
            key=key,
        )
        if task is None:
            return False
        self.changes_operation_started.emit()
        task.succeeded.connect(
            partial(self._handle_changes_operation_succeeded, session)
        )
        task.failed.connect(
            partial(self._handle_changes_operation_failed, session)
        )
        return True

    def load_worktree_diff(
        self, changed_file: ChangedFile, *, staged: bool
    ) -> bool:
        """Запускает фоновую загрузку diff файла рабочей директории или индекса.

        :param staged: ``True`` — diff индекса (``git diff --cached``), ``False`` —
            diff рабочей директории (``git diff``).
        :return: ``True``, если задача поставлена; ``False`` — репозиторий не
            открыт или diff этого файла в этой секции уже загружается.
        """
        repository = self._repository
        if repository is None:
            return False

        session = self._session
        task = self._runner.submit(
            partial(
                self._changes_service.get_file_diff,
                repository,
                changed_file,
                staged=staged,
            ),
            name="worktree-diff",
            key=f"worktree-diff:{session}:{staged}:{changed_file.path}",
        )
        if task is None:
            return False
        self.worktree_diff_loading.emit(changed_file.path, staged)
        task.succeeded.connect(
            partial(
                self._handle_worktree_diff_loaded,
                session,
                changed_file.path,
                staged,
            )
        )
        task.failed.connect(
            partial(
                self._handle_worktree_diff_failed,
                session,
                changed_file.path,
                staged,
            )
        )
        return True

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
        commits = self._history_service.get_history(
            repository, limit=self._history_limit
        )
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

    def _handle_changes_loaded(
        self, session: int, changes: tuple[ChangedFile, ...]
    ) -> None:
        if session != self._session:
            return
        self._changes = changes
        self.changes_loaded.emit(changes)

    def _handle_changes_failed(self, session: int, failure: TaskFailure) -> None:
        if session != self._session:
            return
        self.changes_failed.emit(failure)

    def _handle_changes_operation_succeeded(
        self, session: int, _result: object = None
    ) -> None:
        if session != self._session:
            return
        self.changes_operation_succeeded.emit()
        # Даже если Git ничего не изменил, показываем фактическое состояние:
        # файл могли изменить снаружи между status и операцией.
        self.load_changes()

    def _handle_changes_operation_failed(
        self, session: int, failure: TaskFailure
    ) -> None:
        if session != self._session:
            return
        self.changes_operation_failed.emit(failure)
        # Частичный отказ Git или устаревший список: обновляем статус, чтобы
        # пользователь видел фактическое состояние перед повтором.
        self.load_changes()

    def _handle_commit_succeeded(self, session: int, _result: object = None) -> None:
        if session != self._session:
            return
        self.commit_succeeded.emit()
        # Коммит сдвинул HEAD: снимок Repository устарел (он описывает HEAD на момент
        # открытия). Переоткрываем репозиторий и загружаем всё состояние заново —
        # историю, граф, ветки, refs и remote; Changes обновит MainWindow.
        self._reload_after_head_change("reload-after-commit")

    def _handle_commit_failed(self, session: int, failure: TaskFailure) -> None:
        if session != self._session:
            return
        self.commit_failed.emit(failure)
        # Индекс после неудачного коммита может быть изменён хуком или частично
        # затронут: показываем фактическое состояние, чтобы пользователь мог повторить.
        self.load_changes()

    def _handle_branch_operation_succeeded(
        self, session: int, operation: str, name: str, _result: object = None
    ) -> None:
        if session != self._session:
            return
        self.branch_operation_succeeded.emit(operation, name)
        # Создание или переключение сдвинуло HEAD (и, возможно, рабочую директорию):
        # снимок Repository устарел. Переоткрываем репозиторий и загружаем HEAD,
        # историю, граф, ветки/refs и remote; Changes обновит MainWindow.
        self._reload_after_head_change("reload-after-branch")

    def _handle_branch_operation_failed(
        self, session: int, operation: str, name: str, failure: TaskFailure
    ) -> None:
        if session != self._session:
            return
        # Git отказывает до изменения refs и не трогает рабочую директорию:
        # состояние не перезагружаем и не «исправляем», только сообщаем причину.
        self.branch_operation_failed.emit(operation, name, failure)

    def _handle_remote_operation_succeeded(
        self, session: int, result: RemoteOperationResult
    ) -> None:
        if session != self._session:
            return
        self.remote_operation_succeeded.emit(result)
        # Fetch обновил remote-tracking refs, Pull сдвинул HEAD и рабочую директорию,
        # Push мог настроить upstream: снимок Repository устарел. Перечитываем
        # историю, граф, ветки/refs, текущую ветку и remote.
        self._reload_after_head_change("reload-after-remote")

    def _handle_remote_operation_failed(
        self, session: int, context: RemoteOperationContext, failure: TaskFailure
    ) -> None:
        if session != self._session:
            return
        self.remote_operation_failed.emit(context, failure)
        # Отказ до запуска Git (неизвестный remote, нет upstream, detached HEAD)
        # состояние не менял — перечитывать нечего. После ошибки самой сетевой
        # команды Git мог успеть обновить remote-tracking refs (фаза fetch в Pull)
        # или настройки upstream, поэтому состояние перечитывается.
        if isinstance(failure.exception, ValueError):
            return
        self._reload_after_head_change("reload-after-remote-failure")

    def _reload_after_head_change(self, task_name: str) -> bool:
        """Переоткрывает репозиторий и загружает данные после смены HEAD."""
        repository = self._repository
        if repository is None:
            return False
        # Новая сессия отбрасывает результаты прежних фоновых задач: они относятся
        # к устаревшему HEAD (например, открытый diff старого коммита).
        self._session += 1
        session = self._session
        task = self._runner.submit(
            partial(self._open_and_load, repository.path),
            name=task_name,
            key=f"load:{session}",
        )
        if task is None:
            return False
        self.history_loading.emit()
        task.succeeded.connect(partial(self._handle_reloaded_workspace, session))
        task.failed.connect(partial(self._handle_load_failed, session))
        return True

    def _open_and_load(self, path: Path) -> WorkspaceData:
        """Worker-функция: переоткрывает репозиторий и собирает данные для окна."""
        repository = self._repository_service.open_repository(str(path))
        return self._load_data(repository)

    def _handle_reloaded_workspace(
        self, session: int, data: WorkspaceData
    ) -> None:
        if session != self._session:
            return
        self._repository = data.repository
        self._workspace = data
        self.repository_loaded.emit(data)

    def _handle_worktree_diff_loaded(
        self, session: int, path: str, staged: bool, diff: Diff
    ) -> None:
        if session != self._session:
            return
        self.worktree_diff_loaded.emit(path, staged, diff)

    def _handle_worktree_diff_failed(
        self, session: int, path: str, staged: bool, failure: TaskFailure
    ) -> None:
        if session != self._session:
            return
        self.worktree_diff_failed.emit(path, staged, failure)

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
