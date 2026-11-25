"""Unit-тесты русских текстов ошибок Presentation."""

from pathlib import Path

from git_visualizer.application.errors import (
    BareRepositoryNotSupportedError,
    NotAGitRepositoryError,
    PathDoesNotExistError,
    PathIsNotDirectoryError,
    UnsafeRepositoryError,
    UntrackedFileDeleteError,
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
    branch_user_message,
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


def test_untracked_delete_error_explains_what_failed() -> None:
    error = UntrackedFileDeleteError(
        "Failed to delete untracked path: 'new.txt'",
        path="new.txt",
        details="PermissionError: Permission denied",
    )

    message = user_message(error)

    assert "Не удалось удалить неотслеживаемый файл" in message
    assert "«new.txt»" in message
    assert "занят другой программой" in message
    assert "PermissionError: Permission denied" in technical_details(error)


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


def test_lfs_filter_failure_has_a_clear_russian_message() -> None:
    error = GitCommandError(
        128,
        command=(
            "git",
            "--no-optional-locks",
            "status",
            "--porcelain=v2",
            "-z",
            "--branch",
            "--untracked-files=normal",
        ),
        stderr=(
            "Error cleaning Git LFS object: open "
            r"C:\Users\user\repo\.git\lfs\tmp\2972506489: Access is denied."
            "\n"
            "error: external filter 'git-lfs filter-process' failed\n"
            "fatal: assets/archive.zip: clean filter 'lfs' failed\n"
        ),
    )

    message = user_message(error)

    assert error.kind is GitErrorKind.LFS_FILTER_FAILED
    assert "Git LFS" in message
    assert ".git/lfs/tmp" in message
    assert "права" in message
    assert "Подробнее" in message
    assert "код 128" not in message


def test_index_lock_permission_denied_message_is_clear() -> None:
    error = GitCommandError(
        128,
        command=("git", "switch", "-c", "branch", "a" * 40),
        stderr=(
            "fatal: Unable to create "
            r"'C:/Users/user/repo/.git/index.lock': Permission denied"
            "\n"
        ),
    )

    message = user_message(error)
    branch_message = branch_user_message(
        error, operation="create", branch_name="новая-ветка"
    )

    assert error.kind is GitErrorKind.REPOSITORY_NOT_WRITABLE
    assert "нет доступа на запись" in message
    assert "index.lock" in message
    assert "принадлежит текущему пользователю" in message
    assert "Подробнее" in message
    assert "нет доступа на запись" in branch_message
    assert "код 128" not in message


def test_index_lock_busy_message_explains_another_git_process() -> None:
    error = GitCommandError(
        128,
        command=("git", "add", "--", "file.txt"),
        stderr=(
            "fatal: Unable to create "
            r"'C:/Users/user/repo/.git/index.lock': File exists."
            "\n\n"
            "Another git process seems to be running in this repository.\n"
        ),
    )

    message = user_message(error)

    assert error.kind is GitErrorKind.INDEX_LOCKED
    assert "index.lock" in message
    assert "другую операцию" in message
    assert "Дождитесь завершения другого Git-процесса" in message
    assert "не делает это автоматически" in message
    assert "Подробнее" in message


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


# --- тексты ошибок создания и переключения веток (FR-BRANCH-03/06) ---


def test_invalid_branch_name_message_explains_git_rules() -> None:
    error = GitCommandError(
        128,
        command=("git", "check-ref-format", "--branch", "bad name"),
        stderr="fatal: 'bad name' is not a valid branch name\n",
    )

    message = branch_user_message(
        error, operation="create", branch_name="bad name"
    )

    assert error.kind is GitErrorKind.INVALID_BRANCH_NAME
    assert "Git отклонил имя" in message
    assert "«bad name»" in message
    assert "пробелы" in message
    assert "«..»" in message


def test_branch_already_exists_message_suggests_switching() -> None:
    error = GitCommandError(
        128, stderr="fatal: a branch named 'feature' already exists\n"
    )

    message = branch_user_message(
        error, operation="create", branch_name="feature"
    )

    assert error.kind is GitErrorKind.BRANCH_ALREADY_EXISTS
    assert "«feature» уже существует" in message
    assert "переключитесь" in message


def test_missing_branch_message_differs_for_create_from_commit() -> None:
    switch_error = GitCommandError(128, stderr="fatal: invalid reference: dev\n")
    create_error = GitCommandError(128, stderr="fatal: invalid reference: deadbeef\n")

    switch_message = branch_user_message(
        switch_error, operation="switch", branch_name="dev"
    )
    create_message = branch_user_message(
        create_error,
        operation="create",
        branch_name="from-old",
        start_point="deadbeef" * 5,
    )

    assert "ветка «dev» не найдена" in switch_message
    assert "обновите список веток" in switch_message.lower()
    assert "коммит «deadbeefdead»" in create_message
    assert "обновите историю" in create_message.lower()


def test_dirty_worktree_message_keeps_git_explanation_and_files() -> None:
    error = GitCommandError(
        1,
        command=("git", "switch", "other"),
        stderr=(
            "error: Your local changes to the following files would be overwritten by checkout:\n"
            "\ta.txt\n"
            "Please commit your changes or stash them before you switch branches.\n"
            "Aborting\n"
        ),
    )

    message = branch_user_message(
        error, operation="switch", branch_name="other"
    )

    assert error.kind is GitErrorKind.DIRTY_WORKTREE
    assert "Переключение отменено" in message
    assert "незакоммиченные изменения" in message
    assert "не тронуты" in message
    assert "a.txt" in message
    assert "Закоммитьте" in message
    assert "Подробнее" not in message  # причина уже в основном тексте


def test_operation_in_progress_and_unmerged_messages_are_russian() -> None:
    in_progress = GitCommandError(
        128, stderr="fatal: cannot switch branch while merging\n"
    )
    unmerged = GitCommandError(
        128, stderr="error: you need to resolve your current index first\n"
    )

    assert "незавершённая операция" in branch_user_message(
        in_progress, operation="switch", branch_name="dev"
    )
    assert "неразрешённые конфликты" in branch_user_message(
        unmerged, operation="switch", branch_name="dev"
    )


def test_unknown_branch_error_shows_git_output_excerpt() -> None:
    error = GitCommandError(
        128,
        command=("git", "switch", "-c", "feature/x"),
        stderr="fatal: cannot lock ref 'refs/heads/feature/x'\n",
    )

    message = branch_user_message(
        error, operation="create", branch_name="feature/x"
    )

    assert "Git не смог создать ветку (код 128)" in message
    assert "cannot lock ref" in message
    assert "Подробнее" in message


def test_branch_validation_value_error_is_russian() -> None:
    assert "имя недопустимо" in branch_user_message(
        ValueError("Branch name must not be empty"), operation="create"
    )
    assert "Переключение отменено" in branch_user_message(
        ValueError("Branch name must not be empty"), operation="switch"
    )
