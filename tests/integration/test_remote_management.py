"""Integration-тесты управления remote на реальном Git (без сети)."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from git_visualizer.application.errors import InvalidRemoteUrlError
from git_visualizer.application.services.branch_service import BranchService
from git_visualizer.application.services.changes_service import ChangesService
from git_visualizer.application.services.commit_service import CommitService
from git_visualizer.application.services.diff_service import DiffService
from git_visualizer.application.services.git_environment_service import (
    GitEnvironmentService,
)
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.application.services.history_service import HistoryService
from git_visualizer.application.services.remote_service import RemoteService
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.infrastructure.git.git_errors import GitCommandError, GitErrorKind
from git_visualizer.infrastructure.git.git_repository import GitRepository
from git_visualizer.infrastructure.git.git_runner import GitRunner
from git_visualizer.presentation.controllers.repository_controller import (
    RepositoryController,
)
from git_visualizer.presentation.dialogs.remote_edit_dialog import RemoteEditDialog
from git_visualizer.presentation.windows.main_window import MainWindow

Git = Callable[..., str]
MakeRepo = Callable[..., Path]


def make_factory():
    runner = GitRunner(default_timeout=60)

    def factory(path: Path) -> GitRepository:
        return GitRepository(runner, path)

    return factory


def open_repository(path: Path):
    return RepositoryService(make_factory()).open_repository(path)


def remote_service() -> RemoteService:
    return RemoteService(make_factory())


def configure_author(git: Git, path: Path) -> None:
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "commit.gpgsign", "false")


def rev(git: Git, repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref).strip()


def snapshot(git: Git, repo: Path) -> tuple[str, str, str]:
    return (
        rev(git, repo, "HEAD"),
        git(repo, "branch", "--show-current").strip(),
        git(repo, "status", "--porcelain"),
    )


def remote_names(path: Path) -> tuple[str, ...]:
    return tuple(
        remote.name for remote in make_factory()(path).get_remotes()
    )


# --- Application + Infrastructure на реальном Git ---


def test_add_https_and_ssh_remotes_keeps_local_state(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    local = make_repo("add-remotes", commits=1)
    configure_author(git, local)
    (local / "keep.txt").write_text("keep\n", encoding="utf-8")
    before = snapshot(git, local)
    repository = open_repository(local)
    service = remote_service()

    service.add_remote(
        repository, "origin", "https://example.com/team/repo.git"
    )
    service.add_remote(repository, "fork", "git@example.com:me/fork.git")

    remotes = make_factory()(local).get_remotes()
    by_name = {remote.name: remote for remote in remotes}
    assert set(by_name) == {"origin", "fork"}
    assert by_name["origin"].fetch_url == "https://example.com/team/repo.git"
    assert by_name["origin"].push_url == "https://example.com/team/repo.git"
    assert by_name["fork"].fetch_url == "git@example.com:me/fork.git"
    assert by_name["fork"].push_url == "git@example.com:me/fork.git"
    assert (local / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert snapshot(git, local) == before


def test_duplicate_remote_name_is_rejected_without_changes(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    local = make_repo("duplicate-remote", commits=1)
    repository = open_repository(local)
    service = remote_service()
    service.add_remote(repository, "origin", "https://example.com/one.git")
    before = snapshot(git, local)
    remotes_before = make_factory()(local).get_remotes()

    with pytest.raises(GitCommandError) as info:
        service.add_remote(
            repository, "origin", "https://example.com/two.git"
        )

    assert info.value.kind is GitErrorKind.REMOTE_ALREADY_EXISTS
    assert make_factory()(local).get_remotes() == remotes_before
    assert snapshot(git, local) == before


def test_invalid_remote_name_and_url_are_rejected_without_changes(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    local = make_repo("invalid-remote", commits=1)
    repository = open_repository(local)
    service = remote_service()
    before = snapshot(git, local)

    with pytest.raises(InvalidRemoteUrlError):
        service.add_remote(repository, "origin", "https://")
    with pytest.raises(InvalidRemoteUrlError):
        service.add_remote(repository, "fork", "ssh://")
    with pytest.raises(GitCommandError) as info:
        service.add_remote(
            repository, "bad name", "https://example.com/repo.git"
        )

    assert info.value.kind is GitErrorKind.INVALID_REMOTE_NAME
    assert make_factory()(local).get_remotes() == ()
    assert snapshot(git, local) == before


def test_edit_remote_renames_and_updates_url_using_git(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    local = make_repo("edit-remote", commits=1)
    configure_author(git, local)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    repository = open_repository(local)
    service = remote_service()
    service.add_remote(repository, "origin", str(remote))
    # Настроенный upstream ветки — Git должен обновить его при переименовании.
    git(local, "config", "branch.main.remote", "origin")
    git(local, "config", "branch.main.merge", "refs/heads/main")
    (local / "keep.txt").write_text("keep\n", encoding="utf-8")
    before = snapshot(git, local)

    result = service.edit_remote(
        repository,
        "origin",
        new_name="upstream",
        url="git@example.com:me/repo.git",
    )

    assert result.operation == "edit"
    assert result.remote_name == "origin"
    assert result.new_name == "upstream"
    assert remote_names(local) == ("upstream",)
    assert (
        git(local, "remote", "get-url", "upstream").strip()
        == "git@example.com:me/repo.git"
    )
    assert git(local, "config", "branch.main.remote").strip() == "upstream"
    assert (local / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert snapshot(git, local) == before

    # Изменение только URL не переименовывает remote.
    service.edit_remote(
        repository,
        "upstream",
        new_name="upstream",
        url="https://example.com/team/new.git",
    )
    assert remote_names(local) == ("upstream",)
    assert (
        git(local, "remote", "get-url", "upstream").strip()
        == "https://example.com/team/new.git"
    )


def test_remove_remote_keeps_local_files_and_remote_repository(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    local = make_repo("remove-remote", commits=1)
    configure_author(git, local)
    # Второй репозиторий играет роль сервера: удаление remote не должно его менять.
    remote_repo = make_repo("remote-repo", commits=1)
    remote_head = rev(git, remote_repo, "HEAD")
    remote_file = (remote_repo / "file0.txt").read_text(encoding="utf-8")
    repository = open_repository(local)
    service = remote_service()
    service.add_remote(repository, "origin", str(remote_repo))
    local_head = rev(git, local, "HEAD")
    file_content = (local / "file0.txt").read_text(encoding="utf-8")
    before = snapshot(git, local)

    result = service.remove_remote(repository, "origin")

    assert result.operation == "remove"
    assert result.remote_name == "origin"
    assert result.remotes == ()
    assert remote_names(local) == ()
    assert git(local, "remote").strip() == ""
    # Локальный репозиторий и рабочая директория не изменились.
    assert rev(git, local, "HEAD") == local_head
    assert (local / "file0.txt").read_text(encoding="utf-8") == file_content
    assert snapshot(git, local) == before
    # «Сервер» существует и не изменился.
    assert remote_repo.is_dir()
    assert rev(git, remote_repo, "HEAD") == remote_head
    assert (
        (remote_repo / "file0.txt").read_text(encoding="utf-8") == remote_file
    )


# --- GUI-поток: добавить → изменить → удалить ---


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
    timeout: float = 20.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("условие не выполнилось за отведённое время")
        qt_app.processEvents()
        time.sleep(0.01)
    qt_app.processEvents()


def _branches_panel_texts(window: MainWindow) -> tuple[str, ...]:
    tree = window.workspace_page.branches_panel.tree
    return tuple(
        tree.topLevelItem(group).child(index).text(0)
        for group in range(tree.topLevelItemCount())
        for index in range(tree.topLevelItem(group).childCount())
    )


def test_gui_remote_settings_add_edit_remove_updates_interface(
    qt_app,
    tmp_path: Path,
    git: Git,
    make_repo: MakeRepo,
    monkeypatch: pytest.MonkeyPatch,
    app_settings,
) -> None:
    local = make_repo("gui-remote-settings", commits=1)
    configure_author(git, local)
    (local / "keep.txt").write_text("keep\n", encoding="utf-8")
    local_head = rev(git, local, "HEAD")

    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(local)
    )
    window = MainWindow(build_controller(), app_settings)
    errors: list[tuple[str, BaseException]] = []
    monkeypatch.setattr(
        window, "_show_error", lambda title, exc, **kwargs: errors.append((title, exc))
    )
    try:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and not window.workspace_page.top_bar.is_busy,
        )
        assert window.workspace_page.top_bar.remote_names == ()

        window.workspace_page.top_bar.remote_settings_button.click()
        dialog = window._remote_settings_dialog
        assert dialog is not None
        assert dialog.remotes == ()
        assert dialog.table.rowCount() == 0
        assert dialog.add_button.isEnabled()

        # 1. Добавление: поля ввода открываются отдельным диалогом.
        def fake_add(editor: RemoteEditDialog) -> int:
            editor.name_edit.setText("origin")
            editor.url_edit.setText("https://example.com/team/repo.git")
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(RemoteEditDialog, "exec", fake_add)
        dialog.add_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.top_bar.remote_names == ("origin",)
            and dialog.table.rowCount() == 1
            and not dialog.is_busy
            and not window.workspace_page.top_bar.is_busy,
        )
        assert make_factory()(local).get_remotes()[0].fetch_url == (
            "https://example.com/team/repo.git"
        )
        assert "добавлен" in dialog.status_label.text()
        assert "origin" in _branches_panel_texts(window)

        # 2. Изменение: переименование и URL в одном окне.
        def fake_edit(editor: RemoteEditDialog) -> int:
            assert editor.remote_name == "origin"
            editor.name_edit.setText("upstream")
            editor.url_edit.setText("git@example.com:me/repo.git")
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(RemoteEditDialog, "exec", fake_edit)
        dialog.table.selectRow(0)
        dialog.edit_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.top_bar.remote_names == ("upstream",)
            and dialog.table.rowCount() == 1
            and dialog.table.item(0, 0).text() == "upstream"
            and not dialog.is_busy
            and not window.workspace_page.top_bar.is_busy,
        )
        assert (
            git(local, "remote", "get-url", "upstream").strip()
            == "git@example.com:me/repo.git"
        )
        assert "переименован" in dialog.status_label.text()
        assert "upstream" in _branches_panel_texts(window)

        # 3. Удаление: подтверждение, затем запись remote удаляется.
        confirmations: list[str] = []

        def fake_confirm(box: QMessageBox) -> int:
            confirmations.append(box.text())
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QMessageBox, "exec", fake_confirm)
        dialog.table.selectRow(0)
        dialog.remove_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.top_bar.remote_names == ()
            and dialog.table.rowCount() == 0
            and not dialog.is_busy
            and not window.workspace_page.top_bar.is_busy,
        )
        assert confirmations and "«upstream»" in confirmations[0]
        assert git(local, "remote").strip() == ""
        assert "удалён" in dialog.status_label.text()
        assert "upstream" not in _branches_panel_texts(window)
        # Ни локальные файлы, ни HEAD не изменились.
        assert rev(git, local, "HEAD") == local_head
        assert (local / "keep.txt").read_text(encoding="utf-8") == "keep\n"
        assert errors == []
    finally:
        dialog = window._remote_settings_dialog
        if dialog is not None:
            dialog.close()
        window.close()
