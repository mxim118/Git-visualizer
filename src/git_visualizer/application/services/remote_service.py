"""Сценарии remote: конфигурация, Fetch, безопасный Pull и Push текущей ветки.

Конфигурация (добавление, переименование, URL, удаление) выполняется штатными
командами Git и меняет только локальную конфигурацию репозитория: сервер и файлы
рабочей директории не затрагиваются. URL принимаются HTTPS, SSH и в виде
локального пути; сеть при изменении конфигурации не используется.

Сервис не хранит и не запрашивает пароли или токены: авторизацию выполняет Git
штатными механизмами (SSH, Git Credential Manager, credential helpers). ``Pull``
разрешён только как fast-forward (``--ff-only``): при расхождении веток ничего не
меняется, а ошибка Git пробрасывается без изменений. Force push, merge и rebase
не выполняются.

После успешной операции снимок ``Repository`` может устареть (сдвинулся HEAD,
обновились remote-tracking refs), поэтому Presentation переоткрывает репозиторий
и заново загружает историю/граф/ветки/Changes.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from git_visualizer.application.errors import (
    InvalidRemoteNameError,
    InvalidRemoteUrlError,
    NoCurrentBranchError,
    UnknownRemoteError,
    UpstreamNotConfiguredError,
)
from git_visualizer.application.ports import GitRepositoryFactory, GitRepositoryPort
from git_visualizer.domain.models import Remote, Repository

# Управляющие символы недопустимы в имени remote и в URL: Git либо отвергнет их,
# либо получит аргумент, который невозможно безопасно передать процессу.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_URL_SCHEME = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://")
_ASCII_WHITESPACE = " \t\r\n"

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


@dataclass(frozen=True, kw_only=True)
class RemoteConfigurationContext:
    """Какая настройка remote выполняется — для индикации и текста ошибки.

    ``operation`` — ``"add"``, ``"edit"`` или ``"remove"``. ``remote_name`` —
    текущее имя remote (при добавлении — новое), ``new_name`` — новое имя при
    редактировании.
    """

    operation: str
    remote_name: str | None = None
    new_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class RemoteConfigurationResult:
    """Результат успешной настройки remote и актуальный список remote."""

    operation: str
    remote_name: str | None = None
    new_name: str | None = None
    remotes: tuple[Remote, ...] = ()


class RemoteService:
    """Конфигурация remote, Fetch, Pull и Push текущей ветки через порт."""

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

    # --- конфигурация remote ---

    def add_remote(
        self, repository: Repository, name: str, url: str
    ) -> RemoteConfigurationResult:
        """Добавляет remote в локальную конфигурацию.

        :raises InvalidRemoteNameError: имя пустое или содержит управляющие символы.
        :raises InvalidRemoteUrlError: URL пуст, содержит управляющие символы или
            имеет неполную схему (например, ``https://`` без хоста).
        :raises GitError: Git отказал (имя уже занято, недопустимое имя и т. п.).
        """
        remote_name = self.validate_remote_name(name)
        repository_url = self.validate_remote_url(url)
        git = self._git_repository_factory(repository.path)
        git.add_remote(remote_name, repository_url)
        return RemoteConfigurationResult(
            operation="add",
            remote_name=remote_name,
            remotes=git.get_remotes(),
        )

    def edit_remote(
        self,
        repository: Repository,
        name: str,
        *,
        new_name: str,
        url: str,
    ) -> RemoteConfigurationResult:
        """Меняет имя и/или URL существующего remote штатными командами Git.

        Сначала выполняется ``git remote rename`` (Git обновляет настройки
        отслеживающих веток и remote-tracking refs), затем ``git remote set-url``
        для нового имени. Если URL не изменился, ``set-url`` не вызывается.

        :raises InvalidRemoteNameError: текущее или новое имя недопустимо.
        :raises InvalidRemoteUrlError: URL недопустим.
        :raises UnknownRemoteError: remote с таким именем не настроен.
        :raises GitError: Git отказал (новое имя занято и т. п.).
        """
        remote_name = self.validate_remote_name(name)
        target_name = self.validate_remote_name(new_name)
        repository_url = self.validate_remote_url(url)

        git = self._git_repository_factory(repository.path)
        configured = {remote.name: remote for remote in git.get_remotes()}
        current = configured.get(remote_name)
        if current is None:
            raise UnknownRemoteError(remote_name)

        if target_name != remote_name:
            git.rename_remote(remote_name, target_name)
        current_url = current.fetch_url or current.push_url
        if repository_url != current_url:
            git.set_remote_url(target_name, repository_url)
        return RemoteConfigurationResult(
            operation="edit",
            remote_name=remote_name,
            new_name=target_name,
            remotes=git.get_remotes(),
        )

    def remove_remote(
        self, repository: Repository, name: str
    ) -> RemoteConfigurationResult:
        """Удаляет remote из локальной конфигурации.

        Удаляется только запись remote: рабочая директория и репозиторий на
        сервере не изменяются.

        :raises InvalidRemoteNameError: имя пустое или содержит управляющие символы.
        :raises UnknownRemoteError: remote с таким именем не настроен.
        :raises GitError: Git отказал.
        """
        remote_name = self.validate_remote_name(name)
        git = self._git_repository_factory(repository.path)
        configured = {remote.name for remote in git.get_remotes()}
        if remote_name not in configured:
            raise UnknownRemoteError(remote_name)
        git.remove_remote(remote_name)
        return RemoteConfigurationResult(
            operation="remove",
            remote_name=remote_name,
            remotes=git.get_remotes(),
        )

    @staticmethod
    def validate_remote_name(name: str) -> str:
        """Проверяет заведомо недопустимое имя remote до запуска Git.

        Полный синтаксис (пробелы, ``:``, ``?`` и т. п.) проверяет сам Git:
        ``git remote add`` откажет раньше, чем изменится конфигурация. Здесь
        отсекаются только значения, которые нельзя передать процессу.
        """
        remote_name = name.strip(_ASCII_WHITESPACE)
        if not remote_name:
            raise InvalidRemoteNameError("Remote name must not be empty")
        if _CONTROL_CHARS.search(remote_name):
            raise InvalidRemoteNameError(
                "Remote name must not contain control characters"
            )
        return remote_name

    @staticmethod
    def validate_remote_url(url: str) -> str:
        """Проверяет заведомо недопустимый URL remote до запуска Git.

        Поддерживаются HTTPS/SSH-ссылки и локальные пути. Git принимает почти
        любую строку как URL и сообщил бы об ошибке только при Fetch, поэтому
        пустой URL и неполная схема (``https://`` без хоста) отклоняются здесь.
        """
        repository_url = url.strip(_ASCII_WHITESPACE)
        if not repository_url:
            raise InvalidRemoteUrlError("Remote URL must not be empty")
        if _CONTROL_CHARS.search(repository_url):
            raise InvalidRemoteUrlError(
                "Remote URL must not contain control characters"
            )

        match = _URL_SCHEME.match(repository_url)
        if match is None:
            if "://" in repository_url:
                raise InvalidRemoteUrlError("Remote URL has an invalid scheme")
            # SCP-подобная ссылка (git@host:path) или локальный путь.
            return repository_url

        if any(character.isspace() for character in repository_url):
            raise InvalidRemoteUrlError("Remote URL must not contain whitespace")
        try:
            parts = urlsplit(repository_url)
        except ValueError as error:
            raise InvalidRemoteUrlError("Remote URL is malformed") from error
        if parts.scheme.lower() == "file":
            if not parts.path and not parts.netloc:
                raise InvalidRemoteUrlError("Remote URL has no path")
        elif parts.hostname is None:
            raise InvalidRemoteUrlError("Remote URL must contain a host")
        return repository_url

    # --- сетевые операции ---

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
