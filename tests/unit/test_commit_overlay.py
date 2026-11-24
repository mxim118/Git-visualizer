"""GUI-тесты окна сообщения коммита поверх главного окна."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from git_visualizer.presentation.widgets.commit_overlay import CommitOverlay


def test_overlay_opens_with_empty_message_and_reason(qt_app) -> None:
    overlay = CommitOverlay()
    try:
        assert overlay.isHidden()

        overlay.show_overlay(staged_count=2)

        assert not overlay.isHidden()
        assert overlay.message == ""
        assert overlay.message_edit.isEnabled()
        assert not overlay.commit_button.isEnabled()
        assert overlay.cancel_button.isEnabled()
        assert "Введите сообщение коммита" in overlay.hint_label.text()
    finally:
        overlay.close()


def test_blank_message_is_rejected_and_message_is_submitted(qt_app) -> None:
    overlay = CommitOverlay()
    overlay.show_overlay(staged_count=1)
    requests: list[str] = []
    overlay.submitted.connect(requests.append)

    overlay.message_edit.setPlainText("   \n\t ")
    assert not overlay.commit_button.isEnabled()
    overlay.commit_button.click()
    assert requests == []

    overlay.message_edit.setPlainText("  Subject with spaces  ")
    assert overlay.commit_button.isEnabled()
    assert "только файлы из Staged (1)" in overlay.hint_label.text()
    overlay.commit_button.click()

    assert requests == ["  Subject with spaces  "]
    overlay.close()


def test_no_staged_files_disables_input_and_commit(qt_app) -> None:
    overlay = CommitOverlay()
    overlay.show_overlay(staged_count=0)
    overlay.message_edit.setPlainText("Message")

    assert not overlay.message_edit.isEnabled()
    assert not overlay.commit_button.isEnabled()
    assert "Нет staged-изменений" in overlay.hint_label.text()

    overlay.close()


def test_cancel_clears_message_and_hides(qt_app) -> None:
    overlay = CommitOverlay()
    overlay.show_overlay(staged_count=1)
    overlay.message_edit.setPlainText("Будет отменено")

    overlay.cancel_button.click()

    assert overlay.isHidden()
    assert overlay.message == ""
    overlay.close()


def test_escape_cancels_and_clears(qt_app) -> None:
    overlay = CommitOverlay()
    overlay.show_overlay(staged_count=1)
    overlay.message_edit.setPlainText("Escape")

    QTest.keyClick(overlay.message_edit, Qt.Key.Key_Escape)
    qt_app.processEvents()

    assert overlay.isHidden()
    assert overlay.message == ""
    overlay.close()


def test_busy_state_blocks_inputs_and_keeps_message(qt_app) -> None:
    overlay = CommitOverlay()
    overlay.show_overlay(staged_count=3)
    overlay.message_edit.setPlainText("Сообщение")

    overlay.set_busy(True)

    assert overlay.is_busy
    assert not overlay.message_edit.isEnabled()
    assert not overlay.cancel_button.isEnabled()
    assert not overlay.commit_button.isEnabled()
    assert "Создание коммита" in overlay.hint_label.text()
    # Отмена во время фоновой операции не должна закрывать окно.
    overlay.cancel_button.click()
    QTest.keyClick(overlay.message_edit, Qt.Key.Key_Escape)
    assert not overlay.isHidden()

    overlay.set_busy(False)

    assert not overlay.is_busy
    assert overlay.message == "Сообщение"
    assert overlay.message_edit.isEnabled()
    assert overlay.cancel_button.isEnabled()
    assert overlay.commit_button.isEnabled()

    overlay.close()


def test_staged_count_update_recomputes_state(qt_app) -> None:
    overlay = CommitOverlay()
    overlay.show_overlay(staged_count=2)
    overlay.message_edit.setPlainText("Message")

    overlay.update_staged_count(0)
    assert not overlay.commit_button.isEnabled()
    assert "Нет staged-изменений" in overlay.hint_label.text()
    assert overlay.message == "Message"

    overlay.update_staged_count(5)
    assert overlay.commit_button.isEnabled()
    assert "Staged (5)" in overlay.hint_label.text()
    assert overlay.message == "Message"

    overlay.close()


def test_hide_overlay_clears_message(qt_app) -> None:
    overlay = CommitOverlay()
    overlay.show_overlay(staged_count=1)
    overlay.message_edit.setPlainText("Message")

    overlay.hide_overlay()

    assert overlay.isHidden()
    assert overlay.message == ""
    overlay.close()


def test_card_is_centered_over_parent(qt_app) -> None:
    parent = QWidget()
    parent.resize(900, 600)
    parent.show()
    overlay = CommitOverlay(parent)
    overlay.show_overlay(staged_count=1)
    qt_app.processEvents()
    try:
        assert overlay.geometry() == parent.rect()
        card = overlay.card.geometry()
        assert card.width() == 520
        assert abs(card.center().x() - overlay.rect().center().x()) <= 1
        assert abs(card.center().y() - overlay.rect().center().y()) <= 1
    finally:
        overlay.close()
        parent.close()
