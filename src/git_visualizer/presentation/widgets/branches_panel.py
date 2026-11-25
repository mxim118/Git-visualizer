"""Левая панель: локальные и remote-tracking ветки, создание и переключение.

Виджет не запускает Git. Список веток и признак текущей приходят из
``BranchService``, а нажатия кнопок только испускают сигналы: создание и
переключение выполняет MainWindow через Application Services. Remote-tracking
ветки в MVP показываются без переключения: создание tracking branch не
реализовано. Незакоммиченные изменения панель не трогает — безопасность
переключения обеспечивает Git, который отказывает, если они конфликтуют.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from git_visualizer.domain.models import Branch, Commit, Remote

_EMPTY_ITEM_TEXT = "(нет)"
_INFO_COLOR = "#64748b"


class BranchesPanel(QWidget):
    """Список веток, форма создания локальной ветки и кнопка переключения.

    ``create_branch_requested(name, start_point)`` — создать ветку от текущего
    ``HEAD`` (``start_point is None``) или от выбранного коммита. ``switch_branch_requested``
    — переключиться на выбранную локальную ветку. Оба сигнала испускаются только
    для допустимых на уровне UI действий; окончательную проверку имени и
    безопасность переключения выполняет Application/Git.
    """

    create_branch_requested = Signal(str, object)  # имя, hash коммита или None
    switch_branch_requested = Signal(str)  # имя локальной ветки

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("branches_panel")

        self._loaded = False
        self._busy = False
        self._selected_commit: Commit | None = None

        title = QLabel("Ветки и remote")
        title.setObjectName("branches_panel_title")
        title.setStyleSheet("font-weight: 600;")

        self.tree = QTreeWidget(self)
        self.tree.setObjectName("branches_tree")
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.currentItemChanged.connect(self._on_current_item_changed)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        self.switch_button = QPushButton("Переключиться", self)
        self.switch_button.setObjectName("branches_switch_button")
        self.switch_button.clicked.connect(self._request_switch)

        form_title = QLabel("Новая ветка", self)
        form_title.setObjectName("branches_form_title")
        form_title.setStyleSheet("font-weight: 600;")

        self.branch_name_edit = QLineEdit(self)
        self.branch_name_edit.setObjectName("branches_name_edit")
        self.branch_name_edit.setPlaceholderText("Имя новой ветки")
        self.branch_name_edit.setAccessibleName("Имя новой ветки")
        self.branch_name_edit.setMaxLength(255)
        self.branch_name_edit.textChanged.connect(self._update_actions)

        self.from_commit_checkbox = QCheckBox(
            "Создать от выбранного коммита", self
        )
        self.from_commit_checkbox.setObjectName("branches_from_commit")
        self.from_commit_checkbox.setEnabled(False)
        self.from_commit_checkbox.setToolTip(
            "Сначала выберите коммит в графе истории."
        )
        self.from_commit_checkbox.toggled.connect(self._update_hint)

        self.create_button = QPushButton("Создать", self)
        self.create_button.setObjectName("branches_create_button")
        self.create_button.clicked.connect(self._request_create)

        self.hint_label = QLabel(self)
        self.hint_label.setObjectName("branches_hint")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(f"color: {_INFO_COLOR}; font-size: 11px;")
        self.hint_label.setText(
            "Текущая ветка выделена жирным. Незакоммиченные изменения не "
            "удаляются: при конфликте Git откажет в переключении."
        )

        create_row = QHBoxLayout()
        create_row.setContentsMargins(0, 0, 0, 0)
        create_row.setSpacing(6)
        create_row.addWidget(self.create_button)
        create_row.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        buttons.addWidget(self.switch_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)
        layout.addWidget(form_title)
        layout.addWidget(self.branch_name_edit)
        layout.addWidget(self.from_commit_checkbox)
        layout.addLayout(create_row)
        layout.addWidget(self.hint_label)

        self.clear()

    # --- публичный интерфейс ---

    def set_branches(
        self,
        branches: Iterable[Branch],
        remotes: Iterable[Remote] = (),
    ) -> None:
        """Заполняет панель; текущая ветка выделена, upstream — в подсказке."""
        self.tree.clear()
        branches = tuple(branches)
        remotes = tuple(remotes)
        self._loaded = True

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
        self._update_actions()
        self._update_hint()

    def set_selected_commit(self, commit: Commit | None) -> None:
        """Обновляет коммит, от которого можно создать ветку.

        ``None`` — в графе ничего не выбрано: пункт отключается. Иначе ветку можно
        создать от выбранного коммита вместо текущего ``HEAD``.
        """
        self._selected_commit = commit
        if commit is None:
            self.from_commit_checkbox.setChecked(False)
            self.from_commit_checkbox.setText("Создать от выбранного коммита")
            self.from_commit_checkbox.setToolTip(
                "Сначала выберите коммит в графе истории."
            )
        else:
            self.from_commit_checkbox.setText(f"Создать от коммита {commit.short_hash}")
            self.from_commit_checkbox.setToolTip(
                f"{commit.hash}\n{commit.subject}"
            )
        self._update_actions()
        self._update_hint()

    def set_busy(self, busy: bool) -> None:
        """Блокирует действия, пока выполняется операция с ветками."""
        self._busy = busy
        self._update_actions()
        self._update_hint()

    def select_branch(self, name: str, *, remote: bool = False) -> bool:
        """Выбирает ветку в списке (например, для теста или будущих действий)."""
        for item in self._branch_items():
            branch = item.data(0, Qt.ItemDataRole.UserRole)
            if (
                isinstance(branch, Branch)
                and branch.name == name
                and branch.is_remote is remote
            ):
                self.tree.setCurrentItem(item)
                return True
        return False

    def clear_branch_name(self) -> None:
        """Очищает поле имени (после успешного создания ветки)."""
        self.branch_name_edit.clear()

    def clear(self) -> None:
        """Сбрасывает панель (репозиторий закрыт или ещё не загружен)."""
        self.tree.clear()
        self._loaded = False
        self._busy = False
        self._selected_commit = None
        self.branch_name_edit.clear()
        self.set_selected_commit(None)
        self._update_actions()

    @property
    def selected_branch(self) -> Branch | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        branch = item.data(0, Qt.ItemDataRole.UserRole)
        return branch if isinstance(branch, Branch) else None

    @property
    def branch_name(self) -> str:
        return self.branch_name_edit.text()

    @property
    def create_from_selected_commit(self) -> bool:
        return self._selected_commit is not None and (
            self.from_commit_checkbox.isChecked()
        )

    # --- внутренняя реализация ---

    def _add_group(self, title: str, items: Iterable[QTreeWidgetItem]) -> None:
        group = QTreeWidgetItem([title])
        font = group.font(0)
        font.setBold(True)
        group.setFont(0, font)
        # Группа — только заголовок, не ветка: выбор на ней действий не включает.
        group.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.tree.addTopLevelItem(group)

        children = list(items)
        if children:
            group.addChildren(children)
        else:
            empty = QTreeWidgetItem([_EMPTY_ITEM_TEXT])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
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
        item.setData(0, Qt.ItemDataRole.UserRole, branch)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        if branch.is_current:
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
        tooltip = branch.ref_name
        if branch.upstream:
            tooltip += f"\nОтслеживает: {branch.upstream}"
        if branch.is_remote:
            tooltip += (
                "\nRemote-tracking ветка: в MVP показана только для просмотра."
            )
        item.setToolTip(0, tooltip)
        return item

    @staticmethod
    def _remote_item(remote: Remote) -> QTreeWidgetItem:
        item = QTreeWidgetItem([remote.name])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        lines = [remote.name]
        if remote.fetch_url:
            lines.append(f"Fetch: {remote.fetch_url}")
        if remote.push_url:
            lines.append(f"Push: {remote.push_url}")
        item.setToolTip(0, "\n".join(lines))
        return item

    def _branch_items(self) -> tuple[QTreeWidgetItem, ...]:
        items: list[QTreeWidgetItem] = []
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            for child_index in range(group.childCount()):
                items.append(group.child(child_index))
        return tuple(items)

    def _update_actions(self) -> None:
        ready = self._loaded and not self._busy
        branch = self.selected_branch
        self.branch_name_edit.setEnabled(ready)
        self.from_commit_checkbox.setEnabled(
            ready and self._selected_commit is not None
        )
        self.create_button.setEnabled(
            ready and bool(self.branch_name_edit.text().strip())
        )
        self.create_button.setToolTip(self._create_tooltip(ready))
        self.switch_button.setEnabled(
            ready
            and branch is not None
            and not branch.is_remote
            and not branch.is_current
        )
        self.switch_button.setToolTip(self._switch_tooltip(branch))

    def _create_tooltip(self, ready: bool) -> str:
        if not self._loaded:
            return "Репозиторий ещё не загружен."
        if self._busy:
            return "Выполняется операция с ветками…"
        if not self.branch_name_edit.text().strip():
            return "Введите имя новой ветки."
        if self.create_from_selected_commit:
            assert self._selected_commit is not None
            return (
                f"Создать ветку от коммита {self._selected_commit.short_hash} "
                "и переключиться на неё."
            )
        return "Создать локальную ветку от текущего HEAD и переключиться на неё."

    @staticmethod
    def _switch_tooltip(branch: Branch | None) -> str:
        if branch is None:
            return "Выберите локальную ветку в списке."
        if branch.is_remote:
            return (
                "Remote-tracking ветки в MVP доступны только для просмотра: "
                "создание tracking branch не реализовано."
            )
        if branch.is_current:
            return "Это текущая ветка."
        return (
            f"Переключиться на «{branch.name}». Незакоммиченные изменения не "
            "удаляются: Git откажет, если они конфликтуют с переключением."
        )

    def _update_hint(self, *_args: object) -> None:
        if not self._loaded:
            self.hint_label.setText(
                "История и ветки ещё не загружены."
            )
            return
        if self._busy:
            self.hint_label.setText("Выполняется операция с ветками…")
            return
        if self._selected_commit is None:
            self.hint_label.setText(
                "Ветка создаётся от текущего HEAD. Незакоммиченные изменения "
                "не удаляются: при конфликте Git откажет в переключении."
            )
            return
        if self.from_commit_checkbox.isChecked():
            self.hint_label.setText(
                "Ветка будет создана от коммита "
                f"{self._selected_commit.short_hash} и станет текущей."
            )
        else:
            self.hint_label.setText(
                "Ветка создаётся от текущего HEAD; отметьте пункт выше, чтобы "
                "создать её от выбранного коммита."
            )

    def _on_current_item_changed(
        self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None
    ) -> None:
        self._update_actions()

    def _on_item_double_clicked(
        self, item: QTreeWidgetItem, _column: int
    ) -> None:
        branch = item.data(0, Qt.ItemDataRole.UserRole)
        if (
            isinstance(branch, Branch)
            and not branch.is_remote
            and not branch.is_current
            and self._loaded
            and not self._busy
        ):
            self.tree.setCurrentItem(item)
            self.switch_branch_requested.emit(branch.name)

    def _request_create(self) -> None:
        if not self.create_button.isEnabled():
            return
        start_point = (
            self._selected_commit.hash
            if self.create_from_selected_commit and self._selected_commit is not None
            else None
        )
        self.create_branch_requested.emit(self.branch_name_edit.text(), start_point)

    def _request_switch(self) -> None:
        branch = self.selected_branch
        if branch is None or not self.switch_button.isEnabled():
            return
        self.switch_branch_requested.emit(branch.name)
