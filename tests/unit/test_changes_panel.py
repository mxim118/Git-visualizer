"""GUI-тесты панели Changes: секции, пустые состояния и обновление."""

from __future__ import annotations

from PySide6.QtCore import Qt

from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import ChangedFile
from git_visualizer.presentation.widgets.changes_panel import (
    ChangesPanel,
    scope_details,
    scope_summary,
)

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
    # Встроенного diff больше нет: diff открывается панелью поверх окна.
    assert not hasattr(panel, "diff_view")
    assert panel.splitter.count() == 2


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

    panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(1))

    assert selected[-1] == (CHANGES[2], False)
    assert panel.selected_is_staged is False
    assert panel.staged_tree.currentItem() is None


def test_clear_selection_allows_selecting_the_same_file_again(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    selected: list[tuple[ChangedFile, bool]] = []
    panel.file_selected.connect(lambda change, staged: selected.append((change, staged)))

    item = panel.unstaged_tree.topLevelItem(0)
    panel.unstaged_tree.setCurrentItem(item)
    assert len(selected) == 1

    panel.clear_selection()
    assert panel.selected_file is None
    assert panel.unstaged_tree.currentItem() is None

    panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))
    assert len(selected) == 2
    assert selected[-1] == (CHANGES[1], False)


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


def test_untracked_selection_is_reported_without_diff(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    selected: list[tuple[ChangedFile, bool]] = []
    panel.file_selected.connect(lambda change, staged: selected.append((change, staged)))

    panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(2))

    # Diff панель не запрашивает: неотслеживаемый файл открывается пояснением
    # в DiffOverlay, а панель лишь сообщает выбор.
    assert selected == [(CHANGES[4], False)]
    assert panel.selected_file is CHANGES[4]


def test_refresh_button_requests_status_reload(qt_app) -> None:
    panel = ChangesPanel()
    requests: list[bool] = []
    panel.refresh_requested.connect(lambda: requests.append(True))

    panel.refresh_button.click()

    assert requests == [True]


# --- Stage/Unstage: доступность кнопок и сигналы ---


def test_actions_are_disabled_until_changes_are_loaded(qt_app) -> None:
    panel = ChangesPanel()

    for button in (
        panel.diff_button,
        panel.stage_button,
        panel.unstage_button,
        panel.stage_all_button,
        panel.unstage_all_button,
        panel.commit_button,
    ):
        assert not button.isEnabled()


def test_diff_button_requires_selection_and_emits_request(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    requests: list[tuple[ChangedFile, bool]] = []
    panel.diff_requested.connect(
        lambda change, staged: requests.append((change, staged))
    )

    assert not panel.diff_button.isEnabled()
    panel.diff_button.click()
    assert requests == []

    panel.staged_tree.setCurrentItem(panel.staged_tree.topLevelItem(0))
    assert panel.diff_button.isEnabled()
    panel.diff_button.click()

    assert requests == [(CHANGES[0], True)]


def test_selected_unstaged_file_can_be_staged(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    stage_requests: list[ChangedFile] = []
    panel.stage_requested.connect(stage_requests.append)

    panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))

    assert panel.stage_button.isEnabled()
    assert not panel.unstage_button.isEnabled()
    panel.stage_button.click()

    assert stage_requests == [CHANGES[1]]


def test_selected_staged_file_can_be_unstaged(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    unstage_requests: list[ChangedFile] = []
    panel.unstage_requested.connect(unstage_requests.append)

    panel.staged_tree.setCurrentItem(panel.staged_tree.topLevelItem(0))

    assert panel.unstage_button.isEnabled()
    assert not panel.stage_button.isEnabled()
    panel.unstage_button.click()

    assert unstage_requests == [CHANGES[0]]


def test_conflicted_and_untracked_files_have_expected_stage_availability(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)

    # Индексы в списке Unstaged: unstaged.txt, both.txt, untracked.txt, conflict.txt.
    panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(1))
    assert panel.selected_file is CHANGES[2]
    assert panel.stage_button.isEnabled()

    panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(2))
    assert panel.selected_file is CHANGES[4]
    assert panel.stage_button.isEnabled()

    panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(3))
    assert panel.selected_file is CHANGES[5]
    assert not panel.stage_button.isEnabled()
    # Stage All доступен: другие файлы добавить можно, конфликт будет пропущен.
    assert panel.stage_all_button.isEnabled()


def test_stage_all_and_unstage_all_emit_requests(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    requests: list[str] = []
    panel.stage_all_requested.connect(lambda: requests.append("stage"))
    panel.unstage_all_requested.connect(lambda: requests.append("unstage"))

    assert panel.stage_all_button.isEnabled()
    assert panel.unstage_all_button.isEnabled()
    panel.stage_all_button.click()
    panel.unstage_all_button.click()

    assert requests == ["stage", "unstage"]


def test_bulk_actions_are_disabled_without_matching_files(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes((ChangedFile(path="conflict.txt", is_conflicted=True),))

    assert not panel.stage_all_button.isEnabled()
    assert not panel.unstage_all_button.isEnabled()


def test_busy_state_disables_all_actions(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))
    assert panel.stage_button.isEnabled()
    assert panel.commit_button.isEnabled()

    panel.set_busy(True)

    assert not panel.diff_button.isEnabled()
    assert not panel.stage_button.isEnabled()
    assert not panel.stage_all_button.isEnabled()
    assert not panel.unstage_all_button.isEnabled()
    assert not panel.commit_button.isEnabled()
    assert "операция" in panel.commit_button.toolTip()

    panel.set_busy(False)

    assert panel.diff_button.isEnabled()
    assert panel.stage_button.isEnabled()
    assert panel.stage_all_button.isEnabled()
    assert panel.commit_button.isEnabled()


def test_select_file_switches_section_and_reports_selection(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    selected: list[tuple[ChangedFile, bool]] = []
    panel.file_selected.connect(lambda change, staged: selected.append((change, staged)))

    assert panel.select_file("both.txt", staged=True) is True
    assert selected == [(CHANGES[2], True)]
    assert panel.selected_is_staged is True

    assert panel.select_file("пропавший.txt", staged=False) is False


def test_scope_summary_and_details_show_categories_and_paths() -> None:
    files = (
        ChangedFile(path="изменённый файл.txt", unstaged_change=FileChangeType.MODIFIED),
        ChangedFile(path="удалённый.txt", unstaged_change=FileChangeType.DELETED),
        ChangedFile(path="новый.txt", is_untracked=True),
    )

    summary = scope_summary(files, staged=False)

    assert "Всего файлов: 3" in summary
    assert "изменённые: 1" in summary
    assert "удалённые: 1" in summary
    assert "не отслеживаются: 1" in summary
    details = scope_details(files, staged=False)
    assert "изменён: изменённый файл.txt" in details
    assert "не отслеживается: новый.txt" in details

    staged_summary = scope_summary(CHANGES[3:4], staged=True)
    assert "добавленные: 0" not in staged_summary
    assert "переименованные: 1" in staged_summary


# --- кнопка Commit (сообщение вводится в отдельном окне MainWindow) ---


def test_commit_button_requires_staged_changes(qt_app) -> None:
    panel = ChangesPanel()

    assert not panel.commit_button.isEnabled()
    assert "не загружены" in panel.commit_button.toolTip()

    panel.set_changes(
        (ChangedFile(path="unstaged.txt", unstaged_change=FileChangeType.MODIFIED),)
    )

    # Только unstaged-файлов недостаточно: коммит создаётся только из индекса.
    assert not panel.commit_button.isEnabled()
    assert "Нет staged-изменений" in panel.commit_button.toolTip()
    assert "добавьте файлы в индекс" in panel.commit_button.toolTip()

    panel.set_changes(
        (ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),)
    )
    assert panel.commit_button.isEnabled()
    assert "staged changes" in panel.commit_button.toolTip()

    panel.set_busy(True)
    assert not panel.commit_button.isEnabled()
    assert "операция" in panel.commit_button.toolTip()

    panel.set_busy(False)
    assert panel.commit_button.isEnabled()


def test_commit_button_emits_request_only_when_enabled(qt_app) -> None:
    panel = ChangesPanel()
    requests: list[bool] = []
    panel.commit_requested.connect(lambda: requests.append(True))

    panel.commit_button.click()
    assert requests == []

    panel.set_changes(
        (ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),)
    )
    panel.commit_button.click()

    assert requests == [True]


def test_commit_button_is_next_to_diff_in_header(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(CHANGES)
    panel.resize(1000, 400)
    panel.show()
    qt_app.processEvents()

    assert panel.commit_button.text() == "Commit"
    assert panel.diff_button.text() == "Diff"
    assert panel.commit_button.x() > panel.diff_button.x()
    assert panel.commit_button.geometry().center().y() == (
        panel.diff_button.geometry().center().y()
    )
    panel.close()


def test_commit_becomes_unavailable_when_staged_files_disappear(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(
        (ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),)
    )
    assert panel.commit_button.isEnabled()

    panel.set_changes(
        (ChangedFile(path="staged.txt", unstaged_change=FileChangeType.MODIFIED),)
    )

    assert not panel.commit_button.isEnabled()
    assert "Нет staged-изменений" in panel.commit_button.toolTip()


def test_clear_disables_commit_button(qt_app) -> None:
    panel = ChangesPanel()
    panel.set_changes(
        (ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),)
    )
    assert panel.commit_button.isEnabled()

    panel.clear()

    assert not panel.commit_button.isEnabled()
