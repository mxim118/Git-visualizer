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
    MISSING_AUTHOR = "missing_author"  # не настроены user.name / user.email
    UNMERGED_FILES = "unmerged_files"  # коммит при неразрешённых конфликтах
    BRANCH_ALREADY_EXISTS = "branch_already_exists"  # имя ветки уже занято
    INVALID_BRANCH_NAME = "invalid_branch_name"  # git check-ref-format отклонил имя
    REF_NOT_FOUND = "ref_not_found"  # ветка или коммит не найдены
    DIRTY_WORKTREE = "dirty_worktree"  # переключение перезаписало бы изменения
    OPERATION_IN_PROGRESS = "operation_in_progress"  # идёт merge/rebase/cherry-pick
    LFS_FILTER_FAILED = "lfs_filter_failed"  # не сработал фильтр Git LFS
    REPOSITORY_NOT_WRITABLE = "repository_not_writable"  # нет прав на запись в .git
    INDEX_LOCKED = "index_locked"  # index.lock занят другим Git-процессом
    REMOTE_NOT_FOUND = "remote_not_found"  # remote недоступен / не является репозиторием
    AUTHENTICATION_FAILED = "authentication_failed"  # Git не смог авторизоваться
    PERMISSION_DENIED = "permission_denied"  # remote отклонил операцию: нет прав
    NON_FAST_FORWARD = "non_fast_forward"  # fast-forward невозможен, ветки разошлись
    MERGE_CONFLICT = "merge_conflict"  # конфликт слияния, автоматически не разрешается
    NETWORK_ERROR = "network_error"  # сетевой сбой: хост, соединение, TLS


# (вид ошибки, фрагмент stderr в нижнем регистре); побеждает первое совпадение.
# Порядок важен: например «does not appear to be a git repository» (remote не найден)
# содержит «not a git repository», поэтому проверяется раньше NOT_A_REPOSITORY, а
# «permission denied (publickey)» — это ошибка авторизации, а не прав на remote.
_STDERR_RULES: tuple[tuple[GitErrorKind, str], ...] = (
    (GitErrorKind.UNSAFE_REPOSITORY, "detected dubious ownership"),
    (GitErrorKind.REMOTE_NOT_FOUND, "does not appear to be a git repository"),
    (GitErrorKind.REMOTE_NOT_FOUND, "repository not found"),
    (GitErrorKind.AUTHENTICATION_FAILED, "authentication failed"),
    (GitErrorKind.AUTHENTICATION_FAILED, "could not read username"),
    (GitErrorKind.AUTHENTICATION_FAILED, "could not read password"),
    (GitErrorKind.AUTHENTICATION_FAILED, "terminal prompts disabled"),
    (GitErrorKind.AUTHENTICATION_FAILED, "permission denied (publickey"),
    (GitErrorKind.AUTHENTICATION_FAILED, "invalid username or password"),
    (GitErrorKind.AUTHENTICATION_FAILED, "the requested url returned error: 401"),
    (
        GitErrorKind.AUTHENTICATION_FAILED,
        "support for password authentication was removed",
    ),
    (GitErrorKind.PERMISSION_DENIED, "remote: permission to"),
    (GitErrorKind.PERMISSION_DENIED, "access denied"),
    (GitErrorKind.PERMISSION_DENIED, "the requested url returned error: 403"),
    (GitErrorKind.PERMISSION_DENIED, "pre-receive hook declined"),
    (GitErrorKind.PERMISSION_DENIED, "refusing to update checked out branch"),
    (GitErrorKind.PERMISSION_DENIED, "protected branch"),
    (GitErrorKind.PERMISSION_DENIED, "not allowed to push"),
    (GitErrorKind.PERMISSION_DENIED, "remote rejected"),
    (GitErrorKind.NON_FAST_FORWARD, "non-fast-forward"),
    (GitErrorKind.NON_FAST_FORWARD, "not possible to fast-forward"),
    (
        GitErrorKind.NON_FAST_FORWARD,
        "updates were rejected because the tip of your current branch is behind",
    ),
    (GitErrorKind.NON_FAST_FORWARD, "failed to push some refs"),
    (GitErrorKind.NON_FAST_FORWARD, "fetch first"),
    (GitErrorKind.MERGE_CONFLICT, "automatic merge failed"),
    (GitErrorKind.MERGE_CONFLICT, "conflict (content)"),
    (GitErrorKind.MERGE_CONFLICT, "merge conflict"),
    (GitErrorKind.NETWORK_ERROR, "could not resolve host"),
    (GitErrorKind.NETWORK_ERROR, "connection timed out"),
    (GitErrorKind.NETWORK_ERROR, "connection refused"),
    (GitErrorKind.NETWORK_ERROR, "failed to connect"),
    (GitErrorKind.NETWORK_ERROR, "could not connect"),
    (GitErrorKind.NETWORK_ERROR, "network is unreachable"),
    (GitErrorKind.NETWORK_ERROR, "recv failure"),
    (GitErrorKind.NETWORK_ERROR, "rpc failed"),
    (GitErrorKind.NETWORK_ERROR, "the remote end hung up unexpectedly"),
    (GitErrorKind.NETWORK_ERROR, "ssl certificate problem"),
    (GitErrorKind.NETWORK_ERROR, "early eof"),
    (GitErrorKind.NETWORK_ERROR, "operation timed out"),
    (GitErrorKind.NETWORK_ERROR, "unable to access"),
    (GitErrorKind.NOT_A_REPOSITORY, "not a git repository"),
    (GitErrorKind.MISSING_AUTHOR, "author identity unknown"),
    (GitErrorKind.MISSING_AUTHOR, "unable to auto-detect email address"),
    (GitErrorKind.MISSING_AUTHOR, "please tell me who you are"),
    (GitErrorKind.INVALID_BRANCH_NAME, "is not a valid branch name"),
    (GitErrorKind.BRANCH_ALREADY_EXISTS, "a branch named"),
    (GitErrorKind.REF_NOT_FOUND, "invalid reference:"),
    (GitErrorKind.REF_NOT_FOUND, "not a valid object name"),
    (GitErrorKind.DIRTY_WORKTREE, "would be overwritten by checkout"),
    (GitErrorKind.DIRTY_WORKTREE, "would be overwritten by merge"),
    (
        GitErrorKind.OPERATION_IN_PROGRESS,
        "cannot switch branch while ",
    ),
    (GitErrorKind.LFS_FILTER_FAILED, "error cleaning git lfs object"),
    (GitErrorKind.LFS_FILTER_FAILED, "external filter 'git-lfs filter-process' failed"),
    (GitErrorKind.LFS_FILTER_FAILED, "clean filter 'lfs' failed"),
    (
        GitErrorKind.REPOSITORY_NOT_WRITABLE,
        "index.lock': permission denied",
    ),
    (GitErrorKind.REPOSITORY_NOT_WRITABLE, "index.lock: permission denied"),
    (GitErrorKind.INDEX_LOCKED, "another git process seems to be running"),
    (GitErrorKind.INDEX_LOCKED, "index.lock': file exists"),
    (GitErrorKind.INDEX_LOCKED, "index.lock: file exists"),
    (
        GitErrorKind.UNMERGED_FILES,
        "committing is not possible because you have unmerged files",
    ),
    (GitErrorKind.UNMERGED_FILES, "exiting because of an unresolved conflict"),
    (GitErrorKind.UNMERGED_FILES, "you need to resolve your current index first"),
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
