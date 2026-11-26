"""Окна управления remote: список, поля, подтверждение удаления и сигналы."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from git_visualizer.domain.models import Remote
from git_visualizer.presentation.dialogs.remote_edit_dialog import (
    RemoteEditDialog,
)
from git_visualizer.presentation.dialogs.remote_settings_dialog import (
    RemoteSettingsDialog,
)

REMOTES = (
    Remote(
        name="fork",
        fetch_url="git@example.com:me/fork.git",
        push_url="git@example.com:me/fork.git",
    ),
    Remote(
        name="origin",
        fetch_url="https://example.com/team/repo.git",
        push_url="ssh://git@example.com/team/repo.git",
    ),
)


# --- RemoteEditDialog ---


def test_add_dialog_requires_name_and_url(qt_app) -> None:
    dialog = RemoteEditDialog()
    try:
        assert not dialog.save_button.isEnabled()
        assert dialog.save_button.text() == "Добавить"

        dialog.name_edit.setText(" origin ")
        assert not dialog.save_button.isEnabled()

        dialog.url_edit.setText(" https://example.com/r.git ")
        assert dialog.save_button.isEnabled()
        assert dialog.remote_name == "origin"
        assert dialog.remote_url == "https://example.com/r.git"
    finally:
        dialog.close()


def test_edit_dialog_prefills_remote_and_uses_save_label(qt_app) -> None:
    dialog = RemoteEditDialog(remote=REMOTES[1])
    try:
        assert dialog.windowTitle() == "Изменить remote"
        assert dialog.save_button.text() == "Сохранить"
        assert dialog.remote_name == "origin"
        assert dialog.remote_url == "https://example.com/team/repo.git"
    finally:
        dialog.close()


# --- RemoteSettingsDialog ---


def test_settings_dialog_lists_names_and_urls(qt_app) -> None:
    dialog = RemoteSettingsDialog(remotes=REMOTES)
    try:
        assert dialog.table.rowCount() == 2
        assert dialog.table.item(0, 0).text() == "fork"
        assert dialog.table.item(0, 1).text() == "git@example.com:me/fork.git"
        assert dialog.table.item(1, 0).text() == "origin"
        assert (
            dialog.table.item(1, 1).text()
            == "https://example.com/team/repo.git"
        )
        # Отдельный push URL виден в подсказке, но не подменяет основной URL.
        assert "ssh://git@example.com/team/repo.git" in dialog.table.item(
            1, 1
        ).toolTip()
        assert dialog.remotes == REMOTES
    finally:
        dialog.close()


def test_actions_require_selection(qt_app) -> None:
    dialog = RemoteSettingsDialog(remotes=REMOTES)
    try:
        assert dialog.add_button.isEnabled()
        assert not dialog.edit_button.isEnabled()
        assert not dialog.remove_button.isEnabled()

        dialog.table.selectRow(1)
        assert dialog.edit_button.isEnabled()
        assert dialog.remove_button.isEnabled()
        assert dialog.selected_remote_name == "origin"
    finally:
        dialog.close()


def test_set_remotes_preserves_selection_by_name(qt_app) -> None:
    dialog = RemoteSettingsDialog(remotes=REMOTES)
    try:
        dialog.table.selectRow(0)

        dialog.set_remotes(tuple(reversed(REMOTES)))

        assert dialog.selected_remote_name == "fork"
    finally:
        dialog.close()


def test_set_remotes_can_select_new_name(qt_app) -> None:
    dialog = RemoteSettingsDialog()
    try:
        dialog.set_remotes(REMOTES, selected="origin")

        assert dialog.selected_remote_name == "origin"
    finally:
        dialog.close()


def test_busy_state_blocks_actions_and_shows_progress(qt_app) -> None:
    dialog = RemoteSettingsDialog(remotes=REMOTES)
    try:
        dialog.table.selectRow(0)
        dialog.set_busy(True, "Удаление remote «fork»…")

        assert dialog.is_busy
        assert not dialog.add_button.isEnabled()
        assert not dialog.edit_button.isEnabled()
        assert not dialog.remove_button.isEnabled()
        assert not dialog.close_button.isEnabled()
        assert not dialog.table.isEnabled()
        assert not dialog.status_label.isHidden()
        assert "Удаление" in dialog.status_label.text()

        dialog.set_busy(False)
        assert not dialog.is_busy
        assert dialog.add_button.isEnabled()
        assert dialog.edit_button.isEnabled()
        assert dialog.close_button.isEnabled()
    finally:
        dialog.close()


def test_add_button_opens_dialog_and_emits_request(
    qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = RemoteSettingsDialog()
    requests: list[tuple[str, str]] = []
    dialog.add_requested.connect(
        lambda name, url: requests.append((name, url))
    )

    def fake_exec(editor: RemoteEditDialog) -> int:
        editor.name_edit.setText("origin")
        editor.url_edit.setText("https://example.com/r.git")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RemoteEditDialog, "exec", fake_exec)
    try:
        dialog.add_button.click()

        assert requests == [("origin", "https://example.com/r.git")]
    finally:
        dialog.close()


def test_cancelled_add_dialog_emits_nothing(
    qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = RemoteSettingsDialog()
    requests: list[tuple[str, str]] = []
    dialog.add_requested.connect(
        lambda name, url: requests.append((name, url))
    )
    monkeypatch.setattr(
        RemoteEditDialog,
        "exec",
        lambda editor: QDialog.DialogCode.Rejected,
    )
    try:
        dialog.add_button.click()

        assert requests == []
    finally:
        dialog.close()


def test_edit_button_emits_old_new_name_and_url(
    qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = RemoteSettingsDialog(remotes=REMOTES)
    requests: list[tuple[str, str, str]] = []
    dialog.edit_requested.connect(
        lambda old, new, url: requests.append((old, new, url))
    )
    dialog.table.selectRow(1)

    def fake_exec(editor: RemoteEditDialog) -> int:
        assert editor.remote_name == "origin"
        editor.name_edit.setText("upstream")
        editor.url_edit.setText("ssh://git@example.com/team/new.git")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RemoteEditDialog, "exec", fake_exec)
    try:
        dialog.edit_button.click()

        assert requests == [
            (
                "origin",
                "upstream",
                "ssh://git@example.com/team/new.git",
            )
        ]
    finally:
        dialog.close()


def test_unchanged_edit_emits_nothing_and_reports_it(
    qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = RemoteSettingsDialog(remotes=REMOTES)
    requests: list[tuple[str, str, str]] = []
    dialog.edit_requested.connect(
        lambda old, new, url: requests.append((old, new, url))
    )
    dialog.table.selectRow(1)
    monkeypatch.setattr(
        RemoteEditDialog,
        "exec",
        lambda editor: QDialog.DialogCode.Accepted,
    )
    try:
        dialog.edit_button.click()

        assert requests == []
        assert dialog.status_label.text() == "Изменений нет."
    finally:
        dialog.close()


def test_remove_requires_confirmation_and_emits_name(
    qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = RemoteSettingsDialog(remotes=REMOTES)
    requests: list[str] = []
    dialog.remove_requested.connect(requests.append)
    dialog.table.selectRow(0)
    seen: list[tuple[str, str, str]] = []

    def fake_exec(box: QMessageBox) -> int:
        seen.append(
            (
                box.windowTitle(),
                box.text(),
                box.informativeText(),
            )
        )
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    try:
        dialog.remove_button.click()

        assert requests == ["fork"]
        assert seen[0][0] == "Удаление remote"
        assert "«fork»" in seen[0][1]
        assert "локальной конфигурации Git" in seen[0][2]
        assert "сервере" in seen[0][2]
        assert "файлы рабочей директории" in seen[0][2]
    finally:
        dialog.close()


def test_cancelled_remove_does_not_emit(
    qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = RemoteSettingsDialog(remotes=REMOTES)
    requests: list[str] = []
    dialog.remove_requested.connect(requests.append)
    dialog.table.selectRow(0)
    monkeypatch.setattr(
        QMessageBox, "exec", lambda box: QMessageBox.StandardButton.No
    )
    try:
        dialog.remove_button.click()

        assert requests == []
    finally:
        dialog.close()


def test_double_click_opens_editor(
    qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = RemoteSettingsDialog(remotes=REMOTES)
    requests: list[tuple[str, str, str]] = []
    dialog.edit_requested.connect(
        lambda old, new, url: requests.append((old, new, url))
    )
    monkeypatch.setattr(
        RemoteEditDialog,
        "exec",
        lambda editor: QDialog.DialogCode.Rejected,
    )
    try:
        dialog._on_item_double_clicked(dialog.table.item(0, 0), 0)

        assert dialog.selected_remote_name == "fork"
        assert requests == []
    finally:
        dialog.close()


def test_context_menu_offers_edit_and_delete_and_deletes_with_confirmation(
    qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = RemoteSettingsDialog(remotes=REMOTES)
    requests: list[str] = []
    dialog.remove_requested.connect(requests.append)

    def fake_exec(menu, position):
        texts = [action.text() for action in menu.actions() if action.text()]
        assert "Изменить…" in texts
        assert "Удалить…" in texts
        return next(
            action for action in menu.actions() if action.text() == "Удалить…"
        )

    monkeypatch.setattr(dialog, "_exec_menu", fake_exec)
    monkeypatch.setattr(
        QMessageBox, "exec", lambda box: QMessageBox.StandardButton.Yes
    )
    try:
        dialog.show()
        qt_app.processEvents()
        item = dialog.table.item(0, 0)
        position = dialog.table.visualItemRect(item).center()
        dialog._show_context_menu(position)

        assert dialog.selected_remote_name == "fork"
        assert requests == ["fork"]
    finally:
        dialog.close()
