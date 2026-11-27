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
        self._content_rect = QRectF()
        self._graph_rect = QRectF()
        self._selected_hash: str | None = None
        self._panning = False
        self._pan_start = QPoint()

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
            edge_item = _GraphEdgeItem(
                edge,
                self._lane_color(parent.lane),
                dashed=index > 0,
            )
            self._scene.addItem(edge_item)
            self._edge_items.append(edge_item)

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
        self._add_ref_labels(item, labels, self._lane_color(node.lane))

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
        if delta > 0:
            self._zoom_by(ZOOM_STEP, anchor=event.position().toPoint())
        else:
            self._zoom_by(1.0 / ZOOM_STEP, anchor=event.position().toPoint())
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            item = self._commit_item_at(event.position().toPoint())
            if item is not None:
                self._select_commit(item, emit=True)
            else:
                self._scene.clearSelection()
                self._selected_hash = None
                self._pending_selection_hash = None
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
        self._content_rect = QRectF()
        self._graph_rect = QRectF()
        self._scene.clear()
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
        """
        content = self._content_rect
        if content.isEmpty():
            self._scene.setSceneRect(QRectF(-220.0, -160.0, 440.0, 320.0))
            return
        scale = max(self.zoom_factor, MIN_ZOOM)
        viewport = self.viewport().rect()
        pad_x = max(SCENE_MARGIN, viewport.width() / scale)
        pad_y = max(SCENE_MARGIN, viewport.height() / scale)
        self._scene.setSceneRect(content.adjusted(-pad_x, -pad_y, pad_x, pad_y))

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

    def _select_commit(self, item: _CommitNodeItem, *, emit: bool) -> None:
        self._scene.clearSelection()
        item.setSelected(True)
        self._selected_hash = item.commit_hash
        self._pending_selection_hash = None
        if emit:
            self.commit_selected.emit(item.commit_hash)

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
        self._color = color
        self._is_merge = is_merge
        self._is_head = is_head
        self._radius = NODE_RADIUS + (MERGE_RADIUS_BONUS if is_merge else 0.0)

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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.isSelected():
            painter.setPen(QPen(QColor("#0f172a"), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0.0, 0.0), self._radius + 4.5, self._radius + 4.5)

        if self._is_head:
            painter.setPen(QPen(self._color.lighter(140), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0.0, 0.0), self._radius + 2.5, self._radius + 2.5)

        painter.setBrush(QBrush(self._color))
        painter.setPen(QPen(self._color.darker(140), 1.2))
        painter.drawEllipse(QPointF(0.0, 0.0), self._radius, self._radius)

        if self._is_merge:
            painter.setBrush(QBrush(QColor("#ffffff")))
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
        self._lane_color = lane_color

        font = QFont()
        font.setPointSizeF(LABEL_FONT_SIZE)
        font.setBold(label.is_current)
        self._font = font
        metrics = QFontMetricsF(font)
        text_width = metrics.horizontalAdvance(label.text)
        self._text_width = max(text_width, 8.0)
        self._height = max(metrics.height(), 13.0) + 2.0 * LABEL_PADDING_Y
        self._width = self._text_width + 2.0 * LABEL_PADDING_X

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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(rect.height() / 2.0, rect.width() / 2.0)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        if self.label.is_current:
            fill = self._lane_color
            border = QPen(self._lane_color.darker(140), 1.0)
            text_color = QColor("#ffffff")
        elif self.label.is_remote:
            fill = QColor("#ffffff")
            border = QPen(QColor("#94a3b8"), 1.0, Qt.PenStyle.DashLine)
            text_color = QColor("#475569")
        else:
            fill = QColor("#ffffff")
            border = QPen(QColor("#cbd5e1"), 1.0)
            text_color = QColor("#334155")
        border.setCosmetic(True)

        painter.fillPath(path, QBrush(fill))
        painter.setPen(border)
        painter.drawPath(path)
        painter.setFont(self._font)
        painter.setPen(text_color)
        painter.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignCenter),
            self.label.text,
        )
