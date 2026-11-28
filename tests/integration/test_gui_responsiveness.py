"""Integration-тесты отзывчивости GUI на медленных и больших загрузках.

Тесты идут через ``MainWindow`` и настоящий Git, а не через один ``TaskRunner``:
именно так проверяется, что Git не вызывается в GUI-потоке и что заполнение
``QGraphicsScene`` не блокирует event loop на несколько секунд.

Первый тест детерминированно замедляет ``git log`` и считает тики ``QTimer``;
второй загружает историю из 3000 коммитов и проверяет максимальный разрыв между
тиками, включая построение графа в GUI-потоке. Абсолютное время загрузки не
является критерием: на разных машинах оно разное, а «зависание» видно по разрыву
event loop.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog

from git_visualizer.application.services.branch_service import BranchService
from git_visualizer.application.services.changes_service import ChangesService
from git_visualizer.application.services.commit_service import CommitService
from git_visualizer.application.services.diff_service import DiffService
from git_visualizer.application.services.git_environment_service import (
    GitEnvironmentService,
)
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.application.services.history_service import HistoryService
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.application.services.remote_service import RemoteService
from git_visualizer.infrastructure.git.git_repository import GitRepository
from git_visualizer.infrastructure.git.git_runner import GitRunner
from git_visualizer.presentation.controllers.repository_controller import (
    RepositoryController,
)
from git_visualizer.presentation.windows.main_window import MainWindow

LARGE_HISTORY_COMMITS = 3000
HEARTBEAT_INTERVAL_MS = 20
# Запас на медленную CI-машину: проверяем именно многократное зависание,
# а не точное время загрузки. Секунда без обработки событий уже заметна.
MAX_SLOW_LOG_GAP_SECONDS = 1.0
MAX_EVENT_LOOP_GAP_SECONDS = 2.0


class EventLoopHeartbeat:
    """Считает тики event loop и максимальный разрыв между ними."""

    def __init__(self, interval_ms: int = HEARTBEAT_INTERVAL_MS) -> None:
        self._timer = QTimer()
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)
        self._last_tick: float | None = None
        self._gaps: list[float] = []

    def start(self) -> None:
        self._last_tick = time.perf_counter()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def tick_count(self) -> int:
        return len(self._gaps)

    @property
    def max_gap(self) -> float:
        return max(self._gaps, default=0.0)

    def _tick(self) -> None:
        now = time.perf_counter()
        if self._last_tick is not None:
            self._gaps.append(now - self._last_tick)
        self._last_tick = now


def build_controller(runner: GitRunner | None = None) -> RepositoryController:
    """Собирает контроллер так же, как composition root, но с нужным runner."""
    runner = runner or GitRunner()

    def factory(path: Path) -> GitRepository:
        return GitRepository(runner, path)

    return RepositoryController(
        repository_service=RepositoryService(factory),
        history_service=HistoryService(factory),
        branch_service=BranchService(factory),
        graph_service=GraphService(),
        diff_service=DiffService(factory),
        changes_service=ChangesService(factory),
        commit_service=CommitService(factory),
        remote_service=RemoteService(factory),
        git_environment_service=GitEnvironmentService(runner),
    )


def wait_until(
    qt_app,
    predicate: Callable[[], bool],
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("условие не выполнилось за отведённое время")
        qt_app.processEvents()
        time.sleep(0.01)
    qt_app.processEvents()


def open_repository(
    qt_app,
    window: MainWindow,
    path: Path,
    monkeypatch,
    *,
    timeout: float,
) -> None:
    """Открывает репозиторий через стартовый экран и ждёт загрузки данных."""
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    wait_until(
        qt_app,
        lambda: "поддерживается"
        in window.start_screen.git_status_label.text(),
        timeout=10.0,
    )
    window.start_screen.open_button.click()
    wait_until(
        qt_app,
        lambda: window.workspace_page.workspace_data is not None,
        timeout=timeout,
    )


def import_linear_history(git, path: Path, count: int) -> None:
    """Быстро создаёт ``count`` коммитов через ``git fast-import``.

    Отдельный ``git commit`` на каждый коммит сделал бы подготовку данных
    дороже самой проверки; fast-import создаёт историю одним процессом.
    """
    stream: list[str] = []
    for number in range(count):
        message = f"commit {number}"
        content = f"{number}\n"
        timestamp = 1_700_000_000 + number
        stream.extend(
            [
                "commit refs/heads/main",
                f"mark :{number + 1}",
                f"author Test <test@example.com> {timestamp} +0000",
                f"committer Test <test@example.com> {timestamp} +0000",
                f"data {len(message.encode('utf-8'))}",
                message,
                "M 100644 inline file.txt",
                f"data {len(content.encode('utf-8'))}",
                content,
                "",
            ]
        )
    git(path, "fast-import", "--quiet", stdin="\n".join(stream))


def test_gui_stays_responsive_while_git_log_is_slow(
    qt_app,
    make_repo,
    monkeypatch,
    app_settings,
) -> None:
    """GUI обрабатывает события, пока ``git log`` выполняется в фоне."""
    path = make_repo("slow-log", commits=2)
    real_run = subprocess.run

    def slow_run(command, **kwargs):
        # Замедляем только историю; открытие репозитория и проверка версии — быстрые.
        if list(command)[1:2] == ["log"]:
            time.sleep(1.5)
        return real_run(command, **kwargs)

    controller = build_controller(GitRunner(process_runner=slow_run))
    window = MainWindow(controller, app_settings)
    heartbeat = EventLoopHeartbeat()

    try:
        heartbeat.start()
        started_at = time.perf_counter()
        open_repository(qt_app, window, path, monkeypatch, timeout=20.0)
        elapsed = time.perf_counter() - started_at
        heartbeat.stop()

        data = window.workspace_page.workspace_data
        assert data is not None
        assert len(data.commits) == 2
        assert window.workspace_page.graph_view.commit_count == 2
        # Задержка действительно сработала, иначе тест не проверил бы медленный Git.
        assert elapsed >= 1.4
        assert heartbeat.tick_count >= 20, (
            f"GUI почти не обрабатывал события за {elapsed:.2f} с: "
            f"{heartbeat.tick_count} тиков"
        )
        assert heartbeat.max_gap < MAX_SLOW_LOG_GAP_SECONDS, (
            f"event loop стоял {heartbeat.max_gap:.3f} с при медленном git log; "
            f"всего {elapsed:.2f} с"
        )
    finally:
        heartbeat.stop()
        window.close()


def test_gui_loads_large_history_without_long_event_loop_stalls(
    qt_app,
    make_repo,
    git,
    monkeypatch,
    app_settings,
) -> None:
    """3000 коммитов: загрузка и заполнение графа не дают долгих пауз."""
    path = make_repo("large-history", commits=0)
    import_linear_history(git, path, LARGE_HISTORY_COMMITS)
    assert git(path, "rev-list", "--count", "HEAD").strip() == str(
        LARGE_HISTORY_COMMITS
    )

    controller = build_controller()
    window = MainWindow(controller, app_settings)
    heartbeat = EventLoopHeartbeat()

    try:
        heartbeat.start()
        started_at = time.perf_counter()
        open_repository(qt_app, window, path, monkeypatch, timeout=60.0)

        workspace = window.workspace_page
        data = workspace.workspace_data
        assert data is not None
        assert len(data.commits) == LARGE_HISTORY_COMMITS

        # Большая сцена строится порциями уже после появления workspace_data:
        # ждём завершения, иначе замер не увидит заполнение QGraphicsScene.
        wait_until(
            qt_app,
            lambda: not workspace.graph_view.is_building
            and workspace.graph_view.commit_count == LARGE_HISTORY_COMMITS,
            timeout=60.0,
        )
        elapsed = time.perf_counter() - started_at
        heartbeat.stop()

        assert workspace.graph_view.commit_count == LARGE_HISTORY_COMMITS
        assert data.has_more_history is False

        # Выбор коммита после большой загрузки должен работать без перезапуска.
        workspace.graph_view.commit_selected.emit(data.commits[0].hash)
        assert workspace.details_panel.current_commit is not None

        assert heartbeat.tick_count >= 5, (
            f"GUI почти не обрабатывал события за {elapsed:.2f} с: "
            f"{heartbeat.tick_count} тиков"
        )
        assert heartbeat.max_gap < MAX_EVENT_LOOP_GAP_SECONDS, (
            f"event loop стоял {heartbeat.max_gap:.3f} с при загрузке "
            f"{LARGE_HISTORY_COMMITS} коммитов; всего {elapsed:.2f} с"
        )
    finally:
        heartbeat.stop()
        window.close()
