"""GUI-тесты панели Changes: секции, пустые состояния, diff и обновление."""

from __future__ import annotations

from PySide6.QtCore import Qt

from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import ChangedFile, Diff
from git_visualizer.presentation.widgets.changes_panel import ChangesPanel

CHANGES = (
    ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),
    ChangedFile(path="unstaged.txt", unstaged_change=FileChangeType.MODIFIED),
    ChangedFile(
        path="both.txt",
        staged_change=FileChangeType.MODIFIED,
        unstaged_change=FileChangeType.MODIFIED,
    ),
    ChangedFile(
        path="renamed.txt",
        old_path="old.txt",
        staged_change=FileChangeType.RENAMED,
    ),
    ChangedFile(path="untracked.txt", is_untracked=True),
    ChangedFile(path="conflict.txt", is_conflicted=True),
)


def test_sections_are_split_and_statuses_are_visible(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)

    assert [change.path for change in panel.unstaged_files] == [
        "unstaged.txt",
        "both.txt",
        "untracked.txt",
        "conflict.txt",
    ]
    assert [change.path for change in panel.staged_files] == [
        "staged.txt",
        "both.txt",
        "renamed.txt",
    ]
    assert panel.unstaged_group.title() == "Unstaged (4)"
    assert panel.staged_group.title() == "Staged (3)"
    assert "Вне индекса: 4" in panel.status_label.text()
    assert "В индексе: 3" in panel.status_label.text()

    unstaged_tree = panel.unstaged_tree
    staged_tree = panel.staged_tree
    assert [unstaged_tree.topLevelItem(index).text(0) for index in range(4)] == [
        "unstaged.txt",
        "both.txt",
        "untracked.txt",
        "conflict.txt",
    ]
    assert unstaged_tree.topLevelItem(0).text(1) == "изменён"
    assert unstaged_tree.topLevelItem(2).text(1) == "не отслеживается"
    assert unstaged_tree.topLevelItem(3).text(1) == "конфликт"
    assert staged_tree.topLevelItem(0).text(1) == "добавлен"
    assert staged_tree.topLevelItem(2).text(0) == "old.txt → renamed.txt"
    assert staged_tree.topLevelItem(2).text(1) == "переименован"

    # Файл с двумя изменениями присутствует в обеих секциях, статусы различаются.
    assert "both.txt" in [
        unstaged_tree.topLevelItem(index).text(0) for index in range(4)
    ]
    assert "both.txt" in [
        staged_tree.topLevelItem(index).text(0) for index in range(3)
    ]
    staged_color = staged_tree.topLevelItem(0).foreground(1).color().name()
    unstaged_color = unstaged_tree.topLevelItem(0).foreground(1).color().name()
    assert staged_color != unstaged_color
    assert staged_tree.topLevelItem(1).foreground(1).color().name() == staged_color
    assert unstaged_tree.topLevelItem(1).foreground(1).color().name() == unstaged_color
    assert panel.diff_view.message_text == "Выберите файл, чтобы увидеть diff."


def test_selection_reports_file_and_section_and_clears_other_list(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    selected: list[tuple[ChangedFile, bool]] = []
    panel.file_selected.connect(lambda change, staged: selected.append((change, staged)))

    panel.staged_tree.setCurrentItem(panel.staged_tree.topLevelItem(1))

    assert selected == [(CHANGES[2], True)]
    assert panel.selected_file is CHANGES[2]
    assert panel.selected_is_staged is True
    assert panel.unstaged_tree.currentItem() is None
    assert "в индексе" in panel.diff_view.header_label.text()

    panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(1))

    assert selected[-1] == (CHANGES[2], False)
    assert panel.selected_is_staged is False
    assert panel.staged_tree.currentItem() is None
    assert "вне индекса" in panel.diff_view.header_label.text()


def test_empty_state_is_shown_after_loading_without_changes(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(())

    assert panel.changes == ()
    assert panel.is_clean
    assert panel.status_label.text() == "Изменений нет — рабочая директория чиста."
    assert panel.unstaged_group.title() == "Unstaged (0)"
    assert panel.staged_group.title() == "Staged (0)"
    for tree, text in (
        (panel.unstaged_tree, "Нет незакоммиченных изменений"),
        (panel.staged_tree, "Нет файлов в индексе"),
    ):
        assert tree.topLevelItemCount() == 1
        assert tree.topLevelItem(0).text(0) == text
        assert tree.topLevelItem(0).flags() == Qt.ItemFlag.NoItemFlags
    assert panel.refresh_button.isEnabled()


def test_loading_and_error_states_are_explicit(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)

    panel.set_loading()

    assert panel.changes == ()
    assert panel.status_label.text() == "Загрузка изменений…"
    assert panel.unstaged_tree.topLevelItem(0).text(0) == "Загрузка изменений…"
    assert panel.unstaged_group.title() == "Unstaged"
    assert panel.refresh_button.isEnabled() is False
    assert panel.diff_view.message_text == "Загрузка изменений…"

    panel.set_error("Не удалось загрузить изменения.")

    assert panel.status_label.text() == "Не удалось загрузить изменения."
    assert panel.unstaged_tree.topLevelItem(0).text(0) == "Не удалось загрузить"
    assert panel.refresh_button.isEnabled() is True


def test_clear_returns_to_not_loaded_state(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)

    panel.clear()

    assert panel.changes == ()
    assert panel.status_label.text() == "Изменения не загружены"
    assert panel.unstaged_tree.topLevelItemCount() == 1
    assert panel.selected_file is None


def test_untracked_selection_shows_notice_instead_of_diff(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    selected: list[tuple[ChangedFile, bool]] = []
    panel.file_selected.connect(lambda change, staged: selected.append((change, staged)))

    panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(2))
    panel.show_untracked_notice()

    assert selected == [(CHANGES[4], False)]
    assert "не отслеживается" in panel.diff_view.message_text
    assert "untracked.txt" in panel.diff_view.header_label.text()


def test_binary_diff_is_marked(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))

    panel.show_diff(
        Diff(path="unstaged.txt", text="", is_binary=True)
    )

    assert "Бинарный файл" in panel.diff_view.message_text
    assert panel.diff_view.is_editor_visible is False
    assert "вне индекса" in panel.diff_view.header_label.text()


def test_text_diff_is_shown_with_staged_base_text(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    panel.staged_tree.setCurrentItem(panel.staged_tree.topLevelItem(0))

    panel.show_diff(
        Diff(path="staged.txt", text="@@ -0,0 +1 @@\n+staged\n")
    )

    assert panel.diff_view.is_editor_visible is True
    assert "+staged" in panel.diff_view.text
    assert "Staged diff" in panel.diff_view.base_label.text()


def test_refresh_button_requests_status_reload(qt_app) -> None:
    panel = ChangesPanel()
    requests: list[bool] = []
    panel.refresh_requested.connect(lambda: requests.append(True))

    panel.refresh_button.click()

    assert requests == [True]
