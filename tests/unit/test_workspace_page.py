"""Тесты рабочей области: панели не могут свернуть граф до нулевой ширины."""

from __future__ import annotations

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
