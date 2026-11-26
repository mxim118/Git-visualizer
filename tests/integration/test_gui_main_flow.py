"""Integration-тесты основного потока на реальном Git и реальных репозиториях."""

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
from git_visualizer.domain.enums import RefKind
from git_visualizer.domain.models import ChangedFile
from git_visualizer.infrastructure.git.git_errors import GitCommandError, GitErrorKind
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
        remote_service=RemoteService(factory),
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
    app_settings,
) -> None:
    path = make_repo("gui-repo", commits=2)
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    controller = build_controller()
    window = MainWindow(controller, app_settings)

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
    app_settings,
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
    window = MainWindow(controller, app_settings)

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
    app_settings,
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
    window = MainWindow(controller, app_settings)

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
    app_settings,
) -> None:
    path = make_repo("gui-stage", commits=1)
    file = path / "file0.txt"
    file.write_text("changed\n", encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    controller = build_controller()
    window = MainWindow(controller, app_settings)

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


def test_gui_discard_changes_flow(
    qt_app,
    make_repo,
    git,
    monkeypatch,
    app_settings,
) -> None:
    path = make_repo("gui-discard", commits=1)
    tracked = path / "file0.txt"
    tracked.write_text("changed\n", encoding="utf-8")
    untracked = path / "untracked.txt"
    untracked.write_text("new\n", encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    controller = build_controller()
    window = MainWindow(controller, app_settings)
    confirmations: list[tuple[str, bool]] = []

    try:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None,
        )
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: len(panel.changes) == 2)

        def confirm(changed_file, *, has_head):
            confirmations.append((changed_file.path, has_head))
            return True

        monkeypatch.setattr(window, "_confirm_discard", confirm)

        # Отмена изменений отслеживаемого файла возвращает его к HEAD.
        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))
        panel.diff_button.click()
        wait_until(
            qt_app,
            lambda: not window.diff_overlay.isHidden()
            and bool(window.diff_overlay.diff_view.text),
        )
        assert "+changed" in window.diff_overlay.diff_view.text

        panel.discard_button.click()

        wait_until(
            qt_app,
            lambda: [change.path for change in panel.changes] == ["untracked.txt"],
        )
        assert tracked.read_text(encoding="utf-8") == "0\n"
        assert git(path, "status", "--porcelain") == "?? untracked.txt\n"
        # Открытая панель diff относилась к отменённому содержимому.
        assert window.diff_overlay.isHidden()

        # Untracked-файл удаляется после отдельного подтверждения.
        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))
        panel.discard_button.click()

        wait_until(qt_app, lambda: panel.is_clean)
        assert not untracked.exists()
        assert git(path, "status", "--porcelain") == ""
        assert "Изменений нет" in window.statusBar().currentMessage()
        assert panel.staged_files == ()
        assert confirmations == [("file0.txt", True), ("untracked.txt", True)]
    finally:
        window.close()


def test_empty_repository_flow_with_real_git(
    qt_app,
    make_repo,
    monkeypatch,
    app_settings,
) -> None:
    path = make_repo("empty-repo", commits=0)
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    controller = build_controller()
    window = MainWindow(controller, app_settings)

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


def test_gui_loads_more_old_history(
    qt_app,
    make_repo,
    monkeypatch,
    app_settings,
) -> None:
    """FR-HIST-07: история не вмещается в лимит — показано уведомление и догрузка."""
    path = make_repo("gui-old-history", commits=3)
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    controller = build_controller()
    window = MainWindow(controller, app_settings)

    try:
        # Небольшой лимит: первая страница — два коммита, третий догружается.
        controller.set_history_limit(2)
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.workspace_page.graph_view.commit_count == 2,
        )

        workspace = window.workspace_page
        data = workspace.workspace_data
        assert data is not None
        assert data.has_more_history is True
        assert not workspace.history_notice.isHidden()
        assert "Показаны последние 2 коммитов" in workspace.history_notice.label.text()

        newest = data.commits[0]
        workspace.graph_view.commit_selected.emit(newest.hash)

        workspace.history_notice.button.click()
        wait_until(qt_app, lambda: workspace.graph_view.commit_count == 3)
        qt_app.processEvents()

        data = workspace.workspace_data
        assert data is not None
        assert [commit.subject for commit in data.commits] == [
            "commit 2",
            "commit 1",
            "commit 0",
        ]
        assert data.has_more_history is False
        assert workspace.history_notice.isHidden()
        assert workspace.graph_view.edge_count == 2
        # Выбор коммита не сброшен догрузкой страницы.
        assert window.selected_commit_hash == newest.hash
    finally:
        window.close()


def test_gui_merge_commit_graph_details_and_diff(
    qt_app,
    make_repo,
    git,
    monkeypatch,
    app_settings,
) -> None:
    """Merge-коммит реальной истории: граф, метки веток, детали и diff (ТЗ 14.3)."""
    path = make_repo("gui-merge", commits=1)
    author = (
        "-c", "user.name=Test",
        "-c", "user.email=test@example.com",
        "-c", "commit.gpgsign=false",
    )
    git(path, "switch", "-q", "-c", "feature")
    (path / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(path, "add", "--", "feature.txt")
    git(path, *author, "commit", "-q", "-m", "feature work")
    git(path, "switch", "-q", "main")
    (path / "main.txt").write_text("main\n", encoding="utf-8")
    git(path, "add", "--", "main.txt")
    git(path, *author, "commit", "-q", "-m", "main work")
    git(path, *author, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")

    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    controller = build_controller()
    window = MainWindow(controller, app_settings)

    try:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None,
        )
        workspace = window.workspace_page
        data = workspace.workspace_data
        assert data is not None

        merge = data.commits[0]
        assert merge.is_merge
        assert merge.subject == "Merge feature"
        main_tip, feature_tip = merge.parent_hashes

        # Граф: у merge-узла две связи, линейные участки обеих веток сохранены.
        merge_edges = [
            edge for edge in data.layout.edges if edge.child_hash == merge.hash
        ]
        assert {edge.parent_hash for edge in merge_edges} == {main_tip, feature_tip}
        assert workspace.graph_view.commit_count == 4
        assert workspace.graph_view.edge_count == 4

        # Метки refs: текущая ветка на merge-коммите, feature — на своём коммите.
        merge_labels = {label.name: label for label in data.ref_labels[merge.hash]}
        assert merge_labels["main"].kind is RefKind.LOCAL_BRANCH
        assert merge_labels["main"].is_current
        feature_labels = {
            label.name: label for label in data.ref_labels[feature_tip]
        }
        assert not feature_labels["feature"].is_current

        # Выбор merge-коммита: детали, явная база сравнения, файлы и diff.
        workspace.graph_view.commit_selected.emit(merge.hash)
        details = workspace.details_panel
        wait_until(qt_app, lambda: len(details.files) == 1)

        metadata = details.metadata_view.toPlainText()
        assert merge.hash in metadata
        assert "Merge-коммит" in metadata
        assert "первого родителя" in metadata
        assert details.files[0].path == "feature.txt"

        details.files_tree.setCurrentItem(details.files_tree.topLevelItem(0))
        wait_until(
            qt_app,
            lambda: not window.diff_overlay.isHidden()
            and bool(window.diff_overlay.diff_view.text),
        )
        assert "Сравнение с первым родителем" in (
            window.diff_overlay.diff_view.base_label.text()
        )
        assert "+feature" in window.diff_overlay.diff_view.text
    finally:
        window.close()


def test_gui_detached_head_state(
    qt_app,
    make_repo,
    git,
    monkeypatch,
    app_settings,
) -> None:
    """Detached HEAD: подпись HEAD, метки, выключенные Pull/Push, выбор коммита."""
    path = make_repo("gui-detached", commits=2)
    # Remote нужен, чтобы отключение Pull/Push объяснялось только detached HEAD.
    remote = path.parent / "gui-detached-origin.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-q")
    git(path, "remote", "add", "origin", str(remote))
    head_hash = git(path, "rev-parse", "HEAD").strip()
    git(path, "checkout", "-q", head_hash)

    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    controller = build_controller()
    window = MainWindow(controller, app_settings)

    try:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None,
        )
        workspace = window.workspace_page
        data = workspace.workspace_data
        assert data is not None

        assert data.repository.is_detached
        assert data.repository.current_branch is None
        assert data.repository.head_commit == head_hash
        assert workspace.top_bar.branch_label.text() == (
            f"Ветка: detached HEAD ({head_hash[:7]})"
        )

        # Remote настроен, значит Fetch доступен, а Pull/Push без ветки — нет.
        assert workspace.top_bar.fetch_button.isEnabled()
        assert not workspace.top_bar.pull_button.isEnabled()
        assert not workspace.top_bar.push_button.isEnabled()

        # Ни одна ветка не текущая; detached HEAD подписан отдельной меткой.
        assert all(not branch.is_current for branch in data.branches)
        labels = {label.kind: label for label in data.ref_labels[head_hash]}
        assert labels[RefKind.DETACHED_HEAD].name == "HEAD"
        assert labels[RefKind.LOCAL_BRANCH].name == "main"
        assert not labels[RefKind.LOCAL_BRANCH].is_current

        local_group = workspace.branches_panel.tree.topLevelItem(0)
        assert local_group is not None
        assert local_group.text(0) == "Локальные ветки"
        assert local_group.child(0).text(0) == "main"

        # Выбор коммита и детали работают и в detached HEAD.
        workspace.graph_view.commit_selected.emit(head_hash)
        wait_until(qt_app, lambda: workspace.details_panel.current_commit is not None)
        assert head_hash in workspace.details_panel.metadata_view.toPlainText()
    finally:
        window.close()


def test_gui_conflicted_file_flow(
    qt_app,
    make_repo,
    git,
    monkeypatch,
    app_settings,
) -> None:
    """Конфликт (unmerged): отображение, безопасные массовые операции, отказ коммита."""
    path = make_repo("gui-conflict", commits=1)
    author = (
        "-c", "user.name=Test",
        "-c", "user.email=test@example.com",
        "-c", "commit.gpgsign=false",
    )
    conflict_file = path / "file0.txt"
    git(path, "switch", "-q", "-c", "feature")
    conflict_file.write_text("feature\n", encoding="utf-8")
    git(path, *author, "commit", "-q", "-am", "feature work")
    git(path, "switch", "-q", "main")
    conflict_file.write_text("main\n", encoding="utf-8")
    git(path, *author, "commit", "-q", "-am", "main work")
    with pytest.raises(GitCommandError):
        git(path, *author, "merge", "feature")
    # Staged-файл нужен, чтобы Commit добрался до Git и получил его отказ;
    # untracked-файл — чтобы Stage All было что делать кроме конфликта.
    (path / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(path, "add", "--", "staged.txt")
    (path / "notes.txt").write_text("notes\n", encoding="utf-8")
    # Git пишет маркеры конфликта в рабочий файл; массовая операция не должна их менять.
    conflicted_content = conflict_file.read_text(encoding="utf-8")
    assert conflicted_content.startswith("<<<<<<<")
    head_before = git(path, "rev-parse", "HEAD").strip()

    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    controller = build_controller()
    window = MainWindow(controller, app_settings)

    try:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None,
        )
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: len(panel.changes) == 3)

        assert {change.path for change in panel.unstaged_files} == {
            "file0.txt",
            "notes.txt",
        }
        assert [change.path for change in panel.staged_files] == ["staged.txt"]
        assert "Вне индекса: 2" in panel.status_label.text()
        assert "В индексе: 1" in panel.status_label.text()

        # Конфликт помечен в списке и не даёт Stage/Discard.
        conflict_row = panel.unstaged_tree.topLevelItem(0)
        assert conflict_row is not None
        assert conflict_row.text(0) == "file0.txt"
        assert conflict_row.text(1) == "конфликт"
        panel.unstaged_tree.setCurrentItem(conflict_row)
        assert not panel.stage_button.isEnabled()
        assert not panel.discard_button.isEnabled()

        # Stage All пропускает конфликт: добавляется только untracked-файл.
        confirmations: list[
            tuple[str, tuple[ChangedFile, ...], tuple[ChangedFile, ...]]
        ] = []

        def confirm(operation, files, conflicts):
            confirmations.append((operation, files, conflicts))
            return True

        monkeypatch.setattr(window, "_confirm_bulk_operation", confirm)
        panel.stage_all_button.click()
        wait_until(
            qt_app,
            lambda: {change.path for change in panel.staged_files}
            == {"notes.txt", "staged.txt"},
        )
        assert len(confirmations) == 1
        operation, files, conflicts = confirmations[0]
        assert operation == "stage"
        assert {change.path for change in files} == {"notes.txt"}
        assert {change.path for change in conflicts} == {"file0.txt"}
        # Маркеры конфликта в рабочем файле не тронуты: Stage All его пропустил.
        assert conflict_file.read_text(encoding="utf-8") == conflicted_content

        status = {
            line[3:]: line[:2]
            for line in git(path, "status", "--porcelain").splitlines()
        }
        assert status["file0.txt"] == "UU"
        assert status["notes.txt"] == "A "
        assert status["staged.txt"] == "A "

        # Коммит при неразрешённом конфликте: типизированная ошибка, окно открыто.
        shown: list[tuple[str, BaseException, dict[str, object]]] = []

        def show_error(title, error, **kwargs):
            shown.append((title, error, kwargs))

        monkeypatch.setattr(window, "_show_error", show_error)
        panel.commit_button.click()
        wait_until(qt_app, lambda: not window.commit_overlay.isHidden())
        window.commit_overlay.message_edit.setPlainText("Commit with conflict")
        window.commit_overlay.commit_button.click()
        wait_until(qt_app, lambda: bool(shown))

        title, error, kwargs = shown[0]
        assert title == "Не удалось создать коммит"
        assert isinstance(error, GitCommandError)
        assert error.kind is GitErrorKind.UNMERGED_FILES
        assert "неразрешённые конфликты" in str(kwargs.get("message"))
        # Окно осталось открытым с введённым сообщением, приложение работает.
        assert not window.commit_overlay.isHidden()
        assert window.commit_overlay.message_edit.toPlainText() == (
            "Commit with conflict"
        )
        assert controller.repository is not None
        # После отказа Git панель перечитывает статус: конфликт по-прежнему виден.
        wait_until(
            qt_app,
            lambda: any(change.is_conflicted for change in panel.changes),
        )
        assert git(path, "rev-parse", "HEAD").strip() == head_before
    finally:
        window.close()

