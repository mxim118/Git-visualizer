"""Левая панель: локальные и remote-tracking ветки, настроенные remote.

Действия с ветками появятся в следующих итерациях; сейчас панель только показывает
данные BranchService и RepositoryService.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtWidgets import (
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from git_visualizer.domain.models import Branch, Remote

_EMPTY_ITEM_TEXT = "(нет)"


class BranchesPanel(QWidget):
    """Список локальных веток, удалённых веток и remote."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("branches_panel")

        title = QLabel("Ветки и remote")
        title.setObjectName("branches_panel_title")
        title.setStyleSheet("font-weight: 600;")

        self.tree = QTreeWidget(self)
        self.tree.setObjectName("branches_tree")
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.setUniformRowHeights(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addWidget(self.tree, 1)

    def set_branches(
        self,
        branches: Iterable[Branch],
        remotes: Iterable[Remote] = (),
    ) -> None:
        """Заполняет панель; текущая ветка выделена, upstream — в подсказке."""
        self.tree.clear()
        branches = tuple(branches)
        remotes = tuple(remotes)

        self._add_group(
            "Локальные ветки",
            (
                self._branch_item(branch)
                for branch in branches
                if not branch.is_remote
            ),
        )
        self._add_group(
            "Удалённые ветки",
            (
                self._branch_item(branch)
                for branch in branches
                if branch.is_remote
            ),
        )
        self._add_group(
            "Remote",
            (self._remote_item(remote) for remote in remotes),
        )
        self.tree.expandAll()

    def clear(self) -> None:
        self.tree.clear()

    def _add_group(self, title: str, items: Iterable[QTreeWidgetItem]) -> None:
        group = QTreeWidgetItem([title])
        font = group.font(0)
        font.setBold(True)
        group.setFont(0, font)
        self.tree.addTopLevelItem(group)

        children = list(items)
        if children:
            group.addChildren(children)
        else:
            empty = QTreeWidgetItem([_EMPTY_ITEM_TEXT])
            empty.setForeground(0, self.palette().placeholderText())
            group.addChild(empty)
        group.setExpanded(True)

    def _branch_item(self, branch: Branch) -> QTreeWidgetItem:
        suffix = ""
        if branch.is_current:
            suffix = "  (текущая)"
        elif branch.commit_hash is None:
            suffix = "  (коммитов нет)"
        item = QTreeWidgetItem([branch.name + suffix])
        if branch.is_current:
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
        tooltip = branch.ref_name
        if branch.upstream:
            tooltip += f"\nОтслеживает: {branch.upstream}"
        item.setToolTip(0, tooltip)
        return item

    @staticmethod
    def _remote_item(remote: Remote) -> QTreeWidgetItem:
        item = QTreeWidgetItem([remote.name])
        lines = [remote.name]
        if remote.fetch_url:
            lines.append(f"Fetch: {remote.fetch_url}")
        if remote.push_url:
            lines.append(f"Push: {remote.push_url}")
        item.setToolTip(0, "\n".join(lines))
        return item
