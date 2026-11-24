"""Integration-тесты основного потока на реальном Git и реальных репозиториях."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

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
        diff_service=DiffService(factory),
        changes_service=ChangesService(factory),
        commit_service=CommitService(factory),
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
        assert head_hash in workspace.details_panel.metadata_view.toPlainText()

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


def test_gui_commit_details_and_file_diff_flow(
    qt_app,
    make_repo,
    git,
    monkeypatch,
) -> None:
    path = make_repo("gui-diff", commits=1)
    (path / "file0.txt").write_text("changed\n", encoding="utf-8")
    git(path, "add", "--", "file0.txt")
    git(
        path,
        "-c", "user.name=Test",
        "-c", "user.email=test@example.com",
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", "change file0",
    )
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    controller = build_controller()
    window = MainWindow(controller)

    try:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.workspace_page.graph_view.commit_count == 2,
        )

        data = window.workspace_page.workspace_data
        assert data is not None
        latest = data.commits[0]
        assert latest.subject == "change file0"
        window.workspace_page.graph_view.commit_selected.emit(latest.hash)

        details = window.workspace_page.details_panel
        wait_until(qt_app, lambda: len(details.files) == 1)
        assert details.files[0].path == "file0.txt"
        assert "Изменения относительно родителя" in details.metadata_view.toPlainText()

        details.files_tree.setCurrentItem(details.files_tree.topLevelItem(0))
        wait_until(qt_app, lambda: bool(window.diff_overlay.diff_view.text))

        assert details.selected_file == details.files[0]
        assert not window.diff_overlay.isHidden()
        assert "-0" in window.diff_overlay.diff_view.text
        assert "+changed" in window.diff_overlay.diff_view.text
        assert "Сравнение с родителем" in window.diff_overlay.diff_view.base_label.text()
    finally:
        window.close()


def test_gui_worktree_changes_flow(
    qt_app,
    make_repo,
    git,
    monkeypatch,
) -> None:
    path = make_repo("gui-changes", commits=1)
    (path / "file0.txt").write_text("unstaged change\n", encoding="utf-8")
    (path / "staged.txt").write_text("staged content\n", encoding="utf-8")
    git(path, "add", "--", "staged.txt")
    (path / "untracked.txt").write_text("untracked\n", encoding="utf-8")
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
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: len(panel.changes) == 3)
        assert "Вне индекса: 2" in panel.status_label.text()
        assert "В индексе: 1" in panel.status_label.text()
        assert [change.path for change in panel.unstaged_files] == [
            "file0.txt",
            "untracked.txt",
        ]
        assert [change.path for change in panel.staged_files] == ["staged.txt"]

        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))
        assert window.diff_overlay.isHidden()
        panel.diff_button.click()
        wait_until(
            qt_app,
            lambda: not window.diff_overlay.isHidden()
            and bool(window.diff_overlay.diff_view.text),
        )
        assert "-0" in window.diff_overlay.diff_view.text
        assert "+unstaged change" in window.diff_overlay.diff_view.text
        assert "Unstaged diff" in window.diff_overlay.diff_view.base_label.text()
        assert "вне индекса" in window.diff_overlay.diff_view.header_label.text()

        panel.staged_tree.setCurrentItem(panel.staged_tree.topLevelItem(0))
        assert window.diff_overlay.isHidden()
        panel.diff_button.click()
        wait_until(
            qt_app,
            lambda: "new file mode" in window.diff_overlay.diff_view.text,
        )
        assert "+staged content" in window.diff_overlay.diff_view.text
        assert "Staged diff" in window.diff_overlay.diff_view.base_label.text()
        assert "в индексе" in window.diff_overlay.diff_view.header_label.text()

        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(1))
        assert panel.selected_file is not None
        assert panel.selected_file.path == "untracked.txt"
        assert window.diff_overlay.isHidden()
        panel.diff_button.click()
        assert "не отслеживается" in window.diff_overlay.diff_view.message_text
        assert "untracked.txt" in window.diff_overlay.diff_view.header_label.text()

        window.diff_overlay.close_button.click()
        assert window.diff_overlay.isHidden()
        # Выбор Changes сохраняется: Stage/Unstage доступны сразу после закрытия.
        assert panel.selected_file is not None
        assert panel.selected_file.path == "untracked.txt"

        (path / "another.txt").write_text("another\n", encoding="utf-8")
        panel.refresh_button.click()
        wait_until(qt_app, lambda: len(panel.changes) == 4)
        assert sorted(change.path for change in panel.unstaged_files) == [
            "another.txt",
            "file0.txt",
            "untracked.txt",
        ]
    finally:
        window.close()


def test_gui_stage_and_unstage_flow(
    qt_app,
    make_repo,
    git,
    monkeypatch,
) -> None:
    path = make_repo("gui-stage", commits=1)
    file = path / "file0.txt"
    file.write_text("changed\n", encoding="utf-8")
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
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: len(panel.changes) == 1)
        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))
        # Клик по файлу только выбирает его; diff открывается отдельной кнопкой.
        assert window.diff_overlay.isHidden()
        panel.diff_button.click()
        wait_until(
            qt_app,
            lambda: not window.diff_overlay.isHidden()
            and "Unstaged diff" in window.diff_overlay.diff_view.base_label.text(),
        )

        panel.stage_button.click()

        wait_until(
            qt_app,
            lambda: len(panel.staged_files) == 1 and not panel.unstaged_files,
        )
        wait_until(
            qt_app,
            lambda: "Staged diff"
            in window.diff_overlay.diff_view.base_label.text()
            and "+changed" in window.diff_overlay.diff_view.text,
        )
        assert panel.staged_files[0].path == "file0.txt"
        assert "+changed" in window.diff_overlay.diff_view.text
        assert git(path, "status", "--porcelain") == "M  file0.txt\n"

        panel.unstage_button.click()

        wait_until(
            qt_app,
            lambda: len(panel.unstaged_files) == 1 and not panel.staged_files,
        )
        wait_until(
            qt_app,
            lambda: "Unstaged diff"
            in window.diff_overlay.diff_view.base_label.text()
            and "+changed" in window.diff_overlay.diff_view.text,
        )
        assert panel.unstaged_files[0].path == "file0.txt"
        # Unstage не отменяет изменение в рабочей директории.
        assert file.read_text(encoding="utf-8") == "changed\n"
        assert git(path, "status", "--porcelain") == " M file0.txt\n"
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
