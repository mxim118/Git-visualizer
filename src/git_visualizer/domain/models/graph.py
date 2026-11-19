"""Модели раскладки графа коммитов.

Алгоритм раскладки формирует только геометрию; создание графических элементов
(QGraphicsItem) — задача Presentation.
"""

from dataclasses import dataclass

Point = tuple[float, float]


@dataclass(frozen=True, kw_only=True)
class GraphNode:
    """Узел графа: один коммит и его положение."""

    commit_hash: str
    x: float
    y: float
    lane: int  # номер горизонтальной дорожки
    refs: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class GraphEdge:
    """Связь коммита с одним из его родителей.

    У merge-коммита рёбер столько же, сколько родителей. ``points`` — ломаная от
    дочернего коммита к родителю, включая начальную и конечную точки.
    """

    child_hash: str
    parent_hash: str
    points: tuple[Point, ...]


@dataclass(frozen=True, kw_only=True)
class GraphLayout:
    """Готовая раскладка для отображения; пустая раскладка соответствует пустой истории."""

    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
