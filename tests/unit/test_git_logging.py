"""Тесты локального журнала Git (ТЗ 12.3).

Проверяются определение типа операции, маскировка секретов, записи об успехе и
неудачах GitRunner, а также настройка файлового журнала.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from git_visualizer import logging_setup
from git_visualizer.infrastructure.git import git_logging
from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitLaunchError,
    GitNotFoundError,
    GitTimeoutError,
)
from git_visualizer.infrastructure.git.git_runner import GitRunner
from git_visualizer.logging_setup import setup_logging

LOGGER = logging.getLogger()
_HANDLER_MARKER = "_git_visualizer_log_handler"


class FakeProcess:
    """Подменяет subprocess.run, возвращая заданный результат или бросая ошибку."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        raises: BaseException | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raises = raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            command, self.returncode, stdout=self.stdout, stderr=self.stderr
        )


@pytest.fixture(autouse=True)
def _isolate_git_logger(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Не даёт обработчикам и предупреждениям переходить между тестами."""
    saved_level = LOGGER.level
    monkeypatch.setattr(git_logging, "_warning_emitted", False)
    monkeypatch.setattr(logging_setup, "_warning_emitted", False)

    yield

    # Удаляем только собственный обработчик: у pytest на корневом логгере
    # могут быть свои обработчики, которые трогать нельзя.
    for handler in list(LOGGER.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            LOGGER.removeHandler(handler)
            handler.close()
    LOGGER.setLevel(saved_level)


@pytest.fixture
def log_file(tmp_path: Path) -> Path:
    return setup_logging(tmp_path / "logs")


def _log_lines(path: Path) -> list[str]:
    for handler in LOGGER.handlers:
        handler.flush()
    return path.read_text(encoding="utf-8").splitlines()


def _single_log_line(path: Path) -> str:
    lines = _log_lines(path)
    assert len(lines) == 1, lines
    return lines[0]


# --------------------------------------------------------------------------
# git_subcommand
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["-c", "core.quotePath=false", "diff", "--cached"], "diff"),
        (["--no-optional-locks", "status", "--porcelain=v2"], "status"),
        (["--literal-pathspecs", "add", "--", "file.txt"], "add"),
        (["-c", "user.name=X", "-c", "user.email=x@example.com", "log"], "log"),
        (["status"], "status"),
        (["--version"], "unknown"),
        ([], "unknown"),
        (["-c"], "unknown"),
    ],
)
def test_git_subcommand_returns_operation_type(args: list[str], expected: str) -> None:
    assert git_logging.git_subcommand(args) == expected


# --------------------------------------------------------------------------
# redact
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "https://user:token@host/repo.git",
            "https://***@host/repo.git",
        ),
        (
            "https://token@host/repo.git",
            "https://***@host/repo.git",
        ),
        (
            "https://host/repo.git?token=abc123&ref=main",
            "https://host/repo.git?token=***&ref=main",
        ),
        (
            "https://host/repo.git?access_token=abc123",
            "https://host/repo.git?access_token=***",
        ),
        (
            "https://host/repo.git?private_token=abc123",
            "https://host/repo.git?private_token=***",
        ),
        (
            "https://user:secret@host/r.git?token=abc123",
            "https://***@host/r.git?token=***",
        ),
        ("https://host/repo.git", "https://host/repo.git"),
        ("git@host:path/repo.git", "git@host:path/repo.git"),
        (r"C:\repo", r"C:\repo"),
        (r"\\server\share\repo", r"\\server\share\repo"),
    ],
)
def test_redact_masks_only_secrets(text: str, expected: str) -> None:
    assert git_logging.redact(text) == expected


# --------------------------------------------------------------------------
# Записи GitRunner
# --------------------------------------------------------------------------


def test_success_writes_one_info_record_with_duration_and_exit_zero(
    log_file: Path, tmp_path: Path
) -> None:
    fake = FakeProcess(returncode=0, stdout=b"secret stdout\n", stderr=b"warning\n")

    result = GitRunner(process_runner=fake).run(["status"], cwd=tmp_path)

    assert result.returncode == 0
    line = _single_log_line(log_file)
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} INFO "
        r"git_visualizer\.git git status ",
        line,
    ), line
    assert f"cwd={tmp_path}" in line
    duration = re.search(r"duration_ms=(\d+)", line)
    assert duration is not None and int(duration.group(1)) >= 0
    assert "exit=0" in line
    assert "stdin=no" in line
    assert "kind=" not in line
    assert "stderr=" not in line
    # stdout и stderr успешной команды в журнал не попадают.
    assert "secret stdout" not in line
    assert "warning" not in line


def test_nonzero_exit_is_warning_with_kind_and_single_line_stderr(
    log_file: Path, tmp_path: Path
) -> None:
    fake = FakeProcess(
        returncode=128,
        stderr=(
            b"fatal: not a git repository (or any of the parent directories): .git\n"
            b"second line\n"
        ),
    )

    with pytest.raises(GitCommandError):
        GitRunner(process_runner=fake).run(["status"], cwd=tmp_path)

    line = _single_log_line(log_file)
    assert " WARNING " in line
    assert "git status " in line
    assert "exit=128" in line
    assert "kind=not_a_repository" in line
    assert "stderr=fatal: not a git repository (or any of the parent directories): .git" in line
    assert "second line" in line
    assert " | second line" in line
    assert "\n" not in line


def test_stderr_is_limited_to_1000_characters(log_file: Path, tmp_path: Path) -> None:
    fake = FakeProcess(returncode=1, stderr=b"x" * 1500)

    with pytest.raises(GitCommandError):
        GitRunner(process_runner=fake).run(["status"], cwd=tmp_path)

    line = _single_log_line(log_file)
    logged_stderr = line.split("stderr=", 1)[1]
    assert len(logged_stderr) == git_logging.STDERR_LIMIT


def test_timeout_is_logged_with_kind_timeout(log_file: Path, tmp_path: Path) -> None:
    original = subprocess.TimeoutExpired(
        cmd="git",
        timeout=5,
        output=b"partial stdout",
        stderr=b"partial\nstderr\n",
    )

    with pytest.raises(GitTimeoutError):
        GitRunner(process_runner=FakeProcess(raises=original)).run(["fetch"], cwd=tmp_path)

    line = _single_log_line(log_file)
    assert " ERROR " in line
    assert "git fetch " in line
    assert "exit=none" in line
    assert "kind=timeout" in line
    assert "stderr=partial | stderr" in line
    assert "partial stdout" not in line


def test_git_not_found_is_logged(log_file: Path, tmp_path: Path) -> None:
    original = FileNotFoundError(2, "No such file or directory", "git")

    with pytest.raises(GitNotFoundError):
        GitRunner(process_runner=FakeProcess(raises=original)).run(["status"], cwd=tmp_path)

    line = _single_log_line(log_file)
    assert " ERROR " in line
    assert "exit=none" in line
    assert "kind=git_not_found" in line


def test_launch_error_before_process_is_logged(log_file: Path, tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    fake = FakeProcess()

    with pytest.raises(GitLaunchError):
        GitRunner(process_runner=fake).run(["status"], cwd=missing)

    assert fake.calls == []
    line = _single_log_line(log_file)
    assert " ERROR " in line
    assert f"cwd={missing}" in line
    assert "duration_ms=0" in line
    assert "exit=none" in line
    assert "kind=git_launch" in line


def test_stdin_data_is_never_logged(log_file: Path, tmp_path: Path) -> None:
    secret_message = "secret commit message with token"
    fake = FakeProcess(returncode=0)

    GitRunner(process_runner=fake).run(
        ["commit", "-F", "-"], cwd=tmp_path, stdin_data=secret_message
    )

    line = _single_log_line(log_file)
    assert "stdin=yes" in line
    assert secret_message not in line


def test_credentials_in_stderr_and_arguments_are_redacted(
    log_file: Path, tmp_path: Path
) -> None:
    url = "https://user:secret-token@example.com/r.git"
    fake = FakeProcess(
        returncode=128,
        stderr=(
            f"fatal: unable to access '{url}/': "
            "The requested URL returned error: 403\n"
        ).encode(),
    )

    with pytest.raises(GitCommandError):
        GitRunner(process_runner=fake).run(
            ["remote", "add", "--", "origin", url], cwd=tmp_path
        )

    line = _single_log_line(log_file)
    assert "git remote " in line
    assert "secret-token" not in line
    assert "https://***@example.com/r.git" in line


def test_query_credentials_in_stderr_are_redacted(log_file: Path, tmp_path: Path) -> None:
    fake = FakeProcess(
        returncode=128,
        stderr=b"fatal: unable to access 'https://example.com/r.git?token=secret-token'",
    )

    with pytest.raises(GitCommandError):
        GitRunner(process_runner=fake).run(["fetch"], cwd=tmp_path)

    line = _single_log_line(log_file)
    assert "secret-token" not in line
    assert "?token=***" in line


def test_logging_failure_does_not_affect_git_runner(
    log_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken_log(*args, **kwargs) -> None:
        raise RuntimeError("logging is broken")

    monkeypatch.setattr(git_logging._logger, "log", broken_log)
    runner = GitRunner(process_runner=FakeProcess(returncode=0))

    first = runner.run(["status"], cwd=tmp_path)
    second = runner.run(["status"], cwd=tmp_path)

    assert first.returncode == 0
    assert second.returncode == 0
    warning = capsys.readouterr().err
    assert warning.count("failed to write a Git log record") == 1


# --------------------------------------------------------------------------
# setup_logging
# --------------------------------------------------------------------------


def test_setup_logging_creates_file_with_rotating_handler(tmp_path: Path) -> None:
    path = setup_logging(tmp_path / "logs")

    assert path == tmp_path / "logs" / "git-visualizer.log"
    assert path.is_file()

    handler = next(
        handler
        for handler in LOGGER.handlers
        if getattr(handler, _HANDLER_MARKER, False)
    )
    assert isinstance(handler, RotatingFileHandler)
    assert handler.encoding == "utf-8"
    assert handler.maxBytes == 1_000_000
    assert handler.backupCount == 3

    git_logging.log_git_call(
        ["status"],
        cwd=tmp_path,
        duration_ms=3,
        returncode=0,
        stdin_used=False,
    )

    text = path.read_text(encoding="utf-8")
    assert re.search(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} INFO "
        r"git_visualizer\.git git status cwd=.* duration_ms=3 exit=0 stdin=no$",
        text,
        re.MULTILINE,
    ), text


def test_setup_logging_twice_does_not_duplicate_handlers(tmp_path: Path) -> None:
    path = setup_logging(tmp_path / "logs")
    handlers_after_first = [
        handler for handler in LOGGER.handlers if getattr(handler, _HANDLER_MARKER, False)
    ]

    again = setup_logging(tmp_path / "logs")

    handlers_after_second = [
        handler for handler in LOGGER.handlers if getattr(handler, _HANDLER_MARKER, False)
    ]
    assert again == path
    assert len(handlers_after_first) == 1
    assert handlers_after_second == handlers_after_first


def test_setup_logging_honours_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom-logs"
    monkeypatch.setenv(logging_setup.LOG_DIR_ENV_VAR, str(custom))

    path = setup_logging()

    assert path == custom / "git-visualizer.log"
    assert path.is_file()


def test_setup_logging_unavailable_directory_does_not_raise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_mkdir(*args, **kwargs) -> None:
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)

    path = setup_logging(tmp_path / "logs")
    setup_logging(tmp_path / "other-logs")

    assert isinstance(path, Path)
    assert not any(
        getattr(handler, _HANDLER_MARKER, False) for handler in LOGGER.handlers
    )
    error = capsys.readouterr().err
    assert error.count("cannot create the log file") == 1
    assert "access denied" in error
