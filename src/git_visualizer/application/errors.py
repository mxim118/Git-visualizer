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
