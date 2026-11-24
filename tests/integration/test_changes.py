"""Integration-тесты состояния рабочей директории и diff staged/unstaged."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from git_visualizer.application.services.changes_service import ChangesService
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import ChangedFile, Repository
from git_visualizer.infrastructure.git.git_errors import GitCommandError
from git_visualizer.infrastructure.git.git_repository import GitRepository
from git_visualizer.infrastructure.git.git_runner import GitRunner

Git = Callable[..., str]
MakeRepo = Callable[..., Path]


def make_factory():
    runner = GitRunner(default_timeout=60)

    def factory(path: Path) -> GitRepository:
        return GitRepository(runner, path)

    return factory


def load(path: Path) -> tuple[Repository, ChangesService]:
    factory = make_factory()
    repository = RepositoryService(factory).open_repository(path)
    return repository, ChangesService(factory)


def commit(git: Git, path: Path, message: str) -> None:
    git(path, "add", "-A")
    git(
        path,
        "-c", "user.name=Test",
        "-c", "user.email=test@example.com",
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", message,
    )


def status_by_path(path: Path) -> dict[str, ChangedFile]:
    repository, service = load(path)
    return {change.path: change for change in service.get_status(repository)}


def test_clean_repository_has_no_changes(make_repo: MakeRepo) -> None:
    path = make_repo("clean-status", commits=1)
    repository, service = load(path)

    assert service.get_status(repository) == ()


def test_status_reports_all_categories(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("status-categories", commits=1)
    (path / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    (path / "renamed old.txt").write_text("rename me\n", encoding="utf-8")
    commit(git, path, "prepare files")

    (path / "file0.txt").write_text("changed\n", encoding="utf-8")
    (path / "added.txt").write_text("added\n", encoding="utf-8")
    git(path, "add", "--", "added.txt")
    (path / "both.txt").write_text("first\n", encoding="utf-8")
    git(path, "add", "--", "both.txt")
    (path / "both.txt").write_text("second\n", encoding="utf-8")
    (path / "deleted.txt").unlink()
    git(path, "mv", "--", "renamed old.txt", "renamed new.txt")
    (path / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    changes = status_by_path(path)

    assert changes["file0.txt"].unstaged_change is FileChangeType.MODIFIED
    assert changes["file0.txt"].staged_change is None

    assert changes["added.txt"].staged_change is FileChangeType.ADDED
    assert changes["added.txt"].unstaged_change is None

    both = changes["both.txt"]
    # Файл создан и добавлен в индекс, затем изменён ещё раз: AM.
    assert both.staged_change is FileChangeType.ADDED
    assert both.unstaged_change is FileChangeType.MODIFIED
    assert both.is_staged and both.is_unstaged

    assert changes["deleted.txt"].unstaged_change is FileChangeType.DELETED

    renamed = changes["renamed new.txt"]
    assert renamed.staged_change is FileChangeType.RENAMED
    assert renamed.old_path == "renamed old.txt"

    assert changes["untracked.txt"].is_untracked
    assert changes["untracked.txt"].staged_change is None
    assert changes["untracked.txt"].unstaged_change is None


def test_untracked_directory_is_reported(make_repo: MakeRepo) -> None:
    path = make_repo("untracked-dir", commits=1)
    (path / "new dir").mkdir()
    (path / "new dir" / "file.txt").write_text("new\n", encoding="utf-8")

    changes = status_by_path(path)

    assert changes["new dir/"].is_untracked


def test_unmerged_file_is_reported_as_conflicted(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("conflict-status", commits=0)
    file = path / "conflict.txt"
    file.write_text("base\n", encoding="utf-8")
    commit(git, path, "base")

    git(path, "switch", "-q", "-c", "feature")
    file.write_text("feature\n", encoding="utf-8")
    commit(git, path, "feature")
    git(path, "switch", "-q", "main")
    file.write_text("main\n", encoding="utf-8")
    commit(git, path, "main")
    with pytest.raises(GitCommandError):
        # Идентичность нужна Git и для merge; без неё он падает раньше конфликта.
        git(
            path,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "-c", "commit.gpgsign=false",
            "merge", "feature",
        )

    conflict = status_by_path(path)["conflict.txt"]

    assert conflict.is_conflicted
    assert conflict.staged_change is None
    assert conflict.unstaged_change is None
    assert not conflict.is_staged
    assert not conflict.is_unstaged


def test_unstaged_diff_compares_worktree_with_index(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("unstaged-diff", commits=1)
    (path / "file0.txt").write_text("changed\n", encoding="utf-8")
    repository, service = load(path)

    change = {item.path: item for item in service.get_status(repository)}["file0.txt"]
    diff = service.get_file_diff(repository, change, staged=False)

    assert diff.base_commit_hash is None
    assert diff.is_binary is False
    assert "-0" in diff.text
    assert "+changed" in diff.text


def test_staged_and_unstaged_diffs_differ_for_same_file(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("both-diffs", commits=1)
    (path / "file0.txt").write_text("first\n", encoding="utf-8")
    git(path, "add", "--", "file0.txt")
    (path / "file0.txt").write_text("second\n", encoding="utf-8")
    repository, service = load(path)

    change = {item.path: item for item in service.get_status(repository)}["file0.txt"]
    staged = service.get_file_diff(repository, change, staged=True)
    unstaged = service.get_file_diff(repository, change, staged=False)

    assert "-0" in staged.text and "+first" in staged.text
    assert "-first" not in staged.text
    assert "-first" in unstaged.text and "+second" in unstaged.text
    assert staged.base_commit_hash is None
    assert unstaged.base_commit_hash is None


def test_staged_rename_diff_shows_both_paths(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("rename-worktree", commits=1)
    (path / "старый файл.txt").write_text("line\n", encoding="utf-8")
    commit(git, path, "add rename source")
    git(path, "mv", "--", "старый файл.txt", "новый файл.txt")
    repository, service = load(path)

    change = {item.path: item for item in service.get_status(repository)}[
        "новый файл.txt"
    ]
    assert change.old_path == "старый файл.txt"
    diff = service.get_file_diff(repository, change, staged=True)

    assert "rename from старый файл.txt" in diff.text
    assert "rename to новый файл.txt" in diff.text


def test_binary_file_diff_is_marked(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("binary-worktree", commits=1)
    (path / "image.bin").write_bytes(b"\x00\x01\x02")
    commit(git, path, "add binary")
    (path / "image.bin").write_bytes(b"\x00\x03\x04")
    repository, service = load(path)

    change = {item.path: item for item in service.get_status(repository)}["image.bin"]
    diff = service.get_file_diff(repository, change, staged=False)

    assert diff.is_binary is True
    assert diff.text == ""


def test_deleted_file_unstaged_diff_shows_removed_content(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("deleted-diff", commits=1)
    (path / "file0.txt").unlink()
    repository, service = load(path)

    change = {item.path: item for item in service.get_status(repository)}["file0.txt"]
    diff = service.get_file_diff(repository, change, staged=False)

    assert diff.is_binary is False
    assert "-0" in diff.text


def test_staged_new_file_in_repository_without_commits(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("unborn-status", commits=0)
    (path / "first.txt").write_text("first\n", encoding="utf-8")
    git(path, "add", "--", "first.txt")
    repository, service = load(path)

    change = {item.path: item for item in service.get_status(repository)}["first.txt"]
    assert change.staged_change is FileChangeType.ADDED
    diff = service.get_file_diff(repository, change, staged=True)
    assert "new file mode" in diff.text
    assert "+first" in diff.text


def test_untracked_file_diff_is_rejected(make_repo: MakeRepo) -> None:
    path = make_repo("untracked-diff", commits=1)
    (path / "new.txt").write_text("new\n", encoding="utf-8")
    repository, service = load(path)

    change = {item.path: item for item in service.get_status(repository)}["new.txt"]
    with pytest.raises(ValueError):
        service.get_file_diff(repository, change, staged=False)
