"""Тесты рабочей области: панели не могут свернуть граф до нулевой ширины,
а свернутые сплиттером области возвращаются кнопками на их месте."""

from __future__ import annotations

from pathlib import Path

from git_visualizer.domain.models import Repository
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
