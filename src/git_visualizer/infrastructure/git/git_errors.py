"""Типы ошибок инфраструктурного слоя при работе с Git CLI.

Сообщения исключений технические и на английском. Перевод в понятные
пользователю русские тексты выполняется на уровне Presentation (см. ТЗ 9.4, 9.5).

Классификация ошибок Git по тексту stderr собрана здесь же (``classify_command_error``).
Правила рассчитаны на английские сообщения: ``GitRunner`` запускает Git с ``LC_ALL=C``.
Новые виды ошибок добавляются в ``GitErrorKind`` и ``_STDERR_RULES`` вместе с тестом
на реальном сообщении Git.
"""

import shlex
from collections.abc import Sequence
from enum import Enum


class GitErrorKind(Enum):
    """Вид ошибки команды Git, определённый по тексту stderr."""

    UNKNOWN = "unknown"
    NOT_A_REPOSITORY = "not_a_repository"  # папка не является репозиторием
    UNSAFE_REPOSITORY = "unsafe_repository"  # «dubious ownership»: чужой владелец каталога


# (вид ошибки, фрагмент stderr в нижнем регистре); побеждает первое совпадение.
_STDERR_RULES: tuple[tuple[GitErrorKind, str], ...] = (
    (GitErrorKind.UNSAFE_REPOSITORY, "detected dubious ownership"),
    (GitErrorKind.NOT_A_REPOSITORY, "not a git repository"),
)


def classify_command_error(stderr: str) -> GitErrorKind:
    """Определяет вид ошибки по stderr; неопознанная ошибка — ``UNKNOWN``."""
    lowered = stderr.lower()
    for kind, fragment in _STDERR_RULES:
        if fragment in lowered:
            return kind
    return GitErrorKind.UNKNOWN


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
        self.kind = classify_command_error(stderr)

    def technical_details(self) -> str:
        return _append_streams(super().technical_details(), self.stdout, self.stderr)


class GitOutputParseError(GitError):
    """Git завершился успешно, но его вывод не удалось разобрать.

    Это не ошибка пользователя: вывод не соответствует формату, который запрашивало
    приложение (повреждённый репозиторий, неожиданная версия Git, ошибка в парсере).
    Данные не «угадываются»: разбор прерывается, подробности — в ``technical_details()``.
    """


def _append_streams(text: str, stdout: str, stderr: str) -> str:
    lines = [text]
    if stdout.strip():
        lines.append(f"stdout:\n{stdout.rstrip()}")
    if stderr.strip():
        lines.append(f"stderr:\n{stderr.rstrip()}")
    return "\n".join(lines)
