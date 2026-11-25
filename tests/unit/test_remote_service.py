"""RemoteService: сценарии Fetch/Pull/Push с подменой порта, без запуска Git."""

from pathlib import Path

import pytest

from git_visualizer.application.errors import (
    NoCurrentBranchError,
    UnknownRemoteError,
    UpstreamNotConfiguredError,
)
from git_visualizer.application.services.remote_service import (
    RemoteService,
    RemoteOperationResult,
)
from git_visualizer.domain.models import Remote, Repository
from git_visualizer.infrastructure.git.git_errors import GitCommandError


class FakeGit:
    def __init__(
        self,
        *,
        remotes: tuple[Remote, ...] = (Remote(name="origin"),),
        branch: str | None = "main",
        upstream: str | None = "origin/main",
        fetch_error: BaseException | None = None,
        pull_error: BaseException | None = None,
        push_error: BaseException | None = None,
        initial_push_error: BaseException | None = None,
    ) -> None:
        self.remotes = remotes
        self.branch = branch
        self.upstream = upstream
        self.fetch_error = fetch_error
        self.pull_error = pull_error
        self.push_error = push_error
        self.initial_push_error = initial_push_error
        self.fetch_calls: list[tuple[str, float]] = []
        self.pull_calls: list[float] = []
        self.push_calls: list[float] = []
        self.initial_push_calls: list[tuple[str, str, float]] = []

    def get_remotes(self) -> tuple[Remote, ...]:
        return self.remotes

    def get_current_branch(self) -> str | None:
        return self.branch

    def get_branch_upstream(self, branch_name: str) -> str | None:
        assert branch_name == self.branch
        return self.upstream

    def fetch(self, remote_name: str, *, timeout: float) -> str:
        self.fetch_calls.append((remote_name, timeout))
        if self.fetch_error is not None:
            raise self.fetch_error
        return f"From origin\n * [new branch] main -> origin/main\n"

    def pull_ff_only(self, *, timeout: float) -> str:
        self.pull_calls.append(timeout)
        if self.pull_error is not None:
            raise self.pull_error
        return "Fast-forward\n file.txt | 1 +\n"

    def push(self, *, timeout: float) -> str:
        self.push_calls.append(timeout)
        if self.push_error is not None:
            raise self.push_error
        return "To origin\n   abc..def  main -> main\n"

    def push_set_upstream(
        self, remote_name: str, branch_name: str, *, timeout: float
    ) -> str:
        self.initial_push_calls.append((remote_name, branch_name, timeout))
        if self.initial_push_error is not None:
            raise self.initial_push_error
        return f"To {remote_name}\n * [new branch] {branch_name} -> {branch_name}\n"


def make_repository(tmp_path: Path) -> Repository:
    return Repository(
        path=tmp_path, name="repo", current_branch="main", head_commit="a" * 40
    )


def service_for(
    fake: FakeGit, requested: list[Path] | None = None, *, timeout: float = 42.0
) -> RemoteService:
    def factory(path: Path) -> FakeGit:
        if requested is not None:
            requested.append(path)
        return fake

    return RemoteService(factory, timeout=timeout)


def test_fetch_uses_selected_remote_and_network_timeout(tmp_path: Path) -> None:
    fake = FakeGit()
    requested: list[Path] = []

    result = service_for(fake, requested, timeout=99.0).fetch(
        make_repository(tmp_path), "origin"
    )

    assert requested == [tmp_path]
    assert fake.fetch_calls == [("origin", 99.0)]
    assert result == RemoteOperationResult(
        operation="fetch",
        remote="origin",
        output="From origin\n * [new branch] main -> origin/main\n",
    )


def test_fetch_rejects_unknown_remote_before_running_git(tmp_path: Path) -> None:
    fake = FakeGit(remotes=(Remote(name="origin"),))

    with pytest.raises(UnknownRemoteError) as info:
        service_for(fake).fetch(make_repository(tmp_path), "fork")

    assert info.value.remote_name == "fork"
    assert fake.fetch_calls == []


def test_fetch_requires_remote_name(tmp_path: Path) -> None:
    fake = FakeGit()

    with pytest.raises(ValueError):
        service_for(fake).fetch(make_repository(tmp_path), "  ")

    assert fake.fetch_calls == []


def test_pull_requires_current_branch(tmp_path: Path) -> None:
    fake = FakeGit(branch=None)

    with pytest.raises(NoCurrentBranchError):
        service_for(fake).pull(make_repository(tmp_path))

    assert fake.pull_calls == []


def test_pull_requires_upstream(tmp_path: Path) -> None:
    fake = FakeGit(upstream=None)

    with pytest.raises(UpstreamNotConfiguredError) as info:
        service_for(fake).pull(make_repository(tmp_path))

    assert info.value.branch_name == "main"
    assert fake.pull_calls == []


def test_pull_runs_ff_only_with_timeout(tmp_path: Path) -> None:
    fake = FakeGit()

    result = service_for(fake, timeout=77.0).pull(make_repository(tmp_path))

    assert fake.pull_calls == [77.0]
    assert result.operation == "pull"
    assert result.branch == "main"
    assert result.upstream == "origin/main"
    assert "Fast-forward" in result.output


def test_push_uses_upstream(tmp_path: Path) -> None:
    fake = FakeGit()

    result = service_for(fake, timeout=55.0).push(make_repository(tmp_path))

    assert fake.push_calls == [55.0]
    assert result.operation == "push"
    assert result.branch == "main"
    assert result.upstream == "origin/main"


def test_push_without_upstream_is_rejected(tmp_path: Path) -> None:
    fake = FakeGit(upstream=None)

    with pytest.raises(UpstreamNotConfiguredError):
        service_for(fake).push(make_repository(tmp_path))

    assert fake.push_calls == []


def test_push_initial_sets_upstream_for_explicit_remote(tmp_path: Path) -> None:
    fake = FakeGit(upstream=None, remotes=(Remote(name="fork"),))

    result = service_for(fake, timeout=66.0).push_initial(
        make_repository(tmp_path), "fork"
    )

    assert fake.initial_push_calls == [("fork", "main", 66.0)]
    assert result.operation == "push"
    assert result.remote == "fork"
    assert result.branch == "main"
    assert result.upstream == "fork/main"


def test_push_initial_rejects_unknown_remote(tmp_path: Path) -> None:
    fake = FakeGit(upstream=None, remotes=(Remote(name="origin"),))

    with pytest.raises(UnknownRemoteError):
        service_for(fake).push_initial(make_repository(tmp_path), "fork")

    assert fake.initial_push_calls == []


def test_push_initial_requires_current_branch(tmp_path: Path) -> None:
    fake = FakeGit(branch=None)

    with pytest.raises(NoCurrentBranchError):
        service_for(fake).push_initial(make_repository(tmp_path), "origin")

    assert fake.initial_push_calls == []


@pytest.mark.parametrize(
    ("method", "error"),
    [
        ("fetch", GitCommandError(128, command=("git", "fetch"))),
        ("pull", GitCommandError(1, command=("git", "pull"))),
        ("push", GitCommandError(1, command=("git", "push"))),
    ],
)
def test_git_errors_are_propagated_unchanged(
    tmp_path: Path, method: str, error: GitCommandError
) -> None:
    fake = FakeGit(
        fetch_error=error if method == "fetch" else None,
        pull_error=error if method == "pull" else None,
        push_error=error if method == "push" else None,
    )
    service = service_for(fake)

    with pytest.raises(GitCommandError) as info:
        if method == "fetch":
            service.fetch(make_repository(tmp_path), "origin")
        elif method == "pull":
            service.pull(make_repository(tmp_path))
        else:
            service.push(make_repository(tmp_path))

    assert info.value is error


def test_timeout_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        RemoteService(lambda path: FakeGit(), timeout=0)
