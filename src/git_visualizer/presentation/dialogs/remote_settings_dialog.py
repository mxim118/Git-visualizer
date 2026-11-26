"""Окно настроек remote: список, добавление, изменение и удаление.

Виджет не запускает Git. Он показывает remote из загруженных данных репозитория
и только испускает запросы ``add_requested``/``edit_requested``/``remove_requested``;
операции выполняет MainWindow через Application Services, после чего передаёт
сюда обновлённый список. Удаление подтверждается здесь: удаляется только запись
remote в локальной конфигурации Git, сервер и файлы рабочей директории не
затрагиваются.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from git_visualizer.domain.models import Remote
from git_visualizer.presentation.dialogs.remote_edit_dialog import RemoteEditDialog

_INFO_COLOR = "#64748b"
_OPERATION_COLOR = "#2563eb"
_RESULT_COLOR = "#15803d"
_ERROR_COLOR = "#b91c1c"
_EMPTY_URL = "—"


class RemoteSettingsDialog(QDialog):
    """Список remote и действия над ними.

    ``add_requested(name, url)`` — добавить remote; ``edit_requested(old_name,
    new_name, url)`` — изменить имя и/или URL; ``remove_requested(name)`` —
    удалить remote после подтверждения. Окончательную проверку и запись в
    конфигурацию Git выполняет Application.
    """

    add_requested = Signal(str, str)  # имя, URL
    edit_requested = Signal(str, str, str)  # текущее имя, новое имя, URL
    remove_requested = Signal(str)  # имя

    def __init__(
        self,
        *,
        remotes: Iterable[Remote] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("remote_settings_dialog")
        self.setWindowTitle("Настройки remote")
        self.setModal(False)
        self.resize(760, 430)

        self._remotes: tuple[Remote, ...] = ()
        self._busy = False

        title = QLabel("Настройки remote", self)
        title.setObjectName("remote_settings_title")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")

        hint = QLabel(
            "Remote хранятся в локальной конфигурации Git. Добавлять, изменять "
            "и удалять их можно для любого Git-сервера: поддерживаются HTTPS- "
            "и SSH-ссылки. Сами операции выполняет Git CLI.",
            self,
        )
        hint.setObjectName("remote_settings_hint")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {_INFO_COLOR};")

        self.table = QTableWidget(0, 2, self)
        self.table.setObjectName("remote_settings_table")
        self.table.setHorizontalHeaderLabels(["Имя remote", "URL"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.table.customContextMenuRequested.connect(
            self._show_context_menu
        )

        self.add_button = self._make_button("Добавить", "remote_add_button")
        self.edit_button = self._make_button("Изменить", "remote_edit_button")
        self.remove_button = self._make_button("Удалить", "remote_delete_button")
        self.close_button = self._make_button("Закрыть", "remote_close_button")

        self.add_button.clicked.connect(self._open_add_dialog)
        self.edit_button.clicked.connect(self._open_edit_dialog)
        self.remove_button.clicked.connect(self._confirm_remove)
        self.close_button.clicked.connect(self.reject)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("remote_settings_status")
        self.status_label.setWordWrap(True)
        self.status_label.hide()

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(self.add_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.remove_button)
        actions.addStretch(1)
        actions.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        layout.addWidget(self.status_label)

        self.set_remotes(remotes)

    # --- публичный интерфейс ---

    def set_remotes(
        self,
        remotes: Iterable[Remote],
        *,
        selected: str | None = None,
    ) -> None:
        """Заполняет список, сохраняя выбранный remote, если он ещё существует."""
        remotes = tuple(remotes)
        target = selected if selected is not None else self.selected_remote_name
        self._remotes = remotes

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row, remote in enumerate(remotes):
            self.table.insertRow(row)
            name_item = QTableWidgetItem(remote.name)
            name_item.setData(Qt.ItemDataRole.UserRole, remote)
            url_item = QTableWidgetItem(remote.fetch_url or remote.push_url or _EMPTY_URL)
            url_item.setToolTip(self._remote_tooltip(remote))
            name_item.setToolTip(self._remote_tooltip(remote))
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, url_item)
        self.table.blockSignals(False)

        if target is not None:
            self._select_name(target)
        self._update_actions()

    def set_busy(self, busy: bool, message: str | None = None) -> None:
        """Блокирует действия и показывает ход операции."""
        self._busy = busy
        self.table.setEnabled(not busy)
        if busy:
            self._set_status(message or "Выполняется операция Git…", color=_OPERATION_COLOR)
        self._update_actions()

    def show_status(self, text: str, *, error: bool = False) -> None:
        """Показывает итог операции или её ошибку."""
        self._set_status(text, color=_ERROR_COLOR if error else _RESULT_COLOR)

    def clear_status(self) -> None:
        self.status_label.clear()
        self.status_label.hide()

    @property
    def remotes(self) -> tuple[Remote, ...]:
        return self._remotes

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def selected_remote(self) -> Remote | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        remote = item.data(Qt.ItemDataRole.UserRole)
        return remote if isinstance(remote, Remote) else None

    @property
    def selected_remote_name(self) -> str | None:
        remote = self.selected_remote
        return remote.name if remote is not None else None

    # --- кнопки и контекстное меню ---

    def _open_add_dialog(self) -> None:
        if self._busy:
            return
        dialog = RemoteEditDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.add_requested.emit(dialog.remote_name, dialog.remote_url)

    def _open_edit_dialog(self) -> None:
        remote = self.selected_remote
        if remote is None or self._busy:
            return
        dialog = RemoteEditDialog(remote=remote, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        current_url = remote.fetch_url or remote.push_url or ""
        if (
            dialog.remote_name == remote.name
            and dialog.remote_url == current_url
        ):
            self._set_status("Изменений нет.", color=_INFO_COLOR)
            return
        self.edit_requested.emit(
            remote.name, dialog.remote_name, dialog.remote_url
        )

    def _confirm_remove(self) -> None:
        remote = self.selected_remote
        if remote is None or self._busy:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Удаление remote")
        box.setText(f"Удалить remote «{remote.name}»?")
        box.setInformativeText(
            "Будет удалена только запись remote в локальной конфигурации Git.\n\n"
            "Репозиторий на сервере и файлы рабочей директории не изменятся."
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        # По умолчанию — «Нет»: случайный Enter не должен удалять remote.
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self.remove_requested.emit(remote.name)

    def _show_context_menu(self, position) -> None:  # noqa: ANN001 (Qt API)
        if self._busy:
            return
        item = self.table.itemAt(position)
        menu = QMenu(self)
        if item is None:
            add_action = menu.addAction("Добавить…")
            chosen = self._exec_menu(menu, position)
            if chosen is add_action:
                self._open_add_dialog()
            return

        self.table.selectRow(item.row())
        edit_action = menu.addAction("Изменить…")
        remove_action = menu.addAction("Удалить…")
        menu.addSeparator()
        close_action = menu.addAction("Закрыть")
        chosen = self._exec_menu(menu, position)
        if chosen is edit_action:
            self._open_edit_dialog()
        elif chosen is remove_action:
            self._confirm_remove()
        elif chosen is close_action:
            self.reject()

    def _exec_menu(self, menu: QMenu, position):  # noqa: ANN001 (Qt API)
        """Показывает контекстное меню; отдельный метод упрощает тесты."""
        return menu.exec(self.table.viewport().mapToGlobal(position))

    def _on_item_double_clicked(
        self, item: QTableWidgetItem, _column: int
    ) -> None:
        self.table.selectRow(item.row())
        self._open_edit_dialog()

    # --- внутренняя реализация ---

    def _make_button(self, text: str, object_name: str) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName(object_name)
        button.setEnabled(False)
        return button

    def _select_name(self, name: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.text() == name:
                self.table.selectRow(row)
                return

    def _update_actions(self) -> None:
        ready = not self._busy
        has_selection = self.selected_remote is not None
        self.add_button.setEnabled(ready)
        self.edit_button.setEnabled(ready and has_selection)
        self.remove_button.setEnabled(ready and has_selection)
        self.close_button.setEnabled(ready)

    def _set_status(self, text: str, *, color: str) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")
        self.status_label.setToolTip(text)
        self.status_label.show()

    @staticmethod
    def _remote_tooltip(remote: Remote) -> str:
        lines = [remote.name]
        if remote.fetch_url:
            lines.append(f"Fetch: {remote.fetch_url}")
        if remote.push_url:
            lines.append(f"Push: {remote.push_url}")
        return "\n".join(lines)
