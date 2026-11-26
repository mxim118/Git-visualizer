"""Единственная низкоуровневая точка запуска Git CLI во всём приложении.

GitRunner ничего не знает о репозиториях, истории, ветках и статусе: он только
запускает процесс ``git`` и возвращает результат либо типизированную ошибку.
Рабочий каталог (``cwd``) задаётся при каждом вызове, поэтому один экземпляр
можно использовать для разных репозиториев.

Известное ограничение (проверить на Windows при реализации Fetch/Pull/Push):
при timeout завершается только процесс ``git``; дочерние процессы (например,
``git-remote-https`` или ``ssh``) могут продолжить работу и удерживать каналы
вывода, из-за чего возврат из ``subprocess.run`` способен задержаться.
Если это подтвердится, завершение дерева процессов добавляется здесь же.
"""

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitLaunchError,
    GitNotFoundError,
    GitTimeoutError,
)
from git_visualizer.infrastructure.git.git_logging import log_git_call, log_git_failure

DEFAULT_TIMEOUT_SECONDS = 30.0

# Переменные окружения, добавляемые каждому процессу Git (ТЗ 9.5):
# - LC_ALL=C: сообщения Git на английском независимо от языка системы;
# - GIT_TERMINAL_PROMPT=0: Git не ждёт ввода пароля в консоли, которой у GUI нет.
GIT_ENV_OVERRIDES: Mapping[str, str] = MappingProxyType(
    {"LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}
)

# Значение флага Windows CREATE_NO_WINDOW; нужен, чтобы при запуске из оконного
# приложения (без консоли) на каждую команду Git не появлялось чёрное окно.
_WINDOWS_CREATE_NO_WINDOW = 0x08000000

_VERSION_PATTERN = re.compile(r"git version (\d+)\.(\d+)(?:\.(\d+))?")

# Сигнатура совместима с subprocess.run; подменяется в тестах.
ProcessRunner = Callable[..., "subprocess.CompletedProcess[bytes]"]


@dataclass(frozen=True)
class GitResult:
    """Результат завершившегося процесса Git."""

    command: tuple[str, ...]  # полная командная строка, включая исполняемый файл
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, order=True)
class GitVersion:
    """Версия Git. Сравнивается по (major, minor, patch); ``raw`` не участвует."""

    major: int
    minor: int
    patch: int = 0
    raw: str = field(default="", compare=False)


class GitRunner:
    """Запускает Git CLI без shell и преобразует сбои запуска в типизированные ошибки."""

    def __init__(
        self,
        git_executable: str = "git",
        default_timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
        *,
        process_runner: ProcessRunner = subprocess.run,
    ) -> None:
        """
        :param git_executable: имя или путь исполняемого файла Git.
        :param default_timeout: timeout по умолчанию в секундах; ``None`` — без ограничения.
        :param process_runner: функция запуска процесса (``subprocess.run``);
            параметр существует для подмены в тестах.
        """
        self._executable = git_executable
        self._default_timeout = default_timeout
        self._process_runner = process_runner

    @property
    def executable(self) -> str:
        return self._executable

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        timeout: float | None = None,
        stdin_data: str | bytes | None = None,
        check: bool = True,
    ) -> GitResult:
        """Запускает ``git <args>`` и возвращает результат.

        :param args: аргументы Git без самого ``git``; каждый элемент — отдельный
            аргумент процесса (строка целиком не допускается).
        :param cwd: рабочий каталог процесса; ``None`` — каталог текущего процесса.
        :param timeout: timeout этого вызова; ``None`` — использовать значение по умолчанию.
        :param stdin_data: данные для stdin (``str`` кодируется в UTF-8). Если не заданы,
            stdin закрыт, и Git не может зависнуть в ожидании ввода.
        :param check: при ``True`` ненулевой код завершения вызывает ``GitCommandError``;
            при ``False`` результат возвращается как есть (нужно для команд, где
            ненулевой код — штатный ответ, например ``rev-parse --verify -q HEAD``).
        :raises TypeError: ``args`` — не последовательность строк (ошибка программиста).
        :raises GitNotFoundError: Git не найден.
        :raises GitLaunchError: процесс не удалось запустить.
        :raises GitTimeoutError: превышен timeout.
        :raises GitCommandError: ненулевой код завершения при ``check=True``.
        """
        command = self._build_command(args)
        stdin_used = stdin_data is not None
        try:
            working_dir = self._validate_cwd(cwd, command)
        except GitLaunchError as exc:
            # Проверка cwd выполняется до запуска процесса; это тоже неудачная
            # попытка вызова Git, которую полезно видеть в журнале.
            log_git_failure(
                command[1:],
                cwd=cwd,
                duration_ms=0,
                kind="git_launch",
                stdin_used=stdin_used,
                stderr=exc.message,
            )
            raise

        kwargs: dict[str, Any] = {
            "cwd": working_dir,
            "env": {**os.environ, **GIT_ENV_OVERRIDES},
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": self._default_timeout if timeout is None else timeout,
            "shell": False,
        }
        if stdin_data is None:
            kwargs["stdin"] = subprocess.DEVNULL
        else:
            kwargs["input"] = (
                stdin_data.encode("utf-8") if isinstance(stdin_data, str) else stdin_data
            )
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", _WINDOWS_CREATE_NO_WINDOW
            )

        # Длительность измеряется только для журнала и только вокруг запуска процесса.
        started = time.perf_counter()
        try:
            completed = self._process_runner(command, **kwargs)
        except subprocess.TimeoutExpired as exc:
            duration_ms = _elapsed_ms(started)
            stdout = _decode(exc.stdout)
            stderr = _decode(exc.stderr)
            log_git_failure(
                command[1:],
                cwd=working_dir,
                duration_ms=duration_ms,
                kind="timeout",
                stdin_used=stdin_used,
                stderr=stderr,
            )
            raise GitTimeoutError(
                exc.timeout,
                command=command,
                stdout=stdout,
                stderr=stderr,
            ) from exc
        except FileNotFoundError as exc:
            # Каталог cwd проверен выше, поэтому здесь отсутствует сам исполняемый файл.
            log_git_failure(
                command[1:],
                cwd=working_dir,
                duration_ms=_elapsed_ms(started),
                kind="git_not_found",
                stdin_used=stdin_used,
                stderr=str(exc),
            )
            raise GitNotFoundError(self._executable, command=command) from exc
        except (OSError, ValueError) as exc:
            # OSError: нет прав на запуск и т. п.; ValueError: например, NUL в аргументе.
            log_git_failure(
                command[1:],
                cwd=working_dir,
                duration_ms=_elapsed_ms(started),
                kind="git_launch",
                stdin_used=stdin_used,
                stderr=str(exc),
            )
            raise GitLaunchError(f"Failed to start Git process: {exc}", command=command) from exc

        duration_ms = _elapsed_ms(started)
        result = GitResult(
            command=tuple(command),
            returncode=completed.returncode,
            stdout=_decode(completed.stdout),
            stderr=_decode(completed.stderr),
        )
        log_git_call(
            command[1:],
            cwd=working_dir,
            duration_ms=duration_ms,
            returncode=result.returncode,
            stdin_used=stdin_used,
            stderr=result.stderr,
        )
        if check and result.returncode != 0:
            raise GitCommandError(
                result.returncode,
                command=command,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result

    def get_version(self) -> GitVersion:
        """Возвращает версию Git; одновременно служит проверкой наличия Git в системе.

        :raises GitNotFoundError: Git не найден либо вывод ``--version`` не похож на Git.
        """
        result = self.run(["--version"])
        match = _VERSION_PATTERN.search(result.stdout)
        if match is None:
            raise GitNotFoundError(
                self._executable,
                command=result.command,
                message=(
                    f"{self._executable!r} does not look like Git: "
                    f"unexpected output of --version: {result.stdout.strip()!r}"
                ),
            )
        major, minor, patch = match.groups()
        return GitVersion(
            major=int(major),
            minor=int(minor),
            patch=int(patch or 0),
            raw=result.stdout.strip(),
        )

    def _build_command(self, args: Sequence[str]) -> list[str]:
        # Строка — тоже последовательность: без этой проверки "status" превратилась бы
        # в аргументы "s", "t", "a"... Аргументы всегда передаются списком.
        if isinstance(args, (str, bytes)):
            raise TypeError("Git arguments must be a sequence of strings, not a single string")
        arguments = list(args)
        for argument in arguments:
            if not isinstance(argument, str):
                raise TypeError(
                    f"Git argument must be str, got {type(argument).__name__}: {argument!r}"
                )
        return [self._executable, *arguments]

    @staticmethod
    def _validate_cwd(cwd: str | os.PathLike[str] | None, command: list[str]) -> str | None:
        if cwd is None:
            return None
        path = os.fspath(cwd)
        # Проверка заранее нужна, чтобы отличить неверный каталог от отсутствующего Git:
        # в обоих случаях ОС сообщает FileNotFoundError.
        if not os.path.isdir(path):
            raise GitLaunchError(
                f"Working directory does not exist or is not a directory: {path}",
                command=command,
            )
        return path


def _elapsed_ms(started: float) -> int:
    """Миллисекунды с момента ``started`` (для журнала; не меньше нуля)."""
    return max(0, int((time.perf_counter() - started) * 1000))


def _decode(data: bytes | str | None) -> str:
    """Декодирует вывод процесса как UTF-8 (ТЗ 9.1).

    Вывод читается как байты, а не в текстовом режиме: иначе были бы потеряны
    символы ``\\r`` (CRLF в diff). Недопустимые байты заменяются на U+FFFD.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")
