"""Unit-тесты Discard Changes: аргументы Git, удаление untracked и политика Application."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_visualizer.application.errors import UntrackedFileDeleteError
from git_visualizer.application.services.changes_service import ChangesService
from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import ChangedFile, Repository
from git_visualizer.infrastructure.git.git_errors import GitCommandError
from git_visualizer.infrastructure.git.git_repository import (
    GitRepository,
    build_restore_arguments,
)
from git_visualizer.infrastructure.git.git_runner import GitResult

H = "3" * 40
REPOSITORY = Repository(
    path=Path("repo"), name="repo", current_branch="main", head_commit=H
)


# --- аргументы Git ---


def test_restore_with_head_discards_index_and_worktree() -> None:
    arguments = build_restore_arguments(
        paths=["-starts-with-dash.txt", "файл с пробелом.txt"], has_head=True
    )

    assert arguments == [
        "--literal-pathspecs",
        "restore",
        "--source=HEAD",
        "--staged",
        "--worktree",
        "--",
        "-starts-with-dash.txt",
        "файл с пробелом.txt",
    ]


def test_restore_without_head_clears_index_only() -> None:
    # В репозитории без коммитов восстанавливать рабочую директорию не из чего:
    # содержимое, которое нигде не закоммичено, не должно потеряться.
    assert build_restore_arguments(paths=["первый файл.txt"], has_head=False) == [
        "--literal-pathspecs",
        "rm",
        "--cached",
        "--force",
        "-q",
        "--",
        "первый файл.txt",
    ]


def test_restore_arguments_require_a_path() -> None:
    with pytest.raises(ValueError):
        build_restore_arguments(paths=[], has_head=True)


class SequencedRunner:
    def __init__(self, *results: GitResult) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def run(self, args, *, cwd=None, check=True, **_kwargs):
        self.calls.append(list(args))
        if self.results:
            return self.results.pop(0)
        return GitResult(command=tuple(args), returncode=0, stdout="", stderr="")


def _head_result(*, present: bool) -> GitResult:
    return GitResult(
        command=("git", "rev-parse"),
        returncode=0 if present else 1,
        stdout=f"{H}\n" if present else "",
        stderr="",
    )


def _command_result(command: tuple[str, ...]) -> GitResult:
    return GitResult(command=command, returncode=0, stdout="", stderr="")


def test_repository_uses_restore_when_head_exists() -> None:
    runner = SequencedRunner(
        _head_result(present=True),
        _command_result(("git", "restore")),
    )
    repository = GitRepository(runner, Path("repo"))

    repository.restore_files(["старое имя.txt", "новое имя.txt"])

    assert runner.calls == [
        ["rev-parse", "--verify", "-q", "HEAD"],
        [
            "--literal-pathspecs",
            "restore",
            "--source=HEAD",
            "--staged",
            "--worktree",
            "--",
            "старое имя.txt",
            "новое имя.txt",
        ],
    ]


def test_repository_uses_rm_cached_when_there_is_no_head() -> None:
    runner = SequencedRunner(
        _head_result(present=False),
        _command_result(("git", "rm")),
    )
    repository = GitRepository(runner, Path("repo"))

    repository.restore_files(["first.txt"])

    assert runner.calls[-1] == [
        "--literal-pathspecs",
        "rm",
        "--cached",
        "--force",
        "-q",
        "--",
        "first.txt",
    ]


def _repository_with_toplevel(root: Path) -> GitRepository:
    result = GitResult(
        command=("git", "rev-parse"),
        returncode=0,
        stdout=f"{root}\n",
        stderr="",
    )
    return GitRepository(SequencedRunner(result), root)


# --- удаление неотслеживаемых путей ---


def test_untracked_file_is_deleted(tmp_path: Path) -> None:
    file = tmp_path / "новый файл.txt"
    file.write_text("new\n", encoding="utf-8")
    repository = _repository_with_toplevel(tmp_path)

    repository.delete_untracked_path("новый файл.txt")

    assert not file.exists()


def test_untracked_directory_is_deleted_with_contents(tmp_path: Path) -> None:
    directory = tmp_path / "new dir"
    directory.mkdir()
    (directory / "file.txt").write_text("new\n", encoding="utf-8")
    repository = _repository_with_toplevel(tmp_path)

    # git status сворачивает неотслеживаемый каталог в запись с завершающим "/".
    repository.delete_untracked_path("new dir/")

    assert not directory.exists()


def test_missing_untracked_path_is_not_an_error(tmp_path: Path) -> None:
    repository = _repository_with_toplevel(tmp_path)

    repository.delete_untracked_path("уже удалён.txt")


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "..",
        "../outside.txt",
        "dir/../../outside.txt",
        "C:/Windows/System32/file.txt",
        "/absolute/file.txt",
    ],
)
def test_untracked_path_outside_work_tree_is_rejected(
    tmp_path: Path, path: str
) -> None:
    repository = _repository_with_toplevel(tmp_path / "repo")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me\n", encoding="utf-8")

    with pytest.raises(ValueError):
        repository.delete_untracked_path(path)

    assert outside.exists()


# --- ChangesService: проверки до Git и передача путей ---


class FakeGit:
    def __init__(
        self,
        *,
        restore_error: BaseException | None = None,
        delete_error: BaseException | None = None,
    ) -> None:
        self.restore_error = restore_error
        self.delete_error = delete_error
        self.restore_calls: list[tuple[str, ...]] = []
        self.delete_calls: list[str] = []

    def restore_files(self, paths):
        self.restore_calls.append(tuple(paths))
        if self.restore_error is not None:
            raise self.restore_error

    def delete_untracked_path(self, path):
        self.delete_calls.append(path)
        if self.delete_error is not None:
            raise self.delete_error


def build_service(git: FakeGit) -> ChangesService:
    return ChangesService(lambda path: git)


def test_discard_tracked_file_restores_both_paths_of_rename() -> None:
    git = FakeGit()
    changed = ChangedFile(
        path="новый файл.txt",
        old_path="старый файл.txt",
        staged_change=FileChangeType.RENAMED,
    )

    build_service(git).discard_file(REPOSITORY, changed)

    assert git.restore_calls == [("старый файл.txt", "новый файл.txt")]
    assert git.delete_calls == []


def test_discard_file_with_both_changes_restores_it_once() -> None:
    git = FakeGit()
    changed = ChangedFile(
        path="both.txt",
        staged_change=FileChangeType.ADDED,
        unstaged_change=FileChangeType.MODIFIED,
    )

    build_service(git).discard_file(REPOSITORY, changed)

    assert git.restore_calls == [("both.txt",)]


def test_discard_untracked_file_deletes_it_without_git_restore() -> None:
    git = FakeGit()
    changed = ChangedFile(path="new dir/", is_untracked=True)

    build_service(git).discard_file(REPOSITORY, changed)

    assert git.delete_calls == ["new dir/"]
    assert git.restore_calls == []


@pytest.mark.parametrize(
    "changed",
    [
        ChangedFile(path="conflict.txt", is_conflicted=True),
        ChangedFile(path="clean.txt"),
    ],
)
def test_discard_is_rejected_before_git(changed: ChangedFile) -> None:
    git = FakeGit()

    with pytest.raises(ValueError):
        build_service(git).discard_file(REPOSITORY, changed)

    assert git.restore_calls == []
    assert git.delete_calls == []


def test_git_error_is_propagated() -> None:
    error = GitCommandError(128, command=("git", "restore"), stderr="fatal")
    changed = ChangedFile(
        path="file.txt", unstaged_change=FileChangeType.MODIFIED
    )
    git = FakeGit(restore_error=error)

    with pytest.raises(GitCommandError) as raised:
        build_service(git).discard_file(REPOSITORY, changed)

    assert raised.value is error


def test_delete_error_is_wrapped_for_presentation() -> None:
    error = PermissionError(13, "Permission denied")
    changed = ChangedFile(path="new.txt", is_untracked=True)
    git = FakeGit(delete_error=error)

    with pytest.raises(UntrackedFileDeleteError) as raised:
        build_service(git).discard_file(REPOSITORY, changed)

    assert raised.value.path == "new.txt"
    assert raised.value.__cause__ is error
    assert "Permission denied" in raised.value.technical_details()
