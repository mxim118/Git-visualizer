"""Русские тексты сетевых операций: ход, итог и причины отказа."""

from git_visualizer.application.errors import (
    InvalidRemoteNameError,
    InvalidRemoteUrlError,
    NoCurrentBranchError,
    UnknownRemoteError,
    UpstreamNotConfiguredError,
)
from git_visualizer.application.services.remote_service import (
    RemoteConfigurationContext,
    RemoteConfigurationResult,
    RemoteOperationContext,
    RemoteOperationResult,
)
from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitTimeoutError,
)
from git_visualizer.presentation.remote_texts import (
    remote_configuration_error_title,
    remote_configuration_progress_text,
    remote_configuration_result_text,
    remote_configuration_user_message,
    remote_error_title,
    remote_progress_text,
    remote_result_text,
    remote_user_message,
)

# Реальные сообщения Git (LC_ALL=C), сгруппированные по виду ошибки.
NETWORK_STDERR = (
    "fatal: unable to access 'https://example.com/r.git/': "
    "Could not resolve host: example.com\n"
)
AUTH_STDERR = (
    "fatal: could not read Username for 'https://example.com': "
    "terminal prompts disabled\n"
)
PERMISSION_STDERR = (
    "remote: Permission to user/repo.git denied to other.\n"
    "fatal: unable to access 'https://example.com/r.git/': "
    "The requested URL returned error: 403\n"
)
REMOTE_NOT_FOUND_STDERR = (
    "fatal: '/tmp/missing.git' does not appear to be a git repository\n"
    "fatal: Could not read from remote repository.\n\n"
    "Please make sure you have the correct access rights\n"
    "and the repository exists.\n"
)
NON_FAST_FORWARD_STDERR = (
    "To /tmp/remote.git\n"
    " ! [rejected]        main -> main (non-fast-forward)\n"
    "error: failed to push some refs to '/tmp/remote.git'\n"
)
PULL_FF_STDERR = (
    "From /tmp/remote.git\n"
    " * branch            main       -> FETCH_HEAD\n"
    "fatal: Not possible to fast-forward, aborting.\n"
)
MERGE_CONFLICT_STDERR = (
    "Auto-merging file.txt\n"
    "CONFLICT (content): Merge conflict in file.txt\n"
    "Automatic merge failed; fix conflicts and then commit the result.\n"
)
DIRTY_WORKTREE_STDERR = (
    "error: Your local changes to the following files would be overwritten by merge:\n"
    "\tfile.txt\n"
    "Please commit your changes or stash them before you merge.\n"
    "Aborting\n"
)
REMOTE_ALREADY_EXISTS_STDERR = "error: remote origin already exists.\n"
INVALID_REMOTE_NAME_STDERR = "fatal: 'bad name' is not a valid remote name\n"
REMOTE_NOT_CONFIGURED_STDERR = "error: No such remote: 'origin'\n"


def test_progress_text_names_remote_and_branch() -> None:
    assert "«origin»" in remote_progress_text(
        RemoteOperationContext(operation="fetch", remote="origin")
    )
    assert "--ff-only" in remote_progress_text(
        RemoteOperationContext(operation="pull", branch="main")
    )
    assert "«main»" in remote_progress_text(
        RemoteOperationContext(operation="push", branch="main")
    )


def test_fetch_result_text() -> None:
    result = RemoteOperationResult(operation="fetch", remote="origin")

    assert remote_result_text(result) == "Fetch «origin»: выполнено."


def test_pull_result_text_detects_fast_forward_and_up_to_date() -> None:
    fast_forward = RemoteOperationResult(
        operation="pull", branch="main", upstream="origin/main", output="Fast-forward\n"
    )
    up_to_date = RemoteOperationResult(
        operation="pull", branch="main", upstream="origin/main", output="Already up to date.\n"
    )

    assert "fast-forward выполнен" in remote_result_text(fast_forward)
    assert "уже актуально" in remote_result_text(up_to_date)


def test_push_result_text_mentions_upstream() -> None:
    result = RemoteOperationResult(
        operation="push", branch="main", upstream="origin/main", output=""
    )

    text = remote_result_text(result)

    assert "Push «main»" in text
    assert "origin/main" in text
    assert "выполнено" in text


def test_initial_push_result_text_mentions_remote_and_upstream() -> None:
    result = RemoteOperationResult(
        operation="push",
        remote="fork",
        branch="main",
        upstream="fork/main",
        output="",
    )

    text = remote_result_text(result)

    assert "«fork»" in text
    assert "Upstream: fork/main" in text


def test_unknown_remote_message() -> None:
    message = remote_user_message(
        UnknownRemoteError("fork"), operation="fetch", remote_name="fork"
    )

    assert "«fork»" in message
    assert "не настроен" in message


def test_upstream_messages_differ_for_pull_and_push() -> None:
    pull = remote_user_message(
        UpstreamNotConfiguredError("main"), operation="pull", branch_name="main"
    )
    push = remote_user_message(
        UpstreamNotConfiguredError("main"), operation="push", branch_name="main"
    )

    assert "Pull невозможен" in pull
    assert "-u" in pull
    assert "Push невозможен" in push
    assert "-u" in push


def test_detached_head_message() -> None:
    message = remote_user_message(NoCurrentBranchError(), operation="pull")

    assert "detached HEAD" in message


def test_authentication_message_mentions_standard_git_mechanisms() -> None:
    message = remote_user_message(
        GitCommandError(128, stderr=AUTH_STDERR),
        operation="push",
        remote_name="origin",
        remote_url="https://example.com/r.git",
    )

    assert "авторизоваться" in message
    assert "Git Credential Manager" in message
    assert "GIT_TERMINAL_PROMPT=0" in message
    assert "не хранит" in message


def test_permission_message_is_about_rights() -> None:
    message = remote_user_message(
        GitCommandError(1, stderr=PERMISSION_STDERR),
        operation="push",
        remote_name="origin",
    )

    assert "нет прав" in message or "отсутствии прав" in message
    assert "force push" in message


def test_network_error_message() -> None:
    message = remote_user_message(
        GitCommandError(128, stderr=NETWORK_STDERR),
        operation="fetch",
        remote_name="origin",
    )

    assert "Сетевая ошибка" in message


def test_remote_not_found_message() -> None:
    message = remote_user_message(
        GitCommandError(128, stderr=REMOTE_NOT_FOUND_STDERR),
        operation="fetch",
        remote_name="origin",
        remote_url="/tmp/missing.git",
    )

    assert "не нашёл удалённый репозиторий" in message
    assert "/tmp/missing.git" in message


def test_non_fast_forward_pull_and_push_messages() -> None:
    pull = remote_user_message(
        GitCommandError(128, stderr=PULL_FF_STDERR),
        operation="pull",
        branch_name="main",
        upstream="origin/main",
    )
    push = remote_user_message(
        GitCommandError(1, stderr=NON_FAST_FORWARD_STDERR),
        operation="push",
    )

    assert "разошлись" in pull
    assert "ничего не изменил" in pull
    assert "non-fast-forward" in push
    assert "Force push не выполняется" in push


def test_merge_conflict_message() -> None:
    message = remote_user_message(
        GitCommandError(1, stderr=MERGE_CONFLICT_STDERR),
        operation="pull",
    )

    assert "конфликт слияния" in message
    assert "не разрешает конфликты" in message


def test_dirty_worktree_message_keeps_changes() -> None:
    message = remote_user_message(
        GitCommandError(1, stderr=DIRTY_WORKTREE_STDERR),
        operation="pull",
    )

    assert "незакоммиченные изменения" in message
    assert "file.txt" in message
    assert "не удаляет" in message


def test_timeout_message_contains_timeout() -> None:
    message = remote_user_message(
        GitTimeoutError(120.0, command=("git", "fetch")),
        operation="fetch",
        remote_name="origin",
    )

    assert "120" in message
    assert "не завершилась" in message


def test_unknown_error_includes_git_output() -> None:
    error = GitCommandError(
        1,
        command=("git", "push"),
        stderr="remote: custom hook refusal\nfatal: something\n",
    )

    message = remote_user_message(error, operation="push")

    assert "custom hook refusal" in message
    assert "Подробнее" in message


def test_error_titles_are_russian() -> None:
    assert remote_error_title("fetch") == "Не удалось выполнить Fetch"
    assert remote_error_title("pull") == "Не удалось выполнить Pull"
    assert remote_error_title("push") == "Не удалось выполнить Push"


# --- настройка remote ---


def test_configuration_progress_text_names_operation_and_remote() -> None:
    add = remote_configuration_progress_text(
        RemoteConfigurationContext(operation="add", remote_name="origin")
    )
    edit = remote_configuration_progress_text(
        RemoteConfigurationContext(
            operation="edit", remote_name="origin", new_name="upstream"
        )
    )
    remove = remote_configuration_progress_text(
        RemoteConfigurationContext(operation="remove", remote_name="fork")
    )

    assert "Добавление" in add and "«origin»" in add
    assert "Изменение" in edit and "«origin»" in edit
    assert "Удаление" in remove and "«fork»" in remove


def test_configuration_result_texts() -> None:
    assert (
        remote_configuration_result_text(
            RemoteConfigurationResult(operation="add", remote_name="origin")
        )
        == "Remote «origin» добавлен."
    )
    assert (
        remote_configuration_result_text(
            RemoteConfigurationResult(operation="remove", remote_name="fork")
        )
        == "Remote «fork» удалён."
    )
    assert (
        remote_configuration_result_text(
            RemoteConfigurationResult(
                operation="edit",
                remote_name="origin",
                new_name="upstream",
            )
        )
        == "Remote «origin» переименован в «upstream»."
    )
    assert (
        remote_configuration_result_text(
            RemoteConfigurationResult(
                operation="edit", remote_name="origin", new_name="origin"
            )
        )
        == "Remote «origin» изменён."
    )


def test_configuration_error_titles() -> None:
    assert (
        remote_configuration_error_title(
            RemoteConfigurationContext(operation="add")
        )
        == "Не удалось добавить remote"
    )
    assert (
        remote_configuration_error_title(
            RemoteConfigurationContext(operation="edit")
        )
        == "Не удалось изменить remote"
    )
    assert (
        remote_configuration_error_title(
            RemoteConfigurationContext(operation="remove")
        )
        == "Не удалось удалить remote"
    )


def test_invalid_remote_name_message_explains_rules() -> None:
    message = remote_configuration_user_message(
        InvalidRemoteNameError("empty"),
        context=RemoteConfigurationContext(operation="add", remote_name="bad name"),
    )

    assert "недопустимо" in message
    assert "пробелы" in message


def test_invalid_remote_url_message_explains_supported_urls() -> None:
    message = remote_configuration_user_message(
        InvalidRemoteUrlError("no host"),
        context=RemoteConfigurationContext(operation="add", remote_name="origin"),
    )

    assert "недопустим" in message
    assert "HTTPS" in message
    assert "SSH" in message
    assert "https://" in message


def test_duplicate_remote_message_uses_new_name_when_editing() -> None:
    message = remote_configuration_user_message(
        GitCommandError(3, stderr=REMOTE_ALREADY_EXISTS_STDERR),
        context=RemoteConfigurationContext(
            operation="edit", remote_name="origin", new_name="upstream"
        ),
    )

    assert "«upstream»" in message
    assert "уже существует" in message


def test_invalid_remote_name_git_error_is_translated() -> None:
    message = remote_configuration_user_message(
        GitCommandError(128, stderr=INVALID_REMOTE_NAME_STDERR),
        context=RemoteConfigurationContext(
            operation="add", remote_name="bad name"
        ),
    )

    assert "Git отклонил имя remote" in message
    assert "«bad name»" in message


def test_missing_remote_during_configuration_is_translated() -> None:
    message = remote_configuration_user_message(
        GitCommandError(2, stderr=REMOTE_NOT_CONFIGURED_STDERR),
        context=RemoteConfigurationContext(
            operation="remove", remote_name="origin"
        ),
    )

    assert "не найден" in message
    assert "«origin»" in message


def test_unknown_configuration_error_includes_git_output() -> None:
    message = remote_configuration_user_message(
        GitCommandError(
            1,
            command=("git", "remote", "remove", "--", "origin"),
            stderr="fatal: could not remove config section\n",
        ),
        context=RemoteConfigurationContext(
            operation="remove", remote_name="origin"
        ),
    )

    assert "could not remove config section" in message
    assert "Подробнее" in message
