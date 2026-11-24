"""Unit-тесты stage/unstage: аргументы Git, выбор команды и политика Application."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_visualizer.application.services.changes_service import ChangesService
from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import ChangedFile, Repository
from git_visualizer.infrastructure.git.git_errors import GitCommandError
from git_visualizer.infrastructure.git.git_repository import (
    GitRepository,
    build_stage_all_arguments,
    build_stage_arguments,
    build_unstage_all_arguments,
    build_unstage_arguments,
)
from git_visualizer.infrastructure.git.git_runner import GitResult

H = "3" * 40
REPOSITORY = Repository(
    path=Path("repo"), name="repo", current_branch="main", head_commit=H
)


# --- аргументы Git: "--" и разные команды unstage ---


def test_stage_arguments_put_paths_behind_separator() -> None:
    arguments = build_stage_arguments(["-starts-with-dash.txt", "файл с пробелом.txt"])

    assert arguments == [
        "--literal-pathspecs",
        "add",
        "--",
        "-starts-with-dash.txt",
        "файл с пробелом.txt",
    ]


def test_stage_arguments_require_a_path() -> None:
    with pytest.raises(ValueError):
        build_stage_arguments([])


def test_stage_all_arguments_cover_whole_tree() -> None:
    assert build_stage_all_arguments() == ["add", "-A", "--"]


def test_unstage_with_head_uses_restore_staged() -> None:
    arguments = build_unstage_arguments(
        paths=["renamed old.txt", "renamed new.txt"], has_head=True
    )

    assert arguments == [
        "--literal-pathspecs",
        "restore",
        "--staged",
        "--",
        "renamed old.txt",
        "renamed new.txt",
    ]


def test_unstage_without_head_uses_rm_cached_force() -> None:
    arguments = build_unstage_arguments(paths=["first.txt"], has_head=False)

    assert arguments == [
        "--literal-pathspecs",
        "rm",
        "--cached",
        "--force",
        "-q",
        "--",
        "first.txt",
    ]


def test_unstage_arguments_require_a_path() -> None:
    with pytest.raises(ValueError):
        build_unstage_arguments(paths=[], has_head=True)


def test_unstage_all_arguments_clear_the_whole_index() -> None:
    assert build_unstage_all_arguments(has_head=True) == [
        "restore",
        "--staged",
        "--",
        ":/",
    ]
    assert build_unstage_all_arguments(has_head=False) == [
        "rm",
        "-r",
        "--cached",
        "--force",
        "-q",
        "--",
        ":/",
    ]


# --- GitRepository: выбор команды по наличию HEAD и передача путей ---


class SequencedRunner:
    def __init__(self, *results: GitResult) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def run(self, args, *, cwd=None, check=True):
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

    repository.unstage_files(["a b.txt"])

    assert runner.calls == [
        ["rev-parse", "--verify", "-q", "HEAD"],
        ["--literal-pathspecs", "restore", "--staged", "--", "a b.txt"],
    ]


def test_repository_uses_rm_cached_when_there_is_no_head() -> None:
    runner = SequencedRunner(
        _head_result(present=False),
        _command_result(("git", "rm")),
    )
    repository = GitRepository(runner, Path("repo"))

    repository.unstage_files(["a b.txt"])

    assert runner.calls[-1] == [
        "--literal-pathspecs",
        "rm",
        "--cached",
        "--force",
        "-q",
        "--",
        "a b.txt",
    ]


def test_repository_stage_commands_are_passed_as_a_list() -> None:
    runner = SequencedRunner(
        _head_result(present=True),
        _command_result(("git", "restore")),
    )
    repository = GitRepository(runner, Path("repo"))

    repository.stage_files(["new file.txt"])
    repository.stage_all()
    repository.unstage_all()

    assert runner.calls[0] == [
        "--literal-pathspecs",
        "add",
        "--",
        "new file.txt",
    ]
    assert runner.calls[1] == ["add", "-A", "--"]
    assert runner.calls[-1] == ["restore", "--staged", "--", ":/"]


# --- ChangesService: какие пути и какую команду получает Git ---


class FakeGit:
    def __init__(
        self,
        *,
        status: tuple[ChangedFile, ...] = (),
        error: BaseException | None = None,
    ) -> None:
        self.status = status
        self.error = error
        self.stage_calls: list[tuple[str, ...]] = []
        self.stage_all_calls = 0
        self.unstage_calls: list[tuple[str, ...]] = []
        self.unstage_all_calls = 0

    def get_status(self):
        return self.status

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    def stage_files(self, paths):
        self.stage_calls.append(tuple(paths))
        self._raise()

    def stage_all(self):
        self.stage_all_calls += 1
        self._raise()

    def unstage_files(self, paths):
        self.unstage_calls.append(tuple(paths))
        self._raise()

    def unstage_all(self):
        self.unstage_all_calls += 1
        self._raise()


def build_service(git: FakeGit) -> ChangesService:
    return ChangesService(lambda path: git)


def test_stage_file_of_rename_passes_both_paths() -> None:
    git = FakeGit()
    changed = ChangedFile(
        path="новый файл.txt",
        old_path="старый файл.txt",
        unstaged_change=FileChangeType.RENAMED,
    )

    build_service(git).stage_file(REPOSITORY, changed)

    assert git.stage_calls == [("старый файл.txt", "новый файл.txt")]


def test_stage_untracked_directory_passes_directory_path() -> None:
    git = FakeGit()
    changed = ChangedFile(path="new dir/", is_untracked=True)

    build_service(git).stage_file(REPOSITORY, changed)

    assert git.stage_calls == [("new dir/",)]


@pytest.mark.parametrize(
    "changed",
    [
        ChangedFile(path="staged.txt", staged_change=FileChangeType.MODIFIED),
        ChangedFile(path="clean.txt"),
        ChangedFile(path="conflict.txt", is_conflicted=True),
    ],
)
def test_stage_rejects_file_without_worktree_change_before_git(
    changed: ChangedFile,
) -> None:
    git = FakeGit()

    with pytest.raises(ValueError):
        build_service(git).stage_file(REPOSITORY, changed)

    assert git.stage_calls == []


def test_unstage_file_of_rename_passes_both_paths() -> None:
    git = FakeGit()
    changed = ChangedFile(
        path="new name.txt",
        old_path="old name.txt",
        staged_change=FileChangeType.RENAMED,
    )

    build_service(git).unstage_file(REPOSITORY, changed)

    assert git.unstage_calls == [("old name.txt", "new name.txt")]


@pytest.mark.parametrize(
    "changed",
    [
        ChangedFile(path="worktree.txt", unstaged_change=FileChangeType.MODIFIED),
        ChangedFile(path="untracked.txt", is_untracked=True),
        ChangedFile(path="conflict.txt", is_conflicted=True),
    ],
)
def test_unstage_rejects_file_without_index_change_before_git(
    changed: ChangedFile,
) -> None:
    git = FakeGit()

    with pytest.raises(ValueError):
        build_service(git).unstage_file(REPOSITORY, changed)

    assert git.unstage_calls == []


def test_stage_all_uses_whole_tree_command_without_conflicts() -> None:
    git = FakeGit(
        status=(
            ChangedFile(path="modified.txt", unstaged_change=FileChangeType.MODIFIED),
            ChangedFile(path="new.txt", is_untracked=True),
        )
    )

    build_service(git).stage_all(REPOSITORY)

    assert git.stage_all_calls == 1
    assert git.stage_calls == []


def test_stage_all_skips_conflicts_and_stages_other_paths() -> None:
    conflict = ChangedFile(path="conflict.txt", is_conflicted=True)
    modified = ChangedFile(
        path="modified.txt", unstaged_change=FileChangeType.MODIFIED
    )
    git = FakeGit(status=(conflict, modified))

    build_service(git).stage_all(REPOSITORY)

    assert git.stage_all_calls == 0
    assert git.stage_calls == [("modified.txt",)]


def test_stage_all_with_only_conflicts_does_not_run_git() -> None:
    git = FakeGit(status=(ChangedFile(path="conflict.txt", is_conflicted=True),))

    build_service(git).stage_all(REPOSITORY)

    assert git.stage_all_calls == 0
    assert git.stage_calls == []


def test_unstage_all_uses_whole_index_command_without_conflicts() -> None:
    git = FakeGit(
        status=(ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),)
    )

    build_service(git).unstage_all(REPOSITORY)

    assert git.unstage_all_calls == 1
    assert git.unstage_calls == []


def test_unstage_all_skips_conflicts_and_unstages_staged_files() -> None:
    git = FakeGit(
        status=(
            ChangedFile(path="conflict.txt", is_conflicted=True),
            ChangedFile(
                path="both.txt",
                staged_change=FileChangeType.MODIFIED,
                unstaged_change=FileChangeType.MODIFIED,
            ),
            ChangedFile(
                path="new name.txt",
                old_path="old name.txt",
                staged_change=FileChangeType.RENAMED,
            ),
        )
    )

    build_service(git).unstage_all(REPOSITORY)

    assert git.unstage_all_calls == 0
    assert git.unstage_calls == [("both.txt", "old name.txt", "new name.txt")]


def test_stageable_and_staged_helpers_apply_policy() -> None:
    changes = (
        ChangedFile(path="untracked.txt", is_untracked=True),
        ChangedFile(path="modified.txt", unstaged_change=FileChangeType.MODIFIED),
        ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),
        ChangedFile(
            path="both.txt",
            staged_change=FileChangeType.ADDED,
            unstaged_change=FileChangeType.MODIFIED,
        ),
        ChangedFile(path="conflict.txt", is_conflicted=True),
    )

    assert [change.path for change in ChangesService.stageable(changes)] == [
        "untracked.txt",
        "modified.txt",
        "both.txt",
    ]
    assert [change.path for change in ChangesService.staged(changes)] == [
        "staged.txt",
        "both.txt",
    ]


@pytest.mark.parametrize(
    "operation",
    ["stage_file", "unstage_file", "stage_all", "unstage_all"],
)
def test_git_errors_are_propagated(operation: str) -> None:
    error = GitCommandError(128, command=("git", "add"), stderr="fatal")
    both = ChangedFile(
        path="file.txt",
        staged_change=FileChangeType.MODIFIED,
        unstaged_change=FileChangeType.MODIFIED,
    )
    git = FakeGit(status=(both,), error=error)
    service = build_service(git)

    with pytest.raises(GitCommandError) as raised:
        if operation == "stage_file":
            service.stage_file(REPOSITORY, both)
        elif operation == "unstage_file":
            service.unstage_file(REPOSITORY, both)
        elif operation == "stage_all":
            service.stage_all(REPOSITORY)
        else:
            service.unstage_all(REPOSITORY)

    assert raised.value is error
