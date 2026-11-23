"""Фоновые задачи Presentation на ``QThreadPool`` + ``QRunnable``.

Единый механизм для всех операций, которые нельзя выполнять в GUI-потоке:
callable запускается в пуле потоков, а результат или ошибка возвращаются
в поток владельца задачи через Qt-сигналы. Application Services остаются
обычной синхронной логикой; решение о запуске принимает Presentation.

Рабочий callable не должен обращаться к Qt-виджетам: всё, что связано с UI,
выполняется в слотах сигналов ``succeeded``/``failed``/``finished``, которые
доставляются в GUI-поток. Для конфликтующих операций можно задать ``key``:
повторный запуск с тем же ключом вернёт ``None``, пока предыдущая задача
не завершилась (``active_task(key)``).

Пример::

    runner = TaskRunner(self)
    task = runner.submit(load_history, name="history", key="history")
    if task is None:
        show_message("История уже загружается")
    else:
        task.started.connect(show_busy)
        task.succeeded.connect(show_history)
        task.failed.connect(show_error)   # TaskFailure с техническими деталями
        task.finished.connect(clear_busy)
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from functools import partial

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal


class TaskState(Enum):
    """Состояние фоновой задачи."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, kw_only=True)
class TaskFailure:
    """Ошибка фоновой задачи, пригодная для показа в GUI.

    ``technical_details`` — результат ``exception.technical_details()``, если он
    есть (так устроены ошибки Git), иначе traceback. ``traceback_text`` хранится
    отдельно для журнала и раздела «Подробнее».
    """

    message: str
    technical_details: str
    traceback_text: str
    exception: BaseException

    @classmethod
    def from_exception(cls, error: BaseException) -> "TaskFailure":
        """Собирает описание ошибки; не бросает собственных исключений."""
        try:
            message = str(error) or type(error).__name__
        except Exception:  # noqa: BLE001 - описание не должно теряться из-за __str__
            message = type(error).__name__

        try:
            traceback_text = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
        except Exception:  # noqa: BLE001
            traceback_text = type(error).__name__

        technical_details = ""
        details = getattr(error, "technical_details", None)
        if callable(details):
            try:
                technical_details = str(details())
            except Exception:  # noqa: BLE001 - ошибка форматирования не должна скрывать сбой
                technical_details = ""
        if not technical_details:
            technical_details = traceback_text

        return cls(
            message=message,
            technical_details=technical_details,
            traceback_text=traceback_text,
            exception=error,
        )


class _TaskSignals(QObject):
    """Внутренние сигналы worker-потока; соединены с задачей как queued."""

    started = Signal()
    succeeded = Signal(object)
    failed = Signal(object)


class _TaskRunnable(QRunnable):
    """Выполняет callable и сообщает результат через внутренние сигналы."""

    def __init__(
        self,
        work: Callable[[], object],
        signals: _TaskSignals,
    ) -> None:
        super().__init__()
        self._work = work
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        self._signals.started.emit()
        try:
            result = self._work()
        except BaseException as error:  # noqa: BLE001 - задача обязана завершиться failed
            self._signals.failed.emit(TaskFailure.from_exception(error))
        else:
            self._signals.succeeded.emit(result)


class BackgroundTask(QObject):
    """Одна фоновая задача: состояние, сигналы и жизненный цикл.

    Создаётся через :meth:`TaskRunner.submit`. Публичные сигналы испускаются
    в потоке владельца задачи (GUI), поэтому в их слотах можно менять виджеты.
    """

    started = Signal()
    state_changed = Signal(object)
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(
        self,
        work: Callable[[], object],
        *,
        name: str = "",
        key: str | None = None,
    ) -> None:
        super().__init__()
        self._work = work
        self.name = name or getattr(work, "__name__", "") or "task"
        self.key = key
        self._state = TaskState.PENDING
        self._runnable: _TaskRunnable | None = None
        # Без parent: worker держит ссылку, пока выполняется. Если задача будет
        # уничтожена раньше, Qt безопасно отбросит доставку в удалённый объект.
        self._signals: _TaskSignals | None = _TaskSignals()
        signals = self._signals
        signals.started.connect(
            self._handle_started, Qt.ConnectionType.QueuedConnection
        )
        signals.succeeded.connect(
            self._handle_succeeded, Qt.ConnectionType.QueuedConnection
        )
        signals.failed.connect(
            self._handle_failed, Qt.ConnectionType.QueuedConnection
        )

    @property
    def state(self) -> TaskState:
        return self._state

    @property
    def is_finished(self) -> bool:
        return self._state in (TaskState.SUCCEEDED, TaskState.FAILED)

    def _start(self, pool: QThreadPool) -> None:
        """Ставит задачу в пул; вызывается TaskRunner в GUI-потоке."""
        if not self.thread().isCurrentThread():
            raise RuntimeError("BackgroundTask must be started in its owner thread")
        if self._state is not TaskState.PENDING:
            return
        signals = self._signals
        if signals is None:
            return
        runnable = _TaskRunnable(self._work, signals)
        self._runnable = runnable
        try:
            pool.start(runnable)
        except Exception as error:  # noqa: BLE001 - пул недоступен, задача не стартовала
            self._handle_failed(TaskFailure.from_exception(error))

    def _handle_started(self) -> None:
        if self._state is not TaskState.PENDING:
            return
        self._set_state(TaskState.RUNNING)
        self.started.emit()

    def _handle_succeeded(self, result: object) -> None:
        if self.is_finished:
            return
        self._set_state(TaskState.SUCCEEDED)
        self.succeeded.emit(result)
        self._finish()

    def _handle_failed(self, failure: TaskFailure) -> None:
        if self.is_finished:
            return
        self._set_state(TaskState.FAILED)
        self.failed.emit(failure)
        self._finish()

    def _set_state(self, state: TaskState) -> None:
        self._state = state
        self.state_changed.emit(state)

    def _finish(self) -> None:
        self._work = None  # type: ignore[assignment] - ссылка больше не нужна
        self._runnable = None
        signals = self._signals
        self._signals = None
        if signals is not None:
            # Разрываем цикл task → signals → task; соединения снимаются
            # при удалении объекта в потоке владельца.
            signals.deleteLater()
        self.finished.emit()


class TaskRunner(QObject):
    """Запускает фоновые задачи и управляет их временем жизни.

    Один экземпляр на окно/контроллер Presentation. Все методы, кроме
    рабочего callable, вызываются в GUI-потоке. Пул принадлежит раннеру,
    поэтому задачи не смешиваются с глобальным пулом приложения.

    Рабочий callable получает только данные и не должен обращаться к Qt-виджетам;
    менять интерфейс разрешено только в слотах ``succeeded``/``failed``/
    ``finished``, которые исполняются в потоке владельца задачи.
    """

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        max_thread_count: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        if max_thread_count is not None:
            if max_thread_count < 1:
                raise ValueError("max_thread_count must be at least 1")
            self._pool.setMaxThreadCount(max_thread_count)
        self._tasks: set[BackgroundTask] = set()
        self._by_key: dict[str, BackgroundTask] = {}
        self._pending_starts: list[BackgroundTask] = []
        # Старт откладывается на следующую итерацию event loop: вызывающий
        # успевает подключить сигналы и увидеть состояние pending. Таймер
        # принадлежит раннеру и уничтожается вместе с ним.
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.timeout.connect(self._start_pending)

    @property
    def max_thread_count(self) -> int:
        return self._pool.maxThreadCount()

    def submit(
        self,
        work: Callable[[], object],
        *,
        name: str = "",
        key: str | None = None,
    ) -> BackgroundTask | None:
        """Планирует задачу.

        :return: задача или ``None``, если операция с таким ``key`` уже
            выполняется. Параметр ``key`` — явное управление конфликтами:
            задачи без ключа могут выполняться параллельно.
        """
        self._ensure_owner_thread()
        if not callable(work):
            raise TypeError("work must be callable")

        if key is not None and key in self._by_key:
            return None

        task = BackgroundTask(work, name=name, key=key)
        self._tasks.add(task)
        if key is not None:
            self._by_key[key] = task
        task.finished.connect(partial(self._release, task))
        self._pending_starts.append(task)
        if not self._start_timer.isActive():
            self._start_timer.start(0)
        return task

    def active_task(self, key: str) -> BackgroundTask | None:
        """Активная (pending/running) задача с данным ключом."""
        return self._by_key.get(key)

    def is_running(self, key: str) -> bool:
        return key in self._by_key

    def active_tasks(self) -> tuple[BackgroundTask, ...]:
        return tuple(self._tasks)

    def wait_for_done(self, msecs: int = -1) -> bool:
        """Ждёт завершения задач пула; полезно при закрытии окна и в тестах."""
        if self._pending_starts and self.thread().isCurrentThread():
            self._start_pending()
        return self._pool.waitForDone(msecs)

    def _start_pending(self) -> None:
        self._start_timer.stop()
        pending = self._pending_starts
        self._pending_starts = []
        for task in pending:
            if task in self._tasks:
                task._start(self._pool)

    def _release(self, task: BackgroundTask) -> None:
        self._tasks.discard(task)
        if task.key is not None and self._by_key.get(task.key) is task:
            del self._by_key[task.key]

    def _ensure_owner_thread(self) -> None:
        if not self.thread().isCurrentThread():
            raise RuntimeError(
                "TaskRunner.submit must be called in the thread that owns the runner"
            )
