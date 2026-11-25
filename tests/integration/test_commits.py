"""Integration-тесты создания коммита на реальном Git и в GUI."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog

from git_visualizer.application.services.branch_service import BranchService
from git_visualizer.application.services.changes_service import ChangesService
from git_visualizer.application.services.commit_service import CommitService
from git_visualizer.application.services.diff_service import DiffService
from git_visualizer.application.services.git_environment_service import (
    GitEnvironmentService,
)
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.application.services.history_service import HistoryService
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.application.services.remote_service import RemoteService
from git_visualizer.domain.models import Repository
from git_visualizer.infrastructure.git.git_errors import GitCommandError, GitErrorKind
from git_visualizer.infrastructure.git.git_repository import GitRepository
from git_visualizer.infrastructure.git.git_runner import GitRunner
from git_visualizer.presentation.controllers.repository_controller import (
    RepositoryController,
)
from git_visualizer.presentation.windows.main_window import MainWindow

Git = Callable[..., str]
MakeRepo = Callable[..., Path]


def make_factory():
    runner = GitRunner(default_timeout=60)

    def factory(path: Path) -> GitRepository:
        return GitRepository(runner, path)

    return factory


def load(path: Path) -> tuple[Repository, CommitService]:
    factory = make_factory()
    return RepositoryService(factory).open_repository(path), CommitService(factory)


def configure_author(git: Git, path: Path) -> None:
    """Локальная идентичность автора: глобальный конфиг в тестах отключён."""
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "commit.gpgsign", "false")


def build_controller() -> RepositoryController:
    runner = GitRunner(default_timeout=60)

    def factory(path: Path) -> GitRepository:
        return GitRepository(runner, path)

    return RepositoryController(
        repository_service=RepositoryService(factory),
        history_service=HistoryService(factory),
        branch_service=BranchService(factory),
        graph_service=GraphService(),
        diff_service=DiffService(factory),
        changes_service=ChangesService(factory),
        commit_service=CommitService(factory),
        remote_service=RemoteService(factory),
        git_environment_service=GitEnvironmentService(runner),
    )


def wait_until(
    qt_app,
    predicate: Callable[[], bool],
    *,
    timeout: float = 15.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("условие не выполнилось за отведённое время")
        qt_app.processEvents()
        time.sleep(0.01)
    qt_app.processEvents()


# --- CommitService на реальном Git ---


def test_commit_creates_commit_from_staged_files(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("commit-success", commits=1)
    configure_author(git, path)
    staged = path / "новый файл с пробелами.txt"
    staged.write_text("staged content\n", encoding="utf-8")
    git(path, "add", "--", staged.name)
    repository, service = load(path)
    message = "Заголовок коммита\n\nТело с юникодом: привет\n"

    service.commit(repository, message)

    head = git(path, "rev-parse", "HEAD").strip()
    assert git(path, "log", "-1", "--format=%s").strip() == "Заголовок коммита"
    assert git(path, "log", "-1", "--format=%b").strip() == "Тело с юникодом: привет"
    changed = git(
        path, "-c", "core.quotePath=false", "show", "--name-only", "--format=", "HEAD"
    )
    assert "новый файл с пробелами.txt" in changed.splitlines()
    assert git(path, "status", "--porcelain") == ""
    assert staged.read_text(encoding="utf-8") == "staged content\n"

    # Снимок Repository описывает HEAD на момент открытия; после коммита нужен новый.
    reopened = RepositoryService(make_factory()).open_repository(path)
    assert reopened.head_commit == head


def test_commit_is_rejected_when_index_is_empty(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("commit-empty-index", commits=1)
    file = path / "file0.txt"
    file.write_text("unstaged change\n", encoding="utf-8")
    (path / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    repository, service = load(path)
    head_before = git(path, "rev-parse", "HEAD").strip()

    with pytest.raises(ValueError):
        service.commit(repository, "Message")

    assert git(path, "rev-parse", "HEAD").strip() == head_before
    assert file.read_text(encoding="utf-8") == "unstaged change\n"
    status = git(path, "status", "--porcelain")
    assert " M file0.txt" in status
    assert "?? untracked.txt" in status


def test_unstaged_changes_do_not_get_into_commit(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("commit-staged-only", commits=1)
    configure_author(git, path)
    both = path / "file0.txt"
    both.write_text("staged version\n", encoding="utf-8")
    git(path, "add", "--", "file0.txt")
    # После git add файл изменён ещё раз: в индексе staged, в рабочей директории — нет.
    both.write_text("worktree version\n", encoding="utf-8")
    unstaged = path / "file1.txt"
    unstaged.write_text("only in worktree\n", encoding="utf-8")
    repository, service = load(path)

    service.commit(repository, "Commit staged snapshot")

    committed = git(path, "show", "HEAD:file0.txt")
    assert committed.replace("\r\n", "\n") == "staged version\n"
    # Рабочая директория не изменена коммитом, а unstaged-файл в коммит не попал.
    assert both.read_text(encoding="utf-8") == "worktree version\n"
    assert unstaged.read_text(encoding="utf-8") == "only in worktree\n"
    changed = git(path, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert "file0.txt" in changed
    assert "file1.txt" not in changed
    status = git(path, "status", "--porcelain")
    assert " M file0.txt" in status
    assert "?? file1.txt" in status


def test_commit_without_author_configuration_is_reported(
    make_repo: MakeRepo, git: Git, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_repo("commit-no-author", commits=1)
    for variable in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "EMAIL",
    ):
        monkeypatch.delenv(variable, raising=False)
    (path / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(path, "add", "--", "staged.txt")
    repository, service = load(path)
    head_before = git(path, "rev-parse", "HEAD").strip()

    with pytest.raises(GitCommandError) as info:
        service.commit(repository, "Message")

    assert info.value.kind is GitErrorKind.MISSING_AUTHOR
    assert git(path, "rev-parse", "HEAD").strip() == head_before
    assert "fatal:" in info.value.stderr


def test_commit_hook_failure_is_reported_without_crashing(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("commit-hook", commits=1)
    configure_author(git, path)
    hooks = path / "custom-hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "echo 'pre-commit rejected by test' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    git(path, "config", "core.hooksPath", str(hooks))
    (path / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(path, "add", "--", "staged.txt")
    repository, service = load(path)
    head_before = git(path, "rev-parse", "HEAD").strip()

    # Отказ хука — типизированная ошибка Git, а не падение приложения.
    with pytest.raises(GitCommandError) as info:
        service.commit(repository, "Message")

    assert info.value.returncode != 0
    assert info.value.command[-3:] == ("commit", "-F", "-")
    assert git(path, "rev-parse", "HEAD").strip() == head_before
    # В песочнице Windows запуск sh для хука может сам завершиться ошибкой —
    # приложение всё равно получает GitCommandError с диагностикой.
    assert info.value.stderr or info.value.stdout


def test_commit_with_unmerged_files_returns_typed_error(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("commit-unmerged", commits=0)
    configure_author(git, path)
    file = path / "conflict.txt"
    file.write_text("base\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "base")
    git(path, "switch", "-q", "-c", "feature")
    file.write_text("feature\n", encoding="utf-8")
    git(path, "commit", "-q", "-am", "feature")
    git(path, "switch", "-q", "main")
    file.write_text("main\n", encoding="utf-8")
    git(path, "commit", "-q", "-am", "main")
    with pytest.raises(GitCommandError):
        git(path, "merge", "feature")
    # В индексе есть staged-файл, поэтому CommitService доходит до Git и получает
    # его собственное сообщение о неразрешённом конфликте.
    (path / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(path, "add", "--", "staged.txt")
    repository, service = load(path)

    with pytest.raises(GitCommandError) as info:
        service.commit(repository, "Message")

    assert info.value.kind is GitErrorKind.UNMERGED_FILES
    assert "unmerged files" in info.value.stderr


# --- GUI-поток: коммит через нижнюю панель Changes ---


def open_window(
    qt_app,
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> MainWindow:
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    window = MainWindow(build_controller(), settings)
    window.start_screen.open_button.click()
    wait_until(qt_app, lambda: window.workspace_page.workspace_data is not None)
    return window


def test_gui_commit_updates_history_graph_refs_and_changes(
    qt_app, make_repo: MakeRepo, git: Git, monkeypatch: pytest.MonkeyPatch, app_settings
) -> None:
    path = make_repo("gui-commit", commits=1)
    configure_author(git, path)
    staged = path / "staged.txt"
    staged.write_text("staged content\n", encoding="utf-8")
    git(path, "add", "--", "staged.txt")
    unstaged = path / "file0.txt"
    unstaged.write_text("worktree change\n", encoding="utf-8")
    window = open_window(qt_app, path, monkeypatch, app_settings)
    shown: list[tuple[str, BaseException]] = []
    try:
        monkeypatch.setattr(
            window,
            "_show_error",
            lambda title, exc, **kwargs: shown.append((title, exc)),
        )
        panel = window.workspace_page.changes_panel
        wait_until(
            qt_app,
            lambda: len(panel.staged_files) == 1
            and [change.path for change in panel.unstaged_files] == ["file0.txt"],
        )
        panel.commit_button.click()
        wait_until(qt_app, lambda: not window.commit_overlay.isHidden())
        assert "Введите сообщение" in window.commit_overlay.hint_label.text()
        window.commit_overlay.message_edit.setPlainText("Коммит из GUI")
        assert "Staged (1)" in window.commit_overlay.hint_label.text()
        assert window.commit_overlay.commit_button.isEnabled()

        window.commit_overlay.commit_button.click()

        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.workspace_page.workspace_data.commits[0].subject
            == "Коммит из GUI",
        )
        wait_until(
            qt_app,
            lambda: window.commit_overlay.isHidden()
            and window.commit_overlay.message == ""
            and not panel.staged_files
            and [change.path for change in panel.unstaged_files] == ["file0.txt"],
        )
        assert shown == []
        assert not panel.is_busy
        assert "Нет staged-изменений" in panel.commit_button.toolTip()

        data = window.workspace_page.workspace_data
        head = git(path, "rev-parse", "HEAD").strip()
        assert data is not None
        assert data.repository.head_commit == head
        assert data.repository.current_branch == "main"
        assert window.workspace_page.graph_view.commit_count == 2
        assert any(
            label.name == "main" for label in data.ref_labels[data.commits[0].hash]
        )
        # В коммит попал только staged-файл; рабочая директория сохранена.
        changed = git(path, "show", "--name-only", "--format=", "HEAD").splitlines()
        assert "staged.txt" in changed
        assert "file0.txt" not in changed
        assert unstaged.read_text(encoding="utf-8") == "worktree change\n"
        status = git(path, "status", "--porcelain")
        assert " M file0.txt" in status
        assert "staged.txt" not in status
    finally:
        window.close()


def test_gui_commit_error_without_author_keeps_application_alive(
    qt_app, make_repo: MakeRepo, git: Git, monkeypatch: pytest.MonkeyPatch, app_settings
) -> None:
    path = make_repo("gui-commit-error", commits=1)
    for variable in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "EMAIL",
    ):
        monkeypatch.delenv(variable, raising=False)
    (path / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(path, "add", "--", "staged.txt")
    window = open_window(qt_app, path, monkeypatch, app_settings)
    shown: list[tuple[str, BaseException, dict[str, object]]] = []
    try:
        monkeypatch.setattr(
            window,
            "_show_error",
            lambda title, exc, **kwargs: shown.append((title, exc, kwargs)),
        )
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: len(panel.staged_files) == 1)
        panel.commit_button.click()
        wait_until(qt_app, lambda: not window.commit_overlay.isHidden())
        window.commit_overlay.message_edit.setPlainText("Не пройдёт")

        window.commit_overlay.commit_button.click()

        wait_until(qt_app, lambda: bool(shown))
        wait_until(qt_app, lambda: len(panel.staged_files) == 1)
        wait_until(qt_app, lambda: not panel.is_busy)
        title, error, kwargs = shown[0]
        assert title == "Не удалось создать коммит"
        assert isinstance(error, GitCommandError)
        assert error.kind is GitErrorKind.MISSING_AUTHOR
        assert "user.name" in kwargs["message"]
        # Приложение осталось в рабочей области, коммит не создан, данные целы.
        assert window.stack.currentWidget() is window.workspace_page
        assert window.workspace_page.workspace_data is not None
        assert window.workspace_page.workspace_data.commits[0].subject == "commit 0"
        assert not window.commit_overlay.isHidden()
        assert window.commit_overlay.message == "Не пройдёт"
        assert len(panel.staged_files) == 1
        assert git(path, "log", "-1", "--format=%s").strip() == "commit 0"
    finally:
        window.close()
