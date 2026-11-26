"""Тесты рабочей области: панели не могут свернуть граф до нулевой ширины,
а свернутые сплиттером области возвращаются кнопками на их месте."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.domain.models import Branch, Commit, Repository
from git_visualizer.presentation.controllers.repository_controller import WorkspaceData
from git_visualizer.presentation.widgets.workspace_page import WorkspacePage


def test_body_splitter_cannot_collapse_panels_and_graph(qt_app) -> None:
    page = WorkspacePage()
    try:
        page.resize(1280, 800)
        page.show()
        qt_app.processEvents()

        splitter = page.body_splitter
        assert splitter.childrenCollapsible() is False

        # Ручки: 1 — между ветками и графом, 2 — между графом и деталями.
        # Доводим каждую до края: граф не должен исчезать.
        for handle, position in ((1, 5000), (2, 0)):
            splitter.setSizes([240, 680, 420])
            splitter.moveSplitter(position, handle)
            qt_app.processEvents()
            widths = splitter.sizes()
            assert widths[1] >= page.graph_view.minimumWidth()
            assert widths[0] >= page.branches_panel.minimumWidth()
            assert widths[2] >= page.details_panel.minimumWidth()
    finally:
        page.close()


def test_collapsed_top_area_shows_button_to_restore_graph(qt_app) -> None:
    page = WorkspacePage()
    try:
        page.resize(1280, 800)
        page.show()
        qt_app.processEvents()

        splitter = page.main_splitter
        normal_sizes = list(splitter.sizes())
        assert page.body_restore_button.isHidden()
        assert page.changes_restore_button.isHidden()

        # Ручку утянули до верхнего края: верхняя область (граф и панели)
        # исчезла, но на ее месте появилась кнопка возврата.
        splitter.setSizes([0, sum(normal_sizes)])
        qt_app.processEvents()
        assert page.body_splitter.height() == 0
        assert page.body_restore_button.isVisible()
        assert page.changes_restore_button.isHidden()

        area = splitter.geometry()
        button = page.body_restore_button.geometry()
        assert button.top() == area.top() + 2
        assert abs(button.center().x() - area.center().x()) <= 1

        # Кнопка возвращает прежнее деление и сама исчезает.
        page.body_restore_button.click()
        qt_app.processEvents()
        assert list(splitter.sizes()) == normal_sizes
        assert page.body_restore_button.isHidden()
        assert page.changes_restore_button.isHidden()
    finally:
        page.close()


def test_collapsed_changes_panel_shows_button_to_restore_it(qt_app) -> None:
    page = WorkspacePage()
    try:
        page.resize(1280, 800)
        page.show()
        qt_app.processEvents()

        splitter = page.main_splitter
        normal_sizes = list(splitter.sizes())

        # Явный минимум высоты не дает setSizes свернуть панель в offscreen;
        # при перетаскивании ручки Qt сворачивает панель и с минимумом.
        page.changes_panel.setMinimumHeight(0)
        splitter.setSizes([sum(normal_sizes), 0])
        qt_app.processEvents()
        assert page.changes_panel.height() == 0
        assert page.body_restore_button.isHidden()
        assert page.changes_restore_button.isVisible()

        area = splitter.geometry()
        button = page.changes_restore_button.geometry()
        assert button.bottom() == area.bottom() - 2
        assert abs(button.center().x() - area.center().x()) <= 1

        # Кнопка возвращает нижнюю панель с прежней высотой.
        page.changes_restore_button.click()
        qt_app.processEvents()
        assert list(splitter.sizes()) == normal_sizes
        assert page.changes_restore_button.isHidden()
        assert page.body_restore_button.isHidden()
    finally:
        page.close()


def test_stretched_graph_hides_changes_panel_and_shows_button(qt_app) -> None:
    page = WorkspacePage()
    try:
        page.resize(1280, 800)
        page.show()
        qt_app.processEvents()

        splitter = page.main_splitter
        normal_sizes = list(splitter.sizes())
        panel = page.changes_panel

        # Реальный сценарий: граф растянули до упора вниз. Панель Changes с явным
        # минимумом Qt не сворачивает в ноль, а вытесняет за нижний край: высота
        # остается равной минимуму, флаг видимости не сбрасывается, но геометрия
        # уходит за границу сплиттера и обрезается родителем.
        splitter.setSizes([sum(normal_sizes), 0])
        qt_app.processEvents()
        assert panel.height() > 0
        assert panel.isVisibleTo(page)
        assert not splitter.rect().contains(panel.geometry())

        assert page.changes_restore_button.isVisible()
        assert page.body_restore_button.isHidden()

        # Кнопка возвращает прежнее деление, панель снова целиком внутри области.
        page.changes_restore_button.click()
        qt_app.processEvents()
        assert list(splitter.sizes()) == normal_sizes
        assert splitter.rect().contains(panel.geometry())
        assert page.changes_restore_button.isHidden()
        assert page.body_restore_button.isHidden()
    finally:
        page.close()


def test_restore_button_stays_at_edge_after_window_resize(qt_app) -> None:
    page = WorkspacePage()
    try:
        page.resize(1280, 800)
        page.show()
        qt_app.processEvents()

        splitter = page.main_splitter
        total = sum(splitter.sizes())
        page.changes_panel.setMinimumHeight(0)
        splitter.setSizes([total, 0])
        qt_app.processEvents()
        assert page.changes_restore_button.isVisible()

        # Окно изменили при свернутой панели: кнопка остается у нижнего края.
        page.resize(1000, 600)
        qt_app.processEvents()
        area = splitter.geometry()
        button = page.changes_restore_button.geometry()
        assert button.bottom() == area.bottom() - 2
        assert abs(button.center().x() - area.center().x()) <= 1
        assert page.changes_restore_button.isVisible()
    finally:
        page.close()


def test_top_bar_remote_settings_signal_is_forwarded(qt_app) -> None:
    page = WorkspacePage()
    try:
        page.set_repository(
            Repository(
                path=Path("C:/repo"),
                name="repo",
                current_branch="main",
                head_commit="a" * 40,
            )
        )
        calls: list[bool] = []
        page.remote_settings_requested.connect(lambda: calls.append(True))

        page.top_bar.remote_settings_button.click()

        assert calls == [True]
    finally:
        page.close()


# --- ограниченная история: уведомление и сохранение выбора (FR-HIST-07) ---


def make_commit(label: str, parents: tuple[str, ...] = ()) -> Commit:
    commit_hash = label.ljust(40, "0")
    return Commit(
        hash=commit_hash,
        short_hash=commit_hash[:7],
        parent_hashes=parents,
        author_name="Test Author",
        author_email="author@example.com",
        authored_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        committer_name="Test Author",
        committer_email="author@example.com",
        committed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        subject=f"commit {label}",
        body="",
    )


def workspace_data(
    commits: tuple[Commit, ...], *, has_more: bool
) -> WorkspaceData:
    repository = Repository(
        path=Path("C:/repo"),
        name="repo",
        current_branch="main",
        head_commit=commits[0].hash if commits else None,
    )
    return WorkspaceData(
        repository=repository,
        commits=commits,
        branches=(
            Branch(
                name="main",
                is_remote=False,
                is_current=True,
                commit_hash=commits[0].hash if commits else None,
            ),
        ),
        remotes=(),
        ref_labels={},
        layout=GraphService().build_layout(commits),
        has_more_history=has_more,
    )


def linear_history(*labels: str) -> tuple[Commit, ...]:
    ordered: list[Commit] = []
    for label in reversed(labels):
        parents = (ordered[-1].hash,) if ordered else ()
        ordered.append(make_commit(label, parents=parents))
    return tuple(reversed(ordered))


def test_history_notice_is_shown_only_for_truncated_history(qt_app) -> None:
    page = WorkspacePage()
    try:
        page.set_workspace_data(workspace_data(linear_history("a", "b"), has_more=False))
        assert page.history_notice.isHidden()

        page.set_workspace_data(workspace_data(linear_history("a", "b"), has_more=True))
        assert not page.history_notice.isHidden()
        assert "Показаны последние 2 коммитов" in page.history_notice.label.text()

        page.set_workspace_data(workspace_data(linear_history("a", "b"), has_more=False))
        assert page.history_notice.isHidden()
    finally:
        page.close()


def test_history_loading_blocks_repeat_page_requests(qt_app) -> None:
    page = WorkspacePage()
    try:
        page.set_workspace_data(workspace_data(linear_history("a", "b"), has_more=True))
        requested: list[bool] = []
        page.older_history_requested.connect(lambda: requested.append(True))

        page.set_history_loading(True)
        assert page.history_notice.is_loading
        assert not page.history_notice.button.isEnabled()
        assert "Загрузка более старой истории" in page.history_notice.label.text()

        page.set_history_loading(False)
        assert not page.history_notice.is_loading
        assert page.history_notice.button.isEnabled()
        page.history_notice.button.click()
        assert requested == [True]
    finally:
        page.close()


def test_appended_history_keeps_selected_commit_and_file(qt_app) -> None:
    commits = linear_history("a", "b", "c")
    newest, middle, oldest = commits
    page = WorkspacePage()
    try:
        page.set_workspace_data(workspace_data((newest, middle), has_more=True))
        page.graph_view.commit_selected.emit(middle.hash)
        assert page.selected_commit_hash == middle.hash

        page.set_workspace_data(
            workspace_data(commits, has_more=False), preserve_selection=True
        )

        assert page.workspace_data is not None
        assert page.workspace_data.commits == commits
        assert page.selected_commit_hash == middle.hash
        assert page.details_panel.current_commit == middle
        assert page.graph_view.commit_count == 3
        assert page.history_notice.isHidden()
    finally:
        page.close()


def test_regular_reload_resets_selection(qt_app) -> None:
    commits = linear_history("a", "b", "c", "d")
    page = WorkspacePage()
    try:
        page.set_workspace_data(workspace_data(commits, has_more=True))
        page.graph_view.commit_selected.emit(commits[1].hash)

        page.set_workspace_data(workspace_data(commits, has_more=True))

        assert page.selected_commit_hash is None
        assert page.details_panel.current_commit is None
    finally:
        page.close()


def test_clear_hides_history_notice(qt_app) -> None:
    page = WorkspacePage()
    try:
        page.set_workspace_data(workspace_data(linear_history("a", "b"), has_more=True))
        assert not page.history_notice.isHidden()

        page.clear()

        assert page.history_notice.isHidden()
        assert page.workspace_data is None
    finally:
        page.close()

