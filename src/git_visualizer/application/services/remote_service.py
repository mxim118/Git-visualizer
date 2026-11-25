"""Сценарии сетевых операций: Fetch, безопасный Pull и Push текущей ветки.

Сервис не хранит и не запрашивает пароли или токены: авторизацию выполняет Git
штатными механизмами (SSH, Git Credential Manager, credential helpers). ``Pull``
разрешён только как fast-forward (``--ff-only``): при расхождении веток ничего не
меняется, а ошибка Git пробрасывается без изменений. Force push, merge и rebase
не выполняются.

После успешной операции снимок ``Repository`` может устареть (сдвинулся HEAD,
обновились remote-tracking refs), поэтому Presentation переоткрывает репозиторий
и заново загружает историю/граф/ветки/Changes.
"""

from dataclasses import dataclass

from git_visualizer.application.errors import (
    NoCurrentBranchError,
    UnknownRemoteError,
    UpstreamNotConfiguredError,
)
from git_visualizer.application.ports import GitRepositoryFactory, GitRepositoryPort
from git_visualizer.domain.models import Repository

# Сетевые команды выполняются дольше локальных: 120 с — разумный предел для Fetch,
# Pull и Push среднего репозитория. Значение задаётся в Application и передаётся в
# Infrastructure, чтобы таймаут не был разбросан по командам.
DEFAULT_REMOTE_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True, kw_only=True)
class RemoteOperationContext:
    """Что за операция запущена — для индикации выполнения и текста ошибки."""

    operation: str  # "fetch" | "pull" | "push"
    remote: str | None = None
    branch: str | None = None


@dataclass(frozen=True, kw_only=True)
class RemoteOperationResult:
    """Результат успешной сетевой операции.

    ``operation`` — ``"fetch"``/``"pull"``/``"push"``; ``remote`` — выбранный remote
    (None, если операция шла по настроенному upstream); ``branch`` — текущая ветка;
    ``upstream`` — её upstream после операции; ``output`` — вывод Git для показа.
    """

    operation: str
    remote: str | None = None
    branch: str | None = None
    upstream: str | None = None
    output: str = ""


class RemoteService:
    """Fetch, Pull и Push текущей ветки через ``GitRepositoryPort``."""

    def __init__(
        self,
        git_repository_factory: GitRepositoryFactory,
        *,
        timeout: float = DEFAULT_REMOTE_TIMEOUT_SECONDS,
    ) -> None:
        if timeout <= 0:
            raise ValueError(f"Remote timeout must be positive, got {timeout}")
        self._git_repository_factory = git_repository_factory
        self._timeout = timeout

    @property
    def timeout(self) -> float:
        return self._timeout

    def fetch(self, repository: Repository, remote_name: str) -> RemoteOperationResult:
        """Получает изменения выбранного remote.

        :raises UnknownRemoteError: remote с таким именем не настроен.
        :raises GitError: Git завершился с ошибкой (сеть, авторизация, права и т. п.).
        """
        name = remote_name.strip()
        if not name:
            raise ValueError("Remote name must not be empty")
        git = self._git_repository_factory(repository.path)
        self._ensure_configured(git, name)
        output = git.fetch(name, timeout=self._timeout)
        return RemoteOperationResult(operation="fetch", remote=name, output=output)

    def pull(self, repository: Repository) -> RemoteOperationResult:
        """Выполняет Pull текущей ветки только с ``--ff-only``.

        :raises NoCurrentBranchError: HEAD не указывает на локальную ветку.
        :raises UpstreamNotConfiguredError: у ветки нет настроенного upstream.
        :raises GitError: Git отказал (ветки разошлись, незакоммиченные изменения,
            сеть, авторизация и т. п.); состояние не «исправляется» приложением.
        """
        git = self._git_repository_factory(repository.path)
        branch = git.get_current_branch()
        if branch is None:
            raise NoCurrentBranchError()
        upstream = git.get_branch_upstream(branch)
        if upstream is None:
            raise UpstreamNotConfiguredError(branch)
        output = git.pull_ff_only(timeout=self._timeout)
        return RemoteOperationResult(
            operation="pull",
            branch=branch,
            upstream=upstream,
            output=output,
        )

    def push(self, repository: Repository) -> RemoteOperationResult:
        """Отправляет текущую ветку в настроенный upstream (``git push``).

        :raises NoCurrentBranchError: HEAD не указывает на локальную ветку.
        :raises UpstreamNotConfiguredError: upstream отсутствует; для первого Push
            нужен явный выбор remote (``push_initial``).
        :raises GitError: Git отказал (non-fast-forward, права, авторизация, сеть).
        """
        git = self._git_repository_factory(repository.path)
        branch = git.get_current_branch()
        if branch is None:
            raise NoCurrentBranchError()
        upstream = git.get_branch_upstream(branch)
        if upstream is None:
            raise UpstreamNotConfiguredError(branch)
        output = git.push(timeout=self._timeout)
        return RemoteOperationResult(
            operation="push",
            branch=branch,
            upstream=upstream,
            output=output,
        )

    def push_initial(
        self, repository: Repository, remote_name: str
    ) -> RemoteOperationResult:
        """Первый Push текущей ветки с явным remote и настройкой upstream (``-u``).

        :raises UnknownRemoteError: remote с таким именем не настроен.
        :raises NoCurrentBranchError: HEAD не указывает на локальную ветку.
        :raises GitError: Git отказал (права, авторизация, сеть, non-fast-forward).
        """
        name = remote_name.strip()
        if not name:
            raise ValueError("Remote name must not be empty")
        git = self._git_repository_factory(repository.path)
        branch = git.get_current_branch()
        if branch is None:
            raise NoCurrentBranchError()
        self._ensure_configured(git, name)
        output = git.push_set_upstream(name, branch, timeout=self._timeout)
        # Первый push настраивает branch.<имя>.remote/merge. Читаем фактическое
        # значение: у remote может быть нестандартный fetch refspec.
        upstream = git.get_branch_upstream(branch) or f"{name}/{branch}"
        return RemoteOperationResult(
            operation="push",
            remote=name,
            branch=branch,
            upstream=upstream,
            output=output,
        )

    @staticmethod
    def _ensure_configured(git: GitRepositoryPort, remote_name: str) -> None:
        configured = {remote.name for remote in git.get_remotes()}
        if remote_name not in configured:
            raise UnknownRemoteError(remote_name)
