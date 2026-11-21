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


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (NOT_A_REPOSITORY_STDERR, GitErrorKind.NOT_A_REPOSITORY),
        (DUBIOUS_OWNERSHIP_STDERR, GitErrorKind.UNSAFE_REPOSITORY),
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
    assert GitCommandError(1).kind is GitErrorKind.UNKNOWN
