import pytest

from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitError,
    GitErrorKind,
    GitLaunchError,
    GitNotFoundError,
    GitTimeoutError,
    classify_command_error,
)


@pytest.mark.parametrize(
    "error_class", [GitNotFoundError, GitLaunchError, GitTimeoutError, GitCommandError]
)
def test_all_errors_share_common_base(error_class: type[GitError]) -> None:
    assert issubclass(error_class, GitError)


def test_errors_are_distinguishable_from_each_other() -> None:
    kinds = [GitNotFoundError, GitLaunchError, GitTimeoutError, GitCommandError]

    for kind in kinds:
        for other in kinds:
            if kind is not other:
                assert not issubclass(kind, other)


def test_technical_details_contain_command_and_streams() -> None:
    error = GitCommandError(
        128, command=["git", "commit", "-m", "two words"], stdout="", stderr="fatal: boom\n"
    )

    details = error.technical_details()

    assert "exit code 128" in details
    assert "git commit -m 'two words'" in details  # только отображение, не запуск
    assert "fatal: boom" in details
    assert "stdout:" not in details  # пустой поток не выводится


def test_timeout_error_keeps_timeout_and_partial_output() -> None:
    error = GitTimeoutError(30, command=["git", "fetch"], stdout="a", stderr="b")

    assert error.timeout == 30
    assert "30" in error.message
    assert "stdout:\na" in error.technical_details()


def test_not_found_error_keeps_executable() -> None:
    error = GitNotFoundError("git")

    assert error.executable == "git"
    assert "'git'" in str(error)


# Сообщения ниже получены от реального Git 2.43.0 (LC_ALL=C).
NOT_A_REPOSITORY_STDERR = "fatal: not a git repository (or any of the parent directories): .git\n"
DUBIOUS_OWNERSHIP_STDERR = (
    "fatal: detected dubious ownership in repository at '/tmp/own/r'\n"
    "To add an exception for this directory, call:\n\n"
    "\tgit config --global --add safe.directory /tmp/own/r\n"
)
# Реальные сообщения Git 2.54.0 (LC_ALL=C) при коммите без автора и с конфликтами.
MISSING_AUTHOR_STDERR = (
    "Author identity unknown\n\n"
    "*** Please tell me who you are.\n\n"
    "Run\n\n"
    '  git config --global user.email "you@example.com"\n'
    '  git config --global user.name "Your Name"\n\n'
    "to set your account's default identity.\n"
    "Omit --global to set the identity only in this repository.\n\n"
    "fatal: unable to auto-detect email address (got 'user@host.(none)')\n"
)
UNMERGED_FILES_STDERR = (
    "error: Committing is not possible because you have unmerged files.\n"
    "hint: Fix them up in the work tree, and then use 'git add/rm <file>'\n"
    "hint: as appropriate to mark resolution and make a commit.\n"
    "fatal: Exiting because of an unresolved conflict.\n"
)
# Реальные сообщения Git 2.54.0 (LC_ALL=C) при работе с ветками.
INVALID_BRANCH_NAME_STDERR = (
    "fatal: 'bad name' is not a valid branch name\n"
    "hint: See 'git help check-ref-format'\n"
)
BRANCH_ALREADY_EXISTS_STDERR = "fatal: a branch named 'feature' already exists\n"
REF_NOT_FOUND_STDERR = "fatal: invalid reference: nope\n"
DIRTY_WORKTREE_STDERR = (
    "error: Your local changes to the following files would be overwritten by checkout:\n"
    "\ta.txt\n"
    "Please commit your changes or stash them before you switch branches.\n"
    "Aborting\n"
)
OPERATION_IN_PROGRESS_STDERR = (
    'fatal: cannot switch branch while merging\n'
    'Consider "git merge --quit" or "git worktree add".\n'
)
UNMERGED_INDEX_STDERR = (
    "error: you need to resolve your current index first\n"
    "a.txt: needs merge\n"
)
LFS_FILTER_STDERR = (
    "Error cleaning Git LFS object: open "
    r"C:\Users\user\repo\.git\lfs\tmp\2972506489: Access is denied."
    "\n"
    "error: external filter 'git-lfs filter-process' failed\n"
    "fatal: assets/archive.zip: clean filter 'lfs' failed\n"
)
INDEX_LOCK_PERMISSION_STDERR = (
    "fatal: Unable to create "
    r"'C:/Users/user/repo/.git/index.lock': Permission denied"
    "\n"
)
INDEX_LOCK_EXISTS_STDERR = (
    "fatal: Unable to create "
    r"'C:/Users/user/repo/.git/index.lock': File exists."
    "\n\n"
    "Another git process seems to be running in this repository, e.g.\n"
    "an editor opened by 'git commit'. Please make sure all processes\n"
    "are terminated then try again. If it still fails, a git process\n"
    "may have crashed in this repository earlier:\n"
    "remove the file manually to continue.\n"
)


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (NOT_A_REPOSITORY_STDERR, GitErrorKind.NOT_A_REPOSITORY),
        (DUBIOUS_OWNERSHIP_STDERR, GitErrorKind.UNSAFE_REPOSITORY),
        (MISSING_AUTHOR_STDERR, GitErrorKind.MISSING_AUTHOR),
        (UNMERGED_FILES_STDERR, GitErrorKind.UNMERGED_FILES),
        (INVALID_BRANCH_NAME_STDERR, GitErrorKind.INVALID_BRANCH_NAME),
        (BRANCH_ALREADY_EXISTS_STDERR, GitErrorKind.BRANCH_ALREADY_EXISTS),
        (REF_NOT_FOUND_STDERR, GitErrorKind.REF_NOT_FOUND),
        (DIRTY_WORKTREE_STDERR, GitErrorKind.DIRTY_WORKTREE),
        (OPERATION_IN_PROGRESS_STDERR, GitErrorKind.OPERATION_IN_PROGRESS),
        (UNMERGED_INDEX_STDERR, GitErrorKind.UNMERGED_FILES),
        (LFS_FILTER_STDERR, GitErrorKind.LFS_FILTER_FAILED),
        (INDEX_LOCK_PERMISSION_STDERR, GitErrorKind.REPOSITORY_NOT_WRITABLE),
        (INDEX_LOCK_EXISTS_STDERR, GitErrorKind.INDEX_LOCKED),
        ("FATAL: NOT A GIT REPOSITORY", GitErrorKind.NOT_A_REPOSITORY),  # регистр не важен
        ("fatal: pathspec 'x' did not match any files\n", GitErrorKind.UNKNOWN),
        ("", GitErrorKind.UNKNOWN),
    ],
)
def test_classify_command_error(stderr: str, expected: GitErrorKind) -> None:
    assert classify_command_error(stderr) is expected


def test_command_error_kind_is_derived_from_stderr() -> None:
    assert GitCommandError(128, stderr=NOT_A_REPOSITORY_STDERR).kind is GitErrorKind.NOT_A_REPOSITORY
    assert GitCommandError(128, stderr=DUBIOUS_OWNERSHIP_STDERR).kind is GitErrorKind.UNSAFE_REPOSITORY
    assert GitCommandError(128, stderr=MISSING_AUTHOR_STDERR).kind is GitErrorKind.MISSING_AUTHOR
    assert GitCommandError(128, stderr=UNMERGED_FILES_STDERR).kind is GitErrorKind.UNMERGED_FILES
    assert GitCommandError(128, stderr=INVALID_BRANCH_NAME_STDERR).kind is GitErrorKind.INVALID_BRANCH_NAME
    assert GitCommandError(128, stderr=BRANCH_ALREADY_EXISTS_STDERR).kind is GitErrorKind.BRANCH_ALREADY_EXISTS
    assert GitCommandError(128, stderr=REF_NOT_FOUND_STDERR).kind is GitErrorKind.REF_NOT_FOUND
    assert GitCommandError(1, stderr=DIRTY_WORKTREE_STDERR).kind is GitErrorKind.DIRTY_WORKTREE
    assert GitCommandError(128, stderr=OPERATION_IN_PROGRESS_STDERR).kind is GitErrorKind.OPERATION_IN_PROGRESS
    assert GitCommandError(128, stderr=LFS_FILTER_STDERR).kind is GitErrorKind.LFS_FILTER_FAILED
    assert (
        GitCommandError(128, stderr=INDEX_LOCK_PERMISSION_STDERR).kind
        is GitErrorKind.REPOSITORY_NOT_WRITABLE
    )
    assert (
        GitCommandError(128, stderr=INDEX_LOCK_EXISTS_STDERR).kind
        is GitErrorKind.INDEX_LOCKED
    )
    assert GitCommandError(1).kind is GitErrorKind.UNKNOWN
