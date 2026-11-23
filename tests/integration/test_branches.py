"""Ветки и метки refs на реальных временных репозиториях."""

from collections.abc import Callable
from pathlib import Path

import pytest

from git_visualizer.application.services.branch_service import BranchService
from git_visualizer.application.services.history_service import HistoryService
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.domain.enums import RefKind
from git_visualizer.domain.models import Branch, Repository
from git_visualizer.infrastructure.git.git_repository import GitRepository
from git_visualizer.infrastructure.git.git_runner import GitRunner

MakeRepo = Callable[..., Path]
Git = Callable[..., str]

IDENT = ("-c", "user.name=T", "-c", "user.email=t@e.com", "-c", "commit.gpgsign=false")


@pytest.fixture
def factory():
    runner = GitRunner(default_timeout=60)
    return lambda path: GitRepository(runner, path)


@pytest.fixture
def raw_branches(factory) -> Callable[[Path], tuple[Branch, ...]]:
    """Ответ инфраструктуры без сервиса: то, что отдаёт ``git for-each-ref``."""
    return lambda path: factory(path).get_branches()


@pytest.fixture
def open_repo(factory) -> Callable[[Path], Repository]:
    return lambda path: RepositoryService(factory).open_repository(path)


@pytest.fixture
def branch_service(factory) -> BranchService:
    return BranchService(factory)


def by_name(branches: tuple[Branch, ...]) -> dict[str, Branch]:
    return {b.name: b for b in branches if not b.is_remote} | {
        b.name: b for b in branches if b.is_remote
    }


def rev(git: Git, repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref).strip()


def make_clone(tmp_path: Path, git: Git, make_repo: MakeRepo, *, extra_branches=()) -> Path:
    """Клон локального bare-репозитория: настоящий remote ``origin`` без сети."""
    seed = make_repo("seed", commits=2)
    for name in extra_branches:
        git(seed, "branch", name)
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(tmp_path / "origin.git"))
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(tmp_path / "work"))
    return tmp_path / "work"


# --- локальные ветки ---


def test_several_local_branches(make_repo: MakeRepo, git: Git, raw_branches) -> None:
    repo = make_repo(commits=3)
    git(repo, "branch", "feature/login", "HEAD~1")
    git(repo, "branch", "ветка-ё", "HEAD~2")

    branches = raw_branches(repo)

    assert [b.name for b in branches] == ["feature/login", "main", "ветка-ё"]
    assert all(not b.is_remote for b in branches)
    for branch in branches:
        assert branch.commit_hash == rev(git, repo, f"refs/heads/{branch.name}")
    assert [b.is_current for b in branches] == [False, True, False]
    assert all(b.upstream is None for b in branches)
    assert len({b.commit_hash for b in branches}) == 3


def test_current_branch_follows_head_after_checkout(
    make_repo: MakeRepo, git: Git, raw_branches
) -> None:
    repo = make_repo(commits=1)
    git(repo, "branch", "dev")
    git(repo, "switch", "-q", "dev")

    assert [b.name for b in raw_branches(repo) if b.is_current] == ["dev"]


def test_refs_stored_in_packed_refs_are_read_the_same(
    make_repo: MakeRepo, git: Git, raw_branches
) -> None:
    repo = make_repo(commits=2)
    git(repo, "branch", "a")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    before = raw_branches(repo)

    git(repo, "pack-refs", "--all")

    assert not (repo / ".git" / "refs" / "heads" / "a").exists(), "ref должен быть упакован"
    assert raw_branches(repo) == before


def test_tags_and_stash_are_not_branches(make_repo: MakeRepo, git: Git, raw_branches) -> None:
    repo = make_repo(commits=1)
    git(repo, "tag", "v1")
    git(repo, "update-ref", "refs/stash", "HEAD")
    git(repo, "update-ref", "refs/notes/commits", "HEAD")

    assert [b.name for b in raw_branches(repo)] == ["main"]


def test_many_branches(make_repo: MakeRepo, git: Git, raw_branches) -> None:
    repo = make_repo(commits=1)
    updates = "".join(f"create refs/heads/bulk/{i:04d} HEAD\n" for i in range(500))
    updates += "".join(f"create refs/remotes/origin/bulk/{i:04d} HEAD\n" for i in range(500))
    git(repo, "update-ref", "--stdin", stdin=updates)

    branches = raw_branches(repo)

    assert len(branches) == 1001
    assert sum(b.is_current for b in branches) == 1
    assert sum(b.is_remote for b in branches) == 500


@pytest.mark.parametrize(
    "name",
    [
        "line\u2028separator",  # str.splitlines() разрезал бы запись по этому символу
        "next\u0085line",
        "nbsp\u00a0inside",
        "emoji-😀",
        "100%-done-%(refname)",
        "Ünïcödé/вложенная/ветка",
    ],
)
def test_unusual_branch_names_are_returned_intact(
    make_repo: MakeRepo, git: Git, raw_branches, name: str
) -> None:
    repo = make_repo(commits=1)
    git(repo, "branch", name)
    git(repo, "update-ref", f"refs/remotes/origin/{name}", "HEAD")

    branches = by_name(raw_branches(repo))

    assert branches[name].is_remote is False
    assert branches[f"origin/{name}"].is_remote is True
    assert len(raw_branches(repo)) == 3  # main + две ветки с необычным именем


# --- remote-tracking ветки ---


def test_remote_tracking_branches_of_a_clone(
    tmp_path: Path, make_repo: MakeRepo, git: Git, raw_branches
) -> None:
    work = make_clone(tmp_path, git, make_repo, extra_branches=("feature/y",))
    # Сам Git показывает origin/HEAD как ref: именно он не должен попасть в список веток.
    assert "refs/remotes/origin/HEAD" in git(work, "for-each-ref", "--format=%(refname)").split()

    branches = raw_branches(work)

    assert [(b.name, b.is_remote) for b in branches] == [
        ("main", False),
        ("origin/feature/y", True),
        ("origin/main", True),
    ]
    remote = by_name(branches)["origin/main"]
    assert remote.commit_hash == rev(git, work, "refs/remotes/origin/main")
    assert remote.is_current is False
    assert remote.upstream is None


def test_remote_tracking_branch_hash_is_not_the_local_branch_hash_after_local_commit(
    tmp_path: Path, make_repo: MakeRepo, git: Git, raw_branches
) -> None:
    work = make_clone(tmp_path, git, make_repo)
    git(work, *IDENT, "commit", "-q", "--allow-empty", "-m", "local only")

    branches = by_name(raw_branches(work))

    assert branches["main"].commit_hash == rev(git, work, "HEAD")
    assert branches["origin/main"].commit_hash == rev(git, work, "HEAD~1")
    assert branches["main"].commit_hash != branches["origin/main"].commit_hash


# --- upstream ---


def test_upstream_of_local_branches(
    tmp_path: Path, make_repo: MakeRepo, git: Git, raw_branches
) -> None:
    work = make_clone(tmp_path, git, make_repo, extra_branches=("feature/y",))
    git(work, "branch", "-q", "--track", "tracking", "origin/feature/y")
    git(work, "branch", "-q", "plain")
    git(work, "branch", "-q", "on-local")
    git(work, "branch", "-q", "--set-upstream-to=main", "on-local")  # upstream — локальная ветка

    branches = by_name(raw_branches(work))

    assert branches["main"].upstream == "origin/main"  # клон настраивает его сам
    assert branches["tracking"].upstream == "origin/feature/y"
    assert branches["plain"].upstream is None
    assert branches["on-local"].upstream == "main"


def test_upstream_is_reported_after_its_remote_tracking_branch_is_gone(
    tmp_path: Path, make_repo: MakeRepo, git: Git, raw_branches
) -> None:
    work = make_clone(tmp_path, git, make_repo, extra_branches=("feature/y",))
    git(work, "branch", "-q", "--track", "tracking", "origin/feature/y")
    git(work, "update-ref", "-d", "refs/remotes/origin/feature/y")

    branches = by_name(raw_branches(work))

    assert "origin/feature/y" not in branches
    assert branches["tracking"].upstream == "origin/feature/y"


def test_upstream_is_full_name_even_if_short_name_is_ambiguous(
    tmp_path: Path, make_repo: MakeRepo, git: Git, raw_branches
) -> None:
    # Локальная ветка «origin/main» делает короткое имя origin/main неоднозначным: Git в
    # ``%(upstream:short)`` выдал бы ``remotes/origin/main``.
    work = make_clone(tmp_path, git, make_repo)
    git(work, "branch", "origin/main")
    assert git(work, "for-each-ref", "--format=%(upstream:short)", "refs/heads/main").strip() == (
        "remotes/origin/main"
    )

    branches = raw_branches(work)

    main = next(b for b in branches if b.name == "main")
    assert main.upstream == "origin/main"
    assert [(b.name, b.is_remote) for b in branches if "origin/main" in b.name] == [
        ("origin/main", False),
        ("origin/main", True),
    ]


# --- remote и remote-tracking branch — разные сущности ---


def test_repository_without_remote(make_repo: MakeRepo, factory, raw_branches) -> None:
    repo = make_repo(commits=2)

    assert factory(repo).get_remotes() == ()
    assert [b.name for b in raw_branches(repo)] == ["main"]
    assert not any(b.is_remote for b in raw_branches(repo))


def test_configured_remote_without_fetch_has_no_remote_branches(
    tmp_path: Path, make_repo: MakeRepo, git: Git, factory, raw_branches
) -> None:
    repo = make_repo(commits=1)
    git(repo, "remote", "add", "origin", str(tmp_path / "nowhere.git"))

    assert [r.name for r in factory(repo).get_remotes()] == ["origin"]
    assert not any(b.is_remote for b in raw_branches(repo))


def test_remote_tracking_branch_without_configured_remote(
    make_repo: MakeRepo, git: Git, factory, raw_branches
) -> None:
    repo = make_repo(commits=1)
    git(repo, "update-ref", "refs/remotes/ghost/main", "HEAD")

    assert factory(repo).get_remotes() == ()
    assert [(b.name, b.is_remote) for b in raw_branches(repo)] == [
        ("main", False),
        ("ghost/main", True),
    ]


# --- несколько refs на одном коммите ---


def test_several_refs_on_one_commit_keep_every_label(
    make_repo: MakeRepo, git: Git, open_repo, branch_service
) -> None:
    repo = make_repo(commits=3)
    git(repo, "branch", "alpha")
    git(repo, "branch", "feature/x")
    git(repo, "branch", "elsewhere", "HEAD~2")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "update-ref", "refs/remotes/backup/main", "HEAD")
    head = rev(git, repo, "HEAD")

    labels = branch_service.get_ref_labels(open_repo(repo))

    assert [label.full_name for label in labels[head]] == [
        "refs/heads/main",  # текущая — первой
        "refs/heads/alpha",
        "refs/heads/feature/x",
        "refs/remotes/backup/main",
        "refs/remotes/origin/main",
    ]
    assert [label.kind for label in labels[head]] == [RefKind.LOCAL_BRANCH] * 3 + [
        RefKind.REMOTE_BRANCH
    ] * 2
    assert [label.name for label in labels[head]][-1] == "origin/main"
    assert labels[rev(git, repo, "HEAD~2")][0].full_name == "refs/heads/elsewhere"
    total = int(git(repo, "for-each-ref", "refs/heads", "refs/remotes", "--count=1000").count("\n"))
    assert sum(len(v) for v in labels.values()) == total == 6


def test_labels_match_every_ref_in_the_repository(
    tmp_path: Path, make_repo: MakeRepo, git: Git, open_repo, branch_service
) -> None:
    work = make_clone(tmp_path, git, make_repo, extra_branches=("feature/y",))
    git(work, "branch", "local-extra", "origin/feature/y")
    git(work, "branch", "same-as-main")

    labels = branch_service.get_ref_labels(open_repo(work))

    real_refs = {
        line.split()[0]
        for line in git(
            work, "for-each-ref", "--format=%(refname) %(symref)", "refs/heads", "refs/remotes"
        ).splitlines()
        if len(line.split()) == 1  # без symbolic refs (origin/HEAD)
    }
    assert {label.full_name for group in labels.values() for label in group} == real_refs


# --- detached HEAD ---


def test_detached_head_has_no_current_branch_and_gets_head_label(
    make_repo: MakeRepo, git: Git, open_repo, branch_service
) -> None:
    repo = make_repo(commits=3)
    git(repo, "branch", "old", "HEAD~1")
    git(repo, "checkout", "-q", "--detach", "HEAD~1")
    repository = open_repo(repo)
    detached_at = rev(git, repo, "HEAD")

    branches = branch_service.get_branches(repository)
    labels = branch_service.get_ref_labels(repository)

    assert repository.is_detached
    assert {b.name for b in branches} == {"main", "old"}
    assert not any(b.is_current for b in branches)
    assert [(label.full_name, label.kind, label.is_current) for label in labels[detached_at]] == [
        ("HEAD", RefKind.DETACHED_HEAD, True),
        ("refs/heads/old", RefKind.LOCAL_BRANCH, False),
    ]
    assert [label.full_name for label in labels[rev(git, repo, "main")]] == ["refs/heads/main"]


def test_detached_head_on_commit_without_any_branch(
    make_repo: MakeRepo, git: Git, open_repo, branch_service
) -> None:
    repo = make_repo(commits=1)
    git(repo, "checkout", "-q", "--detach")
    git(repo, *IDENT, "commit", "-q", "--allow-empty", "-m", "detached work")
    repository = open_repo(repo)

    labels = branch_service.get_ref_labels(repository)

    detached_at = rev(git, repo, "HEAD")
    assert [label.full_name for label in labels[detached_at]] == ["HEAD"]
    assert not any(b.is_current for b in branch_service.get_branches(repository))


# --- репозиторий без коммитов и unborn-ветка ---


def test_repository_without_commits(
    make_repo: MakeRepo, open_repo, raw_branches, branch_service
) -> None:
    repo = make_repo(commits=0)
    repository = open_repo(repo)

    assert raw_branches(repo) == ()
    (unborn,) = branch_service.get_branches(repository)
    assert unborn == Branch(name="main", is_remote=False, is_current=True, commit_hash=None)
    assert branch_service.get_ref_labels(repository) == {}


def test_unborn_branch_next_to_existing_branches(
    make_repo: MakeRepo, git: Git, open_repo, branch_service
) -> None:
    repo = make_repo(commits=2)
    git(repo, "checkout", "-q", "--orphan", "fresh")
    repository = open_repo(repo)

    branches = branch_service.get_branches(repository)
    labels = branch_service.get_ref_labels(repository)

    assert [(b.name, b.is_current, b.commit_hash is None) for b in branches] == [
        ("fresh", True, True),
        ("main", False, False),
    ]
    assert [label.full_name for group in labels.values() for label in group] == ["refs/heads/main"]


# --- worktree ---


def test_branch_checked_out_in_another_worktree_is_not_current(
    tmp_path: Path, make_repo: MakeRepo, git: Git, raw_branches
) -> None:
    repo = make_repo(commits=1)
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", "-b", "wt-branch", str(linked))

    assert [b.name for b in raw_branches(repo) if b.is_current] == ["main"]
    assert [b.name for b in raw_branches(linked) if b.is_current] == ["wt-branch"]


# --- согласованность с историей и сценарий целиком ---


@pytest.mark.parametrize("detached", [False, True])
def test_labels_agree_with_refs_attached_to_commits_by_history(
    tmp_path: Path, make_repo: MakeRepo, git: Git, factory, open_repo, branch_service, detached
) -> None:
    work = make_clone(tmp_path, git, make_repo, extra_branches=("feature/y",))
    git(work, "branch", "local-extra", "origin/feature/y")
    git(work, "branch", "same-as-main")
    if detached:
        git(work, "checkout", "-q", "--detach", "HEAD~1")
    repository = open_repo(work)

    labels = branch_service.get_ref_labels(repository)
    commits = HistoryService(factory).get_history(repository)

    assert commits
    for commit in commits:
        from_log = {r for r in commit.refs if r != "HEAD" and r != "refs/remotes/origin/HEAD"}
        from_labels = {
            label.full_name
            for label in labels.get(commit.hash, ())
            if label.kind is not RefKind.DETACHED_HEAD
        }
        assert from_labels == from_log, commit.subject
        has_head_label = any(
            label.kind is RefKind.DETACHED_HEAD for label in labels.get(commit.hash, ())
        )
        # ``Commit.refs`` содержит HEAD всегда; метка HEAD нужна только при detached HEAD.
        assert ("HEAD" in commit.refs and detached) == has_head_label


def test_services_end_to_end(tmp_path: Path, make_repo: MakeRepo, git: Git, factory) -> None:
    work = make_clone(tmp_path, git, make_repo)
    repositories = RepositoryService(factory)
    repository = repositories.open_repository(work / ".")

    branches = BranchService(factory).get_branches(repository)

    assert [(b.name, b.is_remote, b.is_current, b.upstream) for b in branches] == [
        ("main", False, True, "origin/main"),
        ("origin/main", True, False, None),
    ]
    assert [r.name for r in repositories.get_remotes(repository)] == ["origin"]
