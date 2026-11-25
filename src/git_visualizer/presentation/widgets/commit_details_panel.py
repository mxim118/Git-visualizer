"""Правая панель: детали коммита и список изменённых файлов.

Виджет не запускает Git: данные (``Commit``, ``FileChange``) передаёт
Presentation после фоновой загрузки. Diff здесь не показывается — выбранный файл
открывается в большой панели поверх окна (``DiffOverlay``), поэтому маленького
внутреннего diff и его splitter'а нет: панель целиком занята данными коммита и
списком файлов.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import Commit, FileChange

_FILE_CHANGE_LABELS: dict[FileChangeType, str] = {
    FileChangeType.ADDED: "добавлен",
    FileChangeType.MODIFIED: "изменён",
    FileChangeType.DELETED: "удалён",
    FileChangeType.RENAMED: "переименован",
    FileChangeType.COPIED: "скопирован",
    FileChangeType.TYPE_CHANGED: "тип изменён",
}

_PLACEHOLDER_TEXT = "Коммит не выбран"
_EMPTY_REPOSITORY_TEXT = "В репозитории пока нет коммитов."
_FILES_NOT_LOADED_TEXT = "Файлы не загружены"


class CommitDetailsPanel(QWidget):
    """Сведения о коммите и изменённые файлы; выбор файла сообщается сигналом."""

    file_selected = Signal(object)  # FileChange

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("commit_details_panel")

        self._current_commit: Commit | None = None
        self._files: tuple[FileChange, ...] = ()
        self._selected_file: FileChange | None = None
        self._placeholder_text = _PLACEHOLDER_TEXT

        title = QLabel("Детали коммита")
        title.setObjectName("commit_details_title")
        title.setStyleSheet("font-weight: 600;")

        self.metadata_view = QTextBrowser(self)
        self.metadata_view.setObjectName("commit_metadata")
        self.metadata_view.setOpenExternalLinks(False)
        self.metadata_view.setMinimumHeight(120)

        files_title = QLabel("Изменённые файлы")
        files_title.setObjectName("commit_files_title")
        files_title.setStyleSheet("font-weight: 600;")

        self.files_status_label = QLabel(_FILES_NOT_LOADED_TEXT)
        self.files_status_label.setObjectName("commit_files_status")
        self.files_status_label.setStyleSheet("color: #64748b;")
        self.files_status_label.setWordWrap(True)

        self.files_tree = QTreeWidget(self)
        self.files_tree.setObjectName("commit_files_tree")
        self.files_tree.setHeaderLabels(["Файл", "Статус"])
        self.files_tree.setRootIsDecorated(False)
        self.files_tree.setUniformRowHeights(True)
        self.files_tree.setAlternatingRowColors(True)
        self.files_tree.setMinimumHeight(80)
        header = self.files_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.files_tree.currentItemChanged.connect(self._on_current_item_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addWidget(self.metadata_view, 1)
        layout.addWidget(files_title)
        layout.addWidget(self.files_status_label)
        layout.addWidget(self.files_tree, 2)

        self.clear()

    # --- публичный интерфейс ---

    def show_commit(self, commit: Commit) -> None:
        """Показывает данные коммита и начинает состояние загрузки файлов."""
        self._current_commit = commit
        self._placeholder_text = ""
        self.metadata_view.setHtml(_metadata_html(commit))
        self.set_files_loading()

    def set_files_loading(self) -> None:
        self._files = ()
        self._selected_file = None
        self._replace_files_placeholder("Загрузка файлов…")
        self.files_status_label.setText("Загрузка файлов…")

    def set_files(self, files: Sequence[FileChange]) -> None:
        """Заполняет список файлов; выбор файла сообщается сигналом."""
        self._files = tuple(files)
        self._selected_file = None
        self._replace_files_placeholder(None)

        self.files_tree.blockSignals(True)
        for index, file_change in enumerate(self._files):
            item = QTreeWidgetItem(
                [_file_display_path(file_change), _change_label(file_change)]
            )
            item.setToolTip(0, _file_display_path(file_change))
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            self.files_tree.addTopLevelItem(item)
        self.files_tree.blockSignals(False)

        if self._files:
            self.files_status_label.setText(f"Файлов: {len(self._files)}")
        else:
            self.files_status_label.setText("В коммите нет изменённых файлов.")

    def set_files_error(self, message: str) -> None:
        self._files = ()
        self._selected_file = None
        self._replace_files_placeholder(message)
        self.files_status_label.setText(message)

    def clear_file_selection(self) -> None:
        """Снимает выбор файла, чтобы его можно было открыть снова."""
        self.files_tree.blockSignals(True)
        self.files_tree.clearSelection()
        self.files_tree.setCurrentItem(None)
        self.files_tree.blockSignals(False)
        self._selected_file = None

    def show_empty_repository(self) -> None:
        """Пустое состояние для репозитория без коммитов."""
        self._current_commit = None
        self._files = ()
        self._selected_file = None
        self._placeholder_text = _EMPTY_REPOSITORY_TEXT
        self.metadata_view.setHtml(
            f"<p style='color: #64748b;'>{html.escape(_EMPTY_REPOSITORY_TEXT)}</p>"
        )
        self._replace_files_placeholder("Нет коммитов")
        self.files_status_label.setText("Нет коммитов")

    def clear(self) -> None:
        """Сбрасывает панель: коммит не выбран."""
        self._current_commit = None
        self._files = ()
        self._selected_file = None
        self._placeholder_text = _PLACEHOLDER_TEXT
        self.metadata_view.setHtml(
            f"<p style='color: #64748b;'>{html.escape(_PLACEHOLDER_TEXT)}</p>"
        )
        self._replace_files_placeholder(_FILES_NOT_LOADED_TEXT)
        self.files_status_label.setText(_FILES_NOT_LOADED_TEXT)

    @property
    def current_commit(self) -> Commit | None:
        return self._current_commit

    @property
    def selected_file(self) -> FileChange | None:
        return self._selected_file

    @property
    def files(self) -> tuple[FileChange, ...]:
        return self._files

    @property
    def placeholder_text(self) -> str:
        return self._placeholder_text

    # --- внутренняя реализация ---

    def _replace_files_placeholder(self, text: str | None) -> None:
        self.files_tree.blockSignals(True)
        self.files_tree.clear()
        if text:
            item = QTreeWidgetItem([text, ""])
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.files_tree.addTopLevelItem(item)
        self.files_tree.blockSignals(False)

    def _on_current_item_changed(
        self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None
    ) -> None:
        if current is None:
            return
        index = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or not 0 <= index < len(self._files):
            return
        self._selected_file = self._files[index]
        self.file_selected.emit(self._selected_file)


def _metadata_html(commit: Commit) -> str:
    refs = _format_refs(commit.refs)
    message = html.escape(_full_message(commit)).replace("\n", "<br>")
    comparison = html.escape(_comparison_note(commit))
    return (
        f"<p><b>Сообщение</b></p><p>{message}</p>"
        f"<p style='color: #475569;'>{comparison}</p>"
        "<p>"
        f"<b>Короткий hash:</b> <code>{html.escape(commit.short_hash)}</code><br>"
        f"<b>Полный hash:</b> {html.escape(commit.hash)}</p>"
        "<p>"
        f"<b>Автор:</b> {html.escape(commit.author_name)} "
        f"&lt;{html.escape(commit.author_email)}&gt;<br>"
        f"<b>Дата автора:</b> {html.escape(_format_datetime(commit.authored_at))}<br>"
        f"<b>Committer:</b> {html.escape(commit.committer_name)} "
        f"&lt;{html.escape(commit.committer_email)}&gt;<br>"
        f"<b>Дата коммита:</b> {html.escape(_format_datetime(commit.committed_at))}<br>"
        f"<b>Refs:</b> {html.escape(refs)}</p>"
    )


def _comparison_note(commit: Commit) -> str:
    if commit.is_root:
        return "Корневой коммит — изменения относительно пустого дерева."
    parent = commit.parent_hashes[0][:7]
    if commit.is_merge:
        return f"Merge-коммит — изменения относительно первого родителя {parent}."
    return f"Изменения относительно родителя {parent}."


def _format_refs(refs: Sequence[str]) -> str:
    if not refs:
        return "—"
    result: list[str] = []
    for ref in refs:
        if ref == "HEAD":
            result.append("HEAD")
        elif ref.startswith("refs/heads/"):
            result.append(ref.removeprefix("refs/heads/"))
        elif ref.startswith("refs/remotes/"):
            result.append(ref.removeprefix("refs/remotes/"))
        else:
            result.append(ref)
    return ", ".join(result)


def _file_display_path(file_change: FileChange) -> str:
    if file_change.old_path:
        return f"{file_change.old_path} → {file_change.path}"
    return file_change.path


def _change_label(file_change: FileChange) -> str:
    return _FILE_CHANGE_LABELS.get(file_change.change_type, file_change.change_type.value)


def _full_message(commit: Commit) -> str:
    if commit.body:
        return f"{commit.subject}\n\n{commit.body}"
    return commit.subject


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        return value.strftime("%d.%m.%Y %H:%M:%S")
    return value.strftime("%d.%m.%Y %H:%M:%S %z")
