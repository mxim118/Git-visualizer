"""HistoryService с подменой порта доступа к Git: проверяется оркестрация, без запуска Git."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from git_visualizer.application.services.history_service import (
    DEFAULT_HISTORY_LIMIT,
    HistoryPage,
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
    """Порт Git: отдаёт историю тем же срезом, что ``git log --skip --max-count``."""

    def __init__(
        self,
        commits: tuple[Commit, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self.commits = commits
        self.error = error
        self.requests: list[tuple[int, int]] = []

    def get_history(self, *, limit: int, skip: int) -> tuple[Commit, ...]:
        self.requests.append((limit, skip))
        if self.error is not None:
            raise self.error
        return self.commits[skip : skip + limit]


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

    page = service_for(FakeGit(commits)).get_history(repository)

    assert page == HistoryPage(commits=commits, has_more=False)


def test_empty_history(repository: Repository) -> None:
    assert service_for(FakeGit(())).get_history(repository) == HistoryPage(
        commits=(), has_more=False
    )


def test_uses_repository_root_path(repository: Repository) -> None:
    requested: list[Path] = []

    service_for(FakeGit(), requested).get_history(repository)

    assert requested == [repository.path]


def test_default_limit_is_the_single_constant(repository: Repository) -> None:
    fake = FakeGit()

    service_for(fake).get_history(repository)

    # На один коммит больше лимита — так видно, что страница не последняя.
    assert fake.requests == [(DEFAULT_HISTORY_LIMIT + 1, 0)]


def test_default_limit_is_within_the_range_from_the_spec() -> None:
    assert 2000 <= DEFAULT_HISTORY_LIMIT <= 5000  # ТЗ FR-HIST-07


def test_explicit_limit_is_passed_through(repository: Repository) -> None:
    fake = FakeGit(tuple(make_commit("a") for _ in range(5)))

    page = service_for(fake).get_history(repository, limit=50)

    assert fake.requests == [(51, 0)]
    assert len(page.commits) == 5
    assert page.has_more is False


def test_truncated_history_is_reported_and_cut_to_the_limit(
    repository: Repository,
) -> None:
    commits = tuple(make_commit(ch) for ch in "abc")
    fake = FakeGit(commits)

    page = service_for(fake).get_history(repository, limit=2)

    assert fake.requests == [(3, 0)]
    assert page.commits == commits[:2]
    assert page.has_more is True


def test_history_of_exactly_the_limit_is_not_truncated(repository: Repository) -> None:
    # len == limit неоднозначен, поэтому запрашивается limit + 1: ровно limit
    # коммитов — это полная история, а не обрезанная.
    commits = (make_commit("b"), make_commit("a"))

    page = service_for(FakeGit(commits)).get_history(repository, limit=2)

    assert page.commits == commits
    assert page.has_more is False


def test_skip_loads_the_next_older_page(repository: Repository) -> None:
    commits = tuple(make_commit(ch) for ch in "abcde")
    fake = FakeGit(commits)

    page = service_for(fake).get_history(repository, limit=2, skip=2)

    assert fake.requests == [(3, 2)]
    assert page.commits == commits[2:4]
    assert page.has_more is True


def test_last_page_has_nothing_more(repository: Repository) -> None:
    commits = tuple(make_commit(ch) for ch in "abc")

    page = service_for(FakeGit(commits)).get_history(repository, limit=2, skip=2)

    assert page.commits == commits[2:]
    assert page.has_more is False


def test_skip_beyond_history_is_empty(repository: Repository) -> None:
    page = service_for(FakeGit((make_commit("a"),))).get_history(
        repository, limit=2, skip=5
    )

    assert page.commits == ()
    assert page.has_more is False


@pytest.mark.parametrize("limit", [0, -5])
def test_non_positive_limit_is_rejected_before_calling_git(
    repository: Repository, limit: int
) -> None:
    fake = FakeGit()

    with pytest.raises(ValueError):
        service_for(fake).get_history(repository, limit=limit)

    assert fake.requests == []


@pytest.mark.parametrize("skip", [-1, -100])
def test_negative_skip_is_rejected_before_calling_git(
    repository: Repository, skip: int
) -> None:
    fake = FakeGit()

    with pytest.raises(ValueError):
        service_for(fake).get_history(repository, skip=skip)

    assert fake.requests == []


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
