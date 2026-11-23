"""Unit-С‚РµСЃС‚С‹ РєРѕРЅС‚СЂРѕР»Р»РµСЂР°: С„РѕРЅРѕРІС‹Рµ Р·Р°РґР°С‡Рё, СЃРѕСЃС‚РѕСЏРЅРёРµ Рё СЃРёРіРЅР°Р»С‹."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from git_visualizer.application.errors import NotAGitRepositoryError
from git_visualizer.application.services.git_environment_service import GitEnvironment
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import (
    Branch,
    Commit,
    Diff,
    FileChange,
    Remote,
    Repository,
)
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


def _first_parent(commit: Commit) -> str | None:
    return commit.parent_hashes[0] if commit.parent_hashes else None


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


class FakeDiffService:
    def __init__(
        self,
        files: tuple[FileChange, ...] = (),
        diff: Diff | None = None,
        *,
        files_error: BaseException | None = None,
        diff_error: BaseException | None = None,
    ) -> None:
        self.files = files
        self.diff = diff
        self.files_error = files_error
        self.diff_error = diff_error
        self.file_requests: list[tuple[str, str | None]] = []
        self.diff_requests: list[tuple[str, str]] = []

    def get_commit_files(
        self, repository: Repository, commit: Commit
    ) -> tuple[FileChange, ...]:
        self.file_requests.append((commit.hash, _first_parent(commit)))
        if self.files_error is not None:
            raise self.files_error
        return self.files

    def get_file_diff(
        self, repository: Repository, commit: Commit, file_change: FileChange
    ) -> Diff:
        self.diff_requests.append((commit.hash, file_change.path))
        if self.diff_error is not None:
            raise self.diff_error
        assert self.diff is not None
        return self.diff


def build_controller(
    *,
    repository: Repository,
    commits: tuple[Commit, ...] = (),
    branches: tuple[Branch, ...] = (),
    remotes: tuple[Remote, ...] = (),
    open_error: BaseException | None = None,
    history: FakeHistoryService | None = None,
    files: tuple[FileChange, ...] = (),
    diff: Diff | None = None,
    files_error: BaseException | None = None,
    diff_error: BaseException | None = None,
    environment: GitEnvironment | None = None,
    environment_error: BaseException | None = None,
) -> tuple[
    RepositoryController,
    FakeRepositoryService,
    FakeHistoryService,
    FakeDiffService,
]:
    history_service = history or FakeHistoryService(commits)
    repository_service = FakeRepositoryService(
        repository, remotes=remotes, error=open_error
    )
    diff_service = FakeDiffService(
        files, diff, files_error=files_error, diff_error=diff_error
    )
    controller = RepositoryController(
        repository_service=repository_service,
        history_service=history_service,
        branch_service=FakeBranchService(branches),
        graph_service=GraphService(),
        diff_service=diff_service,
        git_environment_service=FakeEnvironmentService(
            environment=environment, error=environment_error
        ),
    )
    return controller, repository_service, history_service, diff_service


def wait_until(
    qt_app,
    predicate: Callable[[], bool],
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("СѓСЃР»РѕРІРёРµ РЅРµ РІС‹РїРѕР»РЅРёР»РѕСЃСЊ Р·Р° РѕС‚РІРµРґС‘РЅРЅРѕРµ РІСЂРµРјСЏ")
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()


def test_git_environment_is_checked_in_background(qt_app) -> None:
    environment = GitEnvironment(version=(2, 54, 0), raw_version="git version 2.54.0")
    controller, _, _, _ = build_controller(
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
    controller, _, _, _ = build_controller(
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
    controller, repository_service, _, _ = build_controller(
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
    controller, _, _, _ = build_controller(
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
    controller, _, _, _ = build_controller(
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
    controller, _, history, _ = build_controller(
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
    assert controller.refresh() is False  # Р·Р°РіСЂСѓР·РєР° СѓР¶Рµ РёРґС‘С‚

    wait_until(qt_app, lambda: len(loaded) == 2)
    assert [commit.hash for commit in loaded[1].commits] == [new.hash, old.hash]
    controller.wait_for_done()


def test_commit_files_and_diff_are_loaded_for_first_parent(
    qt_app, tmp_path: Path
) -> None:
    first_parent = make_commit("first")
    second_parent = make_commit("second")
    merge = make_commit("merge", parents=(first_parent.hash, second_parent.hash))
    file_change = FileChange(
        path="src/файл с пробелом.py",
        change_type=FileChangeType.MODIFIED,
    )
    diff = Diff(
        path=file_change.path,
        text="--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new\n",
        base_commit_hash=first_parent.hash,
    )
    controller, _, _, diff_service = build_controller(
        repository=make_repository(tmp_path, head_commit=merge.hash),
        commits=(merge, first_parent, second_parent),
        branches=(
            Branch(
                name="main",
                is_remote=False,
                is_current=True,
                commit_hash=merge.hash,
            ),
        ),
        files=(file_change,),
        diff=diff,
    )
    opening = []
    files_loaded = []
    diff_loaded = []
    controller.commit_files_loading.connect(opening.append)
    controller.commit_files_loaded.connect(
        lambda commit_hash, files: files_loaded.append((commit_hash, files))
    )
    controller.file_diff_loaded.connect(
        lambda commit_hash, path, diff: diff_loaded.append((commit_hash, path, diff))
    )

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)

    assert controller.load_commit_files(merge.hash) is True
    wait_until(qt_app, lambda: bool(files_loaded))
    assert opening == [merge.hash]
    assert files_loaded[0] == (merge.hash, (file_change,))
    assert diff_service.file_requests == [(merge.hash, first_parent.hash)]

    assert controller.load_file_diff(merge.hash, file_change) is True
    wait_until(qt_app, lambda: bool(diff_loaded))
    assert diff_loaded[0] == (merge.hash, file_change.path, diff)
    assert diff_service.diff_requests == [(merge.hash, file_change.path)]
    controller.wait_for_done()


def test_commit_files_error_and_diff_error_are_reported(
    qt_app, tmp_path: Path
) -> None:
    commit = make_commit("commit")
    file_change = FileChange(path="file.txt", change_type=FileChangeType.ADDED)
    files_error = GitCommandError(128, command=("git", "diff-tree"))
    diff_error = GitCommandError(128, command=("git", "diff"))
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        files_error=files_error,
        diff_error=diff_error,
    )
    files_failed = []
    diff_failed = []
    controller.commit_files_failed.connect(
        lambda commit_hash, failure: files_failed.append((commit_hash, failure))
    )
    controller.file_diff_failed.connect(
        lambda commit_hash, path, failure: diff_failed.append(
            (commit_hash, path, failure)
        )
    )

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)

    assert controller.load_commit_files(commit.hash) is True
    wait_until(qt_app, lambda: bool(files_failed))
    assert files_failed[0][0] == commit.hash
    assert files_failed[0][1].exception is files_error

    assert controller.load_file_diff(commit.hash, file_change) is True
    wait_until(qt_app, lambda: bool(diff_failed))
    failed_hash, failed_path, failure = diff_failed[0]
    assert failed_hash == commit.hash
    assert failed_path == file_change.path
    assert failure.exception is diff_error
    controller.wait_for_done()


def test_root_commit_requests_empty_tree_comparison(qt_app, tmp_path: Path) -> None:
    root = make_commit("root")
    file_change = FileChange(path="new file.txt", change_type=FileChangeType.ADDED)
    diff = Diff(path=file_change.path, text="new file\n", base_commit_hash=None)
    controller, _, _, diff_service = build_controller(
        repository=make_repository(tmp_path, head_commit=root.hash),
        commits=(root,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=root.hash),
        ),
        files=(file_change,),
        diff=diff,
    )
    loaded = []
    diff_loaded = []
    controller.commit_files_loaded.connect(
        lambda commit_hash, files: loaded.append((commit_hash, files))
    )
    controller.file_diff_loaded.connect(
        lambda commit_hash, path, diff: diff_loaded.append((commit_hash, path, diff))
    )

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    controller.load_commit_files(root.hash)
    wait_until(qt_app, lambda: bool(loaded))
    controller.load_file_diff(root.hash, file_change)
    wait_until(qt_app, lambda: bool(diff_loaded))

    assert diff_service.file_requests == [(root.hash, None)]
    assert diff_service.diff_requests == [(root.hash, file_change.path)]
    controller.wait_for_done()


def test_close_repository_ignores_late_background_result(qt_app, tmp_path: Path) -> None:
    gate = threading.Event()
    started = threading.Event()
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
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
