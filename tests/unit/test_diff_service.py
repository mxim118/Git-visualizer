"""Unit-тесты DiffService: выбор родителя, root и ограничение diff."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from git_visualizer.application.services.diff_service import (
    DEFAULT_MAX_DIFF_CHARS,
    DiffService,
)
from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import Commit, Diff, FileChange, Repository
from git_visualizer.infrastructure.git.git_errors import GitCommandError

NOW = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
P1, P2, H = "1" * 40, "2" * 40, "3" * 40


def make_commit(*, parents: tuple[str, ...]) -> Commit:
    return Commit(
        hash=H,
        short_hash=H[:7],
        parent_hashes=parents,
        author_name="Author",
        author_email="author@example.com",
        authored_at=NOW,
        committer_name="Committer",
        committer_email="committer@example.com",
        committed_at=NOW,
        subject="Subject",
        body="",
    )


class FakeGit:
    def __init__(
        self,
        *,
        files: tuple[FileChange, ...] = (),
        diff: Diff | None = None,
        files_error: BaseException | None = None,
        diff_error: BaseException | None = None,
    ) -> None:
        self.files = files
        self.diff = diff
        self.files_error = files_error
        self.diff_error = diff_error
        self.file_calls: list[dict[str, object]] = []
        self.diff_calls: list[dict[str, object]] = []

    def get_commit_files(self, **kwargs):
        self.file_calls.append(kwargs)
        if self.files_error is not None:
            raise self.files_error
        return self.files

    def get_commit_file_diff(self, **kwargs):
        self.diff_calls.append(kwargs)
        if self.diff_error is not None:
            raise self.diff_error
        assert self.diff is not None
        return self.diff


def build_service(
    git: FakeGit, *, max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS
) -> DiffService:
    return DiffService(lambda path: git, max_diff_chars=max_diff_chars)


REPOSITORY = Repository(
    path=Path("repo"), name="repo", current_branch="main", head_commit=H
)
FILE = FileChange(
    path="новый файл.txt", change_type=FileChangeType.RENAMED, old_path="старый файл.txt"
)


def test_default_limit_is_used() -> None:
    service = build_service(FakeGit())

    assert service.max_diff_chars == DEFAULT_MAX_DIFF_CHARS
    assert DEFAULT_MAX_DIFF_CHARS == 200_000


def test_non_positive_limit_is_rejected() -> None:
    with pytest.raises(ValueError):
        DiffService(lambda path: FakeGit(), max_diff_chars=0)


def test_regular_commit_uses_its_only_parent() -> None:
    git = FakeGit(files=(FILE,))
    commit = make_commit(parents=(P1,))

    files = build_service(git).get_commit_files(REPOSITORY, commit)

    assert files == (FILE,)
    assert git.file_calls == [{"commit_hash": H, "parent_hash": P1}]


def test_merge_commit_uses_first_parent() -> None:
    git = FakeGit(files=(FILE,))
    commit = make_commit(parents=(P1, P2))

    build_service(git).get_commit_files(REPOSITORY, commit)

    assert git.file_calls == [{"commit_hash": H, "parent_hash": P1}]


def test_root_commit_uses_empty_tree() -> None:
    git = FakeGit(files=(FILE,))
    commit = make_commit(parents=())

    build_service(git).get_commit_files(REPOSITORY, commit)

    assert git.file_calls == [{"commit_hash": H, "parent_hash": None}]


def test_file_diff_passes_paths_and_limit() -> None:
    diff = Diff(
        path=FILE.path,
        old_path=FILE.old_path,
        text="text",
        base_commit_hash=P1,
    )
    git = FakeGit(diff=diff)
    commit = make_commit(parents=(P1,))

    result = build_service(git, max_diff_chars=123).get_file_diff(
        REPOSITORY, commit, FILE
    )

    assert result is diff
    assert git.diff_calls == [
        {
            "commit_hash": H,
            "parent_hash": P1,
            "path": FILE.path,
            "old_path": FILE.old_path,
            "max_chars": 123,
        }
    ]


def test_root_file_diff_has_no_parent() -> None:
    diff = Diff(path=FILE.path, text="text", base_commit_hash=None)
    git = FakeGit(diff=diff)
    commit = make_commit(parents=())

    build_service(git).get_file_diff(REPOSITORY, commit, FILE)

    assert git.diff_calls[0]["parent_hash"] is None


def test_git_errors_are_propagated() -> None:
    files_error = GitCommandError(128, command=("git", "diff-tree"))
    diff_error = GitCommandError(128, command=("git", "diff"))
    git = FakeGit(files_error=files_error, diff_error=diff_error)
    service = build_service(git)
    commit = make_commit(parents=(P1,))

    with pytest.raises(GitCommandError) as files_raised:
        service.get_commit_files(REPOSITORY, commit)
    assert files_raised.value is files_error

    with pytest.raises(GitCommandError) as diff_raised:
        service.get_file_diff(REPOSITORY, commit, FILE)
    assert diff_raised.value is diff_error
