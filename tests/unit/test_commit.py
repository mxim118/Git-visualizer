"""Unit-тесты создания коммита: stdin, валидация сообщения и политика индекса."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_visualizer.application.services.commit_service import CommitService
from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import ChangedFile, Repository
from git_visualizer.infrastructure.git.git_errors import GitCommandError
from git_visualizer.infrastructure.git.git_repository import (
    GitRepository,
    build_commit_arguments,
)
from git_visualizer.infrastructure.git.git_runner import GitResult

H = "3" * 40
REPOSITORY = Repository(
    path=Path("repo"), name="repo", current_branch="main", head_commit=H
)


# --- аргументы Git и передача сообщения через stdin ---


def test_commit_arguments_read_message_from_stdin() -> None:
    assert build_commit_arguments() == ["commit", "-F", "-"]


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, args, *, cwd=None, check=True, stdin_data=None):
        self.calls.append(
            {
                "args": list(args),
                "cwd": cwd,
                "check": check,
                "stdin_data": stdin_data,
            }
        )
        return GitResult(command=tuple(args), returncode=0, stdout="", stderr="")


def test_repository_commit_passes_message_only_through_stdin() -> None:
    runner = RecordingRunner()
    repository = GitRepository(runner, Path("repo"))
    message = "Заголовок\n\nТело с 'кавычками' и $символами\n"

    repository.commit(message)

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["args"] == ["commit", "-F", "-"]
    assert call["stdin_data"] == message
    # Сообщение не попадает в аргументы процесса (ни целиком, ни частями).
    assert message not in call["args"]
    assert call["cwd"] == Path("repo")
    assert call["check"] is True


# --- CommitService: валидация сообщения ---


@pytest.mark.parametrize("message", ["", " ", "   \n\t ", "\r\n  \r\n"])
def test_blank_message_is_rejected(message: str) -> None:
    with pytest.raises(ValueError):
        CommitService.validate_message(message)


def test_message_with_text_is_returned_unchanged() -> None:
    message = "  Subject  \n\n  Body  "

    assert CommitService.validate_message(message) == message


def test_blank_message_is_rejected_before_git_is_called() -> None:
    created: list[Path] = []

    def factory(path: Path) -> FakeGit:
        created.append(path)
        return FakeGit()

    with pytest.raises(ValueError):
        CommitService(factory).commit(REPOSITORY, "   ")

    assert created == []


# --- CommitService: коммит только из индекса ---


class FakeGit:
    def __init__(
        self,
        *,
        status: tuple[ChangedFile, ...] = (),
        status_error: BaseException | None = None,
        commit_error: BaseException | None = None,
    ) -> None:
        self.status = status
        self.status_error = status_error
        self.commit_error = commit_error
        self.status_requests = 0
        self.commit_calls: list[str] = []

    def get_status(self) -> tuple[ChangedFile, ...]:
        self.status_requests += 1
        if self.status_error is not None:
            raise self.status_error
        return self.status

    def commit(self, message: str) -> None:
        self.commit_calls.append(message)
        if self.commit_error is not None:
            raise self.commit_error


def build_service(git: FakeGit) -> CommitService:
    return CommitService(lambda path: git)


@pytest.mark.parametrize(
    "changes",
    [
        (),
        (ChangedFile(path="modified.txt", unstaged_change=FileChangeType.MODIFIED),),
        (ChangedFile(path="new.txt", is_untracked=True),),
        (ChangedFile(path="conflict.txt", is_conflicted=True),),
    ],
)
def test_commit_rejects_empty_index_before_running_git(
    changes: tuple[ChangedFile, ...],
) -> None:
    git = FakeGit(status=changes)

    with pytest.raises(ValueError):
        build_service(git).commit(REPOSITORY, "Message")

    assert git.status_requests == 1
    assert git.commit_calls == []


def test_commit_uses_current_status_and_passes_message() -> None:
    git = FakeGit(
        status=(
            ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),
            ChangedFile(path="unstaged.txt", unstaged_change=FileChangeType.MODIFIED),
            ChangedFile(path="untracked.txt", is_untracked=True),
        )
    )

    build_service(git).commit(REPOSITORY, "Commit message")

    assert git.status_requests == 1
    assert git.commit_calls == ["Commit message"]


def test_git_errors_are_propagated() -> None:
    status_error = GitCommandError(128, command=("git", "status"))
    commit_error = GitCommandError(1, command=("git", "commit", "-F", "-"))

    with pytest.raises(GitCommandError) as status_raised:
        build_service(FakeGit(status_error=status_error)).commit(REPOSITORY, "Message")
    assert status_raised.value is status_error

    git = FakeGit(
        status=(ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),),
        commit_error=commit_error,
    )
    with pytest.raises(GitCommandError) as commit_raised:
        build_service(git).commit(REPOSITORY, "Message")
    assert commit_raised.value is commit_error


def test_has_staged_changes_policy() -> None:
    staged = ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED)
    both = ChangedFile(
        path="both.txt",
        staged_change=FileChangeType.MODIFIED,
        unstaged_change=FileChangeType.MODIFIED,
    )
    unstaged = ChangedFile(path="unstaged.txt", unstaged_change=FileChangeType.MODIFIED)

    assert CommitService.has_staged_changes((staged, unstaged)) is True
    assert CommitService.has_staged_changes((both,)) is True
    assert CommitService.has_staged_changes((unstaged,)) is False
    assert CommitService.has_staged_changes(()) is False
