"""GraphService: lane-based раскладка истории без Git и без Qt."""

from datetime import datetime, timezone

import pytest

from git_visualizer.application.services.graph_service import (
    LANE_WIDTH,
    ROW_HEIGHT,
    GraphService,
)
from git_visualizer.domain.models import Commit, GraphEdge, GraphLayout, GraphNode

NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def commit_id(label: str) -> str:
    """Предсказуемый полный hash длиной 40 символов."""
    return label.ljust(40, "0")


def make_commit(
    label: str,
    parents: tuple[str, ...] = (),
    refs: tuple[str, ...] = (),
) -> Commit:
    return Commit(
        hash=commit_id(label),
        short_hash=commit_id(label)[:7],
        parent_hashes=tuple(commit_id(parent) for parent in parents),
        author_name="Author",
        author_email="author@example.com",
        authored_at=NOW,
        committer_name="Author",
        committer_email="author@example.com",
        committed_at=NOW,
        subject=f"commit {label}",
        body="",
        refs=refs,
    )


def build_layout(*commits: Commit) -> GraphLayout:
    return GraphService().build_layout(commits)


def node_by_hash(layout: GraphLayout) -> dict[str, GraphNode]:
    return {node.commit_hash: node for node in layout.nodes}


def edge_by_pair(layout: GraphLayout) -> dict[tuple[str, str], GraphEdge]:
    return {(edge.child_hash, edge.parent_hash): edge for edge in layout.edges}


def assert_edge_endpoints_match_nodes(layout: GraphLayout) -> None:
    nodes = node_by_hash(layout)
    for edge in layout.edges:
        child = nodes[edge.child_hash]
        parent = nodes[edge.parent_hash]
        assert edge.points[0] == (child.x, child.y)
        assert edge.points[-1] == (parent.x, parent.y)


def test_empty_history_has_empty_layout() -> None:
    assert build_layout() == GraphLayout()


def test_linear_history_uses_one_lane_and_straight_edges() -> None:
    head = make_commit("head", ("middle",))
    middle = make_commit("middle", ("root",))
    root = make_commit("root")

    layout = build_layout(head, middle, root)

    assert [node.commit_hash for node in layout.nodes] == [
        commit_id("head"),
        commit_id("middle"),
        commit_id("root"),
    ]
    assert [node.y for node in layout.nodes] == [0.0, ROW_HEIGHT, 2 * ROW_HEIGHT]
    assert [node.lane for node in layout.nodes] == [0, 0, 0]
    assert [node.x for node in layout.nodes] == [0.0, 0.0, 0.0]

    assert len(layout.edges) == 2
    assert layout.edges[0].points == (
        (0.0, 0.0),
        (0.0, ROW_HEIGHT),
    )
    assert layout.edges[1].points == (
        (0.0, ROW_HEIGHT),
        (0.0, 2 * ROW_HEIGHT),
    )
    assert_edge_endpoints_match_nodes(layout)


def test_root_commit_has_no_edges() -> None:
    layout = build_layout(make_commit("root"))

    assert len(layout.nodes) == 1
    assert layout.nodes[0].lane == 0
    assert (layout.nodes[0].x, layout.nodes[0].y) == (0.0, 0.0)
    assert layout.edges == ()


def test_two_branches_with_merge() -> None:
    merge = make_commit("merge", ("left", "right"))
    left = make_commit("left", ("root",))
    right = make_commit("right", ("root",))
    root = make_commit("root")

    layout = build_layout(merge, left, right, root)

    assert len(layout.nodes) == 4
    assert len({node.commit_hash for node in layout.nodes}) == 4
    lanes = {node.commit_hash: node.lane for node in layout.nodes}
    assert lanes == {
        commit_id("merge"): 0,
        commit_id("left"): 0,
        commit_id("right"): 1,
        commit_id("root"): 0,
    }

    edges = edge_by_pair(layout)
    assert len(edges) == 4
    assert edges[(commit_id("merge"), commit_id("left"))].points == (
        (0.0, 0.0),
        (0.0, ROW_HEIGHT),
    )
    assert edges[(commit_id("merge"), commit_id("right"))].points == (
        (0.0, 0.0),
        (0.0, ROW_HEIGHT / 2),
        (LANE_WIDTH, ROW_HEIGHT / 2),
        (LANE_WIDTH, 2 * ROW_HEIGHT),
    )
    assert edges[(commit_id("left"), commit_id("root"))].points == (
        (0.0, ROW_HEIGHT),
        (0.0, 3 * ROW_HEIGHT),
    )
    assert edges[(commit_id("right"), commit_id("root"))].points == (
        (LANE_WIDTH, 2 * ROW_HEIGHT),
        (LANE_WIDTH, 2.5 * ROW_HEIGHT),
        (0.0, 2.5 * ROW_HEIGHT),
        (0.0, 3 * ROW_HEIGHT),
    )
    assert_edge_endpoints_match_nodes(layout)


def test_several_merges() -> None:
    second_merge = make_commit("merge2", ("left2", "right2"))
    left2 = make_commit("left2", ("merge1",))
    right2 = make_commit("right2", ("merge1",))
    first_merge = make_commit("merge1", ("left1", "right1"))
    left1 = make_commit("left1", ("root",))
    right1 = make_commit("right1", ("root",))
    root = make_commit("root")

    commits = (second_merge, left2, right2, first_merge, left1, right1, root)
    layout = build_layout(*commits)

    assert len(layout.nodes) == len(commits)
    assert len({node.commit_hash for node in layout.nodes}) == len(commits)
    assert len(layout.edges) == 8

    lanes = {node.commit_hash: node.lane for node in layout.nodes}
    assert lanes == {
        commit_id("merge2"): 0,
        commit_id("left2"): 0,
        commit_id("right2"): 1,
        commit_id("merge1"): 0,
        commit_id("left1"): 0,
        commit_id("right1"): 1,
        commit_id("root"): 0,
    }

    edges = edge_by_pair(layout)
    assert (commit_id("right2"), commit_id("merge1")) in edges
    assert (commit_id("right1"), commit_id("root")) in edges
    # Обе ветки сходятся в левую дорожку, поэтому последняя точка ребра — узел.
    assert edges[(commit_id("right2"), commit_id("merge1"))].points[-1] == (
        0.0,
        3 * ROW_HEIGHT,
    )
    assert edges[(commit_id("right1"), commit_id("root"))].points[-1] == (
        0.0,
        6 * ROW_HEIGHT,
    )
    assert_edge_endpoints_match_nodes(layout)


def test_octopus_merge_with_three_parents() -> None:
    merge = make_commit("merge", ("a", "b", "c"))
    a = make_commit("a", ("root",))
    b = make_commit("b", ("root",))
    c = make_commit("c", ("root",))
    root = make_commit("root")

    layout = build_layout(merge, a, b, c, root)

    assert len(layout.nodes) == 5
    assert len(layout.edges) == 6
    lanes = {node.commit_hash: node.lane for node in layout.nodes}
    assert lanes == {
        commit_id("merge"): 0,
        commit_id("a"): 0,
        commit_id("b"): 1,
        commit_id("c"): 2,
        commit_id("root"): 0,
    }
    assert_edge_endpoints_match_nodes(layout)


def test_missing_parent_edge_is_omitted() -> None:
    head = make_commit("head", ("middle",))
    middle = make_commit("middle", ("missing",))

    layout = build_layout(head, middle)

    assert len(layout.nodes) == 2
    assert len(layout.edges) == 1
    assert layout.edges[0].child_hash == commit_id("head")
    assert layout.edges[0].parent_hash == commit_id("middle")
    assert all(edge.parent_hash != commit_id("missing") for edge in layout.edges)


def test_missing_first_parent_keeps_next_available_parent_on_the_lane() -> None:
    # Merge-коммит, у которого первый родитель не загружен, а второй загружен:
    # линия продолжается по второму родителю, ребро к отсутствующему не рисуется.
    merge = make_commit("merge", ("missing", "present"))
    present = make_commit("present")

    layout = build_layout(merge, present)

    assert len(layout.nodes) == 2
    assert {node.lane for node in layout.nodes} == {0}
    assert len(layout.edges) == 1
    assert layout.edges[0].parent_hash == commit_id("present")
    assert layout.edges[0].points == ((0.0, 0.0), (0.0, ROW_HEIGHT))


def test_merge_whose_first_parent_is_already_active() -> None:
    # Первый родитель уже ожидается другой линией: merge сходится в неё,
    # а второй родитель получает дорожку справа.
    head = make_commit("head", ("a",))
    merge = make_commit("merge", ("a", "b"))
    a = make_commit("a", ("root",))
    b = make_commit("b", ("root",))
    root = make_commit("root")

    layout = build_layout(head, merge, a, b, root)

    lanes = {node.commit_hash: node.lane for node in layout.nodes}
    assert lanes == {
        commit_id("head"): 0,
        commit_id("merge"): 1,
        commit_id("a"): 0,
        commit_id("b"): 2,
        commit_id("root"): 0,
    }
    edges = edge_by_pair(layout)
    assert edges[(commit_id("merge"), commit_id("a"))].points[0] == (LANE_WIDTH, ROW_HEIGHT)
    assert edges[(commit_id("merge"), commit_id("a"))].points[-1] == (0.0, 2 * ROW_HEIGHT)
    assert edges[(commit_id("merge"), commit_id("b"))].points[0] == (LANE_WIDTH, ROW_HEIGHT)
    assert edges[(commit_id("merge"), commit_id("b"))].points[-1] == (2 * LANE_WIDTH, 3 * ROW_HEIGHT)
    assert_edge_endpoints_match_nodes(layout)


def test_multiple_refs_keep_one_node_and_all_refs() -> None:
    refs = ("HEAD", "refs/heads/main", "refs/remotes/origin/main")
    head = make_commit("head", ("root",), refs=refs)
    root = make_commit("root")

    layout = build_layout(head, root)

    assert len(layout.nodes) == 2
    head_node = node_by_hash(layout)[commit_id("head")]
    assert head_node.refs == refs
    assert node_by_hash(layout)[commit_id("root")].refs == ()


def test_layout_is_deterministic_for_the_same_history() -> None:
    commits = (
        make_commit("merge", ("a", "b")),
        make_commit("a", ("root",)),
        make_commit("b", ("root",)),
        make_commit("root"),
    )

    first = GraphService().build_layout(commits)
    second = GraphService().build_layout(tuple(commits))

    assert first == second
    assert first.nodes == second.nodes
    assert first.edges == second.edges


def test_duplicate_commit_hash_is_rejected() -> None:
    duplicate = make_commit("a")

    with pytest.raises(ValueError, match="duplicate"):
        build_layout(duplicate, duplicate)


def test_parent_before_child_is_rejected_as_not_topological() -> None:
    parent = make_commit("parent")
    child = make_commit("child", ("parent",))

    with pytest.raises(ValueError, match="topologically"):
        build_layout(parent, child)
