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


# --- Stage/Unstage через реальный Git ---


def test_stage_modified_file_keeps_working_tree(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("stage-modified", commits=1)
    file = path / "file0.txt"
    file.write_text("changed\n", encoding="utf-8")
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}["file0.txt"]

    service.stage_file(repository, change)

    staged = {item.path: item for item in service.get_status(repository)}["file0.txt"]
    assert staged.staged_change is FileChangeType.MODIFIED
    assert staged.unstaged_change is None
    assert file.read_text(encoding="utf-8") == "changed\n"


def test_stage_untracked_file_with_spaces_and_unicode(
    make_repo: MakeRepo
) -> None:
    path = make_repo("stage-untracked", commits=1)
    name = "новый файл с пробелами.txt"
    file = path / name
    file.write_text("new content\n", encoding="utf-8")
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}[name]

    service.stage_file(repository, change)

    staged = {item.path: item for item in service.get_status(repository)}[name]
    assert staged.staged_change is FileChangeType.ADDED
    assert staged.is_untracked is False
    assert staged.unstaged_change is None
    assert file.read_text(encoding="utf-8") == "new content\n"


def test_stage_deleted_file(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("stage-deleted", commits=1)
    (path / "deleted file.txt").write_text("delete me\n", encoding="utf-8")
    commit(git, path, "add deleted")
    (path / "deleted file.txt").unlink()
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}[
        "deleted file.txt"
    ]

    service.stage_file(repository, change)

    staged = {item.path: item for item in service.get_status(repository)}[
        "deleted file.txt"
    ]
    assert staged.staged_change is FileChangeType.DELETED
    assert staged.unstaged_change is None
    assert not (path / "deleted file.txt").exists()


def test_unstage_keeps_unstaged_changes_in_working_tree(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("unstage-keeps-worktree", commits=1)
    file = path / "файл с пробелом.txt"
    file.write_text("base\n", encoding="utf-8")
    commit(git, path, "add unicode file")
    file.write_text("first\n", encoding="utf-8")
    git(path, "add", "--", file.name)
    file.write_text("second\n", encoding="utf-8")
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}[file.name]
    assert change.staged_change is FileChangeType.MODIFIED
    assert change.unstaged_change is FileChangeType.MODIFIED

    service.unstage_file(repository, change)

    unstaged = {item.path: item for item in service.get_status(repository)}[file.name]
    assert unstaged.staged_change is None
    assert unstaged.unstaged_change is FileChangeType.MODIFIED
    # Unstage убрал файл из индекса, но не отменил изменения рабочей директории.
    assert file.read_text(encoding="utf-8") == "second\n"


def test_unstage_in_repository_without_commits_keeps_file(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("unborn-unstage", commits=0)
    file = path / "первый файл.txt"
    file.write_text("content\n", encoding="utf-8")
    git(path, "add", "--", file.name)
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}[file.name]

    service.unstage_file(repository, change)

    unstaged = {item.path: item for item in service.get_status(repository)}[file.name]
    assert unstaged.is_untracked is True
    assert unstaged.staged_change is None
    assert file.read_text(encoding="utf-8") == "content\n"


def test_unstage_after_add_in_repository_without_commits_keeps_worktree(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("unborn-unstage-modified", commits=0)
    file = path / "changed after add.txt"
    file.write_text("one\n", encoding="utf-8")
    git(path, "add", "--", file.name)
    file.write_text("two\n", encoding="utf-8")
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}[file.name]
    assert change.staged_change is FileChangeType.ADDED
    assert change.unstaged_change is FileChangeType.MODIFIED

    # Без --force git rm --cached отказал бы: индекс и рабочая директория различаются.
    service.unstage_file(repository, change)

    unstaged = {item.path: item for item in service.get_status(repository)}[file.name]
    assert unstaged.is_untracked is True
    assert file.read_text(encoding="utf-8") == "two\n"


def test_stage_all_stages_modified_untracked_and_deleted(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("stage-all", commits=1)
    (path / "deleted.txt").write_text("old\n", encoding="utf-8")
    commit(git, path, "add deleted")
    (path / "file0.txt").write_text("changed\n", encoding="utf-8")
    (path / "deleted.txt").unlink()
    untracked = path / "новый файл.txt"
    untracked.write_text("new\n", encoding="utf-8")
    repository, service = load(path)

    service.stage_all(repository)

    changes = {item.path: item for item in service.get_status(repository)}
    assert changes["file0.txt"].staged_change is FileChangeType.MODIFIED
    assert changes["deleted.txt"].staged_change is FileChangeType.DELETED
    assert changes["новый файл.txt"].staged_change is FileChangeType.ADDED
    assert all(change.unstaged_change is None for change in changes.values())
    assert untracked.read_text(encoding="utf-8") == "new\n"


def test_stage_all_skips_conflicted_files(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("stage-all-conflict", commits=0)
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
        git(
            path,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "-c", "commit.gpgsign=false",
            "merge", "feature",
        )
    (path / "other.txt").write_text("other\n", encoding="utf-8")
    repository, service = load(path)

    service.stage_all(repository)

    changes = {item.path: item for item in service.get_status(repository)}
    assert changes["conflict.txt"].is_conflicted
    assert changes["conflict.txt"].staged_change is None
    assert changes["other.txt"].staged_change is FileChangeType.ADDED


def test_unstage_all_clears_index_and_keeps_working_tree(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("unstage-all", commits=1)
    (path / "file0.txt").write_text("changed\n", encoding="utf-8")
    untracked = path / "новый файл.txt"
    untracked.write_text("new\n", encoding="utf-8")
    git(path, "add", "-A")
    repository, service = load(path)
    assert service.get_status(repository)

    service.unstage_all(repository)

    changes = {item.path: item for item in service.get_status(repository)}
    assert changes["file0.txt"].staged_change is None
    assert changes["file0.txt"].unstaged_change is FileChangeType.MODIFIED
    assert changes["новый файл.txt"].is_untracked is True
    assert not any(change.is_staged for change in changes.values())
    assert untracked.read_text(encoding="utf-8") == "new\n"


def test_unstage_all_in_repository_without_commits_keeps_files(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("unstage-all-unborn", commits=0)
    first = path / "first.txt"
    second = path / "второй файл.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    git(path, "add", "-A")
    repository, service = load(path)

    service.unstage_all(repository)

    changes = {item.path: item for item in service.get_status(repository)}
    assert changes["first.txt"].is_untracked is True
    assert changes["второй файл.txt"].is_untracked is True
    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "two\n"


def test_unstage_all_leaves_conflicts_untouched(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("unstage-all-conflict", commits=0)
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
        git(
            path,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "-c", "commit.gpgsign=false",
            "merge", "feature",
        )
    (path / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(path, "add", "--", "staged.txt")
    repository, service = load(path)

    service.unstage_all(repository)

    changes = {item.path: item for item in service.get_status(repository)}
    assert changes["conflict.txt"].is_conflicted
    assert changes["staged.txt"].is_untracked is True
    assert "<<<<<<<" in file.read_text(encoding="utf-8")


def test_stage_missing_file_reports_git_error(
    make_repo: MakeRepo
) -> None:
    path = make_repo("stage-missing", commits=1)
    file = path / "new file.txt"
    file.write_text("new\n", encoding="utf-8")
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}["new file.txt"]
    # Файл исчез после того, как статус уже был загружен (например, удалён извне).
    file.unlink()

    with pytest.raises(GitCommandError) as info:
        service.stage_file(repository, change)

    assert "did not match any files" in info.value.stderr
    assert "--" in info.value.command
    assert "new file.txt" in info.value.stderr


def test_stage_treats_pathspec_characters_literally(
    make_repo: MakeRepo
) -> None:
    path = make_repo("stage-literal-path", commits=1)
    selected = path / "[ab].txt"
    other = path / "a.txt"
    selected.write_text("selected\n", encoding="utf-8")
    other.write_text("other\n", encoding="utf-8")
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}["[ab].txt"]

    service.stage_file(repository, change)

    changes = {item.path: item for item in service.get_status(repository)}
    assert changes["[ab].txt"].staged_change is FileChangeType.ADDED
    # Без --literal-pathspecs Git принял бы "[ab].txt" за шаблон и добавил a.txt.
    assert changes["a.txt"].is_untracked is True
    assert changes["a.txt"].staged_change is None


# --- Discard Changes через реальный Git ---


def test_discard_restores_staged_and_unstaged_changes_to_head(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("discard-tracked", commits=1)
    file = path / "file0.txt"
    file.write_text("first\n", encoding="utf-8")
    git(path, "add", "--", "file0.txt")
    file.write_text("second\n", encoding="utf-8")
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}["file0.txt"]
    assert change.staged_change is FileChangeType.MODIFIED
    assert change.unstaged_change is FileChangeType.MODIFIED

    service.discard_file(repository, change)

    assert file.read_text(encoding="utf-8") == "0\n"
    assert service.get_status(repository) == ()


def test_discard_restores_deleted_file(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("discard-deleted", commits=1)
    file = path / "file0.txt"
    file.unlink()
    git(path, "add", "--", "file0.txt")
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}["file0.txt"]
    assert change.staged_change is FileChangeType.DELETED
    assert change.unstaged_change is None

    service.discard_file(repository, change)

    assert file.read_text(encoding="utf-8") == "0\n"
    assert service.get_status(repository) == ()


def test_discard_staged_new_file_removes_it(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("discard-added", commits=1)
    file = path / "новый файл.txt"
    file.write_text("new content\n", encoding="utf-8")
    git(path, "add", "--", file.name)
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}[file.name]
    assert change.staged_change is FileChangeType.ADDED

    service.discard_file(repository, change)

    # Файла нет в HEAD: восстановление к коммиту означает удаление.
    assert not file.exists()
    assert service.get_status(repository) == ()


def test_discard_rename_restores_old_name(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("discard-rename", commits=1)
    old = path / "старое имя.txt"
    new = path / "новое имя.txt"
    old.write_text("content\n", encoding="utf-8")
    commit(git, path, "add rename source")
    git(path, "mv", "--", old.name, new.name)
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}[new.name]
    assert change.staged_change is FileChangeType.RENAMED
    assert change.old_path == old.name

    service.discard_file(repository, change)

    assert old.read_text(encoding="utf-8") == "content\n"
    assert not new.exists()
    assert service.get_status(repository) == ()


def test_discard_touches_only_the_selected_tracked_file(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("discard-selected-tracked", commits=1)
    first = path / "first.txt"
    second = path / "second.txt"
    first.write_text("base first\n", encoding="utf-8")
    second.write_text("base second\n", encoding="utf-8")
    commit(git, path, "add both")
    first.write_text("changed first\n", encoding="utf-8")
    second.write_text("changed second\n", encoding="utf-8")
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}["first.txt"]

    service.discard_file(repository, change)

    assert first.read_text(encoding="utf-8") == "base first\n"
    assert second.read_text(encoding="utf-8") == "changed second\n"
    changes = {item.path: item for item in service.get_status(repository)}
    assert list(changes) == ["second.txt"]
    assert changes["second.txt"].unstaged_change is FileChangeType.MODIFIED


def test_discard_untracked_file_deletes_only_that_file(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("discard-untracked", commits=1)
    tracked = path / "file0.txt"
    tracked.write_text("changed\n", encoding="utf-8")
    removed = path / "remove me.txt"
    removed.write_text("remove\n", encoding="utf-8")
    kept = path / "keep me.txt"
    kept.write_text("keep\n", encoding="utf-8")
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}["remove me.txt"]
    assert change.is_untracked

    service.discard_file(repository, change)

    assert not removed.exists()
    assert kept.read_text(encoding="utf-8") == "keep\n"
    assert tracked.read_text(encoding="utf-8") == "changed\n"
    changes = {item.path: item for item in service.get_status(repository)}
    assert sorted(changes) == ["file0.txt", "keep me.txt"]
    assert changes["keep me.txt"].is_untracked
    assert changes["file0.txt"].unstaged_change is FileChangeType.MODIFIED


def test_discard_untracked_directory_removes_it_with_contents(
    make_repo: MakeRepo
) -> None:
    path = make_repo("discard-untracked-dir", commits=1)
    directory = path / "новая папка"
    directory.mkdir()
    (directory / "file.txt").write_text("new\n", encoding="utf-8")
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}[
        "новая папка/"
    ]
    assert change.is_untracked

    service.discard_file(repository, change)

    assert not directory.exists()
    assert service.get_status(repository) == ()


def test_discard_in_repository_without_commits_keeps_file(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("discard-unborn", commits=0)
    file = path / "первый файл.txt"
    file.write_text("content\n", encoding="utf-8")
    git(path, "add", "--", file.name)
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}[file.name]
    assert change.staged_change is FileChangeType.ADDED

    service.discard_file(repository, change)

    # Восстанавливать из HEAD нечего: содержимое остаётся в рабочей директории.
    assert file.read_text(encoding="utf-8") == "content\n"
    changes = {item.path: item for item in service.get_status(repository)}
    assert changes[file.name].is_untracked is True
    assert changes[file.name].staged_change is None


def test_discard_conflicted_file_is_rejected(make_repo: MakeRepo, git: Git) -> None:
    path = make_repo("discard-conflict", commits=0)
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
        git(
            path,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "-c", "commit.gpgsign=false",
            "merge", "feature",
        )
    repository, service = load(path)
    change = {item.path: item for item in service.get_status(repository)}["conflict.txt"]

    with pytest.raises(ValueError):
        service.discard_file(repository, change)

    # Разрешение конфликтов в MVP не входит: файл и его стадии не тронуты.
    assert "<<<<<<<" in file.read_text(encoding="utf-8")
    assert status_by_path(path)["conflict.txt"].is_conflicted
