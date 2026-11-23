"""Integration-тесты основного потока на реальном Git и реальных репозиториях."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from git_visualizer.application.services.branch_service import BranchService
from git_visualizer.application.services.git_environment_service import (
    GitEnvironmentService,
)
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.application.services.history_service import HistoryService
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.infrastructure.git.git_repository import GitRepository
from git_visualizer.infrastructure.git.git_runner import GitRunner
from git_visualizer.presentation.controllers.repository_controller import (
    RepositoryController,
)
from git_visualizer.presentation.windows.main_window import MainWindow


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


def build_controller() -> RepositoryController:
    runner = GitRunner()

    def factory(path: Path) -> GitRepository:
        return GitRepository(runner, path)

    return RepositoryController(
        repository_service=RepositoryService(factory),
        history_service=HistoryService(factory),
        branch_service=BranchService(factory),
        graph_service=GraphService(),
        git_environment_service=GitEnvironmentService(runner),
    )


def test_real_git_version_is_supported() -> None:
    environment = GitEnvironmentService(GitRunner()).check()

    assert environment.is_supported, (
        f"Git {environment.version_text} is older than {environment.minimum_text}"
    )


def test_main_flow_with_real_repository(
    qt_app,
    make_repo,
    git,
    monkeypatch,
) -> None:
    path = make_repo("gui-repo", commits=2)
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    controller = build_controller()
    window = MainWindow(controller)

    try:
        wait_until(
            qt_app,
            lambda: "поддерживается"
            in window.start_screen.git_status_label.text(),
        )
        window.start_screen.open_button.click()

        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.workspace_page.graph_view.commit_count == 2,
        )

        workspace = window.workspace_page
        data = workspace.workspace_data
        assert data is not None
        assert data.repository.name == "gui-repo"
        assert data.repository.current_branch == "main"
        assert workspace.top_bar.repository_name_label.text() == "gui-repo"
        assert workspace.top_bar.branch_label.text() == "Ветка: main"
        assert workspace.top_bar.refresh_button.isEnabled()
        assert workspace.details_panel.current_commit is None

        local_group = workspace.branches_panel.tree.topLevelItem(0)
        assert local_group is not None
        assert local_group.text(0) == "Локальные ветки"
        assert local_group.child(0).text(0).startswith("main")

        head_hash = data.commits[0].hash
        workspace.graph_view.commit_selected.emit(head_hash)
        assert workspace.selected_commit_hash == head_hash
        assert workspace.details_panel.current_commit is not None
        assert workspace.details_panel.hash_value.text() == head_hash

        # Ручное обновление истории видит новый коммит.
        (path / "new.txt").write_text("new\n", encoding="utf-8")
        git(path, "add", "--", "new.txt")
        git(
            path,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "-c", "commit.gpgsign=false",
            "commit", "-q", "-m", "third commit",
        )
        workspace.top_bar.refresh_button.click()
        wait_until(qt_app, lambda: workspace.graph_view.commit_count == 3)

        assert workspace.selected_commit_hash is None
        assert workspace.details_panel.current_commit is None

        workspace.top_bar.close_button.click()
        assert window.stack.currentWidget() is window.start_screen
        assert controller.repository is None
        assert workspace.graph_view.is_empty
    finally:
        window.close()


def test_empty_repository_flow_with_real_git(
    qt_app,
    make_repo,
    monkeypatch,
) -> None:
    path = make_repo("empty-repo", commits=0)
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    controller = build_controller()
    window = MainWindow(controller)

    try:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None,
        )

        workspace = window.workspace_page
        assert workspace.graph_view.is_empty
        assert workspace.details_panel.current_commit is None
        assert "нет коммитов" in workspace.details_panel.placeholder_text
        assert workspace.top_bar.branch_label.text() == "Ветка: main (коммитов нет)"
    finally:
        window.close()
