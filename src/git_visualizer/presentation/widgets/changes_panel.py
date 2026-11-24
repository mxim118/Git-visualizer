"""Нижняя панель Changes: раздельные списки Staged/Unstaged и действия с индексом.

Виджет не запускает Git: ``ChangedFile`` передаёт Presentation после фоновой
загрузки через Application Services, а нажатия кнопок только испускают сигналы —
решение и сам Git-вызов остаются в Application Layer. Один файл может быть в обоих
списках (``MM``/``AM``): выбор строки сообщает не только файл, но и секцию — от неё
зависит, какой diff запрашивать и какое действие доступно. Клик по файлу только
выбирает его; diff открывается отдельной кнопкой ``Diff``, поэтому выбранный файл
можно сначала добавить в индекс или убрать из него. Сам diff показывается большой
панелью поверх окна (``DiffOverlay``), встроенного diff здесь нет.

Кнопки ``Stage``/``Unstage`` действуют на выбранный файл, ``Stage All``/
``Unstage All`` — на всю секцию. Presentation перед массовой операцией показывает
её область действия (``scope_summary``/``scope_details``).
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
from git_visualizer.domain.models import ChangedFile

_CHANGE_LABELS: dict[FileChangeType, str] = {
    FileChangeType.ADDED: "добавлен",
    FileChangeType.MODIFIED: "изменён",
    FileChangeType.DELETED: "удалён",
    FileChangeType.RENAMED: "переименован",
    FileChangeType.COPIED: "скопирован",
    FileChangeType.TYPE_CHANGED: "тип изменён",
}

# Порядок категорий в описании массовой операции фиксирован: так текст не зависит
# от порядка файлов в выводе Git.
_SCOPE_CATEGORY_ORDER: tuple[tuple[FileChangeType, str], ...] = (
    (FileChangeType.ADDED, "добавленные"),
    (FileChangeType.MODIFIED, "изменённые"),
    (FileChangeType.DELETED, "удалённые"),
    (FileChangeType.RENAMED, "переименованные"),
    (FileChangeType.COPIED, "скопированные"),
    (FileChangeType.TYPE_CHANGED, "со сменой типа"),
)
_UNTRACKED_CATEGORY = "не отслеживаются"

# Цвет статуса — вспомогательный признак: секции уже разделены заголовками и списком.
_STAGED_COLOR = "#15803d"
_UNSTAGED_COLOR = "#b45309"
_CONFLICT_COLOR = "#b91c1c"
_UNTRACKED_COLOR = "#64748b"

_NOT_LOADED_TEXT = "Изменения не загружены"
_LOADING_TEXT = "Загрузка изменений…"
_CLEAN_TEXT = "Изменений нет — рабочая директория чиста."
_STAGED_HINT = "В индексе (staged)"
_UNSTAGED_HINT = "Ещё не в индексе (unstaged)"


class ChangesPanel(QWidget):
    """Изменения рабочей директории: списки Unstaged/Staged, обновление и действия.

    Сигнал ``file_selected(ChangedFile, staged)`` сообщает выбор файла вместе с
    секцией; ``refresh_requested()`` — просьбу обновить состояние;
    ``diff_requested(ChangedFile, staged)`` — открыть diff выбранного файла;
    ``stage_requested(ChangedFile)``/``unstage_requested(ChangedFile)`` — действия с
    выбранным файлом; ``stage_all_requested()``/``unstage_all_requested()`` —
    массовые действия. Git отсюда не запускается.
    """

    file_selected = Signal(object, bool)  # ChangedFile, staged
    refresh_requested = Signal()
    diff_requested = Signal(object, bool)  # ChangedFile, staged
    stage_requested = Signal(object)  # ChangedFile
    unstage_requested = Signal(object)  # ChangedFile
    stage_all_requested = Signal()
    unstage_all_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("changes_panel")

        self._changes: tuple[ChangedFile, ...] = ()
        self._unstaged_files: tuple[ChangedFile, ...] = ()
        self._staged_files: tuple[ChangedFile, ...] = ()
        self._selected_file: ChangedFile | None = None
        self._selected_is_staged = False
        self._loaded = False
        self._busy = False

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

        self.diff_button = QPushButton("Diff", self)
        self.diff_button.setObjectName("changes_diff_button")
        self.diff_button.setToolTip(
            "Открыть diff выбранного файла (клик по файлу только выбирает его)"
        )
        self.diff_button.clicked.connect(self._request_diff_selected)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(title)
        header.addWidget(self.status_label, 1)
        header.addWidget(self.diff_button)
        header.addWidget(self.refresh_button)

        (
            self.unstaged_group,
            self.unstaged_tree,
            self.stage_button,
            self.stage_all_button,
        ) = self._make_section(
            "Unstaged",
            "unstaged_changes_group",
            _UNSTAGED_HINT,
            primary_text="Stage",
            primary_object_name="changes_stage_button",
            primary_tooltip="Добавить выбранный файл в индекс",
            primary_slot=self._request_stage_selected,
            bulk_text="Stage All",
            bulk_object_name="changes_stage_all_button",
            bulk_tooltip="Добавить в индекс все изменения (конфликты пропускаются)",
            bulk_slot=self._request_stage_all,
        )
        (
            self.staged_group,
            self.staged_tree,
            self.unstage_button,
            self.unstage_all_button,
        ) = self._make_section(
            "Staged",
            "staged_changes_group",
            _STAGED_HINT,
            primary_text="Unstage",
            primary_object_name="changes_unstage_button",
            primary_tooltip=(
                "Убрать выбранный файл из индекса, сохранив изменения "
                "рабочей директории"
            ),
            primary_slot=self._request_unstage_selected,
            bulk_text="Unstage All",
            bulk_object_name="changes_unstage_all_button",
            bulk_tooltip=(
                "Убрать из индекса все файлы, сохранив изменения "
                "рабочей директории"
            ),
            bulk_slot=self._request_unstage_all,
        )

        self.unstaged_tree.currentItemChanged.connect(
            self._on_unstaged_item_changed
        )
        self.staged_tree.currentItemChanged.connect(self._on_staged_item_changed)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setObjectName("changes_splitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.unstaged_group)
        self.splitter.addWidget(self.staged_group)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([1, 1])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self.splitter, 1)

        self.clear()

    # --- публичный интерфейс ---

    def set_loading(self) -> None:
        """Состояние загрузки: списки заменяются сообщением, действия недоступны."""
        self._reset_files()
        self._loaded = False
        self._busy = False
        self._replace_tree(self.unstaged_tree, (), staged=False, text=_LOADING_TEXT)
        self._replace_tree(self.staged_tree, (), staged=True, text=_LOADING_TEXT)
        self._update_group_titles(loaded=False)
        self._update_action_buttons()
        self.status_label.setText(_LOADING_TEXT)
        self.refresh_button.setEnabled(False)

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
        self._loaded = True
        self._busy = False

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
        self._update_action_buttons()
        self.status_label.setText(_summary_text(self._unstaged_files, self._staged_files))
        self.refresh_button.setEnabled(True)

    def set_error(self, message: str) -> None:
        """Ошибка загрузки статуса: списки очищаются, причина видна в панели."""
        self._reset_files()
        self._loaded = False
        self._busy = False
        self._replace_tree(
            self.unstaged_tree, (), staged=False, text="Не удалось загрузить"
        )
        self._replace_tree(
            self.staged_tree, (), staged=True, text="Не удалось загрузить"
        )
        self._update_group_titles(loaded=False)
        self._update_action_buttons()
        self.status_label.setText(message)
        self.refresh_button.setEnabled(True)

    def set_busy(self, busy: bool) -> None:
        """Блокирует действия, пока Application выполняет операцию с индексом."""
        self._busy = busy
        self._update_action_buttons()

    def select_file(self, path: str, *, staged: bool) -> bool:
        """Выбирает файл в указанной секции (обновление diff после операции).

        :return: ``True``, если файл найден в секции и выбран; ``False`` — файла
            там уже нет (например, после unstage изменений не осталось).
        """
        files = self._staged_files if staged else self._unstaged_files
        tree = self.staged_tree if staged else self.unstaged_tree
        for index, change in enumerate(files):
            if change.path == path:
                tree.setCurrentItem(tree.topLevelItem(index))
                return True
        return False

    def clear_selection(self) -> None:
        """Снимает выбор в обоих списках (закрытие панели diff)."""
        self._selected_file = None
        self._selected_is_staged = False
        for tree in (self.unstaged_tree, self.staged_tree):
            tree.blockSignals(True)
            tree.clearSelection()
            tree.setCurrentItem(None)
            tree.blockSignals(False)
        self._update_action_buttons()

    def clear(self) -> None:
        """Сбрасывает панель (репозиторий закрыт или ещё не загружен)."""
        self._reset_files()
        self._loaded = False
        self._busy = False
        self._replace_tree(
            self.unstaged_tree, (), staged=False, text=_NOT_LOADED_TEXT
        )
        self._replace_tree(self.staged_tree, (), staged=True, text=_NOT_LOADED_TEXT)
        self._update_group_titles(loaded=False)
        self._update_action_buttons()
        self.status_label.setText(_NOT_LOADED_TEXT)
        self.refresh_button.setEnabled(True)

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
    def is_busy(self) -> bool:
        return self._busy

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
        self,
        title: str,
        object_name: str,
        hint_text: str,
        *,
        primary_text: str,
        primary_object_name: str,
        primary_tooltip: str,
        primary_slot,
        bulk_text: str,
        bulk_object_name: str,
        bulk_tooltip: str,
        bulk_slot,
    ) -> tuple[QGroupBox, QTreeWidget, QPushButton, QPushButton]:
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

        primary = self._make_action_button(
            primary_text, primary_object_name, primary_tooltip, primary_slot
        )
        bulk = self._make_action_button(
            bulk_text, bulk_object_name, bulk_tooltip, bulk_slot
        )
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        actions.addWidget(primary)
        actions.addWidget(bulk)
        actions.addStretch(1)

        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(hint)
        layout.addWidget(tree, 1)
        layout.addLayout(actions)
        return group, tree, primary, bulk

    def _make_action_button(
        self, text: str, object_name: str, tooltip: str, slot
    ) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName(object_name)
        button.setToolTip(tooltip)
        button.clicked.connect(slot)
        return button

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

    def _update_action_buttons(self) -> None:
        """Доступность действий: выбрана подходящая секция и нет конфликта."""
        available = self._loaded and not self._busy
        selected = self._selected_file
        self.diff_button.setEnabled(available and selected is not None)
        self.stage_button.setEnabled(
            available
            and selected is not None
            and not self._selected_is_staged
            and selected.can_stage
        )
        self.unstage_button.setEnabled(
            available
            and selected is not None
            and self._selected_is_staged
            and selected.is_staged
        )
        self.stage_all_button.setEnabled(
            available and any(change.can_stage for change in self._changes)
        )
        self.unstage_all_button.setEnabled(
            available and any(change.is_staged for change in self._changes)
        )

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
        self._update_action_buttons()
        self.file_selected.emit(self._selected_file, staged)

    def _request_diff_selected(self) -> None:
        selected = self._selected_file
        if selected is None or not self._loaded or self._busy:
            return
        self.diff_requested.emit(selected, self._selected_is_staged)

    def _request_stage_selected(self) -> None:
        selected = self._selected_file
        if selected is None or self._selected_is_staged or not selected.can_stage:
            return
        self.stage_requested.emit(selected)

    def _request_unstage_selected(self) -> None:
        selected = self._selected_file
        if selected is None or not self._selected_is_staged or not selected.is_staged:
            return
        self.unstage_requested.emit(selected)

    def _request_stage_all(self) -> None:
        if self._loaded and not self._busy:
            self.stage_all_requested.emit()

    def _request_unstage_all(self) -> None:
        if self._loaded and not self._busy:
            self.unstage_all_requested.emit()

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


# --- описание области действия массовой операции (для подтверждения в UI) ---


def scope_summary(files: Sequence[ChangedFile], *, staged: bool) -> str:
    """Категории файлов массовой операции: сколько каких изменений будет затронуто.

    ``staged=True`` — файлы уже в индексе и будут из него убраны (Unstage All);
    ``staged=False`` — файлы будут добавлены в индекс (Stage All). Неотслеживаемые
    файлы отдельной категорией. Порядок категорий фиксирован.
    """
    counts: dict[str, int] = {}
    for change in files:
        category = _scope_category(change, staged=staged)
        if category is not None:
            counts[category] = counts.get(category, 0) + 1

    lines = [f"Всего файлов: {len(files)}"]
    for _change_type, label in _SCOPE_CATEGORY_ORDER:
        if counts.get(label):
            lines.append(f"• {label}: {counts[label]}")
    if counts.get(_UNTRACKED_CATEGORY):
        lines.append(f"• {_UNTRACKED_CATEGORY}: {counts[_UNTRACKED_CATEGORY]}")
    return "\n".join(lines)


def scope_details(files: Sequence[ChangedFile], *, staged: bool) -> str:
    """Построчный список файлов массовой операции для раздела «Подробнее»."""
    return "\n".join(
        f"{_change_label(change, staged=staged)}: {_display_path(change)}"
        for change in files
    )


def _scope_category(change: ChangedFile, *, staged: bool) -> str | None:
    if not staged and change.is_untracked:
        return _UNTRACKED_CATEGORY
    change_type = change.staged_change if staged else change.unstaged_change
    if change_type is None:
        return None
    return dict(_SCOPE_CATEGORY_ORDER).get(change_type, change_type.value)
