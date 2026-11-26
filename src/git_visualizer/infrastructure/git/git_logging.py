"""Локальный журнал вызовов Git CLI (ТЗ 12.3).

Модуль относится к Infrastructure и использует только стандартную библиотеку
(``logging``), без Qt. На каждый запуск Git пишется одна запись: тип операции
(подкоманда), рабочий каталог, длительность, код возврата и факт наличия stdin.
При неудаче добавляются вид ошибки и краткий stderr.

Полный stdout, ``stdin_data`` (сообщения коммитов) и окружение не журналируются.
Из stderr маскируются учётные данные в URL и токены в query-параметрах: push и
fetch могут печатать адрес remote с паролем.

Журналирование не должно влиять на работу Git: любая ошибка записи поглощается,
а предупреждение выводится в stderr не более одного раза за процесс.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
from collections.abc import Sequence
from typing import Final

from git_visualizer.infrastructure.git.git_errors import classify_command_error

LOGGER_NAME: Final = "git_visualizer.git"

# Сколько символов stderr попадает в одну запись журнала.
STDERR_LIMIT: Final = 1000

_UNKNOWN_SUBCOMMAND: Final = "unknown"

# userinfo в URL: ``scheme://user:password@host/...`` -> ``scheme://***@host/...``.
# SSH-адреса вида ``git@host:path/repo.git`` под шаблон не попадают.
_URL_USERINFO = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<userinfo>[^/\s@]+)@")

# Значения секретных query-параметров: ``?token=...`` -> ``?token=***``.
_SECRET_QUERY_PARAMETER = re.compile(
    r"(?P<prefix>[?&;])(?P<name>token|access_token|private_token)=(?P<value>[^&\s#\"'<>]*)",
    re.IGNORECASE,
)

_logger = logging.getLogger(LOGGER_NAME)
# Библиотечный логгер не должен писать в stderr через lastResort, пока
# приложение не вызвало setup_logging().
_logger.addHandler(logging.NullHandler())

_warning_lock = threading.Lock()
_warning_emitted = False


def git_subcommand(args: Sequence[str]) -> str:
    """Возвращает тип операции Git — первую подкоманду в ``args``.

    Глобальная опция ``-c`` (например, ``-c core.quotePath=false``) идёт до
    подкоманды вместе со своим значением, поэтому её значение пропускается.
    Если подкоманды нет (например, ``git --version``), возвращается ``unknown``.
    """
    skip_next = False
    for argument in args:
        if skip_next:
            skip_next = False
            continue
        if argument == "-c":
            skip_next = True
            continue
        if argument.startswith("-"):
            continue
        return argument
    return _UNKNOWN_SUBCOMMAND


def redact(text: str) -> str:
    """Маскирует секреты в тексте, не меняя остального содержимого.

    * userinfo в URL (логин и пароль) заменяется на ``***``;
    * значения query-параметров ``token``, ``access_token`` и ``private_token``
      заменяются на ``***``.

    HTTPS-адрес без userinfo, SSH-адрес ``git@host:path/repo.git`` и локальный
    путь не изменяются.
    """
    if not text:
        return text

    redacted = _URL_USERINFO.sub(
        lambda match: f"{match.group('scheme')}***@",
        text,
    )
    return _SECRET_QUERY_PARAMETER.sub(
        lambda match: f"{match.group('prefix')}{match.group('name')}=***",
        redacted,
    )


def log_git_call(
    args: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None,
    duration_ms: int,
    returncode: int,
    stdin_used: bool,
    stderr: str = "",
) -> None:
    """Пишет запись о завершившемся запуске Git.

    Успех журналируется уровнем INFO, ненулевой код — WARNING с видом ошибки
    из ``GitCommandError``. stderr попадает в запись только при неудаче.
    """
    kind: str | None = None
    level = logging.INFO
    failure_stderr = ""
    if returncode != 0:
        kind = classify_command_error(stderr).value
        level = logging.WARNING
        failure_stderr = stderr

    _emit(
        level,
        args=args,
        cwd=cwd,
        duration_ms=duration_ms,
        exit_code=str(returncode),
        stdin_used=stdin_used,
        kind=kind,
        stderr=failure_stderr,
    )


def log_git_failure(
    args: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None,
    duration_ms: int,
    kind: str,
    stdin_used: bool,
    stderr: str = "",
) -> None:
    """Пишет запись о запуске Git, который не завершился обычным кодом.

    Используется для ``GitTimeoutError`` (``kind=timeout``),
    ``GitNotFoundError`` и ``GitLaunchError``: у них нет кода возврата, поэтому
    в записи стоит ``exit=none``.
    """
    _emit(
        logging.ERROR,
        args=args,
        cwd=cwd,
        duration_ms=duration_ms,
        exit_code="none",
        stdin_used=stdin_used,
        kind=kind,
        stderr=stderr,
    )


def _emit(
    level: int,
    *,
    args: Sequence[str],
    cwd: str | os.PathLike[str] | None,
    duration_ms: int,
    exit_code: str,
    stdin_used: bool,
    kind: str | None,
    stderr: str,
) -> None:
    try:
        fields = [
            f"git {git_subcommand(args)}",
            f"cwd={_format_cwd(cwd)}",
            f"duration_ms={max(0, int(duration_ms))}",
            f"exit={exit_code}",
            f"stdin={'yes' if stdin_used else 'no'}",
        ]
        if kind:
            fields.append(f"kind={kind}")
        formatted_stderr = _format_stderr(stderr)
        if formatted_stderr:
            fields.append(f"stderr={formatted_stderr}")
        _logger.log(level, " ".join(fields))
    except Exception:
        # Журнал не должен менять поведение GitRunner и не должен бросать.
        _warn_once(
            "Git Visualizer: failed to write a Git log record; "
            "the operation continues without that record."
        )


def _format_cwd(cwd: str | os.PathLike[str] | None) -> str:
    try:
        path = os.getcwd() if cwd is None else os.fspath(cwd)
    except (OSError, TypeError):
        return "<unknown>"
    return _single_line(path)


def _format_stderr(stderr: str) -> str:
    """Приводит stderr к однострочному виду, маскирует секреты и ограничивает длину."""
    return _single_line(redact(stderr).strip())[:STDERR_LIMIT]


def _single_line(text: str) -> str:
    return text.replace("\r\n", " | ").replace("\n", " | ").replace("\r", " | ")


def _warn_once(message: str) -> None:
    global _warning_emitted

    with _warning_lock:
        if _warning_emitted:
            return
        _warning_emitted = True

    stream = sys.stderr
    if stream is None:
        return
    try:
        print(message, file=stream)
        stream.flush()
    except Exception:
        # В оконном приложении stderr может отсутствовать.
        pass
