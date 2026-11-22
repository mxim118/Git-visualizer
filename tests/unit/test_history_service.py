"""HistoryService с подменой порта доступа к Git: проверяется оркестрация, без запуска Git."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from git_visualizer.application.services.history_service import (
    DEFAULT_HISTORY_LIMIT,
    HistoryService,
)
from git_visualizer.domain.models import Commit, Repository
from git_visualizer.infrastructure.git.git_errors import GitCommandError, GitTimeoutError

NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def make_commit(hash_char: str, parents: tuple[str, ...] = ()) -> Commit:
    return Commit(
        hash=hash_char * 40,
        short_hash=hash_char * 7,
        parent_hashes=parents,
        author_name="A",
        author_email="a@example.com",
        authored_at=NOW,
        committer_name="A",
        committer_email="a@example.com",
        committed_at=NOW,
        subject="s",
        body="",
    )


class FakeGit:
    def __init__(self, commits: tuple[Commit, ...] = (), error: Exception | None = None) -> None:
        self.commits = commits
        self.error = error
        self.limits: list[int] = []

    def get_history(self, *, limit: int) -> tuple[Commit, ...]:
        self.limits.append(limit)
        if self.error is not None:
            raise self.error
        return self.commits


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    return Repository(path=tmp_path, name="repo", current_branch="main", head_commit="a" * 40)


def service_for(fake: FakeGit, requested: list[Path] | None = None) -> HistoryService:
    def factory(path: Path) -> FakeGit:
        if requested is not None:
            requested.append(path)
        return fake

    return HistoryService(factory)


def test_returns_commits_in_the_order_given_by_git(repository: Repository) -> None:
    commits = (make_commit("c", ("b" * 40,)), make_commit("b", ("a" * 40,)), make_commit("a"))

    assert service_for(FakeGit(commits)).get_history(repository) == commits


def test_empty_history(repository: Repository) -> None:
    assert service_for(FakeGit(())).get_history(repository) == ()


def test_uses_repository_root_path(repository: Repository) -> None:
    requested: list[Path] = []

    service_for(FakeGit(), requested).get_history(repository)

    assert requested == [repository.path]


def test_default_limit_is_the_single_constant(repository: Repository) -> None:
    fake = FakeGit()

    service_for(fake).get_history(repository)

    assert fake.limits == [DEFAULT_HISTORY_LIMIT]


def test_default_limit_is_within_the_range_from_the_spec() -> None:
    assert 2000 <= DEFAULT_HISTORY_LIMIT <= 5000  # ТЗ FR-HIST-07


def test_explicit_limit_is_passed_through(repository: Repository) -> None:
    fake = FakeGit()

    service_for(fake).get_history(repository, limit=50)

    assert fake.limits == [50]


@pytest.mark.parametrize("limit", [0, -5])
def test_non_positive_limit_is_rejected_before_calling_git(
    repository: Repository, limit: int
) -> None:
    fake = FakeGit()

    with pytest.raises(ValueError):
        service_for(fake).get_history(repository, limit=limit)

    assert fake.limits == []


@pytest.mark.parametrize(
    "error",
    [
        GitCommandError(128, command=("git", "log"), stderr="fatal: bad object\n"),
        GitTimeoutError(30.0, command=("git", "log")),
    ],
)
def test_git_errors_are_propagated_unchanged(repository: Repository, error: Exception) -> None:
    with pytest.raises(type(error)) as info:
        service_for(FakeGit(error=error)).get_history(repository)

    assert info.value is error
