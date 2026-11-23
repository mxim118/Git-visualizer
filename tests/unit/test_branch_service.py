"""BranchService и сопоставление refs с коммитами — с подменой порта, без запуска Git."""

from pathlib import Path

import pytest

from git_visualizer.application.services.branch_service import BranchService, build_ref_labels
from git_visualizer.domain.enums import RefKind
from git_visualizer.domain.models import Branch, RefLabel, Repository
from git_visualizer.infrastructure.git.git_errors import GitCommandError

H1, H2, H3 = "1" * 40, "2" * 40, "3" * 40


def local(name: str, commit: str | None, *, current: bool = False, upstream: str | None = None):
    return Branch(
        name=name, is_remote=False, is_current=current, commit_hash=commit, upstream=upstream
    )


def remote(name: str, commit: str):
    return Branch(name=name, is_remote=True, is_current=False, commit_hash=commit)


def make_repository(
    tmp_path: Path, *, branch: str | None = "main", head: str | None = H1
) -> Repository:
    return Repository(path=tmp_path, name="repo", current_branch=branch, head_commit=head)


class FakeGit:
    def __init__(self, branches: tuple[Branch, ...] = (), error: Exception | None = None) -> None:
        self.branches = branches
        self.error = error

    def get_branches(self) -> tuple[Branch, ...]:
        if self.error is not None:
            raise self.error
        return self.branches


def service_for(fake: FakeGit, requested: list[Path] | None = None) -> BranchService:
    def factory(path: Path) -> FakeGit:
        if requested is not None:
            requested.append(path)
        return fake

    return BranchService(factory)


# --- Branch / RefLabel ---


def test_branch_ref_name_uses_full_ref_format() -> None:
    assert local("main", H1).ref_name == "refs/heads/main"
    assert local("feature/x", H1).ref_name == "refs/heads/feature/x"
    assert remote("origin/main", H1).ref_name == "refs/remotes/origin/main"


def test_ref_label_defaults_and_immutability() -> None:
    label = RefLabel(name="main", full_name="refs/heads/main", kind=RefKind.LOCAL_BRANCH)

    assert label.is_current is False
    with pytest.raises(AttributeError):
        label.name = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        RefLabel("main", "refs/heads/main", RefKind.LOCAL_BRANCH)  # type: ignore[misc]


# --- BranchService.get_branches ---


def test_returns_branches_from_git_in_given_order(tmp_path: Path) -> None:
    branches = (local("main", H1, current=True), local("dev", H2), remote("origin/main", H1))

    assert service_for(FakeGit(branches)).get_branches(make_repository(tmp_path)) == branches


def test_uses_repository_root_path(tmp_path: Path) -> None:
    requested: list[Path] = []

    service_for(FakeGit(), requested).get_branches(make_repository(tmp_path))

    assert requested == [tmp_path]


def test_repository_without_branches(tmp_path: Path) -> None:
    assert service_for(FakeGit()).get_branches(make_repository(tmp_path)) == ()


def test_unborn_current_branch_is_added_first(tmp_path: Path) -> None:
    repository = make_repository(tmp_path, branch="main", head=None)

    (unborn,) = service_for(FakeGit()).get_branches(repository)

    assert unborn == local("main", None, current=True)


def test_unborn_branch_is_added_alongside_existing_branches(tmp_path: Path) -> None:
    # git checkout --orphan fresh: HEAD не указывает на коммит, другие ветки остаются.
    repository = make_repository(tmp_path, branch="fresh", head=None)
    existing = (local("main", H1), remote("origin/main", H1))

    branches = service_for(FakeGit(existing)).get_branches(repository)

    assert branches == (local("fresh", None, current=True), *existing)
    assert [b.is_current for b in branches] == [True, False, False]


def test_detached_head_adds_nothing(tmp_path: Path) -> None:
    repository = make_repository(tmp_path, branch=None, head=H1)
    existing = (local("main", H1),)

    branches = service_for(FakeGit(existing)).get_branches(repository)

    assert branches == existing
    assert not any(b.is_current for b in branches)


def test_normal_repository_adds_nothing(tmp_path: Path) -> None:
    existing = (local("main", H1, current=True),)

    assert service_for(FakeGit(existing)).get_branches(make_repository(tmp_path)) == existing


def test_no_phantom_branch_when_repository_has_commits(tmp_path: Path) -> None:
    # Ветку из снимка Repository добавляют только в репозитории без коммитов (unborn).
    # Если снимок устарел, а коммиты есть, список веток — только то, что отдал Git.
    repository = make_repository(tmp_path, branch="deleted-meanwhile", head=H1)
    existing = (local("main", H1),)

    assert service_for(FakeGit(existing)).get_branches(repository) == existing


def test_git_errors_are_propagated_unchanged(tmp_path: Path) -> None:
    error = GitCommandError(128, command=("git", "for-each-ref"), stderr="fatal: boom\n")

    with pytest.raises(GitCommandError) as info:
        service_for(FakeGit(error=error)).get_branches(make_repository(tmp_path))

    assert info.value is error


def test_get_ref_labels_combines_branches_and_repository_state(tmp_path: Path) -> None:
    branches = (local("main", H1, current=True), remote("origin/main", H1))

    labels = service_for(FakeGit(branches)).get_ref_labels(make_repository(tmp_path))

    assert [label.full_name for label in labels[H1]] == ["refs/heads/main", "refs/remotes/origin/main"]


# --- build_ref_labels ---


def names(labels: tuple[RefLabel, ...]) -> list[str]:
    return [label.full_name for label in labels]


def test_no_branches_no_labels(tmp_path: Path) -> None:
    assert build_ref_labels(make_repository(tmp_path), ()) == {}


def test_each_branch_labels_its_commit(tmp_path: Path) -> None:
    branches = (local("main", H1, current=True), local("dev", H2), remote("origin/dev", H3))

    labels = build_ref_labels(make_repository(tmp_path), branches)

    assert set(labels) == {H1, H2, H3}
    assert labels[H1] == (
        RefLabel(name="main", full_name="refs/heads/main", kind=RefKind.LOCAL_BRANCH, is_current=True),
    )
    assert labels[H3] == (
        RefLabel(name="origin/dev", full_name="refs/remotes/origin/dev", kind=RefKind.REMOTE_BRANCH),
    )


def test_several_refs_on_one_commit_are_all_kept(tmp_path: Path) -> None:
    branches = (
        local("zeta", H1),
        local("main", H1, current=True),
        local("alpha", H1),
        remote("origin/main", H1),
        remote("backup/main", H1),
        local("elsewhere", H2),
    )

    labels = build_ref_labels(make_repository(tmp_path), branches)

    assert len(labels[H1]) == 5
    # порядок: текущая ветка, остальные локальные по имени, затем remote-tracking по имени
    assert names(labels[H1]) == [
        "refs/heads/main",
        "refs/heads/alpha",
        "refs/heads/zeta",
        "refs/remotes/backup/main",
        "refs/remotes/origin/main",
    ]
    assert [label.is_current for label in labels[H1]] == [True, False, False, False, False]
    assert names(labels[H2]) == ["refs/heads/elsewhere"]


def test_label_count_is_conserved(tmp_path: Path) -> None:
    branches = tuple(local(f"b{i}", (H1, H2, H3)[i % 3]) for i in range(30)) + (
        remote("origin/b0", H1),
        remote("origin/b1", H2),
    )

    labels = build_ref_labels(make_repository(tmp_path), branches)

    assert sum(len(v) for v in labels.values()) == len(branches)
    assert {name for v in labels.values() for name in names(v)} == {b.ref_name for b in branches}


def test_result_does_not_depend_on_input_order(tmp_path: Path) -> None:
    branches = (local("b", H1), local("a", H1, current=True), remote("origin/a", H1), local("c", H2))
    repository = make_repository(tmp_path)

    assert build_ref_labels(repository, branches) == build_ref_labels(
        repository, tuple(reversed(branches))
    )


def test_detached_head_gets_its_own_label_first(tmp_path: Path) -> None:
    repository = make_repository(tmp_path, branch=None, head=H1)
    branches = (local("main", H1), remote("origin/main", H2))

    labels = build_ref_labels(repository, branches)

    head_label = labels[H1][0]
    assert head_label == RefLabel(
        name="HEAD", full_name="HEAD", kind=RefKind.DETACHED_HEAD, is_current=True
    )
    assert names(labels[H1]) == ["HEAD", "refs/heads/main"]
    assert not any(label.is_current for label in labels[H1][1:])
    assert names(labels[H2]) == ["refs/remotes/origin/main"]


def test_detached_head_on_commit_without_branches(tmp_path: Path) -> None:
    repository = make_repository(tmp_path, branch=None, head=H3)

    labels = build_ref_labels(repository, (local("main", H1),))

    assert names(labels[H3]) == ["HEAD"]
    assert names(labels[H1]) == ["refs/heads/main"]


def test_attached_head_has_no_separate_head_label(tmp_path: Path) -> None:
    labels = build_ref_labels(make_repository(tmp_path), (local("main", H1, current=True),))

    assert names(labels[H1]) == ["refs/heads/main"]
    assert labels[H1][0].is_current


def test_unborn_branch_has_no_label_but_other_branches_do(tmp_path: Path) -> None:
    repository = make_repository(tmp_path, branch="fresh", head=None)
    branches = (local("fresh", None, current=True), local("main", H1))

    labels = build_ref_labels(repository, branches)

    assert labels == {
        H1: (RefLabel(name="main", full_name="refs/heads/main", kind=RefKind.LOCAL_BRANCH),)
    }


def test_repository_without_commits_has_no_labels(tmp_path: Path) -> None:
    repository = make_repository(tmp_path, branch="main", head=None)

    assert build_ref_labels(repository, (local("main", None, current=True),)) == {}


def test_local_and_remote_branches_with_similar_names_stay_distinct(tmp_path: Path) -> None:
    # Локальная ветка «origin/main» (редкий, но допустимый случай) не сливается с remote-tracking.
    branches = (local("origin/main", H1), remote("origin/main", H1))

    labels = build_ref_labels(make_repository(tmp_path), branches)

    assert names(labels[H1]) == ["refs/heads/origin/main", "refs/remotes/origin/main"]
    assert [label.kind for label in labels[H1]] == [RefKind.LOCAL_BRANCH, RefKind.REMOTE_BRANCH]
