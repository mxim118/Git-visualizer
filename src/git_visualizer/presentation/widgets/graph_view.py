"""Виджет графа истории на QGraphicsView/QGraphicsScene.

Виджет только отображает готовую ``GraphLayout``: узлы, рёбра, refs и метаданные
коммитов приходят снаружи. Ни Git-команды, ни раскладка здесь не выполняются.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)

from git_visualizer.application.services.graph_service import ROW_HEIGHT
from git_visualizer.domain.enums import RefKind
from git_visualizer.domain.models import Commit, GraphEdge, GraphLayout, GraphNode, RefLabel

NODE_RADIUS = 7.0
MERGE_RADIUS_BONUS = 1.5
LABEL_FONT_SIZE = 8.0
LABEL_PADDING_X = 6.0
LABEL_PADDING_Y = 2.0
LABEL_GAP = 3.0
LABEL_OFFSET_X = 12.0
FIT_MARGIN = 28.0
SCENE_MARGIN = 80.0
MIN_ZOOM = 0.001
MAX_ZOOM = 4.0
ZOOM_STEP = 1.2
# Минимальная доля окна, которую по каждой оси занимает граф при перетаскивании.
# Граф меньше этой доли остаётся виден целиком.
MIN_VISIBLE_SHARE = 2.0 / 3.0
EMPTY_STATE_TEXT = "История пуста"

# Большая сцена строится порциями, чтобы не блокировать GUI на время создания
# тысяч QGraphicsItem. До порога включительно граф строится синхронно: это
# сохраняет простое поведение для обычных репозиториев и существующих тестов.
ASYNC_BUILD_NODE_THRESHOLD = 1000
# Бюджет одной порции. По истечении этого времени управление возвращается
# event loop, поэтому окно продолжает реагировать на ввод и перерисовку.
BUILD_CHUNK_BUDGET_SECONDS = 0.010

# Обзорный режим (LOD). Когда строка коммита занимает на экране меньше этого
# числа пикселей, отдельные элементы сцены не рисуются: кадр с тысячами
# QGraphicsItem стоит десятки и сотни миллисекунд, хотя геометрия уже
# субпиксельная. Порог выбран по замерам: на 4 px/строку обычный режим ещё
# укладывается в кадр, ниже — время кадра растёт пропорционально числу
# элементов в окне.
OVERVIEW_ROW_PIXEL_THRESHOLD = 4.0
OVERVIEW_MAX_ZOOM = OVERVIEW_ROW_PIXEL_THRESHOLD / ROW_HEIGHT
# Гистерезис выхода из обзорного режима. Переключение стоит разовой паузы
# (скрыть или показать тысячи элементов), а шаг колеса у самого порога иначе
# переключал бы режим на каждое событие.
OVERVIEW_EXIT_ZOOM = OVERVIEW_MAX_ZOOM * 1.5
# Радиус маркеров HEAD и выделения в обзорном режиме, в пикселях окна:
# в координатах сцены он зависит от масштаба.
OVERVIEW_MARKER_RADIUS_PX = 4.5
# Допуск попадания мышью по узлу в обзорном режиме, в пикселях окна.
OVERVIEW_PICK_RADIUS_PX = 10.0
# Ниже этого масштаба подписи ref неразборчивы, но их отрисовка (самый
# дорогой примитив) остаётся, поэтому они скрываются.
LABEL_MIN_ZOOM = 0.45
# Окно слияния событий колеса. За один оборот приходит несколько событий, и
# если применять каждое, каждая перерисовка сцены превращается в отдельный кадр.
ZOOM_COALESCE_MS = 16

_LANE_COLORS = (
    "#2563eb",  # синий
    "#16a34a",  # зелёный
    "#d97706",  # янтарный
    "#dc2626",  # красный
    "#7c3aed",  # фиолетовый
    "#0891b2",  # бирюзовый
    "#db2777",  # розовый
    "#65a30d",  # оливковый
)


@dataclass(frozen=True, slots=True)
class _LabelSpec:
    """Готовая подпись ref для отрисовки."""

    text: str
    full_name: str
    is_remote: bool
    is_current: bool


@dataclass(frozen=True, kw_only=True)
class GraphViewState:
    """Положение графа в окне: масштаб и центр видимой области в координатах сцены.

    Центр необязателен: в настройках прежней версии сохранялся только масштаб.
    В таком случае восстанавливается масштаб вокруг центра вписанного графа, а
    после следующего закрытия сохраняется уже точное положение.
    """

    zoom: float
    center_x: float | None = None
    center_y: float | None = None


class GraphView(QGraphicsView):
    """Отображает готовую раскладку истории; выбранный hash сообщается сигналом."""

    commit_selected = Signal(str)
    # Испускается после того, как сцена полностью построена. Для маленьких
    # графов — синхронно из set_graph, для больших — из следующего цикла event loop.
    graph_ready = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._node_items: dict[str, _CommitNodeItem] = {}
        self._edge_items: list[_GraphEdgeItem] = []
        self._label_items: list[_RefLabelItem] = []
        self._content_rect = QRectF()
        self._graph_rect = QRectF()
        self._selected_hash: str | None = None
        self._panning = False
        self._pan_start = QPoint()

        # Уровни детализации. detail-элементы остаются в сцене верхнего уровня:
        # вложенность под общим корнем замедляет выборку элементов из индекса
        # сцены в разы, а переключение видимости происходит только при
        # пересечении порога масштаба. Обзорный режим рисует весь граф
        # несколькими примитивами (см. _GraphOverviewItem).
        self._overview_item: _GraphOverviewItem | None = None
        self._overview_active = False
        self._detail_visible = True
        # Данные обзорного режима накапливаются во время сборки сцены, чтобы не
        # проходить по всей истории вторым проходом после её завершения.
        self._overview_edges: dict[int, QPainterPath] = {}
        self._overview_nodes: dict[int, list[QPointF]] = {}
        self._overview_head: QPointF | None = None
        self._labels_visible = True

        # Слияние событий колеса: первое событие применяется сразу, остальные
        # копятся в пределах ZOOM_COALESCE_MS и применяются одним кадром.
        self._pending_zoom_factor = 1.0
        self._pending_zoom_anchor: QPoint | None = None
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self._flush_zoom)

        # Построение сцены. Пока идёт асинхронная сборка, set_graph уже вернул
        # управление, но часть узлов может отсутствовать: view_state/подсчёт
        # коммитов отражают только готовую часть, а select_commit запоминает
        # выбор до завершения.
        self._building = False
        self._build_nodes: tuple[GraphNode, ...] = ()
        self._build_nodes_by_hash: dict[str, GraphNode] = {}
        self._build_edges_by_child: dict[str, tuple[GraphEdge, ...]] = {}
        self._build_commits: dict[str, Commit] = {}
        self._build_labels: dict[str, tuple[_LabelSpec, ...]] = {}
        self._build_index = 0
        self._build_fit = False
        self._pending_view_state: GraphViewState | None = None
        self._pending_selection_hash: str | None = None
        self._build_timer = QTimer(self)
        self._build_timer.setSingleShot(True)
        self._build_timer.timeout.connect(self._build_next_chunk)

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        # Поправка области перерисовки под сглаживание не нужна: элементы
        # сглаживаются сами, а лишний запас только увеличивает область
        # перерисовки на каждом кадре.
        # DontSavePainterState здесь не используется: без восстановления
        # состояния между элементами кисть и перо одного элемента попадают в
        # следующий, и заливка подписи ref перекрашивается цветом узла.
        self.setOptimizationFlags(
            QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing
        )
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        # Ползунки не нужны: граф двигается мышью, а от ухода из окна его
        # удерживает ограничение перетаскивания. Внутренние scroll-смещения
        # QGraphicsView при этом продолжают работать.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor("#f8fafc")))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        self._scene.setSceneRect(QRectF(-220.0, -160.0, 440.0, 320.0))

        self.fit_graph_action = QAction("Вписать граф", self)
        self.fit_graph_action.setObjectName("fit_graph_action")
        self.fit_graph_action.setShortcut(QKeySequence("Ctrl+0"))
        self.fit_graph_action.setShortcutContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.fit_graph_action.setStatusTip("Вписать загруженную историю в окно")
        self.fit_graph_action.setEnabled(False)
        self.fit_graph_action.triggered.connect(self.fit_graph)
        self.addAction(self.fit_graph_action)

    # --- публичный интерфейс ---

    def set_graph(
        self,
        layout: GraphLayout,
        *,
        commits: Sequence[Commit] = (),
        ref_labels: Mapping[str, Sequence[RefLabel]] | None = None,
        fit: bool = True,
    ) -> None:
        """Показывает готовую раскладку.

        Для небольших графов сцена строится сразу. Для больших ``set_graph``
        только подготавливает данные и запускает сборку порциями: управление
        возвращается в event loop, а ``graph_ready`` испускается после того, как
        все узлы, рёбра и подписи добавлены. Так создание тысяч
        ``QGraphicsItem`` не блокирует окно.

        :param layout: узлы и рёбра, рассчитанные Application.
        :param commits: метаданные для подсказок (hash, автор, сообщение, дата).
        :param ref_labels: готовые метки ``BranchService.get_ref_labels``; если не
            переданы, подписи собираются из ``GraphNode.refs``. Для точного признака
            текущей ветки лучше передавать ``RefLabel``.
        :param fit: вписать историю в окно после загрузки.
        """
        self._reset_scene()
        if not layout.nodes:
            self._show_empty_state()
            self.graph_ready.emit()
            return

        nodes_by_hash = {node.commit_hash: node for node in layout.nodes}
        commits_by_hash = {commit.hash: commit for commit in commits}
        edges_by_child: dict[str, list[GraphEdge]] = {}
        for edge in layout.edges:
            if edge.child_hash in nodes_by_hash and edge.parent_hash in nodes_by_hash:
                edges_by_child.setdefault(edge.child_hash, []).append(edge)

        self._build_nodes = layout.nodes
        self._build_nodes_by_hash = nodes_by_hash
        self._build_edges_by_child = {
            child_hash: tuple(child_edges)
            for child_hash, child_edges in edges_by_child.items()
        }
        self._build_commits = commits_by_hash
        self._build_labels = self._display_labels(layout.nodes, ref_labels)
        self._build_index = 0
        self._build_fit = fit
        self._building = True

        if len(layout.nodes) <= ASYNC_BUILD_NODE_THRESHOLD:
            self._consume_build_queue(budget=None)
            self._finish_build()
            return
        self._build_timer.start(0)

    @property
    def is_building(self) -> bool:
        """Идёт ли сейчас постепенная сборка сцены."""
        return self._building

    def _consume_build_queue(self, *, budget: float | None) -> None:
        """Добавляет элементы сцены, пока не истёк бюджет времени.

        ``budget=None`` — построить всё сразу (маленький граф).
        """
        deadline = None if budget is None else perf_counter() + budget
        nodes = self._build_nodes
        while self._build_index < len(nodes):
            self._add_graph_node(nodes[self._build_index])
            self._build_index += 1
            if deadline is not None and perf_counter() >= deadline:
                break

    def _build_next_chunk(self) -> None:
        if not self._building:
            return
        self._consume_build_queue(budget=BUILD_CHUNK_BUDGET_SECONDS)
        if self._build_index < len(self._build_nodes):
            self._build_timer.start(0)
        else:
            self._finish_build()

    def _add_graph_node(self, node: GraphNode) -> None:
        """Добавляет рёбра коммита, его узел и подписи ref."""
        child_edges = self._build_edges_by_child.get(node.commit_hash, ())
        for index, edge in enumerate(child_edges):
            parent = self._build_nodes_by_hash[edge.parent_hash]
            lane_color = self._lane_color(parent.lane)
            edge_item = _GraphEdgeItem(edge, lane_color, dashed=index > 0)
            self._scene.addItem(edge_item)
            self._edge_items.append(edge_item)
            self._add_overview_edge(edge, parent.lane)

        labels = self._build_labels.get(node.commit_hash, ())
        commit = self._build_commits.get(node.commit_hash)
        is_merge = bool(commit is not None and commit.is_merge) or len(child_edges) > 1
        is_head = "HEAD" in node.refs or any(label.is_current for label in labels)
        item = _CommitNodeItem(
            node,
            self._lane_color(node.lane),
            is_merge=is_merge,
            is_head=is_head,
            tooltip=self._tooltip(node, commit),
        )
        item.setPos(node.x, node.y)
        self._scene.addItem(item)
        self._node_items[node.commit_hash] = item
        self._graph_rect = self._graph_rect.united(item.sceneBoundingRect())
        self._add_overview_node(node)
        if is_head:
            self._overview_head = QPointF(node.x, node.y)
        self._add_ref_labels(item, labels, self._lane_color(node.lane))

    def _add_overview_edge(self, edge: GraphEdge, lane: int) -> None:
        """Копит рёбра обзорного режима одним путём на цвет дорожки."""
        key = lane % len(_LANE_COLORS)
        path = self._overview_edges.get(key)
        if path is None:
            path = QPainterPath()
            self._overview_edges[key] = path
        first_x, first_y = edge.points[0]
        path.moveTo(first_x, first_y)
        for x, y in edge.points[1:]:
            path.lineTo(x, y)

    def _add_overview_node(self, node: GraphNode) -> None:
        """Копит центры узлов обзорного режима по цветам дорожек."""
        self._overview_nodes.setdefault(node.lane % len(_LANE_COLORS), []).append(
            QPointF(node.x, node.y)
        )

    def _build_overview_item(self) -> None:
        """Создаёт обзорный элемент по накопленным во время сборки данным."""
        if self._overview_item is not None:
            if self._overview_item.scene() is self._scene:
                self._scene.removeItem(self._overview_item)
            self._overview_item = None
        if not self._overview_edges and not self._overview_nodes:
            return
        edges = [
            (self._lane_color(key), path)
            for key, path in sorted(self._overview_edges.items())
        ]
        nodes = [
            (self._lane_color(key), QPolygonF(points))
            for key, points in sorted(self._overview_nodes.items())
        ]
        item = _GraphOverviewItem(
            self._content_rect,
            edges,
            nodes,
            head_position=self._overview_head,
        )
        # В сцену элемент попадает только в обзорном режиме: в обычном он бы
        # напрасно участвовал в обходе сцены на каждом кадре.
        item.setVisible(False)
        item.sync(self.zoom_factor, self._selected_position())
        self._overview_item = item

    def _finish_build(self) -> None:
        """Завершает сборку: считает границы, вписывает граф и сообщает готовность."""
        self._build_timer.stop()
        self._building = False
        self._content_rect = self._scene.itemsBoundingRect()
        self.fit_graph_action.setEnabled(True)

        pending_view = self._pending_view_state
        self._pending_view_state = None
        if pending_view is not None:
            # Положение, запрошенное во время сборки (например, после refresh),
            # важнее автоматической подгонки.
            self.restore_view_state(pending_view)
        elif self._build_fit:
            self.fit_graph()
        else:
            self._update_scene_rect()
            self.viewport().update()

        pending_selection = self._pending_selection_hash
        self._pending_selection_hash = None
        if pending_selection is not None and self._selected_hash is None:
            self.select_commit(pending_selection)

        self._build_nodes = ()
        self._build_nodes_by_hash = {}
        self._build_edges_by_child = {}
        self._build_commits = {}
        self._build_labels = {}
        self._build_index = 0
        self._build_fit = False
        self._build_overview_item()
        self._apply_level_of_detail()
        self.graph_ready.emit()

    def clear(self) -> None:
        """Очищает сцену и показывает пустое состояние."""
        self._reset_scene()
        self._show_empty_state()

    def fit_graph(self) -> None:
        """Вписывает загруженную историю в окно с сохранением пропорций."""
        if self._building:
            # Во время порционной сборки граф ещё неполный: откладываем Fit до
            # завершения, иначе он впишет только уже созданную часть.
            self._build_fit = True
            self._pending_view_state = None
            return
        if not self._node_items:
            self.resetTransform()
            self.centerOn(0.0, 0.0)
            self.viewport().update()
            return

        content = self._content_rect
        if content.isEmpty():
            return
        padded = content.adjusted(-FIT_MARGIN, -FIT_MARGIN, FIT_MARGIN, FIT_MARGIN)
        viewport = self.viewport().rect()
        if viewport.width() <= 1 or viewport.height() <= 1:
            return

        scale = min(viewport.width() / padded.width(), viewport.height() / padded.height())
        scale = max(MIN_ZOOM, min(MAX_ZOOM, scale))
        self.setTransform(QTransform().scale(scale, scale))
        self._update_scene_rect()
        self.centerOn(padded.center())
        self._apply_level_of_detail()

    def zoom_in(self) -> None:
        self._zoom_by(ZOOM_STEP)

    def zoom_out(self) -> None:
        self._zoom_by(1.0 / ZOOM_STEP)

    def view_state(self) -> GraphViewState | None:
        """Сохраняемое положение графа: масштаб и центр окна.

        ``None`` — граф не загружен, запоминать нечего. Центр берётся в
        координатах сцены, поэтому состояние не зависит от размера окна.
        Во время порционной сборки возвращается ``None``: частичное положение
        не должно попадать в настройки.
        """
        if self._building or not self._node_items:
            return None
        center = self.mapToScene(self.viewport().rect().center())
        return GraphViewState(
            zoom=self.zoom_factor,
            center_x=center.x(),
            center_y=center.y(),
        )

    def restore_view_state(self, state: GraphViewState) -> bool:
        """Восстанавливает масштаб и центр окна из прошлого запуска.

        Если в сохранённом состоянии нет центра (настройки прежней версии
        хранили только масштаб), применяется масштаб вокруг центра вписанного
        графа: точного положения там нет. Иначе положение могло быть получено
        на другом графе (другой репозиторий) или при другом размере окна,
        поэтому после установки оно ограничивается теми же пределами, что и
        перетаскивание мышью: граф не уходит из окна. ``False`` — граф не загружен
        (в том числе если порционная сборка ещё не завершена: положение будет
        применено автоматически после её окончания).
        """
        if self._building:
            self._pending_view_state = state
            return False
        if not self._node_items:
            return False
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, float(state.zoom)))
        if state.center_x is None or state.center_y is None:
            current = self.zoom_factor
            if current > 0:
                self._zoom_by(zoom / current)
            return True
        self.setTransform(QTransform().scale(zoom, zoom))
        self._update_scene_rect()
        self.centerOn(state.center_x, state.center_y)
        # centerOn двигает полосы прокрутки целыми пикселями и теряет доли
        # координаты сцены. Доводим центр субпиксельным translate, как при зуме
        # колесом, чтобы позиция восстанавливалась точнее.
        anchor = self.viewport().rect().center()
        current = self.mapToScene(anchor)
        self.translate(
            current.x() - state.center_x, current.y() - state.center_y
        )
        # Положение с другого графа не должно уводить узлы из окна.
        self._apply_origin(self.mapToScene(QPoint(0, 0)), strict=True)
        self._apply_level_of_detail()
        self.viewport().update()
        return True

    def select_commit(self, commit_hash: str, *, emit_signal: bool = False) -> bool:
        """Выбирает узел по hash; возвращает ``False``, если такого узла нет.

        Если сцена ещё строится порциями, выбор запоминается и применяется после
        появления узла. Это нужно при refresh/догрузке большой истории, когда
        ``WorkspacePage`` восстанавливает выбранный коммит сразу после
        ``set_graph``.
        """
        item = self._node_items.get(commit_hash)
        if item is None:
            if self._building:
                self._pending_selection_hash = commit_hash
            return False
        self._select_commit(item, emit=emit_signal)
        return True

    @property
    def selected_commit_hash(self) -> str | None:
        return self._selected_hash

    @property
    def zoom_factor(self) -> float:
        return float(self.transform().m11())

    @property
    def commit_count(self) -> int:
        return len(self._node_items)

    @property
    def edge_count(self) -> int:
        return len(self._edge_items)

    @property
    def is_empty(self) -> bool:
        return not self._node_items

    # --- события ---

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt API)
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        step = ZOOM_STEP if delta > 0 else 1.0 / ZOOM_STEP
        self._pending_zoom_factor *= step
        self._pending_zoom_anchor = event.position().toPoint()
        if not self._zoom_timer.isActive():
            # Одиночный щелчок колеса не должен ждать таймера: первое событие
            # применяется сразу, а следующие в пределах окна копятся в него.
            self._flush_zoom(start_window=True)
        event.accept()

    def _flush_zoom(self, *, start_window: bool = False) -> None:
        """Применяет накопленный за окно слияния шаг масштабирования."""
        factor = self._pending_zoom_factor
        anchor = self._pending_zoom_anchor
        self._pending_zoom_factor = 1.0
        self._zoom_timer.stop()
        if abs(factor - 1.0) > 1e-9:
            self._zoom_by(factor, anchor=anchor)
        if start_window and self._node_items:
            self._zoom_timer.start(ZOOM_COALESCE_MS)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            item = self._node_at(event.position().toPoint())
            if item is not None:
                self._select_commit(item, emit=True)
            else:
                self._clear_selection()
            self._start_pan(event)
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._start_pan(event)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._panning:
            current = event.position().toPoint()
            delta = current - self._pan_start
            self._scroll_by(delta.x(), delta.y())
            self._pan_start = current
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._panning and event.button() in (
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.MiddleButton,
        ):
            self._panning = False
            self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if not self._node_items:
            super().resizeEvent(event)
            self._update_scene_rect()
            return
        old_size = event.oldSize()
        center: QPointF | None = None
        if old_size.width() > 0 and old_size.height() > 0:
            center = self.mapToScene(
                QPoint(old_size.width() // 2, old_size.height() // 2)
            )
        super().resizeEvent(event)
        self._update_scene_rect()
        if center is not None:
            self.centerOn(center)
        self._clamp_view()
        self._apply_level_of_detail()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().paintEvent(event)
        if self._node_items:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = QFont(self.font())
        font.setPointSizeF(11.0)
        painter.setFont(font)
        painter.setPen(QColor("#64748b"))
        painter.drawText(
            self.viewport().rect(),
            int(Qt.AlignmentFlag.AlignCenter),
            EMPTY_STATE_TEXT,
        )
        painter.end()

    # --- внутренняя реализация ---

    def _reset_scene(self) -> None:
        # Незавершённая порционная сборка больше не должна добавлять элементы
        # в новую сцену. Таймер принадлежит виджету, поэтому его достаточно
        # остановить; проверка _building отбросит уже поставленный в очередь тик.
        self._build_timer.stop()
        self._zoom_timer.stop()
        self._pending_zoom_factor = 1.0
        self._pending_zoom_anchor = None
        self._building = False
        self._build_nodes = ()
        self._build_nodes_by_hash = {}
        self._build_edges_by_child = {}
        self._build_commits = {}
        self._build_labels = {}
        self._build_index = 0
        self._build_fit = False
        self._pending_view_state = None
        self._pending_selection_hash = None
        self._node_items.clear()
        self._edge_items.clear()
        self._label_items.clear()
        self._content_rect = QRectF()
        self._graph_rect = QRectF()
        self._scene.clear()
        # Сцена очищена вместе с обзорным элементом: следующие данные должны
        # попасть в новую сцену.
        self._overview_item = None
        self._overview_active = False
        self._detail_visible = True
        self._overview_edges.clear()
        self._overview_nodes.clear()
        self._overview_head = None
        self._labels_visible = True
        self._selected_hash = None
        self._panning = False
        self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor))

    def _show_empty_state(self) -> None:
        self._scene.setSceneRect(QRectF(-220.0, -160.0, 440.0, 320.0))
        self.fit_graph_action.setEnabled(False)
        self.viewport().setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.resetTransform()
        self.centerOn(0.0, 0.0)
        self.viewport().update()

    def _update_scene_rect(self) -> None:
        """Расширяет сцену вокруг содержимого.

        Ползунки скрыты, но ``QGraphicsView`` всё равно ограничивает внутренний
        сдвиг пределами ``sceneRect``. Запас в один экран с каждой стороны
        позволяет довести видимую область до любой позиции, которую разрешает
        ограничение перетаскивания (``_clamp_view_origin``).

        ``setSceneRect`` пересчитывает диапазоны прокрутки и стоит кадр, поэтому
        при зуме сцена расширяется только когда прежнего запаса перестало
        хватать: уменьшать её не нужно, показать лишнее место ползунки не дают.
        """
        content = self._content_rect
        if content.isEmpty():
            self._scene.setSceneRect(QRectF(-220.0, -160.0, 440.0, 320.0))
            return
        scale = max(self.zoom_factor, MIN_ZOOM)
        viewport = self.viewport().rect()
        pad_x = max(SCENE_MARGIN, viewport.width() / scale)
        pad_y = max(SCENE_MARGIN, viewport.height() / scale)
        target = content.adjusted(-pad_x, -pad_y, pad_x, pad_y)
        if self._scene.sceneRect().contains(target):
            return
        self._scene.setSceneRect(target)

    def _clamp_view_origin(
        self, origin: QPointF, *, current: QPointF | None = None
    ) -> QPointF:
        """Ограничивает положение окна относительно графа.

        По каждой оси видимая часть графа не меньше ``MIN_VISIBLE_SHARE`` окна;
        граф меньше этой доли остаётся виден целиком. Считается по узлам и
        рёбрам, без подписей ref: длинные подписи иначе дают утащить сами
        коммиты за край окна.

        ``current`` — текущее положение окна. Если оно уже вне допустимых
        пределов (зум под курсором у края графа), пределы расширяются до него:
        окно не возвращается скачком, но и уйти дальше не даёт.
        """
        graph = self._graph_rect
        if graph.isEmpty():
            return origin
        scale = max(self.zoom_factor, MIN_ZOOM)
        viewport = self.viewport().rect()
        x_bounds = self._clamp_bounds(
            graph.left(), graph.right(), viewport.width() / scale
        )
        y_bounds = self._clamp_bounds(
            graph.top(), graph.bottom(), viewport.height() / scale
        )
        if current is not None:
            x_bounds = (min(x_bounds[0], current.x()), max(x_bounds[1], current.x()))
            y_bounds = (min(y_bounds[0], current.y()), max(y_bounds[1], current.y()))
        return QPointF(
            min(max(origin.x(), x_bounds[0]), x_bounds[1]),
            min(max(origin.y(), y_bounds[0]), y_bounds[1]),
        )

    @staticmethod
    def _clamp_bounds(
        start: float, end: float, viewport_size: float
    ) -> tuple[float, float]:
        visible = min(end - start, viewport_size * MIN_VISIBLE_SHARE)
        return start + visible - viewport_size, end - visible

    def _scroll_by(self, dx: float, dy: float) -> None:
        """Сдвигает видимую область, удерживая граф в окне."""
        scale = max(self.zoom_factor, MIN_ZOOM)
        origin = self.mapToScene(QPoint(0, 0))
        self._apply_origin(origin - QPointF(dx / scale, dy / scale))

    def _apply_origin(self, target: QPointF, *, strict: bool = False) -> None:
        """Ставит левый верхний угол окна в ``target`` с ограничением.

        ``strict`` возвращает окно в допустимые пределы даже скачком; иначе
        уже вышедшее за них окно остаётся на месте (см. ``_clamp_view_origin``).
        """
        scale = max(self.zoom_factor, MIN_ZOOM)
        origin = self.mapToScene(QPoint(0, 0))
        clamped = self._clamp_view_origin(target, current=None if strict else origin)
        shift_x = (origin.x() - clamped.x()) * scale
        shift_y = (origin.y() - clamped.y()) * scale
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() - round(shift_x)
        )
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() - round(shift_y)
        )

    def _clamp_view(self) -> None:
        """Удерживает видимую область в допустимых пределах вокруг графа."""
        if not self._node_items:
            return
        self._apply_origin(self.mapToScene(QPoint(0, 0)))

    # --- уровни детализации ---

    def _apply_level_of_detail(self) -> None:
        """Выбирает режим отрисовки по текущему масштабу.

        Пока сцена строится порциями, обзорные данные ещё неполные: остаёмся в
        detail-режиме до ``_finish_build``.
        """
        if self._building or not self._node_items:
            self._set_overview(False)
            return
        overview = self._overview_item is not None and self._is_overview_zoom()
        self._set_overview(overview)
        self._set_labels_visible(
            not overview and self.zoom_factor >= LABEL_MIN_ZOOM
        )

    def _is_overview_zoom(self) -> bool:
        """Нужен ли обзорный режим; выход из него — с гистерезисом."""
        zoom = self.zoom_factor
        limit = OVERVIEW_EXIT_ZOOM if self._overview_active else OVERVIEW_MAX_ZOOM
        return zoom < limit

    def _set_overview(self, active: bool) -> None:
        """Переключает detail/overview и обновляет маркеры обзорного режима."""
        if self._overview_active == active:
            if active and self._overview_item is not None:
                self._overview_item.sync(
                    self.zoom_factor, self._selected_position()
                )
            return
        self._overview_active = active
        self._set_detail_visible(not active)
        item = self._overview_item
        if item is not None:
            # Скрытый элемент всё равно участвует в обходе сцены на каждом
            # кадре, поэтому вне обзорного режима он из сцены убирается.
            if active and item.scene() is not self._scene:
                self._scene.addItem(item)
            elif not active and item.scene() is self._scene:
                self._scene.removeItem(item)
            item.setVisible(active)
            item.sync(self.zoom_factor, self._selected_position())
        self.viewport().update()

    def _set_detail_visible(self, visible: bool) -> None:
        """Скрывает или показывает элементы обычного режима.

        Переключение поштучное: группа под общим корнем ускорила бы его, но
        замедлила бы выборку элементов из индекса сцены на каждом кадре. Порог
        масштаба пересекается редко, поэтому разовая пауза предпочтительнее.
        """
        if self._detail_visible == visible:
            return
        self._detail_visible = visible
        for item in self._edge_items:
            item.setVisible(visible)
        for item in self._node_items.values():
            item.setVisible(visible)

    def _set_labels_visible(self, visible: bool) -> None:
        """Скрывает подписи ref, когда их текст на экране неразборчив."""
        if self._labels_visible == visible:
            return
        self._labels_visible = visible
        for item in self._label_items:
            item.setVisible(visible)

    def _selected_position(self) -> QPointF | None:
        """Положение выбранного узла в координатах сцены для обзорного режима."""
        if self._selected_hash is None:
            return None
        item = self._node_items.get(self._selected_hash)
        return None if item is None else item.scenePos()

    # --- выбор и попадание мышью ---

    def _select_commit(self, item: _CommitNodeItem, *, emit: bool) -> None:
        self._scene.clearSelection()
        item.setSelected(True)
        self._selected_hash = item.commit_hash
        self._pending_selection_hash = None
        if self._overview_item is not None:
            self._overview_item.set_selection(item.scenePos())
        if emit:
            self.commit_selected.emit(item.commit_hash)

    def _clear_selection(self) -> None:
        self._scene.clearSelection()
        self._selected_hash = None
        self._pending_selection_hash = None
        if self._overview_item is not None:
            self._overview_item.set_selection(None)

    def _node_at(self, position: QPoint) -> _CommitNodeItem | None:
        """Узел под точкой окна с учётом текущего режима отрисовки."""
        if self._overview_active:
            return self._overview_node_at(position)
        return self._commit_item_at(position)

    def _overview_node_at(self, position: QPoint) -> _CommitNodeItem | None:
        """Ближайший узел в пределах допуска.

        В обзорном режиме узлы субпиксельные, поэтому попадание по точной фигуре
        невозможно: выбирается ближайший к клику коммит, если он ближе допуска.
        """
        point = self.mapToScene(position)
        scale = max(self.zoom_factor, MIN_ZOOM)
        limit = OVERVIEW_PICK_RADIUS_PX / scale
        best: _CommitNodeItem | None = None
        best_distance = limit * limit
        for item in self._node_items.values():
            position_scene = item.scenePos()
            dx = position_scene.x() - point.x()
            dy = position_scene.y() - point.y()
            distance = dx * dx + dy * dy
            if distance <= best_distance:
                best_distance = distance
                best = item
        return best

    def _commit_item_at(self, position: QPoint) -> _CommitNodeItem | None:
        item = self.itemAt(position)
        while item is not None:
            if isinstance(item, _CommitNodeItem):
                return item
            item = item.parentItem()
        return None

    def _start_pan(self, event) -> None:
        self._panning = True
        self._pan_start = event.position().toPoint()
        self.viewport().setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
        event.accept()

    def _zoom_by(self, factor: float, *, anchor: QPoint | None = None) -> None:
        """Меняет масштаб, оставляя точку под ``anchor`` на месте.

        ``anchor`` — точка окна (для колеса — позиция курсора). С явной точкой
        масштаб меняется вокруг неё: если она на видимой части графа (узлы,
        рёбра, подписи), точка остаётся на месте; если вне содержимого, граф
        удерживается в окне так же, как при перетаскивании. Без точки
        масштабируется центр окна с прежним ограничением.
        """
        if not self._node_items:
            return
        current = self.zoom_factor
        if current <= 0:
            return
        target = max(MIN_ZOOM, min(MAX_ZOOM, current * factor))
        ratio = target / current
        if abs(ratio - 1.0) < 1e-9:
            return

        explicit_anchor = anchor is not None
        if anchor is None:
            anchor = self.viewport().rect().center()
        before = self.mapToScene(anchor)
        on_content = explicit_anchor and self._content_rect.contains(before)
        self.scale(ratio, ratio)
        self._update_scene_rect()
        # translate сдвигает сцену ровно настолько, чтобы точка под курсором
        # осталась под ним; в отличие от centerOn он не округляет до пикселя.
        after = self.mapToScene(anchor)
        delta = after - before
        self.translate(delta.x(), delta.y())
        if not on_content:
            # Точка вне содержимого: якорить нечего, зато граф нельзя потерять.
            self._apply_origin(self.mapToScene(QPoint(0, 0)), strict=True)
        self._apply_level_of_detail()

    def _add_ref_labels(
        self,
        node_item: _CommitNodeItem,
        labels: Sequence[_LabelSpec],
        lane_color: QColor,
    ) -> None:
        if not labels:
            return
        label_items = [_RefLabelItem(label, lane_color) for label in labels]
        total_height = sum(item.boundingRect().height() for item in label_items)
        total_height += LABEL_GAP * (len(label_items) - 1)
        y = -total_height / 2.0
        for item in label_items:
            item.setParentItem(node_item)
            item.setPos(NODE_RADIUS + LABEL_OFFSET_X, y)
            y += item.boundingRect().height() + LABEL_GAP
        self._label_items.extend(label_items)
        for item in label_items:
            item.setVisible(self._labels_visible)

    def _display_labels(
        self,
        nodes: Sequence[GraphNode],
        ref_labels: Mapping[str, Sequence[RefLabel]] | None,
    ) -> dict[str, tuple[_LabelSpec, ...]]:
        if ref_labels is not None:
            return {
                node.commit_hash: self._labels_from_ref_labels(
                    ref_labels.get(node.commit_hash, ())
                )
                for node in nodes
            }
        return {
            node.commit_hash: self._labels_from_refs(node.refs) for node in nodes
        }

    @staticmethod
    def _labels_from_ref_labels(labels: Sequence[RefLabel]) -> tuple[_LabelSpec, ...]:
        result: list[_LabelSpec] = []
        for label in labels:
            if label.kind is RefKind.DETACHED_HEAD:
                result.append(
                    _LabelSpec("HEAD (detached)", label.full_name, False, True)
                )
            elif label.kind is RefKind.REMOTE_BRANCH:
                if label.name.endswith("/HEAD"):
                    continue
                result.append(_LabelSpec(label.name, label.full_name, True, False))
            else:
                text = (
                    f"HEAD → {label.name}" if label.is_current else label.name
                )
                result.append(
                    _LabelSpec(text, label.full_name, False, label.is_current)
                )
        return tuple(result)

    @staticmethod
    def _labels_from_refs(refs: Sequence[str]) -> tuple[_LabelSpec, ...]:
        has_head = "HEAD" in refs
        has_local_branch = any(ref.startswith("refs/heads/") for ref in refs)
        result: list[_LabelSpec] = []

        for ref in refs:
            if ref == "HEAD":
                if not has_local_branch:
                    result.append(_LabelSpec("HEAD (detached)", "HEAD", False, True))
                continue
            if ref.startswith("refs/heads/"):
                name = ref.removeprefix("refs/heads/")
                text = f"HEAD → {name}" if has_head else name
                result.append(_LabelSpec(text, ref, False, has_head))
                continue
            if ref.startswith("refs/remotes/"):
                if ref.endswith("/HEAD"):
                    continue
                name = ref.removeprefix("refs/remotes/")
                result.append(_LabelSpec(name, ref, True, False))
        return tuple(result)

    @staticmethod
    def _tooltip(node: GraphNode, commit: Commit | None) -> str:
        short_hash = commit.short_hash if commit is not None else node.commit_hash[:7]
        parts = [f"<b>{html.escape(short_hash)}</b>"]
        if commit is not None:
            if commit.author_name:
                parts.append(html.escape(commit.author_name))
            if commit.subject:
                parts.append(f"<i>{html.escape(commit.subject)}</i>")
            parts.append(
                html.escape(commit.committed_at.strftime("%d.%m.%Y %H:%M %z"))
            )
        else:
            parts.append(html.escape(node.commit_hash))
        return "<br>".join(parts)

    @staticmethod
    def _lane_color(lane: int) -> QColor:
        return QColor(_LANE_COLORS[lane % len(_LANE_COLORS)])


class _CommitNodeItem(QGraphicsItem):
    """Узел коммита: круг с признаками merge/текущего HEAD и выделением."""

    def __init__(
        self,
        node: GraphNode,
        color: QColor,
        *,
        is_merge: bool,
        is_head: bool,
        tooltip: str,
    ) -> None:
        super().__init__()
        self.node = node
        self.commit_hash = node.commit_hash
        self._is_merge = is_merge
        self._is_head = is_head
        self._radius = NODE_RADIUS + (MERGE_RADIUS_BONUS if is_merge else 0.0)
        # Перья и кисти не меняются за время жизни элемента: создавать их на
        # каждый кадр дорого, когда элементов тысячи.
        self._selection_pen = QPen(QColor("#0f172a"), 2.0)
        self._head_pen = QPen(color.lighter(140), 2.0)
        self._body_brush = QBrush(color)
        self._body_pen = QPen(color.darker(140), 1.2)
        self._merge_brush = QBrush(QColor("#ffffff"))

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setZValue(2.0)
        self.setToolTip(tooltip)

    def boundingRect(self) -> QRectF:
        radius = self._radius + 6.0
        return QRectF(-radius, -radius, 2.0 * radius, 2.0 * radius)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        radius = self._radius + 2.0
        path.addEllipse(QPointF(0.0, 0.0), radius, radius)
        return path

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        if self.isSelected():
            painter.setPen(self._selection_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0.0, 0.0), self._radius + 4.5, self._radius + 4.5)

        if self._is_head:
            painter.setPen(self._head_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0.0, 0.0), self._radius + 2.5, self._radius + 2.5)

        painter.setBrush(self._body_brush)
        painter.setPen(self._body_pen)
        painter.drawEllipse(QPointF(0.0, 0.0), self._radius, self._radius)

        if self._is_merge:
            painter.setBrush(self._merge_brush)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(0.0, 0.0), 2.8, 2.8)

    def itemChange(self, change, value):  # noqa: N802 (Qt API)
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update()
        return super().itemChange(change, value)


class _GraphEdgeItem(QGraphicsPathItem):
    """Ребро к родителю; вторые и следующие рёбра merge рисуются штриховой линией."""

    def __init__(self, edge: GraphEdge, color: QColor, *, dashed: bool) -> None:
        super().__init__()
        path = QPainterPath()
        first_x, first_y = edge.points[0]
        path.moveTo(QPointF(first_x, first_y))
        for x, y in edge.points[1:]:
            path.lineTo(QPointF(x, y))
        self.setPath(path)

        pen = QPen(color, 2.0 if not dashed else 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCosmetic(True)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setZValue(0.0)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)


class _RefLabelItem(QGraphicsItem):
    """Компактная подпись ref; текущая ветка отличается заливкой и текстом HEAD →."""

    def __init__(self, label: _LabelSpec, lane_color: QColor) -> None:
        super().__init__()
        self.label = label

        font = QFont()
        font.setPointSizeF(LABEL_FONT_SIZE)
        font.setBold(label.is_current)
        self._font = font
        metrics = QFontMetricsF(font)
        text_width = metrics.horizontalAdvance(label.text)
        self._text_width = max(text_width, 8.0)
        self._height = max(metrics.height(), 13.0) + 2.0 * LABEL_PADDING_Y
        self._width = self._text_width + 2.0 * LABEL_PADDING_X

        # Геометрия и оформление подписи постоянны: заполняем их один раз,
        # чтобы paint не создавал путь и цвета на каждый кадр.
        self._text_rect = self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(
            self._text_rect.height() / 2.0, self._text_rect.width() / 2.0
        )
        self._path = QPainterPath()
        self._path.addRoundedRect(self._text_rect, radius, radius)

        if label.is_current:
            self._fill = QBrush(lane_color)
            border = QPen(lane_color.darker(140), 1.0)
            self._text_color = QColor("#ffffff")
        elif label.is_remote:
            self._fill = QBrush(QColor("#ffffff"))
            border = QPen(QColor("#94a3b8"), 1.0, Qt.PenStyle.DashLine)
            self._text_color = QColor("#475569")
        else:
            self._fill = QBrush(QColor("#ffffff"))
            border = QPen(QColor("#cbd5e1"), 1.0)
            self._text_color = QColor("#334155")
        border.setCosmetic(True)
        self._border = border

        self.setZValue(1.0)
        self.setToolTip(label.full_name or label.text)

    def boundingRect(self) -> QRectF:
        return QRectF(0.0, 0.0, self._width, self._height)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        # Кисть и перо выставляются явно: drawPath и заливает, и обводит путь,
        # поэтому от унаследованной кисти не должно зависеть ничего.
        painter.setBrush(self._fill)
        painter.setPen(self._border)
        painter.drawPath(self._path)
        painter.setFont(self._font)
        painter.setPen(self._text_color)
        painter.drawText(
            self._text_rect,
            int(Qt.AlignmentFlag.AlignCenter),
            self.label.text,
        )


class _GraphOverviewItem(QGraphicsItem):
    """Обзорный режим: весь граф рисуется несколькими примитивами.

    Кадр с тысячами отдельных ``QGraphicsItem`` стоит десятки и сотни
    миллисекунд, хотя при сильном отдалении геометрия уже субпиксельная. Здесь
    рёбра каждой дорожки сведены в один путь, а узлы — в один набор точек на
    цвет, поэтому время кадра не зависит от размера истории.

    Подписи ref, маркеры merge и кольцо HEAD в этом режиме не рисуются: они
    неразличимы на таком масштабе. Остаются маркеры HEAD и выделения — уже
    постоянного размера в пикселях окна, чтобы выбор не терялся.
    """

    def __init__(
        self,
        content_rect: QRectF,
        edges: Sequence[tuple[QColor, QPainterPath]],
        nodes: Sequence[tuple[QColor, QPolygonF]],
        *,
        head_position: QPointF | None,
    ) -> None:
        super().__init__()
        self._rect = QRectF(content_rect)
        self._edges: list[tuple[QPen, QPainterPath]] = [
            (self._edge_pen(color), path) for color, path in edges
        ]
        self._nodes: list[tuple[QPen, QPolygonF]] = [
            (self._dot_pen(color), points) for color, points in nodes
        ]
        self._head_position = head_position
        self._selection_position: QPointF | None = None
        self._zoom = 1.0

        self._head_pen = QPen(QColor("#ffffff"), 2.0)
        self._head_pen.setCosmetic(True)
        self._selection_pen = QPen(QColor("#0f172a"), 2.0)
        self._selection_pen.setCosmetic(True)

        self.setZValue(-1.0)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    @staticmethod
    def _edge_pen(color: QColor) -> QPen:
        """Ребро — волосок в один пиксель окна независимо от масштаба."""
        pen = QPen(color, 0.0)
        pen.setCosmetic(True)
        return pen

    @staticmethod
    def _dot_pen(color: QColor) -> QPen:
        """Узел — точка диаметром с обычный узел в координатах сцены."""
        pen = QPen(color, 2.0 * NODE_RADIUS)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def sync(self, zoom: float, selection: QPointF | None) -> None:
        """Передаёт текущий масштаб: от него зависит размер маркеров."""
        self._selection_position = selection
        zoom = max(zoom, MIN_ZOOM)
        if zoom != self._zoom:
            # Радиус маркеров задан в пикселях окна, поэтому его проекция в
            # координатах сцены меняется вместе с масштабом.
            self.prepareGeometryChange()
            self._zoom = zoom
        self.update()

    def set_selection(self, position: QPointF | None) -> None:
        self._selection_position = position
        self.update()

    def boundingRect(self) -> QRectF:
        pad = OVERVIEW_MARKER_RADIUS_PX / max(self._zoom, MIN_ZOOM) + 2.0
        return self._rect.adjusted(-pad, -pad, pad, pad)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for pen, path in self._edges:
            painter.setPen(pen)
            painter.drawPath(path)
        for pen, points in self._nodes:
            painter.setPen(pen)
            painter.drawPoints(points)

        radius = OVERVIEW_MARKER_RADIUS_PX / max(self._zoom, MIN_ZOOM)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._head_position is not None:
            painter.setPen(self._head_pen)
            painter.drawEllipse(self._head_position, radius, radius)
        if self._selection_position is not None:
            painter.setPen(self._selection_pen)
            painter.drawEllipse(self._selection_position, radius, radius)
        painter.restore()
