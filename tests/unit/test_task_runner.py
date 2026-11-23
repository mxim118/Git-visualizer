"""GUI-тесты механизма фоновых задач Presentation."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest
from PySide6.QtCore import QTimer

from git_visualizer.infrastructure.git.git_errors import GitCommandError
from git_visualizer.presentation.workers.task_runner import (
    BackgroundTask,
    TaskFailure,
    TaskRunner,
    TaskState,
)


def wait_for(
    predicate: Callable[[], bool],
    qt_app,
    *,
    timeout: float = 5.0,
) -> None:
    """Крутит event loop, пока условие не выполнится или не истечёт таймаут."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("task did not reach the expected state in time")
        qt_app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def runner(qt_app) -> TaskRunner:
    runner = TaskRunner(max_thread_count=1)
    yield runner
    qt_app.processEvents()
    runner.wait_for_done(5000)


def test_result_is_delivered_through_signal_and_gui_stays_responsive(
    qt_app, runner: TaskRunner
) -> None:
    ticks: list[bool] = []
    results: list[object] = []

    def work() -> int:
        time.sleep(0.4)
        return 42

    started_at = time.monotonic()
    task = runner.submit(work, name="sample")
    submit_duration = time.monotonic() - started_at

    assert isinstance(task, BackgroundTask)
    assert task.state is TaskState.PENDING
    assert submit_duration < 0.25

    task.succeeded.connect(results.append)
    QTimer.singleShot(20, lambda: ticks.append(True))
    wait_for(lambda: task.state is TaskState.SUCCEEDED, qt_app)

    assert results == [42]
    assert ticks == [True]
    assert task.is_finished
    assert runner.active_tasks() == ()


def test_started_and_finished_signals_follow_state_transitions(
    qt_app, runner: TaskRunner
) -> None:
    events: list[object] = []
    states: list[TaskState] = []

    task = runner.submit(lambda: 7)
    assert task is not None
    task.started.connect(lambda: events.append("started"))
    task.succeeded.connect(lambda value: events.append(("succeeded", value)))
    task.finished.connect(lambda: events.append("finished"))
    task.state_changed.connect(states.append)

    wait_for(lambda: task.state is TaskState.SUCCEEDED, qt_app)

    assert events == ["started", ("succeeded", 7), "finished"]
    assert states == [TaskState.RUNNING, TaskState.SUCCEEDED]


def test_error_is_delivered_with_technical_details(
    qt_app, runner: TaskRunner
) -> None:
    results: list[object] = []
    failures: list[TaskFailure] = []
    finished: list[bool] = []
    states: list[TaskState] = []

    def work() -> None:
        raise GitCommandError(
            128,
            command=("git", "log"),
            stderr="fatal: bad object",
        )

    task = runner.submit(work)
    assert task is not None
    task.succeeded.connect(results.append)
    task.failed.connect(failures.append)
    task.finished.connect(lambda: finished.append(True))
    task.state_changed.connect(states.append)

    wait_for(lambda: task.state is TaskState.FAILED, qt_app)

    assert results == []
    assert finished == [True]
    assert states == [TaskState.RUNNING, TaskState.FAILED]
    assert len(failures) == 1
    failure = failures[0]
    assert isinstance(failure, TaskFailure)
    assert "exit code 128" in failure.message
    assert "Command: git log" in failure.technical_details
    assert "fatal: bad object" in failure.technical_details
    assert isinstance(failure.exception, GitCommandError)
    assert "in work" in failure.traceback_text
    assert task.is_finished
    assert runner.active_tasks() == ()

    # После ошибки механизм остаётся работоспособным.
    follow_up = runner.submit(lambda: "after-error")
    assert follow_up is not None
    wait_for(lambda: follow_up.state is TaskState.SUCCEEDED, qt_app)


def test_worker_runs_off_gui_thread_and_callbacks_run_in_gui_thread(
    qt_app, runner: TaskRunner
) -> None:
    gui_thread_id = threading.get_ident()
    worker_thread_ids: list[int] = []
    slot_thread_ids: list[int] = []
    state_thread_ids: list[int] = []

    def work() -> str:
        worker_thread_ids.append(threading.get_ident())
        return "ok"

    task = runner.submit(work)
    assert task is not None
    task.succeeded.connect(
        lambda _result: slot_thread_ids.append(threading.get_ident())
    )
    task.state_changed.connect(
        lambda _state: state_thread_ids.append(threading.get_ident())
    )

    wait_for(lambda: task.state is TaskState.SUCCEEDED, qt_app)

    assert worker_thread_ids
    assert worker_thread_ids[0] != gui_thread_id
    assert slot_thread_ids == [gui_thread_id]
    assert state_thread_ids == [gui_thread_id, gui_thread_id]


def test_conflicting_key_is_blocked_and_released_after_finish(
    qt_app, runner: TaskRunner
) -> None:
    gate = threading.Event()

    first = runner.submit(
        lambda: gate.wait(5.0) and "first",
        name="first",
        key="history",
    )
    assert first is not None
    wait_for(lambda: first.state is TaskState.RUNNING, qt_app)
    assert runner.active_task("history") is first
    assert runner.is_running("history")

    assert runner.submit(lambda: "blocked", key="history") is None

    other = runner.submit(lambda: "other", name="other", key="other")
    assert other is not None
    qt_app.processEvents()
    assert other.state is TaskState.PENDING
    assert runner.is_running("other")

    gate.set()
    wait_for(lambda: first.is_finished and other.is_finished, qt_app)

    assert runner.active_task("history") is None
    assert runner.active_task("other") is None
    assert runner.active_tasks() == ()

    again = runner.submit(lambda: "again", key="history")
    assert again is not None
    wait_for(lambda: again.is_finished, qt_app)
    assert runner.active_task("history") is None


def test_submit_from_worker_thread_is_rejected(
    qt_app, runner: TaskRunner
) -> None:
    errors: list[str] = []

    def work() -> None:
        try:
            runner.submit(lambda: 1)
        except RuntimeError as error:
            errors.append(str(error))

    task = runner.submit(work)
    assert task is not None
    wait_for(lambda: task.is_finished, qt_app)

    assert errors
    assert "thread" in errors[0]


def test_submit_rejects_non_callable(runner: TaskRunner) -> None:
    with pytest.raises(TypeError):
        runner.submit("not callable")  # type: ignore[arg-type]
