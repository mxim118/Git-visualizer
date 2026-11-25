"""GUI-тесты GraphView: отображение, выбор, масштаб, панорамирование и пустое состояние."""

from datetime import datetime, timezone

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsPathItem

from git_visualizer.domain.enums import RefKind
from git_visualizer.domain.models import (
    Commit,
    GraphEdge,
    GraphLayout,
    GraphNode,
    RefLabel,
)
from git_visualizer.presentation.widgets.graph_view import (
    EMPTY_STATE_TEXT,
    MAX_ZOOM,
    MIN_VISIBLE_SHARE,
    MIN_ZOOM,
    GraphView,
)

NOW = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)


def h(label: str) -> str:
    return label.ljust(40, "0")


def make_commit(label: str, subject: str = "Subject") -> Commit:
    commit_hash = h(label)
    return Commit(
        hash=commit_hash,
        short_hash=commit_hash[:7],
        parent_hashes=(),
        author_name="Test Author",
        author_email="author@example.com",
        authored_at=NOW,
        committer_name="Test Author",
        committer_email="author@example.com",
        committed_at=NOW,
        subject=subject,
        body="",
    )


MERGE = h("merge")
FEATURE = h("feature")
ROOT = h("root")


def make_layout() -> GraphLayout:
    merge = GraphNode(commit_hash=MERGE, x=0.0, y=0.0, lane=0)
    feature = GraphNode(commit_hash=FEATURE, x=40.0, y=40.0, lane=1)
    root = GraphNode(commit_hash=ROOT, x=0.0, y=80.0, lane=0)
    return GraphLayout(
        nodes=(merge, feature, root),
        edges=(
            GraphEdge(
                child_hash=MERGE,
                parent_hash=FEATURE,
                points=((0.0, 0.0), (0.0, 20.0), (40.0, 20.0), (40.0, 40.0)),
            ),
            GraphEdge(
                child_hash=MERGE,
                parent_hash=ROOT,
                points=((0.0, 0.0), (0.0, 80.0)),
            ),
            GraphEdge(
                child_hash=FEATURE,
                parent_hash=ROOT,
                points=((40.0, 40.0), (40.0, 60.0), (0.0, 60.0), (0.0, 80.0)),
            ),
        ),
    )


def make_ref_labels() -> dict[str, tuple[RefLabel, ...]]:
    return {
        MERGE: (
            RefLabel(
                name="main",
                full_name="refs/heads/main",
                kind=RefKind.LOCAL_BRANCH,
                is_current=True,
            ),
        ),
        FEATURE: (
            RefLabel(
                name="feature/login",
                full_name="refs/heads/feature/login",
                kind=RefKind.LOCAL_BRANCH,
            ),
            RefLabel(
                name="origin/feature/login",
                full_name="refs/remotes/origin/feature/login",
                kind=RefKind.REMOTE_BRANCH,
            ),
        ),
        ROOT: (
            RefLabel(
                name="origin/main",
                full_name="refs/remotes/origin/main",
                kind=RefKind.REMOTE_BRANCH,
            ),
        ),
    }


def make_view(qt_app) -> GraphView:
    view = GraphView()
    view.resize(640, 480)
    view.set_graph(
        make_layout(),
        commits=(
            make_commit("merge", "Merge subject"),
            make_commit("feature"),
            make_commit("root"),
        ),
        ref_labels=make_ref_labels(),
    )
    view.show()
    qt_app.processEvents()
    return view


def test_empty_state(qt_app) -> None:
    view = GraphView()
    view.resize(400, 300)

    view.set_graph(GraphLayout())

    assert view.is_empty
    assert view.commit_count == 0
    assert view.edge_count == 0
    assert view.selected_commit_hash is None
    assert not view.fit_graph_action.isEnabled()
    assert view.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor
    assert not view.grab().isNull()
    assert EMPTY_STATE_TEXT == "История пуста"
    view.close()


def test_graph_is_rendered_with_nodes_edges_and_labels(qt_app) -> None:
    view = make_view(qt_app)

    assert view.commit_count == 3
    assert view.edge_count == 3
    assert not view.is_empty

    texts = [
        item.label.text
        for item in view.scene().items()
        if hasattr(item, "label")
    ]
    assert "HEAD → main" in texts
    assert "feature/login" in texts
    assert "origin/feature/login" in texts
    assert "origin/main" in texts

    node_items = [
        item for item in view.scene().items() if hasattr(item, "commit_hash")
    ]
    merge_item = next(item for item in node_items if item.commit_hash == MERGE)
    assert "Merge subject" in merge_item.toolTip()
    assert "Test Author" in merge_item.toolTip()

    edge_styles = [
        item.pen().style()
        for item in view.scene().items()
        if isinstance(item, QGraphicsPathItem)
    ]
    assert Qt.PenStyle.DashLine in edge_styles
    assert Qt.PenStyle.SolidLine in edge_styles
    view.close()


def test_single_click_selects_node_and_emits_hash(qt_app) -> None:
    view = make_view(qt_app)
    received: list[str] = []
    view.commit_selected.connect(received.append)

    point = view.mapFromScene(QPointF(0.0, 0.0))
    assert view.viewport().rect().contains(point)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)

    assert view.selected_commit_hash == MERGE
    assert received == [MERGE]

    assert view.select_commit(ROOT)
    assert view.selected_commit_hash == ROOT
    assert not view.select_commit(h("missing"))
    view.close()


def test_zoom_is_clamped(qt_app) -> None:
    view = make_view(qt_app)
    view.resetTransform()

    view.zoom_in()
    assert view.zoom_factor > 1.0

    for _ in range(50):
        view.zoom_in()
    assert MIN_ZOOM <= view.zoom_factor <= MAX_ZOOM + 1e-9

    for _ in range(300):
        view.zoom_out()
    assert MIN_ZOOM - 1e-9 <= view.zoom_factor <= MAX_ZOOM
    view.close()


def test_wheel_zoom_uses_the_same_limits(qt_app) -> None:
    view = make_view(qt_app)
    view.resetTransform()

    event = QWheelEvent(
        QPointF(300.0, 240.0),
        QPointF(300.0, 240.0),
        QPoint(0.0, 0.0),
        QPoint(0.0, 120.0),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    view.wheelEvent(event)

    assert view.zoom_factor > 1.0
    view.close()


def make_narrow_layout() -> GraphLayout:
    return GraphLayout(
        nodes=tuple(
            GraphNode(commit_hash=h(f"narrow{i}"), x=0.0, y=i * 40.0, lane=0)
            for i in range(6)
        )
    )


def make_dense_layout() -> GraphLayout:
    return GraphLayout(
        nodes=tuple(
            GraphNode(
                commit_hash=h(f"dense{i}"),
                x=(i % 8) * 40.0,
                y=i * 40.0,
                lane=i % 8,
            )
            for i in range(12)
        )
    )


def show_view(
    layout: GraphLayout,
    size: tuple[int, int] = (640, 480),
    ref_labels: dict[str, tuple[RefLabel, ...]] | None = None,
) -> GraphView:
    view = GraphView()
    view.resize(*size)
    view.set_graph(layout, ref_labels=ref_labels)
    view.show()
    QApplication.processEvents()
    return view


def wheel_at(view: GraphView, cursor: QPoint, *, steps: int = 1) -> None:
    for _ in range(steps):
        event = QWheelEvent(
            QPointF(cursor),
            QPointF(view.viewport().mapToGlobal(cursor)),
            QPoint(),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(view.viewport(), event)
    QApplication.processEvents()


def test_wheel_zoom_keeps_the_point_under_the_cursor(qt_app) -> None:
    """При зуме точка графа под курсором не уезжает ни по горизонтали, ни по вертикали."""
    view = show_view(make_dense_layout())
    for _ in range(5):
        view.zoom_in()
    qt_app.processEvents()

    content = view.scene().itemsBoundingRect()
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    assert content.width() > visible.width()
    assert content.height() > visible.height()

    cursor = view.mapFromScene(QPointF(120.0, 200.0))
    assert view.viewport().rect().contains(cursor)
    for _ in range(5):
        anchor = view.mapToScene(cursor)
        wheel_at(view, cursor)
        moved = view.mapFromScene(anchor)
        assert abs(moved.x() - cursor.x()) <= 1
        assert abs(moved.y() - cursor.y()) <= 1
    view.close()


def test_wheel_zoom_keeps_the_cursor_anchor_at_max_zoom(qt_app) -> None:
    """Даже на максимальном масштабе у крайней дорожки точка под курсором стоит на месте."""
    view = show_view(make_dense_layout())
    cursor = view.mapFromScene(QPointF(280.0, 280.0))
    assert view.viewport().rect().contains(cursor)

    anchor = view.mapToScene(cursor)
    wheel_at(view, cursor, steps=25)
    assert view.zoom_factor >= MAX_ZOOM - 1e-9

    moved = view.mapFromScene(anchor)
    assert abs(moved.x() - cursor.x()) <= 1
    assert abs(moved.y() - cursor.y()) <= 1
    view.close()


def test_fit_graph_action_puts_all_nodes_into_viewport(qt_app) -> None:
    view = make_view(qt_app)
    view.resetTransform()

    view.fit_graph_action.trigger()

    assert MIN_ZOOM <= view.zoom_factor <= MAX_ZOOM + 1e-9
    for node in make_layout().nodes:
        point = view.mapFromScene(QPointF(node.x, node.y))
        assert view.viewport().rect().contains(point)
    view.close()


def test_refs_fallback_marks_current_branch_and_skips_remote_head(qt_app) -> None:
    node = GraphNode(
        commit_hash=ROOT,
        x=0.0,
        y=0.0,
        lane=0,
        refs=(
            "HEAD",
            "refs/heads/main",
            "refs/remotes/origin/main",
            "refs/remotes/origin/HEAD",
        ),
    )
    view = GraphView()
    view.resize(400, 300)

    view.set_graph(GraphLayout(nodes=(node,)))

    texts = [
        item.label.text
        for item in view.scene().items()
        if hasattr(item, "label")
    ]
    assert "HEAD → main" in texts
    assert "origin/main" in texts
    assert "origin/HEAD" not in texts
    view.close()


def test_dragging_empty_space_pans_view(qt_app) -> None:
    view = make_view(qt_app)
    view.scale(4.0, 4.0)
    view.centerOn(view.scene().itemsBoundingRect().center())
    qt_app.processEvents()

    horizontal_before = view.horizontalScrollBar().value()
    vertical_before = view.verticalScrollBar().value()
    start = view.viewport().rect().topLeft() + QPoint(20, 20)
    end = start + QPoint(60, 40)

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)

    assert (
        view.horizontalScrollBar().value() != horizontal_before
        or view.verticalScrollBar().value() != vertical_before
    )
    view.close()


def test_scrollbars_are_hidden(qt_app) -> None:
    view = make_view(qt_app)

    assert (
        view.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert view.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert not view.horizontalScrollBar().isVisible()
    assert not view.verticalScrollBar().isVisible()
    view.close()


def _graph_rect(view: GraphView) -> QRectF:
    """Границы узлов графа без подписей ref."""
    rect = QRectF()
    for item in view.scene().items():
        if getattr(item, "commit_hash", None) is not None:
            rect = rect.united(item.sceneBoundingRect())
    return rect


def _assert_graph_in_view(view: GraphView) -> None:
    """Граф занимает заметную часть окна (или виден целиком, если он меньше)."""
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    graph = _graph_rect(view)
    assert visible.intersects(graph)
    overlap = visible.intersected(graph)
    assert (
        overlap.width()
        >= min(graph.width(), visible.width() * MIN_VISIBLE_SHARE) - 1.0
    )
    assert (
        overlap.height()
        >= min(graph.height(), visible.height() * MIN_VISIBLE_SHARE) - 1.0
    )


def test_graph_can_be_panned_when_it_fits_the_viewport(qt_app) -> None:
    """Граф, вписанный в окно, всё равно можно двигать мышью в обе стороны."""
    view = make_view(qt_app)
    assert view.mapToScene(view.viewport().rect()).boundingRect().contains(
        view.scene().itemsBoundingRect()
    )

    center = view.viewport().rect().center()
    before = view.mapToScene(center)
    start = view.viewport().rect().topLeft() + QPoint(20, 20)

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    assert view.viewport().cursor().shape() == Qt.CursorShape.ClosedHandCursor
    QTest.mouseMove(view.viewport(), start + QPoint(-70, -50))
    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, pos=start + QPoint(-70, -50)
    )

    after = view.mapToScene(center)
    assert after.x() > before.x()
    assert after.y() > before.y()
    assert view.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor
    _assert_graph_in_view(view)
    view.close()


def test_dragging_from_a_node_pans_and_keeps_the_click_selection(qt_app) -> None:
    view = make_view(qt_app)
    node_point = view.mapFromScene(QPointF(0.0, 0.0))
    center = view.viewport().rect().center()
    before = view.mapToScene(center)

    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=node_point)
    QTest.mouseMove(view.viewport(), node_point + QPoint(60, 40))
    QTest.mouseRelease(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=node_point + QPoint(60, 40),
    )

    assert view.selected_commit_hash == MERGE
    after = view.mapToScene(center)
    assert after.x() < before.x()
    assert after.y() < before.y()
    node_item = next(
        item
        for item in view.scene().items()
        if getattr(item, "commit_hash", None) == MERGE
    )
    assert node_item.pos() == QPointF(0.0, 0.0)
    view.close()


def _drag(view: GraphView, offset: QPoint) -> None:
    start = view.viewport().rect().center()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), start + offset)
    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, pos=start + offset
    )


def test_panning_works_at_every_zoom_level(qt_app) -> None:
    view = make_view(qt_app)

    def pan_is_available() -> bool:
        center = view.viewport().rect().center()
        before = view.mapToScene(center)
        _drag(view, QPoint(-40, -30))
        return view.mapToScene(center) != before

    assert pan_is_available()

    for _ in range(12):
        view.zoom_out()
    assert pan_is_available()

    for _ in range(30):
        view.zoom_in()
    assert pan_is_available()
    view.close()


def test_dragging_far_keeps_the_graph_in_view(qt_app) -> None:
    view = make_view(qt_app)

    for offset in (QPoint(400, 300), QPoint(-800, -600), QPoint(400, 300)):
        _drag(view, offset)
        _assert_graph_in_view(view)
    view.close()


def test_dragging_a_large_graph_stops_at_the_boundary(qt_app) -> None:
    view = make_view(qt_app)
    view.resetTransform()
    view.scale(6.0, 6.0)
    view.centerOn(view.scene().itemsBoundingRect().center())
    qt_app.processEvents()

    content = view.scene().itemsBoundingRect()
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    assert content.width() > visible.width()
    assert content.height() > visible.height()

    _drag(view, QPoint(2000, 2000))
    _assert_graph_in_view(view)
    at_boundary = view.mapToScene(view.viewport().rect().center())

    _drag(view, QPoint(2000, 2000))
    _assert_graph_in_view(view)
    after = view.mapToScene(view.viewport().rect().center())
    assert abs(after.x() - at_boundary.x()) < 0.01
    assert abs(after.y() - at_boundary.y()) < 0.01
    view.close()


def test_resizing_the_view_keeps_the_visible_center(qt_app) -> None:
    view = make_view(qt_app)
    before = view.mapToScene(view.viewport().rect().center())

    view.resize(900, 620)
    qt_app.processEvents()

    after = view.mapToScene(view.viewport().rect().center())
    assert abs(after.x() - before.x()) < 1.0
    assert abs(after.y() - before.y()) < 1.0
    view.close()


def test_zoom_does_not_recenter_a_narrow_graph(qt_app) -> None:
    """Узкий граф зумится под курсором, а не притягивается к центру окна."""
    view = show_view(make_narrow_layout())
    cursor = view.mapFromScene(QPointF(0.0, 80.0))
    assert view.viewport().rect().contains(cursor)
    anchor = view.mapToScene(cursor)

    wheel_at(view, cursor, steps=5)

    moved = view.mapFromScene(anchor)
    assert abs(moved.x() - cursor.x()) <= 1
    assert abs(moved.y() - cursor.y()) <= 1
    view.close()


def test_wheel_zoom_off_the_graph_keeps_it_in_view(qt_app) -> None:
    """Если курсор вне графа, якорить нечего, но граф не должен уйти из окна."""
    view = show_view(make_narrow_layout())
    cursor = QPoint(20, 20)
    assert not _graph_rect(view).contains(view.mapToScene(cursor))

    wheel_at(view, cursor, steps=8)

    _assert_graph_in_view(view)
    view.close()


def test_panning_stays_available_after_zooming_the_graph(qt_app) -> None:
    """После зума под курсором граф по-прежнему можно двигать в обе стороны."""
    view = show_view(make_narrow_layout())
    cursor = view.mapFromScene(QPointF(0.0, 80.0))
    wheel_at(view, cursor, steps=5)

    center = view.viewport().rect().center()
    start = view.mapToScene(center).x()
    _drag(view, QPoint(-80, 0))
    left = view.mapToScene(center).x()
    _drag(view, QPoint(80, 0))
    right = view.mapToScene(center).x()

    assert left > start
    assert right < left
    _assert_graph_in_view(view)
    view.close()


def test_zoom_after_a_horizontal_pan_keeps_the_cursor_anchor(qt_app) -> None:
    view = show_view(make_dense_layout())
    for _ in range(5):
        view.zoom_in()
    _drag(view, QPoint(-200, 0))

    cursor = view.mapFromScene(QPointF(280.0, 280.0))
    assert view.viewport().rect().contains(cursor)
    anchor = view.mapToScene(cursor)
    wheel_at(view, cursor)

    moved = view.mapFromScene(anchor)
    assert abs(moved.x() - cursor.x()) <= 1
    assert abs(moved.y() - cursor.y()) <= 1
    _assert_graph_in_view(view)
    view.close()


def test_view_already_outside_the_pan_limits_is_not_pulled_back(qt_app) -> None:
    """Зум у края графа может оставить окно за пределами перетаскивания.

    Возвращать его скачком нельзя: разрешено только не уходить дальше.
    """
    view = show_view(make_dense_layout())
    graph = _graph_rect(view)
    scale = view.zoom_factor
    viewport = view.viewport().rect()
    _, high = view._clamp_bounds(
        graph.left(), graph.right(), viewport.width() / scale
    )
    outside = QPointF(high + 100.0, graph.center().y())

    assert view._clamp_view_origin(outside).x() < outside.x()
    assert view._clamp_view_origin(outside, current=outside) == outside
    view.close()


def make_long_label_refs() -> dict[str, tuple[RefLabel, ...]]:
    return {
        h("narrow0"): (
            RefLabel(
                name="feature/very-long-branch-name-that-does-not-fit",
                full_name=(
                    "refs/heads/feature/very-long-branch-name-that-does-not-fit"
                ),
                kind=RefKind.LOCAL_BRANCH,
                is_current=True,
            ),
        ),
    }


def test_zoom_over_a_ref_label_keeps_the_label_under_the_cursor(qt_app) -> None:
    """Подпись ref — часть видимого графа, зум и над ней идёт под курсором."""
    view = show_view(make_narrow_layout(), ref_labels=make_long_label_refs())
    label_item = next(
        item for item in view.scene().items() if hasattr(item, "label")
    )
    cursor = view.mapFromScene(label_item.sceneBoundingRect().center())
    assert view.viewport().rect().contains(cursor)
    anchor = view.mapToScene(cursor)

    wheel_at(view, cursor, steps=3)

    moved = view.mapFromScene(anchor)
    assert abs(moved.x() - cursor.x()) <= 1
    assert abs(moved.y() - cursor.y()) <= 1
    view.close()


def test_long_ref_labels_do_not_push_the_graph_out_of_view(qt_app) -> None:
    """Длинные подписи не дают утащить сами коммиты за край окна."""
    view = show_view(make_narrow_layout(), ref_labels=make_long_label_refs())
    content = view.scene().itemsBoundingRect()
    graph = _graph_rect(view)
    assert content.width() > graph.width() * 1.5

    wheel_at(view, QPoint(80, 120), steps=12)
    _assert_graph_in_view(view)

    for offset in (QPoint(3000, 0), QPoint(-6000, 0)):
        _drag(view, offset)
        _assert_graph_in_view(view)
    view.close()
