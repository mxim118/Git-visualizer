"""Построение lane-based раскладки графа истории.

Модуль не зависит от Qt и не создаёт графических элементов: результатом являются
только координаты узлов и рёбер, которые Presentation отображает (ТЗ 5.3.1, 10.3).

Алгоритм работает в два прохода:

1. назначает каждому коммиту дорожку (lane), обходя историю сверху вниз;
2. строит узлы и рёбра по уже известным дорожкам.

Второй проход нужен потому, что несколько линий могут сойтись на одном коммите:
дорогая линия должна указывать на итоговую дорожку узла, а не на ту, которая была
активна в момент обработки дочернего коммита.

Ограниченная история: если родитель отсутствует среди загруженных коммитов, ребро к
нему не создаётся. ``GraphEdge`` описывает реальную связь двух известных узлов; для
отсутствующего родителя нет координаты, а добавление признака «обрезано» изменило бы
модель и потребовало поддержки в GUI. Такой коммит остаётся визуальной границей
загруженного окна истории (аналогично root-коммиту).
"""

from collections.abc import Iterator, Mapping, Sequence

from git_visualizer.domain.models import Commit, GraphEdge, GraphLayout, GraphNode, Point

# Шаг сетки раскладки. Presentation может масштабировать готовые координаты.
ROW_HEIGHT = 40.0
LANE_WIDTH = 40.0


class GraphService:
    """Строит раскладку графа по топологически упорядоченной истории коммитов."""

    def build_layout(self, commits: Sequence[Commit]) -> GraphLayout:
        """Раскладывает историю по дорожкам и вычисляет координаты.

        :param commits: коммиты в топологическом порядке (потомок раньше родителей),
            каждый hash встречается один раз. Порядок сохраняется: он задаёт
            вертикальные позиции узлов.

        Узел создаётся ровно один на коммит; ``refs`` переносятся без изменений.
        Ребро создаётся для каждого родителя, который присутствует в ``commits``.
        Порядок узлов — входной, порядок рёбер — по коммитам и их родителям.

        :raises ValueError: есть повторяющийся hash или родитель расположен в списке
            раньше своего потомка (нарушен топологический порядок).
        """
        if not commits:
            return GraphLayout()

        index = _validate_history(commits)
        lanes_by_hash = _assign_lanes(commits, index)

        nodes = tuple(
            GraphNode(
                commit_hash=commit.hash,
                x=lanes_by_hash[commit.hash] * LANE_WIDTH,
                y=row * ROW_HEIGHT,
                lane=lanes_by_hash[commit.hash],
                refs=tuple(commit.refs),
            )
            for row, commit in enumerate(commits)
        )
        edges = tuple(
            edge
            for row, commit in enumerate(commits)
            for edge in _edges_for_commit(commit, row, index, lanes_by_hash)
        )
        return GraphLayout(nodes=nodes, edges=edges)


def _validate_history(commits: Sequence[Commit]) -> dict[str, int]:
    """Проверяет предусловия алгоритма и возвращает hash → номер строки."""
    index: dict[str, int] = {}
    for row, commit in enumerate(commits):
        if commit.hash in index:
            raise ValueError(f"Commit history contains duplicate hash {commit.hash!r}")
        index[commit.hash] = row

    for row, commit in enumerate(commits):
        for parent in commit.parent_hashes:
            parent_row = index.get(parent)
            if parent_row is not None and parent_row <= row:
                raise ValueError(
                    "Commit history is not topologically ordered: "
                    f"parent {parent!r} of {commit.hash!r} is at row {parent_row}, "
                    f"before its child at row {row}"
                )
    return index


def _assign_lanes(commits: Sequence[Commit], index: Mapping[str, int]) -> dict[str, int]:
    """Первый проход: назначает дорожку каждому коммиту.

    ``lanes[lane]`` — hash коммита, которого эта линия ждёт на следующей строке;
    ``None`` — дорожка свободна. Коммит, которого ждут несколько дорожек, ставится
    на самую левую, остальные линии считаются сошедшимися и освобождаются.
    """
    lanes: list[str | None] = []
    lanes_by_hash: dict[str, int] = {}

    for row, commit in enumerate(commits):
        incoming = [
            lane for lane, expected in enumerate(lanes) if expected == commit.hash
        ]
        if incoming:
            lane = min(incoming)
            for waiting_lane in incoming:
                lanes[waiting_lane] = None
        else:
            lane = _free_lane(lanes)
        lanes_by_hash[commit.hash] = lane

        first_available_parent = True
        seen_parents: set[str] = set()
        for parent in commit.parent_hashes:
            if parent in seen_parents:
                continue
            seen_parents.add(parent)
            if parent not in index:
                # Родитель за пределами загруженной истории: линия здесь заканчивается.
                continue

            if _active_lane(lanes, parent) is not None:
                # Линия родителя уже существует: ребро сойдётся в неё, текущая
                # дорожка после этого свободна.
                first_available_parent = False
                continue

            if first_available_parent and lanes[lane] is None:
                lanes[lane] = parent
            else:
                parent_lane = _free_lane_to_the_right(lanes, lane)
                lanes[parent_lane] = parent
            first_available_parent = False

    return lanes_by_hash


def _active_lane(lanes: Sequence[str | None], commit_hash: str) -> int | None:
    """Дорожка, которая уже ждёт ``commit_hash``, либо ``None``."""
    for lane, expected in enumerate(lanes):
        if expected == commit_hash:
            return lane
    return None


def _free_lane(lanes: list[str | None]) -> int:
    """Самая левая свободная дорожка; если свободных нет — новая справа."""
    for lane, expected in enumerate(lanes):
        if expected is None:
            return lane
    lanes.append(None)
    return len(lanes) - 1


def _free_lane_to_the_right(lanes: list[str | None], start: int) -> int:
    """Ближайшая свободная дорожка справа от ``start``; иначе новая справа."""
    for lane in range(start + 1, len(lanes)):
        if lanes[lane] is None:
            return lane
    lanes.append(None)
    return len(lanes) - 1


def _edges_for_commit(
    commit: Commit,
    row: int,
    index: Mapping[str, int],
    lanes_by_hash: Mapping[str, int],
) -> Iterator[GraphEdge]:
    """Рёбра от коммита к его присутствующим родителям (по одному на родителя)."""
    child_lane = lanes_by_hash[commit.hash]
    child_x = child_lane * LANE_WIDTH
    child_y = row * ROW_HEIGHT
    seen_parents: set[str] = set()

    for parent in commit.parent_hashes:
        if parent in seen_parents:
            continue
        seen_parents.add(parent)
        if parent not in index:
            # Ограниченная или shallow-история: ребро опускается.
            continue

        parent_lane = lanes_by_hash[parent]
        parent_x = parent_lane * LANE_WIDTH
        parent_y = index[parent] * ROW_HEIGHT
        points: tuple[Point, ...]

        if parent_lane == child_lane:
            points = ((child_x, child_y), (parent_x, parent_y))
        else:
            middle_y = child_y + ROW_HEIGHT / 2.0
            points = (
                (child_x, child_y),
                (child_x, middle_y),
                (parent_x, middle_y),
                (parent_x, parent_y),
            )

        yield GraphEdge(
            child_hash=commit.hash,
            parent_hash=parent,
            points=points,
        )
