"""Unit-тесты ChangesService: выбор секции diff, лимит и передача путей."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_visualizer.application.services.changes_service import ChangesService
from git_visualizer.application.services.diff_service import DEFAULT_MAX_DIFF_CHARS
from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import ChangedFile, Diff, Repository
from git_visualizer.infrastructure.git.git_errors import GitCommandError

H = "3" * 40
REPOSITORY = Repository(
    path=Path("repo"), name="repo", current_branch="main", head_commit=H
)


class FakeGit:
    def __init__(
        self,
        *,
        status: tuple[ChangedFile, ...] = (),
        diff: Diff | None = None,
        status_error: BaseException | None = None,
        diff_error: BaseException | None = None,
    ) -> None:
        self.status = status
        self.diff = diff
        self.status_error = status_error
        self.diff_error = diff_error
        self.diff_calls: list[dict[str, object]] = []

    def get_status(self):
        if self.status_error is not None:
            raise self.status_error
        return self.status

    def get_worktree_diff(self, **kwargs):
        self.diff_calls.append(kwargs)
        if self.diff_error is not None:
            raise self.diff_error
        assert self.diff is not None
        return self.diff


def build_service(
    git: FakeGit, *, max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS
) -> ChangesService:
    return ChangesService(lambda path: git, max_diff_chars=max_diff_chars)


def test_default_limit_is_shared_with_diff_service() -> None:
    service = build_service(FakeGit())

    assert service.max_diff_chars == DEFAULT_MAX_DIFF_CHARS
    assert DEFAULT_MAX_DIFF_CHARS == 200_000


def test_non_positive_limit_is_rejected() -> None:
    with pytest.raises(ValueError):
        ChangesService(lambda path: FakeGit(), max_diff_chars=0)


def test_status_is_requested_without_arguments() -> None:
    changed = (
        ChangedFile(path="both.txt", staged_change=FileChangeType.MODIFIED,
                    unstaged_change=FileChangeType.DELETED),
    )
    service = build_service(FakeGit(status=changed))

    assert service.get_status(REPOSITORY) == changed


def test_staged_diff_passes_paths_and_limit() -> None:
    changed_file = ChangedFile(
        path="новый файл.txt",
        old_path="старый файл.txt",
        staged_change=FileChangeType.RENAMED,
    )
    diff = Diff(path=changed_file.path, old_path=changed_file.old_path, text="text")
    git = FakeGit(diff=diff)

    result = build_service(git, max_diff_chars=321).get_file_diff(
        REPOSITORY, changed_file, staged=True
    )

    assert result is diff
    assert git.diff_calls == [
        {
            "path": "новый файл.txt",
            "old_path": "старый файл.txt",
            "staged": True,
            "max_chars": 321,
        }
    ]


def test_unstaged_diff_for_conflicted_file_is_allowed() -> None:
    changed_file = ChangedFile(path="conflict.txt", is_conflicted=True)
    diff = Diff(path=changed_file.path, text="@@@")
    git = FakeGit(diff=diff)

    build_service(git).get_file_diff(REPOSITORY, changed_file, staged=False)

    assert git.diff_calls[0]["staged"] is False


@pytest.mark.parametrize(
    ("changed_file", "staged"),
    [
        (ChangedFile(path="untracked.txt", is_untracked=True), False),
        (ChangedFile(path="only-staged.txt", staged_change=FileChangeType.ADDED), False),
        (ChangedFile(path="only-unstaged.txt", unstaged_change=FileChangeType.MODIFIED), True),
        (ChangedFile(path="conflict.txt", is_conflicted=True), True),
    ],
)
def test_section_mismatch_is_rejected_before_git(
    changed_file: ChangedFile, staged: bool
) -> None:
    git = FakeGit()

    with pytest.raises(ValueError):
        build_service(git).get_file_diff(REPOSITORY, changed_file, staged=staged)

    assert git.diff_calls == []


def test_git_errors_are_propagated() -> None:
    status_error = GitCommandError(128, command=("git", "status"))
    diff_error = GitCommandError(128, command=("git", "diff"))
    changed_file = ChangedFile(
        path="file.txt", unstaged_change=FileChangeType.MODIFIED
    )
    git = FakeGit(status_error=status_error, diff_error=diff_error)
    service = build_service(git)

    with pytest.raises(GitCommandError) as status_raised:
        service.get_status(REPOSITORY)
    assert status_raised.value is status_error

    with pytest.raises(GitCommandError) as diff_raised:
        service.get_file_diff(REPOSITORY, changed_file, staged=False)
    assert diff_raised.value is diff_error
