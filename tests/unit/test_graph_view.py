"""GUI-тесты GraphView: отображение, выбор, масштаб, Fit Graph и пустое состояние."""

from datetime import datetime, timezone

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QGraphicsPathItem

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


def test_wheel_zoom_keeps_scene_point_under_cursor(qt_app) -> None:
    view = make_view(qt_app)
    view.resetTransform()
    # Точка под курсором сохраняется, когда сцену можно прокручивать.
    view.setSceneRect(QRectF(-500.0, -500.0, 1000.0, 1000.0))
    view.centerOn(0.0, 0.0)
    qt_app.processEvents()
    cursor = QPoint(500, 300)
    before = view.mapToScene(cursor)

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
    qt_app.sendEvent(view.viewport(), event)
    qt_app.processEvents()

    after = view.mapToScene(cursor)
    assert abs(after.x() - before.x()) < 0.01
    assert abs(after.y() - before.y()) < 0.01
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
