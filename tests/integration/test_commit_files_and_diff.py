"""Integration-тесты списка файлов и diff коммитов на реальном Git."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from git_visualizer.application.services.diff_service import DiffService
from git_visualizer.application.services.history_service import HistoryService
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import Commit, Repository
from git_visualizer.infrastructure.git.git_repository import GitRepository
from git_visualizer.infrastructure.git.git_runner import GitRunner

Git = Callable[..., str]
MakeRepo = Callable[..., Path]


def make_factory():
    runner = GitRunner(default_timeout=60)

    def factory(path: Path) -> GitRepository:
        return GitRepository(runner, path)

    return factory


def load_repository(path: Path) -> tuple[Repository, tuple[Commit, ...], DiffService]:
    factory = make_factory()
    repository = RepositoryService(factory).open_repository(path)
    commits = HistoryService(factory).get_history(repository)
    return repository, commits, DiffService(factory)


def commit_all(git: Git, path: Path, message: str) -> str:
    git(path, "add", "-A")
    git(
        path,
        "-c", "user.name=Test",
        "-c", "user.email=test@example.com",
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", message,
    )
    return git(path, "rev-parse", "HEAD").strip()


def by_subject(commits: tuple[Commit, ...], subject: str) -> Commit:
    return next(commit for commit in commits if commit.subject == subject)


def test_root_commit_files_and_diff_use_empty_tree(make_repo: MakeRepo) -> None:
    path = make_repo("root-diff", commits=1)
    repository, commits, diff_service = load_repository(path)
    root = commits[-1]

    files = diff_service.get_commit_files(repository, root)

    assert [(change.path, change.change_type) for change in files] == [
        ("file0.txt", FileChangeType.ADDED)
    ]
    diff = diff_service.get_file_diff(repository, root, files[0])
    assert diff.base_commit_hash is None
    assert diff.is_binary is False
    assert "new file mode" in diff.text
    assert "+0" in diff.text


def test_regular_commit_compares_with_parent_and_shows_changes(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("regular-diff", commits=1)
    (path / "file0.txt").write_text("changed\n", encoding="utf-8")
    commit_all(git, path, "modify file")

    repository, commits, diff_service = load_repository(path)
    latest = by_subject(commits, "modify file")
    parent_hash = latest.parent_hashes[0]

    files = diff_service.get_commit_files(repository, latest)

    assert len(files) == 1
    assert files[0].path == "file0.txt"
    assert files[0].change_type is FileChangeType.MODIFIED
    diff = diff_service.get_file_diff(repository, latest, files[0])
    assert diff.base_commit_hash == parent_hash
    assert "-0" in diff.text
    assert "+changed" in diff.text


def test_rename_and_delete_with_unicode_and_spaces(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("rename-diff", commits=1)
    old_path = "старый файл.txt"
    new_path = "новый файл.txt"
    (path / old_path).write_text("строка один\nстрока два\n", encoding="utf-8")
    commit_all(git, path, "add unicode file")

    git(path, "mv", "--", old_path, new_path)
    (path / new_path).write_text(
        "строка один\nстрока два\nстрока три\n", encoding="utf-8"
    )
    git(path, "rm", "--", "file0.txt")
    commit_all(git, path, "rename and delete")

    repository, commits, diff_service = load_repository(path)
    latest = by_subject(commits, "rename and delete")

    files = diff_service.get_commit_files(repository, latest)
    changes = {change.path: change for change in files}

    renamed = changes[new_path]
    assert renamed.change_type is FileChangeType.RENAMED
    assert renamed.old_path == old_path
    assert changes["file0.txt"].change_type is FileChangeType.DELETED

    diff = diff_service.get_file_diff(repository, latest, renamed)
    assert diff.base_commit_hash == latest.parent_hashes[0]
    assert f"rename from {old_path}" in diff.text
    assert f"rename to {new_path}" in diff.text
    assert "+строка три" in diff.text


def test_copy_is_reported_when_git_finds_it(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("copy-diff", commits=1)
    (path / "copy of file0.txt").write_text("0\n", encoding="utf-8")
    commit_all(git, path, "copy file")

    repository, commits, diff_service = load_repository(path)
    latest = by_subject(commits, "copy file")

    files = diff_service.get_commit_files(repository, latest)

    assert [(change.path, change.change_type, change.old_path) for change in files] == [
        ("copy of file0.txt", FileChangeType.COPIED, "file0.txt")
    ]


def test_merge_commit_compares_with_first_parent(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("merge-diff", commits=1)
    git(path, "switch", "-c", "feature")
    (path / "feature.txt").write_text("feature\n", encoding="utf-8")
    commit_all(git, path, "feature change")

    git(path, "switch", "main")
    (path / "main.txt").write_text("main\n", encoding="utf-8")
    commit_all(git, path, "main change")
    first_parent = git(path, "rev-parse", "HEAD").strip()

    git(
        path,
        "-c", "user.name=Test",
        "-c", "user.email=test@example.com",
        "-c", "commit.gpgsign=false",
        "merge", "--no-ff", "-m", "merge feature", "feature",
    )

    repository, commits, diff_service = load_repository(path)
    merge = by_subject(commits, "merge feature")
    assert merge.is_merge
    assert merge.parent_hashes[0] == first_parent

    files = diff_service.get_commit_files(repository, merge)

    assert [(change.path, change.change_type) for change in files] == [
        ("feature.txt", FileChangeType.ADDED)
    ]
    diff = diff_service.get_file_diff(repository, merge, files[0])
    assert diff.base_commit_hash == first_parent
    assert "+feature" in diff.text


def test_binary_file_diff_is_flagged(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("binary-diff", commits=1)
    (path / "image.bin").write_bytes(b"\x00\x01\x02\x03")
    commit_all(git, path, "add binary")

    repository, commits, diff_service = load_repository(path)
    latest = by_subject(commits, "add binary")
    files = diff_service.get_commit_files(repository, latest)
    image = next(change for change in files if change.path == "image.bin")

    diff = diff_service.get_file_diff(repository, latest, image)

    assert diff.is_binary is True
    assert diff.text == ""
    assert diff.base_commit_hash == latest.parent_hashes[0]


def test_large_diff_is_truncated(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("large-diff", commits=1)
    (path / "file0.txt").write_text(
        "\n".join(f"line {number}" for number in range(100)) + "\n",
        encoding="utf-8",
    )
    commit_all(git, path, "large change")

    factory = make_factory()
    repository = RepositoryService(factory).open_repository(path)
    commits = HistoryService(factory).get_history(repository)
    latest = by_subject(commits, "large change")
    service = DiffService(factory, max_diff_chars=80)

    files = service.get_commit_files(repository, latest)
    diff = service.get_file_diff(repository, latest, files[0])

    assert diff.is_truncated is True
    assert 0 < len(diff.text) <= 80
    assert diff.text.endswith("\n")
