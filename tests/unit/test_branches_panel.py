"""Тесты левой панели веток: отображение, выбор и запросы действий."""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt

from git_visualizer.domain.models import Branch, Commit, Remote
from git_visualizer.presentation.widgets.branches_panel import BranchesPanel

NOW = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
HASH = "a" * 40


def make_commit(*, label: str = "a") -> Commit:
    commit_hash = label.ljust(40, "a")
    return Commit(
        hash=commit_hash,
        short_hash=commit_hash[:7],
        parent_hashes=(),
        author_name="Test",
        author_email="test@example.com",
        authored_at=NOW,
        committer_name="Test",
        committer_email="test@example.com",
        committed_at=NOW,
        subject="Selected commit",
        body="",
    )


def make_branches() -> tuple[Branch, ...]:
    return (
        Branch(name="main", is_remote=False, is_current=True, commit_hash=HASH),
        Branch(name="dev", is_remote=False, is_current=False, commit_hash=HASH),
        Branch(name="origin/main", is_remote=True, is_current=False, commit_hash=HASH),
    )


def make_panel() -> BranchesPanel:
    panel = BranchesPanel()
    panel.set_branches(
        make_branches(),
        (Remote(name="origin", fetch_url="https://example.com/repo.git"),),
    )
    return panel


def find_branch_item(panel: BranchesPanel, name: str):
    for index in range(panel.tree.topLevelItemCount()):
        group = panel.tree.topLevelItem(index)
        for child_index in range(group.childCount()):
            child = group.child(child_index)
            branch = child.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(branch, Branch) and branch.name == name:
                return child
    return None


def test_panel_shows_local_and_remote_branches_and_highlights_current(qt_app) -> None:
    panel = make_panel()
    try:
        local_group = panel.tree.topLevelItem(0)
        remote_group = panel.tree.topLevelItem(1)
        assert local_group.text(0) == "Локальные ветки"
        assert [local_group.child(i).text(0) for i in range(local_group.childCount())] == [
            "main  (текущая)",
            "dev",
        ]
        assert [remote_group.child(i).text(0) for i in range(remote_group.childCount())] == [
            "origin/main"
        ]
        current = local_group.child(0)
        assert current.font(0).bold()
        assert "HEAD" not in current.text(0)  # текущая выделена суффиксом и жирным
        assert panel.tree.topLevelItem(2).text(0) == "Remote"
        assert panel.switch_button.isEnabled() is False
    finally:
        panel.close()


def test_selecting_local_branch_enables_switch_and_emits_request(qt_app) -> None:
    panel = make_panel()
    requested: list[str] = []
    panel.switch_branch_requested.connect(requested.append)
    try:
        assert panel.select_branch("dev")
        assert panel.switch_button.isEnabled()
        assert "main" in panel.switch_button.toolTip() or "dev" in panel.switch_button.toolTip()

        panel.switch_button.click()

        assert requested == ["dev"]
    finally:
        panel.close()


def test_remote_and_current_branches_cannot_be_switched(qt_app) -> None:
    panel = make_panel()
    requested: list[str] = []
    panel.switch_branch_requested.connect(requested.append)
    try:
        assert panel.select_branch("origin/main", remote=True)
        assert not panel.switch_button.isEnabled()
        assert "только для просмотра" in panel.switch_button.toolTip()
        panel.switch_button.click()
        assert requested == []

        assert panel.select_branch("main")
        assert not panel.switch_button.isEnabled()
        assert panel.switch_button.toolTip() == "Это текущая ветка."
        panel.switch_button.click()
        assert requested == []
    finally:
        panel.close()


def test_double_click_local_branch_emits_switch(qt_app) -> None:
    panel = make_panel()
    requested: list[str] = []
    panel.switch_branch_requested.connect(requested.append)
    try:
        item = find_branch_item(panel, "dev")
        assert item is not None
        panel.tree.itemDoubleClicked.emit(item, 0)

        assert requested == ["dev"]
    finally:
        panel.close()


def test_create_branch_from_head_emits_name_without_start_point(qt_app) -> None:
    panel = make_panel()
    requested: list[tuple[str, object]] = []
    panel.create_branch_requested.connect(
        lambda name, start_point: requested.append((name, start_point))
    )
    try:
        panel.branch_name_edit.setText("feature/new")
        assert panel.create_button.isEnabled()

        panel.create_button.click()

        assert requested == [("feature/new", None)]
        # Поле не очищается виджетом: это делает MainWindow только после успеха.
        assert panel.branch_name == "feature/new"
    finally:
        panel.close()


def test_create_branch_from_selected_commit_emits_its_hash(qt_app) -> None:
    panel = make_panel()
    commit = make_commit()
    requested: list[tuple[str, object]] = []
    panel.create_branch_requested.connect(
        lambda name, start_point: requested.append((name, start_point))
    )
    try:
        panel.set_selected_commit(commit)
        assert panel.from_commit_checkbox.isEnabled()
        assert commit.short_hash in panel.from_commit_checkbox.text()

        panel.branch_name_edit.setText("from-selected")
        panel.from_commit_checkbox.setChecked(True)
        panel.create_button.click()

        assert requested == [("from-selected", commit.hash)]
    finally:
        panel.close()


def test_create_requires_a_name_and_clears_after_success(qt_app) -> None:
    panel = make_panel()
    try:
        assert not panel.create_button.isEnabled()
        panel.branch_name_edit.setText("   ")
        assert not panel.create_button.isEnabled()

        panel.branch_name_edit.setText("feature")
        assert panel.create_button.isEnabled()
        panel.clear_branch_name()
        assert panel.branch_name == ""
        assert not panel.create_button.isEnabled()
    finally:
        panel.close()


def test_busy_panel_blocks_actions_but_keeps_the_list(qt_app) -> None:
    panel = make_panel()
    try:
        assert panel.select_branch("dev")
        panel.set_busy(True)

        assert not panel.create_button.isEnabled()
        assert not panel.switch_button.isEnabled()
        assert not panel.branch_name_edit.isEnabled()
        assert panel.tree.topLevelItem(0).childCount() == 2

        panel.set_busy(False)

        assert panel.switch_button.isEnabled()
        assert panel.branch_name_edit.isEnabled()
    finally:
        panel.close()


def test_clear_resets_list_and_actions(qt_app) -> None:
    panel = make_panel()
    try:
        panel.set_selected_commit(make_commit())
        panel.branch_name_edit.setText("feature")
        panel.clear()

        assert panel.tree.topLevelItemCount() == 0
        assert panel.branch_name == ""
        assert panel.selected_branch is None
        assert not panel.create_button.isEnabled()
        assert not panel.switch_button.isEnabled()
        assert not panel.from_commit_checkbox.isEnabled()
    finally:
        panel.close()
