"""Рабочая область открытого репозитория: верхняя панель, сплиттеры и панели."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from git_visualizer.domain.models import Commit, FileChange, Repository
from git_visualizer.presentation.controllers.repository_controller import WorkspaceData
from git_visualizer.presentation.widgets.branches_panel import BranchesPanel
from git_visualizer.presentation.widgets.changes_panel import ChangesPanel
from git_visualizer.presentation.widgets.commit_details_panel import CommitDetailsPanel
from git_visualizer.presentation.widgets.graph_view import GraphView
from git_visualizer.presentation.widgets.top_bar import TopBar


class WorkspacePage(QWidget):
    """Основное окно репозитория с изменяемыми по размеру панелями.

    Центральная область — ``GraphView``; слева ветки и remote, справа детали
    коммита со списком файлов, снизу панель Changes. Diff выбранного файла коммита
    открывается большой панелью поверх окна (``DiffOverlay``). Выбор узла сохраняется
    в ``selected_commit_hash``, обновляет панель деталей и сообщается сигналом
    ``commit_selected``; выбор файла коммита сохраняется в ``selected_file`` и
    сообщается сигналом ``file_selected``. Выбор файла в Changes сообщается
    сигналом ``worktree_file_selected`` вместе с секцией (staged/unstaged).
    """

    refresh_requested = Signal()
    close_repository_requested = Signal()
    commit_selected = Signal(str)
    file_selected = Signal(str, object)  # commit hash, FileChange
    worktree_file_selected = Signal(object, bool)  # ChangedFile, staged
    changes_refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("workspace_page")

        self._data: WorkspaceData | None = None
        self._commits_by_hash: dict[str, Commit] = {}
        self._selected_commit_hash: str | None = None
        self._selected_file: FileChange | None = None

        self.top_bar = TopBar(self)
        self.graph_view = GraphView(self)
        self.branches_panel = BranchesPanel(self)
        self.details_panel = CommitDetailsPanel(self)
        self.changes_panel = ChangesPanel(self)

        self.body_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.body_splitter.setObjectName("body_splitter")
        self.body_splitter.setChildrenCollapsible(True)
        self.body_splitter.addWidget(self.branches_panel)
        self.body_splitter.addWidget(self.graph_view)
        self.body_splitter.addWidget(self.details_panel)
        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 1)
        self.body_splitter.setStretchFactor(2, 0)
        self.body_splitter.setSizes([240, 680, 420])

        self.main_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.main_splitter.setObjectName("main_splitter")
        self.main_splitter.setChildrenCollapsible(True)
        self.main_splitter.addWidget(self.body_splitter)
        self.main_splitter.addWidget(self.changes_panel)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)
        self.main_splitter.setSizes([620, 170])

        self.branches_panel.setMinimumWidth(160)
        self.graph_view.setMinimumWidth(280)
        self.details_panel.setMinimumWidth(280)
        self.changes_panel.setMinimumHeight(90)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.top_bar)
        layout.addWidget(self.main_splitter, 1)

        self.top_bar.refresh_requested.connect(self.refresh_requested)
        self.top_bar.close_repository_requested.connect(
            self.close_repository_requested
        )
        self.graph_view.commit_selected.connect(self._on_commit_selected)
        self.details_panel.file_selected.connect(self._on_file_selected)
        self.changes_panel.file_selected.connect(self.worktree_file_selected)
        self.changes_panel.refresh_requested.connect(self.changes_refresh_requested)

        self.clear()

    def set_repository(self, repository: Repository) -> None:
        """Показывает репозиторий в верхней панели до загрузки истории."""
        self.top_bar.set_repository(repository)

    def set_workspace_data(self, data: WorkspaceData) -> None:
        """Отображает загруженные историю, граф, ветки и remote."""
        self._data = data
        self._commits_by_hash = {commit.hash: commit for commit in data.commits}
        self._selected_commit_hash = None
        self._selected_file = None

        self.top_bar.set_repository(data.repository)
        self.graph_view.set_graph(
            data.layout,
            commits=data.commits,
            ref_labels=data.ref_labels,
        )
        self.branches_panel.set_branches(data.branches, data.remotes)

        if data.commits:
            self.details_panel.clear()
        else:
            self.details_panel.show_empty_repository()

    def set_busy(self, busy: bool) -> None:
        self.top_bar.set_busy(busy)

    def clear(self) -> None:
        """Сбрасывает рабочую область (закрытие репозитория или ошибка открытия)."""
        self._data = None
        self._commits_by_hash = {}
        self._selected_commit_hash = None
        self._selected_file = None
        self.top_bar.clear()
        self.graph_view.clear()
        self.branches_panel.clear()
        self.details_panel.clear()
        self.changes_panel.clear()

    @property
    def selected_commit_hash(self) -> str | None:
        """Hash выбранного в графе коммита или ``None``."""
        return self._selected_commit_hash

    @property
    def selected_file(self) -> FileChange | None:
        """Файл, выбранный в списке изменённых файлов."""
        return self._selected_file

    @property
    def workspace_data(self) -> WorkspaceData | None:
        return self._data

    def clear_file_selection(self) -> None:
        """Снимает выбор файла коммита (закрытие панели diff или смена коммита)."""
        self._selected_file = None
        self.details_panel.clear_file_selection()

    def _on_commit_selected(self, commit_hash: str) -> None:
        self._selected_commit_hash = commit_hash
        self._selected_file = None
        commit = self._commits_by_hash.get(commit_hash)
        if commit is None:
            self.details_panel.clear()
        else:
            self.details_panel.show_commit(commit)
        self.commit_selected.emit(commit_hash)

    def _on_file_selected(self, file_change: FileChange) -> None:
        commit_hash = self._selected_commit_hash
        if commit_hash is None:
            return
        self._selected_file = file_change
        self.file_selected.emit(commit_hash, file_change)
