"""Ошибки сценариев использования (Application).

Сообщения технические, на английском; русские тексты формирует Presentation по типу
исключения. Технические подробности для раздела «Подробнее» — ``technical_details()``;
исходная ошибка Git (если была) доступна через ``__cause__``.
"""

from pathlib import Path


class RepositoryOpenError(Exception):
    """Базовый класс отказов при открытии репозитория."""

    def __init__(self, message: str, *, path: Path, details: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        self._details = details

    def technical_details(self) -> str:
        lines = [self.message, f"Path: {self.path}"]
        if self._details:
            lines.append(self._details)
        return "\n".join(lines)


class PathDoesNotExistError(RepositoryOpenError):
    """Указанный путь не существует (или пуст)."""


class PathIsNotDirectoryError(RepositoryOpenError):
    """Указанный путь существует, но это не каталог (например, файл)."""


class NotAGitRepositoryError(RepositoryOpenError):
    """Каталог не находится внутри рабочей директории Git-репозитория.

    В том числе когда выбран служебный каталог ``.git``.
    """


class BareRepositoryNotSupportedError(RepositoryOpenError):
    """Bare-репозиторий не поддерживается: нужен репозиторий с рабочей директорией."""


class UnsafeRepositoryError(RepositoryOpenError):
    """Git отказался работать с каталогом из-за владельца (safe.directory, «dubious ownership»)."""


class UntrackedFileDeleteError(Exception):
    """Не удалось удалить неотслеживаемый файл при отмене изменений.

    Оборачивает ошибку файловой системы (``OSError``) из Infrastructure: у
    неотслеживаемого файла нет Git-команды отмены, поэтому Presentation получает
    отдельный тип, а не общий текст неожиданной ошибки.
    """

    def __init__(self, message: str, *, path: str, details: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        self._details = details

    def technical_details(self) -> str:
        lines = [self.message, f"Path: {self.path}"]
        if self._details:
            lines.append(self._details)
        return "\n".join(lines)


class RemoteOperationValidationError(ValueError):
    """Операция с remote отклонена до запуска Git: состояние не менялось."""


class UnknownRemoteError(RemoteOperationValidationError):
    """Запрошенный remote не настроен в репозитории."""

    def __init__(self, remote_name: str) -> None:
        super().__init__(f"Remote is not configured: {remote_name!r}")
        self.remote_name = remote_name


class NoCurrentBranchError(RemoteOperationValidationError):
    """HEAD не указывает на локальную ветку (detached HEAD или нет коммитов)."""

    def __init__(
        self, message: str = "Current HEAD does not point to a local branch"
    ) -> None:
        super().__init__(message)


class UpstreamNotConfiguredError(RemoteOperationValidationError):
    """У текущей ветки нет настроенного upstream."""

    def __init__(self, branch_name: str) -> None:
        super().__init__(f"Branch has no configured upstream: {branch_name!r}")
        self.branch_name = branch_name
