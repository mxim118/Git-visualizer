"""Типы ошибок инфраструктурного слоя при работе с Git CLI.

Сообщения исключений технические и на английском. Перевод в понятные
пользователю русские тексты выполняется на уровне Presentation (см. ТЗ 9.4, 9.5).

Классификация по содержимому stderr (авторизация, сеть, конфликт и т. п.)
в этот модуль добавляется отдельным шагом; здесь только ошибки самого запуска.
"""

import shlex
from collections.abc import Sequence


class GitError(Exception):
    """Базовый класс всех ошибок запуска Git.

    Хранит выполнявшуюся команду; исходное исключение (если было) доступно
    через ``__cause__``.
    """

    def __init__(self, message: str, *, command: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.command: tuple[str, ...] = tuple(command)

    def technical_details(self) -> str:
        """Многострочное описание для раздела «Подробнее» и журнала."""
        lines = [self.message]
        if self.command:
            # shlex.join — только для отображения; процесс запускается без shell.
            lines.append(f"Command: {shlex.join(self.command)}")
        return "\n".join(lines)


class GitNotFoundError(GitError):
    """Git не найден в системе (или указанный файл не является Git)."""

    def __init__(
        self,
        executable: str,
        *,
        command: Sequence[str] = (),
        message: str | None = None,
    ) -> None:
        super().__init__(
            message or f"Git executable not found: {executable!r}",
            command=command,
        )
        self.executable = executable


class GitLaunchError(GitError):
    """Процесс Git не удалось запустить.

    Например: недопустимый рабочий каталог, нет прав на запуск файла,
    недопустимые символы в аргументах.
    """


class GitTimeoutError(GitError):
    """Команда не завершилась за отведённое время и была прервана."""

    def __init__(
        self,
        timeout: float,
        *,
        command: Sequence[str] = (),
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(f"Git command timed out after {timeout} s", command=command)
        self.timeout = timeout
        self.stdout = stdout
        self.stderr = stderr

    def technical_details(self) -> str:
        return _append_streams(super().technical_details(), self.stdout, self.stderr)


class GitCommandError(GitError):
    """Git запустился, но завершился с ненулевым кодом."""

    def __init__(
        self,
        returncode: int,
        *,
        command: Sequence[str] = (),
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(f"Git command failed with exit code {returncode}", command=command)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def technical_details(self) -> str:
        return _append_streams(super().technical_details(), self.stdout, self.stderr)


def _append_streams(text: str, stdout: str, stderr: str) -> str:
    lines = [text]
    if stdout.strip():
        lines.append(f"stdout:\n{stdout.rstrip()}")
    if stderr.strip():
        lines.append(f"stderr:\n{stderr.rstrip()}")
    return "\n".join(lines)
