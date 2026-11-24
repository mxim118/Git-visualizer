"""Нижняя панель Changes: раздельные списки Staged/Unstaged и diff рабочей директории.

Виджет не запускает Git: ``ChangedFile`` и ``Diff`` передаёт Presentation после
фоновой загрузки через Application Services. Один файл может быть в обоих списках
(``MM``/``AM``): выбор строки сообщает не только файл, но и секцию — от неё зависит,
какой diff запрашивать (``git diff --cached`` или ``git diff``).

Stage/Unstage и создание коммита появятся в следующей задаче; сейчас панель только
показывает состояние.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import ChangedFile, Diff
from git_visualizer.presentation.widgets.diff_view import DiffView

_CHANGE_LABELS: dict[FileChangeType, str] = {
    FileChangeType.ADDED: "добавлен",
    FileChangeType.MODIFIED: "изменён",
    FileChangeType.DELETED: "удалён",
    FileChangeType.RENAMED: "переименован",
    FileChangeType.COPIED: "скопирован",
    FileChangeType.TYPE_CHANGED: "тип изменён",
}

# Цвет статуса — вспомогательный признак: секции уже разделены заголовками и списком.
_STAGED_COLOR = "#15803d"
_UNSTAGED_COLOR = "#b45309"
_CONFLICT_COLOR = "#b91c1c"
_UNTRACKED_COLOR = "#64748b"

_NOT_LOADED_TEXT = "Изменения не загружены"
_LOADING_TEXT = "Загрузка изменений…"
_CLEAN_TEXT = "Изменений нет — рабочая директория чиста."
_CHOOSE_FILE_TEXT = "Выберите файл, чтобы увидеть diff."
_STAGED_HINT = "В индексе (staged)"
_UNSTAGED_HINT = "Ещё не в индексе (unstaged)"
_UNTRACKED_NOTICE = (
    "Файл не отслеживается Git: текстовый diff недоступен, пока файл не добавлен "
    "в индекс."
)


class ChangesPanel(QWidget):
    """Изменения рабочей директории: списки Unstaged/Staged, diff и обновление.

    Сигнал ``file_selected(ChangedFile, staged)`` сообщает выбор файла вместе с
    секцией; ``refresh_requested()`` — просьбу обновить состояние.
    """

    file_selected = Signal(object, bool)  # ChangedFile, staged
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("changes_panel")

        self._changes: tuple[ChangedFile, ...] = ()
        self._unstaged_files: tuple[ChangedFile, ...] = ()
        self._staged_files: tuple[ChangedFile, ...] = ()
        self._selected_file: ChangedFile | None = None
        self._selected_is_staged = False

        title = QLabel("Изменения")
        title.setObjectName("changes_panel_title")
        title.setStyleSheet("font-weight: 600;")

        self.status_label = QLabel(_NOT_LOADED_TEXT)
        self.status_label.setObjectName("changes_status")
        self.status_label.setStyleSheet("color: #64748b;")
        self.status_label.setWordWrap(True)

        self.refresh_button = QPushButton("Обновить", self)
        self.refresh_button.setObjectName("changes_refresh_button")
        self.refresh_button.setToolTip("Запросить состояние рабочей директории заново")
        self.refresh_button.clicked.connect(self.refresh_requested)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(title)
        header.addWidget(self.status_label, 1)
        header.addWidget(self.refresh_button)

        self.unstaged_group, self.unstaged_tree = self._make_section(
            "Unstaged", "unstaged_changes_group", _UNSTAGED_HINT
        )
        self.staged_group, self.staged_tree = self._make_section(
            "Staged", "staged_changes_group", _STAGED_HINT
        )

        self.unstaged_tree.currentItemChanged.connect(
            self._on_unstaged_item_changed
        )
        self.staged_tree.currentItemChanged.connect(self._on_staged_item_changed)

        self.diff_view = DiffView(self)
        self.diff_view.setMinimumWidth(240)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setObjectName("changes_splitter")
        self.splitter.setChildrenCollapsible(True)
        self.splitter.addWidget(self.unstaged_group)
        self.splitter.addWidget(self.staged_group)
        self.splitter.addWidget(self.diff_view)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setStretchFactor(2, 1)
        self.splitter.setSizes([260, 260, 640])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self.splitter, 1)

        self.clear()

    # --- публичный интерфейс ---

    def set_loading(self) -> None:
        """Состояние загрузки: списки и diff заменяются сообщением."""
        self._reset_files()
        self._replace_tree(self.unstaged_tree, (), staged=False, text=_LOADING_TEXT)
        self._replace_tree(self.staged_tree, (), staged=True, text=_LOADING_TEXT)
        self._update_group_titles(loaded=False)
        self.status_label.setText(_LOADING_TEXT)
        self.refresh_button.setEnabled(False)
        self.diff_view.clear(_LOADING_TEXT)

    def set_changes(self, changes: Sequence[ChangedFile]) -> None:
        """Заполняет списки; при отсутствии изменений показывает пустое состояние."""
        self._changes = tuple(changes)
        self._unstaged_files = tuple(
            change for change in self._changes if _belongs_to_unstaged(change)
        )
        self._staged_files = tuple(
            change for change in self._changes if change.is_staged
        )
        self._selected_file = None
        self._selected_is_staged = False

        self._replace_tree(
            self.unstaged_tree,
            self._unstaged_files,
            staged=False,
            text="Нет незакоммиченных изменений",
        )
        self._replace_tree(
            self.staged_tree,
            self._staged_files,
            staged=True,
            text="Нет файлов в индексе",
        )
        self._update_group_titles(loaded=True)
        self.status_label.setText(_summary_text(self._unstaged_files, self._staged_files))
        self.refresh_button.setEnabled(True)
        self.diff_view.clear(_CHOOSE_FILE_TEXT)

    def set_error(self, message: str) -> None:
        """Ошибка загрузки статуса: списки очищаются, причина видна в панели."""
        self._reset_files()
        self._replace_tree(
            self.unstaged_tree, (), staged=False, text="Не удалось загрузить"
        )
        self._replace_tree(
            self.staged_tree, (), staged=True, text="Не удалось загрузить"
        )
        self._update_group_titles(loaded=False)
        self.status_label.setText(message)
        self.refresh_button.setEnabled(True)
        self.diff_view.clear(message)

    def set_diff_loading(self) -> None:
        """Загрузка diff выбранного файла (секция берётся из выбора)."""
        file = self._selected_file
        if file is None:
            return
        self.diff_view.show_message(
            "Загрузка diff…",
            title=_diff_title(file, self._selected_is_staged),
            base_text=_diff_base_text(file, self._selected_is_staged),
        )

    def show_diff(self, diff: Diff) -> None:
        """Показывает готовый diff выбранного файла с явной базой сравнения."""
        file = self._selected_file
        if file is None:
            self.diff_view.show_message(_CHOOSE_FILE_TEXT)
            return
        self.diff_view.show_diff(
            diff,
            title=_diff_title(file, self._selected_is_staged),
            base_text=_diff_base_text(file, self._selected_is_staged),
        )

    def set_diff_error(self, message: str) -> None:
        file = self._selected_file
        if file is None:
            return
        self.diff_view.show_message(
            message,
            title=_diff_title(file, self._selected_is_staged),
            base_text="",
        )

    def show_untracked_notice(self) -> None:
        """Пояснение для неотслеживаемого файла: Git diff по нему не строит."""
        file = self._selected_file
        if file is None:
            return
        self.diff_view.show_message(
            _UNTRACKED_NOTICE,
            title=_diff_title(file, self._selected_is_staged),
            base_text=_diff_base_text(file, self._selected_is_staged),
        )

    def clear(self) -> None:
        """Сбрасывает панель (репозиторий закрыт или ещё не загружен)."""
        self._reset_files()
        self._replace_tree(
            self.unstaged_tree, (), staged=False, text=_NOT_LOADED_TEXT
        )
        self._replace_tree(self.staged_tree, (), staged=True, text=_NOT_LOADED_TEXT)
        self._update_group_titles(loaded=False)
        self.status_label.setText(_NOT_LOADED_TEXT)
        self.refresh_button.setEnabled(True)
        self.diff_view.clear()

    @property
    def changes(self) -> tuple[ChangedFile, ...]:
        return self._changes

    @property
    def unstaged_files(self) -> tuple[ChangedFile, ...]:
        return self._unstaged_files

    @property
    def staged_files(self) -> tuple[ChangedFile, ...]:
        return self._staged_files

    @property
    def selected_file(self) -> ChangedFile | None:
        return self._selected_file

    @property
    def selected_is_staged(self) -> bool:
        return self._selected_is_staged

    @property
    def is_clean(self) -> bool:
        """Изменения загружены, и их нет."""
        return (
            not self._changes
            and self.status_label.text() == _CLEAN_TEXT
        )

    # --- внутренняя реализация ---

    def _reset_files(self) -> None:
        self._changes = ()
        self._unstaged_files = ()
        self._staged_files = ()
        self._selected_file = None
        self._selected_is_staged = False

    def _make_section(
        self, title: str, object_name: str, hint_text: str
    ) -> tuple[QGroupBox, QTreeWidget]:
        group = QGroupBox(title)
        group.setObjectName(object_name)

        hint = QLabel(hint_text)
        hint.setObjectName(f"{object_name}_hint")
        hint.setStyleSheet("color: #94a3b8;")

        tree = QTreeWidget()
        tree.setObjectName(f"{object_name}_tree")
        tree.setHeaderLabels(["Файл", "Статус"])
        tree.setRootIsDecorated(False)
        tree.setUniformRowHeights(True)
        tree.setAlternatingRowColors(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(hint)
        layout.addWidget(tree, 1)
        return group, tree

    def _replace_tree(
        self,
        tree: QTreeWidget,
        files: Sequence[ChangedFile],
        *,
        staged: bool,
        text: str,
    ) -> None:
        tree.blockSignals(True)
        tree.clear()
        if not files:
            item = QTreeWidgetItem([text, ""])
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            tree.addTopLevelItem(item)
        else:
            for index, change in enumerate(files):
                item = QTreeWidgetItem(
                    [_display_path(change), _change_label(change, staged=staged)]
                )
                item.setToolTip(0, _display_path(change))
                item.setToolTip(1, _status_hint(change, staged=staged))
                item.setForeground(
                    1, QBrush(QColor(_status_color(change, staged=staged)))
                )
                item.setData(0, Qt.ItemDataRole.UserRole, index)
                tree.addTopLevelItem(item)
        tree.blockSignals(False)

    def _update_group_titles(self, *, loaded: bool) -> None:
        if loaded:
            self.unstaged_group.setTitle(f"Unstaged ({len(self._unstaged_files)})")
            self.staged_group.setTitle(f"Staged ({len(self._staged_files)})")
        else:
            self.unstaged_group.setTitle("Unstaged")
            self.staged_group.setTitle("Staged")

    def _on_unstaged_item_changed(
        self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None
    ) -> None:
        self._select(current, files=self._unstaged_files, staged=False)

    def _on_staged_item_changed(
        self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None
    ) -> None:
        self._select(current, files=self._staged_files, staged=True)

    def _select(
        self,
        current: QTreeWidgetItem | None,
        *,
        files: Sequence[ChangedFile],
        staged: bool,
    ) -> None:
        if current is None:
            return
        index = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or not 0 <= index < len(files):
            return

        self._selected_file = files[index]
        self._selected_is_staged = staged
        self._clear_selection(self.staged_tree if not staged else self.unstaged_tree)
        self.set_diff_loading()
        self.file_selected.emit(self._selected_file, staged)

    @staticmethod
    def _clear_selection(tree: QTreeWidget) -> None:
        tree.blockSignals(True)
        tree.clearSelection()
        tree.setCurrentItem(None)
        tree.blockSignals(False)


def _belongs_to_unstaged(change: ChangedFile) -> bool:
    """Секция Unstaged: рабочее изменение, неотслеживаемый файл или конфликт."""
    return change.is_unstaged or change.is_untracked or change.is_conflicted


def _summary_text(
    unstaged: Sequence[ChangedFile], staged: Sequence[ChangedFile]
) -> str:
    if not unstaged and not staged:
        return _CLEAN_TEXT
    return f"Вне индекса: {len(unstaged)} · В индексе: {len(staged)}"


def _display_path(change: ChangedFile) -> str:
    if change.old_path:
        return f"{change.old_path} → {change.path}"
    return change.path


def _change_label(change: ChangedFile, *, staged: bool) -> str:
    if change.is_conflicted:
        return "конфликт"
    if change.is_untracked:
        return "не отслеживается (папка)" if change.path.endswith("/") else "не отслеживается"
    change_type = change.staged_change if staged else change.unstaged_change
    if change_type is None:
        return ""
    return _CHANGE_LABELS.get(change_type, change_type.value)


def _status_hint(change: ChangedFile, *, staged: bool) -> str:
    if change.is_conflicted:
        return "Файл не согласован (unmerged): разрешите конфликт в Git"
    if change.is_untracked:
        return "Git не отслеживает этот файл"
    if staged:
        return "Изменение добавлено в индекс"
    return "Изменение ещё не добавлено в индекс"


def _status_color(change: ChangedFile, *, staged: bool) -> str:
    if change.is_conflicted:
        return _CONFLICT_COLOR
    if change.is_untracked:
        return _UNTRACKED_COLOR
    return _STAGED_COLOR if staged else _UNSTAGED_COLOR


def _diff_title(change: ChangedFile, staged: bool) -> str:
    section = "в индексе" if staged else "вне индекса"
    return f"{_display_path(change)} — {section}"


def _diff_base_text(change: ChangedFile, staged: bool) -> str:
    if change.is_conflicted:
        return "Конфликт (unmerged): показан объединённый diff Git."
    if staged:
        return "Staged diff: индекс относительно HEAD."
    return "Unstaged diff: рабочая директория относительно индекса."
