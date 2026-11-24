"""Unit-тесты русских текстов ошибок Presentation."""

from pathlib import Path

from git_visualizer.application.errors import (
    BareRepositoryNotSupportedError,
    NotAGitRepositoryError,
    PathDoesNotExistError,
    PathIsNotDirectoryError,
    UnsafeRepositoryError,
)
from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitErrorKind,
    GitLaunchError,
    GitNotFoundError,
    GitOutputParseError,
    GitTimeoutError,
)
from git_visualizer.presentation.error_texts import (
    GIT_INSTALL_URL,
    commit_user_message,
    technical_details,
    user_message,
)

PATH = Path("C:/projects/Пример проекта")


def test_missing_path_message_is_russian_and_contains_path() -> None:
    message = user_message(PathDoesNotExistError("Path does not exist", path=PATH))

    assert "Папка не найдена" in message
    assert str(PATH) in message


def test_file_instead_of_directory_message() -> None:
    message = user_message(PathIsNotDirectoryError("Path is not a directory", path=PATH))

    assert "не является папкой" in message


def test_not_a_repository_message_explains_what_to_choose() -> None:
    message = user_message(
        NotAGitRepositoryError("Path is not inside a Git repository", path=PATH)
    )

    assert "нет Git-репозитория" in message
    assert str(PATH) in message


def test_bare_repository_message() -> None:
    message = user_message(
        BareRepositoryNotSupportedError("Bare repositories are not supported", path=PATH)
    )

    assert "Bare-репозитории не поддерживаются" in message


def test_unsafe_repository_message_contains_safe_directory_command() -> None:
    message = user_message(
        UnsafeRepositoryError("Git refuses to use this directory", path=PATH)
    )

    assert "владелец" in message
    assert f'git config --global --add safe.directory "{PATH}"' in message


def test_missing_git_message_contains_download_url() -> None:
    message = user_message(GitNotFoundError("git"))

    assert "Git не найден" in message
    assert GIT_INSTALL_URL in message


def test_timeout_message_is_russian() -> None:
    message = user_message(GitTimeoutError(30.0, command=("git", "log")))

    assert "не ответил за отведённое время" in message


def test_git_launch_and_parse_messages() -> None:
    assert "Не удалось запустить Git" in user_message(
        GitLaunchError("Failed to start", command=("git",))
    )
    assert "Не удалось разобрать ответ Git" in user_message(
        GitOutputParseError("Unexpected output", command=("git", "log"))
    )


def test_git_command_error_contains_exit_code() -> None:
    message = user_message(
        GitCommandError(128, command=("git", "log"), stderr="fatal: bad object")
    )

    assert "код 128" in message


def test_git_command_error_kinds_have_specific_messages() -> None:
    not_a_repository = GitCommandError(
        128, stderr="fatal: not a git repository (or any of the parent directories)"
    )
    unsafe = GitCommandError(
        128, stderr="fatal: detected dubious ownership in repository at ..."
    )

    assert not_a_repository.kind is GitErrorKind.NOT_A_REPOSITORY
    assert "не является Git-репозиторием" in user_message(not_a_repository)
    assert unsafe.kind is GitErrorKind.UNSAFE_REPOSITORY
    assert "владельца" in user_message(unsafe)


def test_technical_details_uses_error_method() -> None:
    error = GitCommandError(
        128, command=("git", "log"), stdout="out", stderr="fatal: bad object"
    )

    details = technical_details(error)

    assert "Command: git log" in details
    assert "fatal: bad object" in details


def test_technical_details_falls_back_to_type_and_message() -> None:
    details = technical_details(ValueError("broken"))

    assert details == "ValueError: broken"


# --- тексты ошибок создания коммита (FR-COMMIT-06) ---


def test_commit_without_author_configuration_explains_what_to_configure() -> None:
    error = GitCommandError(
        128,
        command=("git", "commit", "-F", "-"),
        stderr=(
            "Author identity unknown\n"
            "*** Please tell me who you are.\n"
            "fatal: unable to auto-detect email address\n"
        ),
    )

    message = commit_user_message(error)

    assert error.kind is GitErrorKind.MISSING_AUTHOR
    assert "автор" in message
    assert "user.name" in message
    assert "user.email" in message


def test_commit_with_unmerged_files_is_explained() -> None:
    error = GitCommandError(
        128,
        command=("git", "commit", "-F", "-"),
        stderr=(
            "error: Committing is not possible because you have unmerged files.\n"
            "fatal: Exiting because of an unresolved conflict.\n"
        ),
    )

    message = commit_user_message(error)

    assert error.kind is GitErrorKind.UNMERGED_FILES
    assert "конфликт" in message


def test_commit_hook_output_is_shown_in_the_main_message() -> None:
    error = GitCommandError(
        1,
        command=("git", "commit", "-F", "-"),
        stdout="hook stdout line\n",
        stderr="pre-commit hook rejected the commit\n",
    )

    message = commit_user_message(error)

    assert "Git не создал коммит (код 1)" in message
    assert "pre-commit hook rejected the commit" in message
    assert "hook stdout line" in message
    assert "Подробнее" in message


def test_commit_failure_without_output_mentions_hooks_and_author() -> None:
    message = commit_user_message(
        GitCommandError(1, command=("git", "commit", "-F", "-"))
    )

    assert "Git не создал коммит (код 1)" in message
    assert "хуки" in message
    assert "автора" in message


def test_commit_validation_error_message_is_russian() -> None:
    message = commit_user_message(ValueError("Commit message must not be empty"))

    assert "staged-изменений" in message
    assert "сообщение" in message
