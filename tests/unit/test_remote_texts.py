"""Русские тексты сетевых операций: ход, итог и причины отказа."""

from git_visualizer.application.errors import (
    NoCurrentBranchError,
    UnknownRemoteError,
    UpstreamNotConfiguredError,
)
from git_visualizer.application.services.remote_service import (
    RemoteOperationContext,
    RemoteOperationResult,
)
from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitTimeoutError,
)
from git_visualizer.presentation.remote_texts import (
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
