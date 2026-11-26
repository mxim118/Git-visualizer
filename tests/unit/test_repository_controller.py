"""Unit-С‚РµСЃС‚С‹ РєРѕРЅС‚СЂРѕР»Р»РµСЂР°: С„РѕРЅРѕРІС‹Рµ Р·Р°РґР°С‡Рё, СЃРѕСЃС‚РѕСЏРЅРёРµ Рё СЃРёРіРЅР°Р»С‹."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

from git_visualizer.application.errors import (
    NotAGitRepositoryError,
    UpstreamNotConfiguredError,
)
from git_visualizer.application.services.git_environment_service import GitEnvironment
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.application.services.history_service import DEFAULT_HISTORY_LIMIT
from git_visualizer.application.services.remote_service import (
    RemoteConfigurationResult,
    RemoteOperationResult,
)
from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import (
    Branch,
    ChangedFile,
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
        self.limits: list[int] = []

    def get_history(
        self, repository: Repository, *, limit: int
    ) -> tuple[Commit, ...]:
        self.limits.append(limit)
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
        self.created: list[tuple[str, str | None]] = []
        self.switched: list[str] = []

    def get_branches(self, repository: Repository) -> tuple[Branch, ...]:
        return self.branches

    def create_branch(
        self, repository: Repository, name: str, *, start_point: str | None = None
    ) -> None:
        self.created.append((name, start_point))

    def switch_branch(self, repository: Repository, name: str) -> None:
        self.switched.append(name)


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


class FakeChangesService:
    def __init__(
        self,
        changes: tuple[ChangedFile, ...] = (),
        diff: Diff | None = None,
        *,
        changes_error: BaseException | None = None,
        diff_error: BaseException | None = None,
        operation_error: BaseException | None = None,
    ) -> None:
        self.changes = changes
        self.diff = diff
        self.changes_error = changes_error
        self.diff_error = diff_error
        self.operation_error = operation_error
        self.after_discard: tuple[ChangedFile, ...] | None = None
        self.status_requests = 0
        self.diff_requests: list[tuple[str, bool]] = []
        self.stage_requests: list[ChangedFile] = []
        self.unstage_requests: list[ChangedFile] = []
        self.stage_all_requests = 0
        self.unstage_all_requests = 0
        self.discard_requests: list[ChangedFile] = []

    def get_status(self, repository: Repository) -> tuple[ChangedFile, ...]:
        self.status_requests += 1
        if self.changes_error is not None:
            raise self.changes_error
        return self.changes

    def get_file_diff(
        self, repository: Repository, changed_file: ChangedFile, *, staged: bool
    ) -> Diff:
        self.diff_requests.append((changed_file.path, staged))
        if self.diff_error is not None:
            raise self.diff_error
        assert self.diff is not None
        return self.diff

    def _raise_operation_error(self) -> None:
        if self.operation_error is not None:
            raise self.operation_error

    def stage_file(
        self, repository: Repository, changed_file: ChangedFile
    ) -> None:
        self.stage_requests.append(changed_file)
        self._raise_operation_error()

    def unstage_file(
        self, repository: Repository, changed_file: ChangedFile
    ) -> None:
        self.unstage_requests.append(changed_file)
        self._raise_operation_error()

    def stage_all(self, repository: Repository) -> None:
        self.stage_all_requests += 1
        self._raise_operation_error()

    def unstage_all(self, repository: Repository) -> None:
        self.unstage_all_requests += 1
        self._raise_operation_error()

    def discard_file(
        self, repository: Repository, changed_file: ChangedFile
    ) -> None:
        self.discard_requests.append(changed_file)
        self._raise_operation_error()
        if self.after_discard is not None:
            self.changes = self.after_discard


class FakeCommitService:
    def __init__(
        self,
        *,
        error: BaseException | None = None,
        gate: threading.Event | None = None,
        started: threading.Event | None = None,
    ) -> None:
        self.error = error
        self._gate = gate
        self._started = started
        self.requests: list[str] = []

    def commit(self, repository: Repository, message: str) -> None:
        self.requests.append(message)
        if self._started is not None:
            self._started.set()
        if self._gate is not None:
            assert self._gate.wait(5.0)
        if self.error is not None:
            raise self.error


class FakeRemoteService:
    def __init__(
        self,
        *,
        error: BaseException | None = None,
        configuration_error: BaseException | None = None,
        remotes: tuple[Remote, ...] = (Remote(name="origin"),),
        gate: threading.Event | None = None,
        started: threading.Event | None = None,
    ) -> None:
        self.error = error
        self.configuration_error = configuration_error
        self.remotes = remotes
        self._gate = gate
        self._started = started
        self.fetch_requests: list[str] = []
        self.pull_requests = 0
        self.push_requests = 0
        self.initial_push_requests: list[str] = []
        self.add_requests: list[tuple[str, str]] = []
        self.edit_requests: list[tuple[str, str, str]] = []
        self.remove_requests: list[str] = []

    def _wait(self) -> None:
        if self._started is not None:
            self._started.set()
        if self._gate is not None:
            assert self._gate.wait(5.0)

    def _configuration_result(
        self,
        operation: str,
        *,
        remote_name: str,
        new_name: str | None = None,
    ) -> RemoteConfigurationResult:
        if self._started is not None:
            self._started.set()
        if self._gate is not None:
            assert self._gate.wait(5.0)
        if self.configuration_error is not None:
            raise self.configuration_error
        return RemoteConfigurationResult(
            operation=operation,
            remote_name=remote_name,
            new_name=new_name,
            remotes=self.remotes,
        )

    def add_remote(
        self, repository: Repository, name: str, url: str
    ) -> RemoteConfigurationResult:
        self.add_requests.append((name, url))
        return self._configuration_result("add", remote_name=name)

    def edit_remote(
        self,
        repository: Repository,
        name: str,
        *,
        new_name: str,
        url: str,
    ) -> RemoteConfigurationResult:
        self.edit_requests.append((name, new_name, url))
        return self._configuration_result(
            "edit", remote_name=name, new_name=new_name
        )

    def remove_remote(
        self, repository: Repository, name: str
    ) -> RemoteConfigurationResult:
        self.remove_requests.append(name)
        return self._configuration_result("remove", remote_name=name)

    def fetch(self, repository: Repository, remote_name: str) -> RemoteOperationResult:
        self.fetch_requests.append(remote_name)
        self._wait()
        if self.error is not None:
            raise self.error
        return RemoteOperationResult(operation="fetch", remote=remote_name)

    def pull(self, repository: Repository) -> RemoteOperationResult:
        self.pull_requests += 1
        self._wait()
        if self.error is not None:
            raise self.error
        return RemoteOperationResult(
            operation="pull",
            branch=repository.current_branch,
            upstream="origin/main",
        )

    def push(self, repository: Repository) -> RemoteOperationResult:
        self.push_requests += 1
        self._wait()
        if self.error is not None:
            raise self.error
        return RemoteOperationResult(
            operation="push",
            branch=repository.current_branch,
            upstream="origin/main",
        )

    def push_initial(
        self, repository: Repository, remote_name: str
    ) -> RemoteOperationResult:
        self.initial_push_requests.append(remote_name)
        self._wait()
        if self.error is not None:
            raise self.error
        return RemoteOperationResult(
            operation="push",
            remote=remote_name,
            branch=repository.current_branch,
            upstream=f"{remote_name}/{repository.current_branch}",
        )


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
    changes_service: FakeChangesService | None = None,
    commit_service: FakeCommitService | None = None,
    remote_service: FakeRemoteService | None = None,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
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
        changes_service=changes_service or FakeChangesService(),
        commit_service=commit_service or FakeCommitService(),
        remote_service=remote_service or FakeRemoteService(),
        git_environment_service=FakeEnvironmentService(
            environment=environment, error=environment_error
        ),
        history_limit=history_limit,
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


def test_changes_and_worktree_diff_are_loaded_in_background(
    qt_app, tmp_path: Path
) -> None:
    commit = make_commit("only")
    changed = (
        ChangedFile(
            path="both.txt",
            staged_change=FileChangeType.MODIFIED,
            unstaged_change=FileChangeType.MODIFIED,
        ),
        ChangedFile(path="new.txt", is_untracked=True),
    )
    diff = Diff(path="both.txt", text="@@ -1 +1 @@\n-old\n+new\n")
    changes_service = FakeChangesService(changed, diff)
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        changes_service=changes_service,
    )
    changes_loaded: list[tuple[ChangedFile, ...]] = []
    diffs_loaded: list[tuple[str, bool, Diff]] = []
    controller.changes_loaded.connect(changes_loaded.append)
    controller.worktree_diff_loaded.connect(
        lambda path, staged, diff: diffs_loaded.append((path, staged, diff))
    )

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)

    assert controller.load_changes() is True
    assert controller.load_changes() is False  # статус уже загружается
    wait_until(qt_app, lambda: bool(changes_loaded))
    assert changes_loaded[0] == changed
    assert controller.changes == changed
    assert changes_service.status_requests == 1

    assert controller.load_worktree_diff(changed[0], staged=True) is True
    wait_until(qt_app, lambda: bool(diffs_loaded))
    assert diffs_loaded[0] == ("both.txt", True, diff)
    assert changes_service.diff_requests == [("both.txt", True)]

    assert controller.load_worktree_diff(changed[0], staged=False) is True
    wait_until(qt_app, lambda: len(diffs_loaded) == 2)
    assert changes_service.diff_requests[-1] == ("both.txt", False)
    controller.wait_for_done()


def test_changes_failure_is_reported_as_task_failure(qt_app, tmp_path: Path) -> None:
    error = GitCommandError(128, command=("git", "status"), stderr="fatal")
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        changes_service=FakeChangesService(changes_error=error),
    )
    failed: list[TaskFailure] = []
    loaded: list[tuple[ChangedFile, ...]] = []
    controller.changes_failed.connect(failed.append)
    controller.changes_loaded.connect(loaded.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    controller.load_changes()

    wait_until(qt_app, lambda: bool(failed))
    assert failed[0].exception is error
    assert loaded == []
    assert controller.changes == ()
    controller.wait_for_done()


def test_changes_require_open_repository(qt_app) -> None:
    controller, _, _, _ = build_controller(
        repository=make_repository(Path("repo"), head_commit=None)
    )

    assert controller.load_changes() is False
    assert (
        controller.load_worktree_diff(
            ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED),
            staged=False,
        )
        is False
    )


# --- stage/unstage через контроллер ---


def test_stage_file_runs_in_background_and_refreshes_status(
    qt_app, tmp_path: Path
) -> None:
    changed = ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED)
    changes_service = FakeChangesService((changed,))
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        changes_service=changes_service,
    )
    started: list[bool] = []
    succeeded: list[bool] = []
    failed: list[TaskFailure] = []
    controller.changes_operation_started.connect(lambda: started.append(True))
    controller.changes_operation_succeeded.connect(lambda: succeeded.append(True))
    controller.changes_operation_failed.connect(failed.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert controller.stage_file(changed) is True

    wait_until(qt_app, lambda: bool(succeeded))
    wait_until(qt_app, lambda: controller.changes == (changed,))
    assert started == [True]
    assert failed == []
    assert changes_service.stage_requests == [changed]
    assert changes_service.status_requests == 1
    controller.wait_for_done()


def test_unstage_and_bulk_operations_are_submitted(
    qt_app, tmp_path: Path
) -> None:
    changed = ChangedFile(path="file.txt", staged_change=FileChangeType.MODIFIED)
    changes_service = FakeChangesService((changed,))
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        changes_service=changes_service,
    )
    succeeded: list[bool] = []
    controller.changes_operation_succeeded.connect(lambda: succeeded.append(True))

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)

    assert controller.unstage_file(changed) is True
    wait_until(qt_app, lambda: len(succeeded) == 1)
    assert changes_service.unstage_requests == [changed]

    assert controller.stage_all() is True
    wait_until(qt_app, lambda: len(succeeded) == 2)
    assert changes_service.stage_all_requests == 1

    assert controller.unstage_all() is True
    wait_until(qt_app, lambda: len(succeeded) == 3)
    wait_until(qt_app, lambda: changes_service.status_requests == 3)
    assert changes_service.unstage_all_requests == 1
    controller.wait_for_done()


def test_changes_operation_failure_is_reported_and_status_is_reloaded(
    qt_app, tmp_path: Path
) -> None:
    changed = ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED)
    error = GitCommandError(128, command=("git", "add"), stderr="fatal")
    changes_service = FakeChangesService((changed,), operation_error=error)
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        changes_service=changes_service,
    )
    failed: list[TaskFailure] = []
    succeeded: list[bool] = []
    controller.changes_operation_failed.connect(failed.append)
    controller.changes_operation_succeeded.connect(lambda: succeeded.append(True))

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert controller.stage_file(changed) is True

    wait_until(qt_app, lambda: bool(failed))
    wait_until(qt_app, lambda: changes_service.status_requests == 1)
    assert succeeded == []
    assert failed[0].exception is error
    controller.wait_for_done()


def test_discard_file_runs_in_background_and_refreshes_status(
    qt_app, tmp_path: Path
) -> None:
    changed = ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED)
    changes_service = FakeChangesService((changed,))
    changes_service.after_discard = ()
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        changes_service=changes_service,
    )
    started: list[bool] = []
    succeeded: list[bool] = []
    failed: list[TaskFailure] = []
    controller.changes_operation_started.connect(lambda: started.append(True))
    controller.changes_operation_succeeded.connect(lambda: succeeded.append(True))
    controller.changes_operation_failed.connect(failed.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert controller.discard_file(changed) is True

    wait_until(qt_app, lambda: bool(succeeded))
    wait_until(qt_app, lambda: changes_service.status_requests == 1)
    assert controller.changes == ()
    assert started == [True]
    assert failed == []
    assert changes_service.discard_requests == [changed]
    # После отмены статус запрашивается заново: панель показывает фактическое
    # состояние (файл исчез из списка изменений).
    controller.wait_for_done()


def test_changes_operations_require_open_repository(qt_app) -> None:
    controller, _, _, _ = build_controller(
        repository=make_repository(Path("repo"), head_commit=None)
    )
    changed = ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED)

    assert controller.stage_file(changed) is False
    assert controller.unstage_file(changed) is False
    assert controller.stage_all() is False
    assert controller.unstage_all() is False
    assert controller.discard_file(changed) is False


# --- создание коммита через контроллер ---


def test_commit_runs_in_background_and_reloads_workspace(
    qt_app, tmp_path: Path
) -> None:
    started = threading.Event()
    gate = threading.Event()
    commit_service = FakeCommitService(gate=gate, started=started)
    commit = make_commit("only")
    controller, _, history, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        commit_service=commit_service,
    )
    loaded: list[WorkspaceData] = []
    succeeded: list[bool] = []
    failed: list[TaskFailure] = []
    controller.repository_loaded.connect(loaded.append)
    controller.commit_succeeded.connect(lambda: succeeded.append(True))
    controller.commit_failed.connect(failed.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert len(loaded) == 1

    # Возврат из commit не ждёт Git: задача выполняется в фоне.
    assert controller.commit("Коммит из теста") is True
    wait_until(qt_app, started.is_set)
    assert commit_service.requests == ["Коммит из теста"]
    assert succeeded == []
    assert loaded == [controller.workspace]

    gate.set()
    wait_until(qt_app, lambda: bool(succeeded))
    wait_until(qt_app, lambda: len(loaded) == 2)
    assert failed == []
    # После успеха репозиторий переоткрыт, история запрошена заново.
    assert controller.repository is not None
    assert history.commits == (commit,)
    controller.wait_for_done()


def test_commit_failure_is_reported_and_status_is_reloaded(
    qt_app, tmp_path: Path
) -> None:
    error = GitCommandError(
        128,
        command=("git", "commit", "-F", "-"),
        stderr="Author identity unknown",
    )
    commit_service = FakeCommitService(error=error)
    changes_service = FakeChangesService()
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        changes_service=changes_service,
        commit_service=commit_service,
    )
    failed: list[TaskFailure] = []
    succeeded: list[bool] = []
    controller.commit_failed.connect(failed.append)
    controller.commit_succeeded.connect(lambda: succeeded.append(True))

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert controller.commit("message") is True

    wait_until(qt_app, lambda: bool(failed))
    wait_until(qt_app, lambda: changes_service.status_requests == 1)
    assert failed[0].exception is error
    assert succeeded == []
    assert commit_service.requests == ["message"]
    controller.wait_for_done()


def test_commit_requires_open_repository(qt_app) -> None:
    controller, _, _, _ = build_controller(
        repository=make_repository(Path("repo"), head_commit=None)
    )

    assert controller.commit("message") is False


def test_commit_is_blocked_while_another_index_operation_runs(
    qt_app, tmp_path: Path
) -> None:
    started = threading.Event()
    gate = threading.Event()
    commit_service = FakeCommitService(gate=gate, started=started)
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        commit_service=commit_service,
    )
    succeeded: list[bool] = []
    controller.commit_succeeded.connect(lambda: succeeded.append(True))

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert controller.commit("message") is True
    wait_until(qt_app, started.is_set)

    changed = ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED)
    # Коммит и stage/unstage/discard делят ключ: индекс нельзя менять параллельно.
    assert controller.stage_file(changed) is False
    assert controller.discard_file(changed) is False
    assert controller.commit("second") is False

    gate.set()
    wait_until(qt_app, lambda: bool(succeeded))
    controller.wait_for_done()


# --- сетевые операции через контроллер ---


def test_fetch_runs_in_background_and_reloads_workspace(
    qt_app, tmp_path: Path
) -> None:
    started = threading.Event()
    gate = threading.Event()
    remote_service = FakeRemoteService(gate=gate, started=started)
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        remote_service=remote_service,
    )
    contexts = []
    results = []
    loaded: list[WorkspaceData] = []
    controller.remote_operation_started.connect(contexts.append)
    controller.remote_operation_succeeded.connect(results.append)
    controller.repository_loaded.connect(loaded.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert len(loaded) == 1

    assert controller.fetch("origin") is True
    wait_until(qt_app, started.is_set)
    assert contexts[0].operation == "fetch"
    assert contexts[0].remote == "origin"
    assert remote_service.fetch_requests == ["origin"]
    assert results == []
    assert len(loaded) == 1

    gate.set()
    wait_until(qt_app, lambda: bool(results))
    wait_until(qt_app, lambda: len(loaded) == 2)
    assert results[0].operation == "fetch"
    assert results[0].remote == "origin"
    controller.wait_for_done()


def test_remote_operations_do_not_run_in_parallel(qt_app, tmp_path: Path) -> None:
    started = threading.Event()
    gate = threading.Event()
    remote_service = FakeRemoteService(gate=gate, started=started)
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        remote_service=remote_service,
    )
    succeeded: list[object] = []
    controller.remote_operation_succeeded.connect(succeeded.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert controller.fetch("origin") is True
    wait_until(qt_app, started.is_set)

    assert controller.pull() is False
    assert controller.push() is False
    assert controller.push_initial("origin") is False
    # Сетевые операции делят ключ с индексом, коммитом и ветками: параллельно
    # они не запускаются, иначе возможны гонки за refs/HEAD/рабочую директорию.
    changed = ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED)
    assert controller.stage_file(changed) is False
    assert controller.commit("message") is False
    assert controller.create_branch("feature") is False
    assert controller.switch_branch("dev") is False
    assert remote_service.pull_requests == 0

    gate.set()
    wait_until(qt_app, lambda: bool(succeeded))
    controller.wait_for_done()


def test_remote_failure_is_reported_and_workspace_is_reread(
    qt_app, tmp_path: Path
) -> None:
    error = GitCommandError(
        128,
        command=("git", "fetch", "origin"),
        stderr="fatal: unable to access 'https://example.com/r.git': "
        "Could not resolve host: example.com\n",
    )
    remote_service = FakeRemoteService(error=error)
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        remote_service=remote_service,
    )
    failures = []
    loaded: list[WorkspaceData] = []
    succeeded: list[object] = []
    controller.remote_operation_failed.connect(
        lambda context, failure: failures.append((context, failure))
    )
    controller.remote_operation_succeeded.connect(succeeded.append)
    controller.repository_loaded.connect(loaded.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert len(loaded) == 1

    assert controller.fetch("origin") is True
    wait_until(qt_app, lambda: bool(failures))
    assert failures[0][1].exception is error
    assert succeeded == []
    # После ошибки сетевой команды состояние перечитывается: Git мог обновить refs.
    wait_until(qt_app, lambda: len(loaded) == 2)
    controller.wait_for_done()


def test_remote_validation_failure_does_not_reload_workspace(
    qt_app, tmp_path: Path
) -> None:
    error = UpstreamNotConfiguredError("main")
    remote_service = FakeRemoteService(error=error)
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        remote_service=remote_service,
    )
    failures: list[TaskFailure] = []
    loaded: list[WorkspaceData] = []
    controller.remote_operation_failed.connect(
        lambda context, failure: failures.append(failure)
    )
    controller.repository_loaded.connect(loaded.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert controller.pull() is True
    wait_until(qt_app, lambda: bool(failures))

    assert failures[0].exception is error
    assert len(loaded) == 1
    controller.wait_for_done()


def test_push_initial_uses_explicit_remote(qt_app, tmp_path: Path) -> None:
    remote_service = FakeRemoteService()
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        remote_service=remote_service,
    )
    results: list[object] = []
    controller.remote_operation_succeeded.connect(results.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert controller.push_initial("fork") is True
    wait_until(qt_app, lambda: bool(results))

    assert remote_service.initial_push_requests == ["fork"]
    controller.wait_for_done()


def test_remote_operations_require_open_repository(qt_app) -> None:
    controller, _, _, _ = build_controller(
        repository=make_repository(Path("repo"), head_commit=None)
    )

    assert controller.fetch("origin") is False
    assert controller.pull() is False
    assert controller.push() is False
    assert controller.push_initial("origin") is False


# --- конфигурация remote через контроллер ---


def test_remote_configuration_add_runs_in_background_and_reloads_workspace(
    qt_app, tmp_path: Path
) -> None:
    started = threading.Event()
    gate = threading.Event()
    remote_service = FakeRemoteService(gate=gate, started=started)
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        remote_service=remote_service,
    )
    contexts = []
    results: list[RemoteConfigurationResult] = []
    loaded: list[WorkspaceData] = []
    controller.remote_configuration_started.connect(contexts.append)
    controller.remote_configuration_succeeded.connect(results.append)
    controller.repository_loaded.connect(loaded.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert len(loaded) == 1

    assert (
        controller.add_remote("upstream", "https://example.com/r.git") is True
    )
    wait_until(qt_app, started.is_set)
    assert contexts[0].operation == "add"
    assert contexts[0].remote_name == "upstream"
    assert remote_service.add_requests == [
        ("upstream", "https://example.com/r.git")
    ]
    assert results == []
    assert len(loaded) == 1

    gate.set()
    wait_until(qt_app, lambda: bool(results))
    wait_until(qt_app, lambda: len(loaded) == 2)

    assert results[0].operation == "add"
    assert results[0].remote_name == "upstream"
    assert results[0].remotes == remote_service.remotes
    controller.wait_for_done()


def test_remote_configuration_edit_and_remove_use_service(
    qt_app, tmp_path: Path
) -> None:
    remote_service = FakeRemoteService()
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        remote_service=remote_service,
    )
    results: list[RemoteConfigurationResult] = []
    controller.remote_configuration_succeeded.connect(results.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)

    assert (
        controller.edit_remote(
            "origin", new_name="upstream", url="git@example.com:me/r.git"
        )
        is True
    )
    wait_until(qt_app, lambda: len(results) == 1)
    assert remote_service.edit_requests == [
        ("origin", "upstream", "git@example.com:me/r.git")
    ]
    assert results[0].new_name == "upstream"

    assert controller.remove_remote("upstream") is True
    wait_until(qt_app, lambda: len(results) == 2)
    assert remote_service.remove_requests == ["upstream"]
    assert results[1].operation == "remove"
    controller.wait_for_done()


def test_remote_configuration_git_failure_reloads_workspace(
    qt_app, tmp_path: Path
) -> None:
    error = GitCommandError(
        3, stderr="error: remote upstream already exists.\n"
    )
    remote_service = FakeRemoteService(configuration_error=error)
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        remote_service=remote_service,
    )
    failures = []
    loaded: list[WorkspaceData] = []
    controller.remote_configuration_failed.connect(
        lambda context, failure: failures.append((context, failure))
    )
    controller.repository_loaded.connect(loaded.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert len(loaded) == 1

    assert (
        controller.edit_remote(
            "origin", new_name="upstream", url="https://example.com/r.git"
        )
        is True
    )
    wait_until(qt_app, lambda: bool(failures))
    assert failures[0][1].exception is error
    # Git-ошибка могла произойти после частичной правки: состояние перечитывается.
    wait_until(qt_app, lambda: len(loaded) == 2)
    controller.wait_for_done()


def test_remote_configuration_validation_failure_does_not_reload_workspace(
    qt_app, tmp_path: Path
) -> None:
    error = ValueError("Remote URL must not be empty")
    remote_service = FakeRemoteService(configuration_error=error)
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        remote_service=remote_service,
    )
    failures: list[TaskFailure] = []
    loaded: list[WorkspaceData] = []
    controller.remote_configuration_failed.connect(
        lambda context, failure: failures.append(failure)
    )
    controller.repository_loaded.connect(loaded.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)

    assert controller.add_remote("origin", "  ") is True
    wait_until(qt_app, lambda: bool(failures))

    assert failures[0].exception is error
    assert len(loaded) == 1
    controller.wait_for_done()


def test_remote_configuration_is_blocked_while_another_operation_runs(
    qt_app, tmp_path: Path
) -> None:
    started = threading.Event()
    gate = threading.Event()
    remote_service = FakeRemoteService(gate=gate, started=started)
    commit = make_commit("only")
    controller, _, _, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        remote_service=remote_service,
    )
    succeeded: list[object] = []
    controller.remote_operation_succeeded.connect(succeeded.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: controller.workspace is not None)
    assert controller.fetch("origin") is True
    wait_until(qt_app, started.is_set)

    assert controller.add_remote("fork", "https://example.com/fork.git") is False
    assert (
        controller.edit_remote(
            "origin", new_name="upstream", url="https://example.com/r.git"
        )
        is False
    )
    assert controller.remove_remote("origin") is False
    assert remote_service.add_requests == []
    assert remote_service.edit_requests == []
    assert remote_service.remove_requests == []

    gate.set()
    wait_until(qt_app, lambda: bool(succeeded))
    controller.wait_for_done()


def test_remote_configuration_requires_open_repository(qt_app) -> None:
    controller, _, _, _ = build_controller(
        repository=make_repository(Path("repo"), head_commit=None)
    )

    assert controller.add_remote("origin", "https://example.com/r.git") is False
    assert (
        controller.edit_remote(
            "origin", new_name="upstream", url="https://example.com/r.git"
        )
        is False
    )
    assert controller.remove_remote("origin") is False


# --- лимит загрузки коммитов (пользовательская настройка) ---


def test_history_limit_is_passed_to_the_history_service(
    qt_app, tmp_path: Path
) -> None:
    commit = make_commit("only")
    controller, _, history, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        history_limit=500,
    )
    loaded: list[WorkspaceData] = []
    controller.repository_loaded.connect(loaded.append)

    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: bool(loaded))

    assert history.limits == [500]
    controller.wait_for_done()


def test_set_history_limit_applies_to_next_load(qt_app, tmp_path: Path) -> None:
    commit = make_commit("only")
    controller, _, history, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
    )
    loaded: list[WorkspaceData] = []
    controller.repository_loaded.connect(loaded.append)

    assert controller.history_limit == DEFAULT_HISTORY_LIMIT
    controller.set_history_limit(1000)
    controller.open_repository(str(tmp_path))
    wait_until(qt_app, lambda: bool(loaded))

    assert history.limits == [1000]
    controller.wait_for_done()


def test_set_history_limit_rejects_non_positive_value(qt_app) -> None:
    controller, _, _, _ = build_controller(
        repository=make_repository(Path("repo"), head_commit=None)
    )

    with pytest.raises(ValueError):
        controller.set_history_limit(0)

    assert controller.history_limit == DEFAULT_HISTORY_LIMIT
