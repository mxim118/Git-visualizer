"""Интеграционные проверки локального журнала на реальном Git (ТЗ 12.3)."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Callable

import pytest

from git_visualizer import logging_setup
from git_visualizer.infrastructure.git.git_runner import GitRunner
from git_visualizer.logging_setup import setup_logging

LOGGER = logging.getLogger()
_HANDLER_MARKER = "_git_visualizer_log_handler"


@pytest.fixture(autouse=True)
def _isolate_git_logger(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    saved_level = LOGGER.level
    monkeypatch.setattr(logging_setup, "_warning_emitted", False)

    yield

    # Удаляем только собственный обработчик, не трогая обработчики pytest.
    for handler in list(LOGGER.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            LOGGER.removeHandler(handler)
            handler.close()
    LOGGER.setLevel(saved_level)


def _flush_log() -> None:
    for handler in LOGGER.handlers:
        handler.flush()


def test_real_git_status_and_log_are_logged_outside_repository(
    make_repo: Callable[..., Path], tmp_path: Path
) -> None:
    repository = make_repo("logged-repository", commits=2)
    # Непустой вывод status: он не должен оказаться в журнале.
    (repository / "untracked.txt").write_text("private file content\n", encoding="utf-8")
    log_path = setup_logging(tmp_path / "logs")

    runner = GitRunner(default_timeout=60)
    status = runner.run(["status", "--porcelain=v2"], cwd=repository)
    history = runner.run(["log", "-1", "--format=%s"], cwd=repository)

    _flush_log()
    lines = log_path.read_text(encoding="utf-8").splitlines()

    status_line = next(line for line in lines if " git status " in line)
    log_line = next(line for line in lines if " git log " in line)

    assert f"cwd={repository}" in status_line
    assert "duration_ms=" in status_line
    assert "exit=0" in status_line
    assert "stdin=no" in status_line
    assert re.search(r"duration_ms=\d+", status_line)
    assert f"cwd={repository}" in log_line
    assert "exit=0" in log_line

    # stdout успешных команд не журналируется.
    assert status.stdout.strip()
    assert status.stdout.strip() not in "\n".join(lines)
    assert history.stdout.strip() not in "\n".join(lines)

    # Журнал создан вне репозитория; внутри репозитория файлов журнала нет.
    assert log_path == tmp_path / "logs" / "git-visualizer.log"
    assert log_path.is_file()
    assert not log_path.is_relative_to(repository)
    assert list(repository.rglob("git-visualizer.log*")) == []
    assert list(repository.rglob("*.log")) == []
