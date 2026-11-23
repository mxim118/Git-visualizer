"""Unit-тесты контроллера: фоновые задачи, состояние и сигналы."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from git_visualizer.application.errors import NotAGitRepositoryError
from git_visualizer.application.services.git_environment_service import GitEnvironment
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.domain.models import Branch, Commit, Remote, Repository
from git_visualizer.infrastructure.git.git_errors import GitCommandError, GitNotFoundError
from git_visualizer.presentation.controllers.repository_controller import (
    RepositoryController,
    WorkspaceData,
)
from git_visualizer.presentation.workers.task_runner import TaskFailure

NOW = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_commit(
    label: str,
    *,
    subject: str = "Subject",
    parents: tuple[str, ...] = (),
) -> Commit:
    commit_hash = label.ljust(40, "0")
    return Commit(
        hash=commit_hash,
        short_hash=commit_hash[:7],
        parent_hashes=parents,
        author_name="Test Author",
        author_email="author@example.com",
        authored_at=NOW,
        committer_name="Test Author",
        committer_email="author@example.com",
        committed_at=NOW,
        subject=subject,
        body="",
    )


def make_repository(
    path: Path,
    *,
    head_commit: str | None,
    name: str = "repo",
    branch: str | None = "main",
) -> Repository:
    return Repository(
        path=path, name=name, current_branch=branch, head_commit=head_commit
    )


class FakeEnvironmentService:
    def __init__(
        self,
        *,
        environment: GitEnvironment | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._environment = environment or GitEnvironment(
            version=(2, 54, 0), raw_version="git version 2.54.0"
        )
        self._error = error

    def check(self) -> GitEnvironment:
        if self._error is not None:
            raise self._error
        return self._environment


class FakeRepositoryService:
    def __init__(
        self,
        repository: Repository,
        *,
        remotes: tuple[Remote, ...] = (),
        error: BaseException | None = None,
    ) -> None:
        self.repository = repository
        self.remotes = remotes
        self.error = error
        self.open_paths: list[str] = []

    def open_repository(self, path: str) -> Repository:
        self.open_paths.append(path)
        if self.error is not None:
            raise self.error
        return self.repository

    def get_remotes(self, repository: Repository) -> tuple[Remote, ...]:
        return self.remotes


class FakeHistoryService:
    def __init__(
        self,
        commits: tuple[Commit, ...] = (),
        *,
        error: BaseException | None = None,
        gate: threading.Event | None = None,
        started: threading.Event | None = None,
    ) -> None:
        self.commits = commits
        self.error = error
        self._gate = gate
        self._started = started

    def get_history(self, repository: Repository) -> tuple[Commit, ...]:
        if self._started is not None:
            self._started.set()
        if self._gate is not None:
            assert self._gate.wait(5.0)
        if self.error is not None:
            raise self.error
        return self.commits


class FakeBranchService:
    def __init__(self, branches: tuple[Branch, ...]) -> None:
        self.branches = branches

    def get_branches(self, repository: Repository) -> tuple[Branch, ...]:
        return self.branches


def build_controller(
    *,
    repository: Repository,
    commits: tuple[Commit, ...] = (),
    branches: tuple[Branch, ...] = (),
    remotes: tuple[Remote, ...] = (),
    open_error: BaseException | None = None,
    history: FakeHistoryService | None = None,
    environment: GitEnvironment | None = None,
    environment_error: BaseException | None = None,
) -> tuple[RepositoryController, FakeRepositoryService, FakeHistoryService]:
    history_service = history or FakeHistoryService(commits)
    repository_service = FakeRepositoryService(
        repository, remotes=remotes, error=open_error
    )
    controller = RepositoryController(
        repository_service=repository_service,
        history_service=history_service,
        branch_service=FakeBranchService(branches),
        graph_service=GraphService(),
        git_environment_service=FakeEnvironmentService(
            environment=environment, error=environment_error
        ),
    )
    return controller, repository_service, history_service


def wait_until(
    qt_app,
    predicate: Callable[[], bool],
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("условие не выполнилось за отведённое время")
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()


def test_git_environment_is_checked_in_background(qt_app) -> None:
    environment = GitEnvironment(version=(2, 54, 0), raw_version="git version 2.54.0")
    controller, _, _ = build_controller(
        repository=make_repository(Path("repo"), head_commit=None),
        environment=environment,
    )
    ready: list[GitEnvironment] = []
    failed: list[TaskFailure] = []
    controller.git_environment_ready.connect(ready.append)
    controller.git_environment_failed.connect(failed.append)

    controller.check_git_environment()

    wait_until(qt_app, lambda: bool(ready or failed))
    assert ready == [environment]
    assert failed == []
    controller.wait_for_done()


def test_git_environment_failure_is_reported(qt_app) -> None:
    error = GitNotFoundError("git")
    controller, _, _ = build_controller(
        repository=make_repository(Path("repo"), head_commit=None),
        environment_error=error,
    )
    failed: list[TaskFailure] = []
    controller.git_environment_failed.connect(failed.append)

    controller.check_git_environment()

    wait_until(qt_app, lambda: bool(failed))
    assert failed[0].exception is error
    controller.wait_for_done()


def test_open_repository_loads_workspace_data(qt_app, tmp_path: Path) -> None:
    old = make_commit("old")
    new = make_commit("new", parents=(old.hash,))
    repository = make_repository(tmp_path, head_commit=new.hash, name="demo")
    branches = (
        Branch(name="main", is_remote=False, is_current=True, commit_hash=new.hash),
        Branch(name="origin/main", is_remote=True, is_current=False, commit_hash=new.hash),
    )
    remotes = (Remote(name="origin", fetch_url="https://example.com/demo.git"),)
    controller, repository_service, _ = build_controller(
        repository=repository,
        commits=(new, old),
        branches=branches,
        remotes=remotes,
    )
    opened: list[Repository] = []
    loaded: list[WorkspaceData] = []
    controller.repository_opened.connect(opened.append)
    controller.repository_loaded.connect(loaded.append)

    controller.open_repository(str(tmp_path))

    wait_until(qt_app, lambda: bool(loaded))
    assert opened == [repository]
    assert repository_service.open_paths == [str(tmp_path)]
    assert controller.repository is repository

    data = loaded[0]
    assert data.repository is repository
    assert data.commits == (new, old)
    assert data.branches == branches
    assert data.remotes == remotes
    assert [node.commit_hash for node in data.layout.nodes] == [new.hash, old.hash]
    assert data.ref_labels[new.hash][0].name == "main"
    assert data.ref_labels[new.hash][0].is_current
    assert any(
        label.name == "origin/main" for label in data.ref_labels[new.hash]
    )
    assert controller.workspace is data
    controller.wait_for_done()


def test_open_repository_failure_is_reported_as_task_failure(
    qt_app, tmp_path: Path
) -> None:
    error = NotAGitRepositoryError("Path is not inside a Git repository", path=tmp_path)
    controller, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=None),
        open_error=error,
    )
    failed: list[TaskFailure] = []
    opened: list[Repository] = []
    controller.open_failed.connect(failed.append)
    controller.repository_opened.connect(opened.append)

    controller.open_repository(str(tmp_path))

    wait_until(qt_app, lambda: bool(failed))
    assert failed[0].exception is error
    assert opened == []
    assert controller.repository is None
    controller.wait_for_done()


def test_history_failure_is_reported_without_repository_state(
    qt_app, tmp_path: Path
) -> None:
    error = GitCommandError(128, command=("git", "log"), stderr="fatal: bad object")
    controller, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit="a" * 40),
        history=FakeHistoryService(error=error),
    )
    failed: list[TaskFailure] = []
    loaded: list[WorkspaceData] = []
    controller.load_failed.connect(failed.append)
    controller.repository_loaded.connect(loaded.append)

    controller.open_repository(str(tmp_path))

    wait_until(qt_app, lambda: bool(failed))
    assert failed[0].exception is error
    assert loaded == []
    assert controller.repository is not None
    assert controller.workspace is None
    controller.wait_for_done()


def test_refresh_reloads_workspace_and_blocks_parallel_load(qt_app, tmp_path: Path) -> None:
    old = make_commit("old")
    controller, _, history = build_controller(
        repository=make_repository(tmp_path, head_commit=old.hash),
        commits=(old,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=old.hash),
        ),
    )
    loaded: list[WorkspaceData] = []
    controller.repository_loaded.connect(loaded.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: len(loaded) == 1)

    new = make_commit("new")
    history.commits = (new, old)
    assert controller.refresh() is True
    assert controller.refresh() is False  # загрузка уже идёт

    wait_until(qt_app, lambda: len(loaded) == 2)
    assert [commit.hash for commit in loaded[1].commits] == [new.hash, old.hash]
    controller.wait_for_done()


def test_close_repository_ignores_late_background_result(qt_app, tmp_path: Path) -> None:
    gate = threading.Event()
    started = threading.Event()
    commit = make_commit("only")
    controller, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        history=FakeHistoryService((commit,), gate=gate, started=started),
    )
    opened: list[Repository] = []
    loaded: list[WorkspaceData] = []
    closed: list[bool] = []
    controller.repository_opened.connect(opened.append)
    controller.repository_loaded.connect(loaded.append)
    controller.repository_closed.connect(lambda: closed.append(True))

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: started.is_set())
    assert opened == [controller.repository]
    controller.close_repository()
    assert closed == [True]
    assert controller.repository is None

    gate.set()
    controller.wait_for_done()
    qt_app.processEvents()

    assert loaded == []
    assert controller.workspace is None
