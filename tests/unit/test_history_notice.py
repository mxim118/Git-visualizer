"""Виджет уведомления об ограниченной истории (ТЗ FR-HIST-07)."""

from __future__ import annotations

from git_visualizer.presentation.widgets.history_notice import HistoryNoticeBar


def test_bar_is_hidden_until_history_is_truncated(qt_app) -> None:
    bar = HistoryNoticeBar()
    try:
        assert bar.isHidden()
        assert bar.button.isEnabled()

        bar.show_truncated(3000)

        assert not bar.isHidden()
        assert "3000" in bar.label.text()
        assert bar.button.text() == "Загрузить ещё"
        assert not bar.is_loading
    finally:
        bar.close()


def test_button_emits_load_more_requested(qt_app) -> None:
    bar = HistoryNoticeBar()
    requested: list[bool] = []
    bar.load_more_requested.connect(lambda: requested.append(True))
    try:
        bar.show_truncated(10)

        bar.button.click()

        assert requested == [True]
    finally:
        bar.close()


def test_loading_disables_the_button_and_reports_progress(qt_app) -> None:
    bar = HistoryNoticeBar()
    try:
        bar.show_truncated(10)

        bar.show_loading()

        assert bar.is_loading
        assert not bar.button.isEnabled()
        assert "Загрузка более старой истории" in bar.label.text()
        assert not bar.isHidden()

        # После ответа кнопка снова доступна, счётчик обновлён.
        bar.show_truncated(20)
        assert not bar.is_loading
        assert "20" in bar.label.text()
        assert bar.button.text() == "Загрузить ещё"
    finally:
        bar.close()
