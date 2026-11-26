"""Рабочая область открытого репозитория: верхняя панель, сплиттеры и панели."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QPushButton, QSplitter, QVBoxLayout, QWidget

from git_visualizer.domain.models import Commit, FileChange, Repository
from git_visualizer.presentation.controllers.repository_controller import WorkspaceData
from git_visualizer.presentation.widgets.branches_panel import BranchesPanel
from git_visualizer.presentation.widgets.changes_panel import ChangesPanel
from git_visualizer.presentation.widgets.commit_details_panel import CommitDetailsPanel
from git_visualizer.presentation.widgets.graph_view import GraphView
from git_visualizer.presentation.widgets.history_notice import HistoryNoticeBar
from git_visualizer.presentation.widgets.top_bar import TopBar

# Ручку вертикального сплиттера можно утянуть за край: верхняя область (граф и
# боковые панели) или нижняя панель Changes тогда исчезают полностью. Тонкую
# линию-ручку искать мышью неудобно, поэтому на месте исчезнувшей панели
# появляется маленькая кнопка возврата.
_INITIAL_SPLIT_SIZES = (620, 170)
_RESTORE_BUTTON_HEIGHT = 20
_RESTORE_BUTTON_MARGIN = 2

_RESTORE_BUTTON_STYLE = (
    "QPushButton {"
    "  background: #f8fafc;"
    "  border: 1px solid #94a3b8;"
    "  border-radius: 10px;"
    "  color: #334155;"
    "  font-size: 11px;"
    "  padding: 0 10px;"
    "}"
    "QPushButton:hover { background: #e2e8f0; border-color: #475569; }"
    "QPushButton:pressed { background: #cbd5e1; }"
)


class WorkspacePage(QWidget):
    """Основное окно репозитория с изменяемыми по размеру панелями.

    Центральная область — ``GraphView``; слева ветки и remote, справа детали
    коммита со списком файлов, снизу панель Changes. Под графом показывается
    ``HistoryNoticeBar``, если загружена не вся история (ТЗ FR-HIST-07): она
    сообщает, сколько коммитов загружено, и догружает следующую страницу по
    кнопке. Diff выбранного файла коммита
    открывается большой панелью поверх окна (``DiffOverlay``). Выбор узла сохраняется
    в ``selected_commit_hash``, обновляет панель деталей и сообщается сигналом
    ``commit_selected``; выбор файла коммита сохраняется в ``selected_file`` и
    сообщается сигналом ``file_selected``. Выбор файла в Changes сообщается
    сигналом ``worktree_file_selected`` вместе с секцией (staged/unstaged), а
    запросы Stage/Unstage, массовых действий, необратимой отмены изменений и
    коммита пересылаются из панели
    Changes. Панель веток сообщает выбранный коммит (для создания от него) и
    запросы создания/переключения веток сигналами ``branch_create_requested`` и
    ``branch_switch_requested``.

    Ручку ``main_splitter`` можно утянуть до края: верхняя область (граф с боковыми
    панелями) или нижняя панель Changes исчезают. Тогда на месте исчезнувшей панели
    появляется маленькая кнопка возврата (``body_restore_button`` или
    ``changes_restore_button``), возвращающая прежние размеры сплиттера.
    """

    refresh_requested = Signal()
    close_repository_requested = Signal()
    fetch_requested = Signal()
    pull_requested = Signal()
    push_requested = Signal()
    remote_settings_requested = Signal()
    commit_selected = Signal(str)
    file_selected = Signal(str, object)  # commit hash, FileChange
    worktree_file_selected = Signal(object, bool)  # ChangedFile, staged
    worktree_diff_requested = Signal(object, bool)  # ChangedFile, staged
    changes_refresh_requested = Signal()
    changes_stage_requested = Signal(object)  # ChangedFile
    changes_unstage_requested = Signal(object)  # ChangedFile
    changes_stage_all_requested = Signal()
    changes_unstage_all_requested = Signal()
    changes_discard_requested = Signal(object)  # ChangedFile
    changes_commit_requested = Signal()  # открыть окно сообщения коммита
    branch_create_requested = Signal(str, object)  # имя, hash коммита или None
    branch_switch_requested = Signal(str)  # имя локальной ветки
    older_history_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("workspace_page")

        self._data: WorkspaceData | None = None
        self._commits_by_hash: dict[str, Commit] = {}
        self._selected_commit_hash: str | None = None
        self._selected_file: FileChange | None = None
        self._history_loading = False

        self.top_bar = TopBar(self)
        self.graph_view = GraphView(self)
        self.history_notice = HistoryNoticeBar(self)
        self.branches_panel = BranchesPanel(self)
        self.details_panel = CommitDetailsPanel(self)
        self.changes_panel = ChangesPanel(self)

        # Граф и уведомление об ограниченной истории живут в одной области
        # сплиттера: при догрузке страницы уведомление не сдвигает панели.
        self.graph_area = QWidget(self)
        self.graph_area.setObjectName("graph_area")
        graph_layout = QVBoxLayout(self.graph_area)
        graph_layout.setContentsMargins(0, 0, 0, 0)
        graph_layout.setSpacing(0)
        graph_layout.addWidget(self.graph_view, 1)
        graph_layout.addWidget(self.history_notice)

        self.body_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.body_splitter.setObjectName("body_splitter")
        # Иначе ручку можно дотянуть до края, и граф сворачивается до нулевой
        # ширины, исчезая с экрана: панели не должны «съедать» центральную область.
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.addWidget(self.branches_panel)
        self.body_splitter.addWidget(self.graph_area)
        self.body_splitter.addWidget(self.details_panel)
        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 1)
        self.body_splitter.setStretchFactor(2, 0)
        self.body_splitter.setSizes([240, 680, 420])

        self.main_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.main_splitter.setObjectName("main_splitter")
        self.main_splitter.setChildrenCollapsible(True)
        self.main_splitter.addWidget(self.body_splitter)
        self.main_splitter.addWidget(self.changes_panel)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)
        self.main_splitter.setSizes(list(_INITIAL_SPLIT_SIZES))

        # Размеры до сворачивания: по ним кнопка возврата восстанавливает панели.
        self._last_split_sizes = list(_INITIAL_SPLIT_SIZES)
        self._body_collapsed = False
        self._changes_collapsed = False

        self.body_restore_button = self._make_restore_button(
            "▾ Граф и панели",
            "Вернуть граф и боковые панели",
            self._restore_body,
            object_name="body_restore_button",
        )
        self.changes_restore_button = self._make_restore_button(
            "▴ Изменения",
            "Вернуть нижнюю панель «Изменения»",
            self._restore_changes,
            object_name="changes_restore_button",
        )

        self.branches_panel.setMinimumWidth(160)
        self.graph_view.setMinimumWidth(280)
        self.details_panel.setMinimumWidth(280)
        self.changes_panel.setMinimumHeight(90)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.top_bar)
        layout.addWidget(self.main_splitter, 1)

        self.top_bar.refresh_requested.connect(self.refresh_requested)
        self.top_bar.close_repository_requested.connect(
            self.close_repository_requested
        )
        self.top_bar.fetch_requested.connect(self.fetch_requested)
        self.top_bar.pull_requested.connect(self.pull_requested)
        self.top_bar.push_requested.connect(self.push_requested)
        self.top_bar.remote_settings_requested.connect(
            self.remote_settings_requested
        )
        self.graph_view.commit_selected.connect(self._on_commit_selected)
        self.details_panel.file_selected.connect(self._on_file_selected)
        self.changes_panel.file_selected.connect(self.worktree_file_selected)
        self.changes_panel.diff_requested.connect(self.worktree_diff_requested)
        self.changes_panel.refresh_requested.connect(self.changes_refresh_requested)
        self.changes_panel.stage_requested.connect(self.changes_stage_requested)
        self.changes_panel.unstage_requested.connect(self.changes_unstage_requested)
        self.changes_panel.stage_all_requested.connect(
            self.changes_stage_all_requested
        )
        self.changes_panel.unstage_all_requested.connect(
            self.changes_unstage_all_requested
        )
        self.changes_panel.discard_requested.connect(
            self.changes_discard_requested
        )
        self.changes_panel.commit_requested.connect(
            self.changes_commit_requested
        )
        self.branches_panel.create_branch_requested.connect(
            self.branch_create_requested
        )
        self.branches_panel.switch_branch_requested.connect(
            self.branch_switch_requested
        )
        self.history_notice.load_more_requested.connect(
            self.older_history_requested.emit
        )
        # Сворачивание меняет размеры панелей: при перетаскивании ручки, при
        # изменении размеров окна и программном setSizes. Уведомителем служит
        # resize любого из трех виджетов, поэтому свернутая в ноль панель тоже
        # замечается — за нее «цепляется» соседняя, видимая.
        self.main_splitter.splitterMoved.connect(self._on_splitter_moved)
        for watched in (self.main_splitter, self.body_splitter, self.changes_panel):
            watched.installEventFilter(self)

        self.clear()

    def set_repository(self, repository: Repository) -> None:
        """Показывает репозиторий в верхней панели до загрузки истории."""
        self.top_bar.set_repository(repository)

    def set_workspace_data(
        self, data: WorkspaceData, *, preserve_selection: bool = False
    ) -> None:
        """Отображает загруженные историю, граф, ветки и remote.

        :param preserve_selection: сохранить выбранный коммит и файл. Догрузка
            более старой истории перерисовывает граф целиком, но продолжает его
            вниз: выбранный коммит остаётся на месте, и панель деталей не
            сбрасывается. При обычной загрузке и обновлении выбор снимается.
        """
        selected_hash = (
            self._selected_commit_hash if preserve_selection else None
        )
        selected_file = self._selected_file if preserve_selection else None
        self._data = data
        self._commits_by_hash = {commit.hash: commit for commit in data.commits}
        self._selected_commit_hash = (
            selected_hash if selected_hash in self._commits_by_hash else None
        )
        if self._selected_commit_hash is None:
            selected_file = None
        self._selected_file = selected_file
        selected_commit = (
            self._commits_by_hash[self._selected_commit_hash]
            if self._selected_commit_hash is not None
            else None
        )

        self.top_bar.set_repository(data.repository)
        self.top_bar.set_remotes(data.remotes)
        self.graph_view.set_graph(
            data.layout,
            commits=data.commits,
            ref_labels=data.ref_labels,
        )
        if self._selected_commit_hash is not None:
            # set_graph пересоздаёт узлы, поэтому выделение возвращается отдельно.
            self.graph_view.select_commit(self._selected_commit_hash)
        self.branches_panel.set_branches(data.branches, data.remotes)
        self.branches_panel.set_selected_commit(selected_commit)

        if selected_commit is None:
            if data.commits:
                self.details_panel.clear()
            else:
                self.details_panel.show_empty_repository()
        self._update_history_notice()

    def set_history_loading(self, loading: bool) -> None:
        """Показывает загрузку более старой страницы истории (FR-HIST-07).

        Пока идёт запрос, кнопка догрузки заблокирована: следующая страница
        запрашивается только после завершения текущей.
        """
        self._history_loading = loading
        self._update_history_notice()

    def set_busy(self, busy: bool, message: str | None = None) -> None:
        self.top_bar.set_busy(busy, message)
        self.branches_panel.set_busy(busy)

    def clear(self) -> None:
        """Сбрасывает рабочую область (закрытие репозитория или ошибка открытия)."""
        self._data = None
        self._commits_by_hash = {}
        self._selected_commit_hash = None
        self._selected_file = None
        self._history_loading = False
        self.top_bar.clear()
        self.graph_view.clear()
        self.history_notice.hide()
        self.branches_panel.clear()
        self.details_panel.clear()
        self.changes_panel.clear()

    @property
    def selected_commit_hash(self) -> str | None:
        """Hash выбранного в графе коммита или ``None``."""
        return self._selected_commit_hash

    @property
    def selected_file(self) -> FileChange | None:
        """Файл, выбранный в списке изменённых файлов."""
        return self._selected_file

    @property
    def workspace_data(self) -> WorkspaceData | None:
        return self._data

    def panel_state(self) -> tuple[QByteArray, QByteArray]:
        """Состояние сплиттеров для ``QSettings``: ``(main, body)``."""
        return self.main_splitter.saveState(), self.body_splitter.saveState()

    def restore_panel_state(
        self,
        *,
        main_state: QByteArray | None,
        body_state: QByteArray | None,
    ) -> bool:
        """Восстанавливает деление панелей, сохранённое в прошлом запуске.

        Qt до первого показа окна ещё не знает фактических размеров, поэтому
        ``restoreState`` применяет сохранённые размеры при первой раскладке.
        Некорректное или пустое состояние игнорируется: панели остаются на
        начальных размерах.
        """
        restored = False
        if body_state is not None:
            restored = self.body_splitter.restoreState(body_state) or restored
        if main_state is not None:
            restored = self.main_splitter.restoreState(main_state) or restored
        return restored

    def clear_file_selection(self) -> None:
        """Снимает выбор файла коммита (закрытие панели diff или смена коммита)."""
        self._selected_file = None
        self.details_panel.clear_file_selection()

    def _on_commit_selected(self, commit_hash: str) -> None:
        self._selected_commit_hash = commit_hash
        self._selected_file = None
        commit = self._commits_by_hash.get(commit_hash)
        self.branches_panel.set_selected_commit(commit)
        if commit is None:
            self.details_panel.clear()
        else:
            self.details_panel.show_commit(commit)
        self.commit_selected.emit(commit_hash)

    def _on_file_selected(self, file_change: FileChange) -> None:
        commit_hash = self._selected_commit_hash
        if commit_hash is None:
            return
        self._selected_file = file_change
        self.file_selected.emit(commit_hash, file_change)

    def _update_history_notice(self) -> None:
        """Показывает уведомление, только если история обрезана лимитом."""
        data = self._data
        if data is None or not data.has_more_history:
            self.history_notice.hide()
            return
        if self._history_loading:
            self.history_notice.show_loading()
        else:
            self.history_notice.show_truncated(len(data.commits))

    # --- кнопки возврата свернутых панелей ---

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        """Реагирует на изменение размеров и положения сплиттера и его панелей."""
        # Move нужен наравне с Resize: «вытесненная» за край панель меняет
        # положение при прежней высоте, и момент вытеснения может не дать resize.
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._update_collapse_state()
        return super().eventFilter(watched, event)

    def _make_restore_button(
        self,
        text: str,
        tooltip: str,
        slot: Callable[[], None],
        *,
        object_name: str,
    ) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName(object_name)
        button.setFixedHeight(_RESTORE_BUTTON_HEIGHT)
        button.setStyleSheet(_RESTORE_BUTTON_STYLE)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        # Кнопка — подсказка поверх панелей: фокус ей не нужен.
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(slot)
        button.hide()
        return button

    def _on_splitter_moved(self, _position: int, _index: int) -> None:
        """Ручку перетащили — проверяем, не исчезла ли панель."""
        self._update_collapse_state()

    def _update_collapse_state(self) -> None:
        """Панель свернута в ноль — на ее месте показывается кнопка возврата."""
        self._body_collapsed = self._is_pane_collapsed(self.body_splitter)
        self._changes_collapsed = self._is_pane_collapsed(self.changes_panel)
        if not self._body_collapsed and not self._changes_collapsed:
            self._remember_split_sizes()
        self._sync_restore_buttons()

    @staticmethod
    def _is_pane_collapsed(pane: QWidget) -> bool:
        """Панели нет на экране: нулевая высота, скрыта сплиттером или вынесена
        целиком за пределы его области. Последнее — как Qt прячет панель с явным
        минимумом размеров, когда ручку утянули в противоположный край: высота
        остается равной минимуму, флаг видимости не сбрасывается, но геометрия
        уходит за границу сплиттера и обрезается родителем."""
        if pane.height() <= 0 or not pane.isVisibleTo(pane.parentWidget()):
            return True
        splitter = pane.parentWidget()
        if splitter is None:
            return False
        return pane.geometry().intersected(splitter.rect()).height() <= 0

    def _remember_split_sizes(self) -> None:
        """Запоминает устоявшееся деление по фактическим высотам панелей: обе
        целиком внутри области сплиттера и вместе занимают всю его высоту.
        Промежуточные состояния перетаскивания и «вытесненная» панель с минимумом
        (сумма фактических высот больше области) не годятся для восстановления."""
        splitter = self.main_splitter
        heights = [self.body_splitter.height(), self.changes_panel.height()]
        if not all(heights):
            return
        available = splitter.height() - splitter.handleWidth() * (len(heights) - 1)
        both_inside = all(
            splitter.rect().contains(pane.geometry())
            for pane in (self.body_splitter, self.changes_panel)
        )
        if both_inside and available > 0 and sum(heights) == available:
            self._last_split_sizes = heights

    def _restore_body(self) -> None:
        """Возвращает верхнюю область: граф и боковые панели."""
        self._restore_panes()

    def _restore_changes(self) -> None:
        """Возвращает нижнюю панель «Изменения»."""
        self._restore_panes()

    def _restore_panes(self) -> None:
        sizes = list(self._last_split_sizes)
        if not sizes or any(size <= 0 for size in sizes):
            sizes = list(_INITIAL_SPLIT_SIZES)
        self.main_splitter.setSizes(sizes)
        self._update_collapse_state()

    def _sync_restore_buttons(self) -> None:
        self._sync_restore_button(
            self.body_restore_button, self._body_collapsed, at_bottom=False
        )
        self._sync_restore_button(
            self.changes_restore_button, self._changes_collapsed, at_bottom=True
        )

    def _sync_restore_button(
        self, button: QPushButton, visible: bool, *, at_bottom: bool
    ) -> None:
        if not visible:
            if not button.isHidden():
                button.hide()
            return
        self._place_restore_button(button, at_bottom=at_bottom)
        if button.isHidden():
            button.show()
        button.raise_()

    def _place_restore_button(self, button: QPushButton, *, at_bottom: bool) -> None:
        """Ставит кнопку по центру у верхнего или нижнего края сплиттера."""
        area = self.main_splitter.geometry()
        if area.height() <= 0:
            return
        button.adjustSize()
        x = area.center().x() - button.width() // 2
        if at_bottom:
            y = area.top() + area.height() - button.height() - _RESTORE_BUTTON_MARGIN
        else:
            y = area.top() + _RESTORE_BUTTON_MARGIN
        button.move(max(0, x), max(0, y))
