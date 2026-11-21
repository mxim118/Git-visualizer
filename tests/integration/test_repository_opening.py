"""Открытие репозитория через RepositoryService + GitRepository + настоящий Git."""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from git_visualizer.application.errors import (
    BareRepositoryNotSupportedError,
    NotAGitRepositoryError,
    PathDoesNotExistError,
    PathIsNotDirectoryError,
    UnsafeRepositoryError,
)
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.domain.models import Remote
from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitNotFoundError,
)
from git_visualizer.infrastructure.git.git_repository import GitRepository
from git_visualizer.infrastructure.git.git_runner import GitRunner, GitVersion

MakeRepo = Callable[..., Path]
Git = Callable[..., str]


@pytest.fixture
def service() -> RepositoryService:
    runner = GitRunner(default_timeout=60)
    return RepositoryService(lambda path: GitRepository(runner, path))


# --- успешное открытие ---


def test_open_repository_root(make_repo: MakeRepo, git: Git, service: RepositoryService) -> None:
    root = make_repo("project", commits=2)

    repository = service.open_repository(root)

    assert repository.path.samefile(root)
    assert repository.name == "project"
    assert repository.current_branch == "main"
    assert repository.head_commit == git(root, "rev-parse", "HEAD").strip()
    assert repository.is_bare is False
    assert not repository.is_detached and not repository.is_unborn


def test_open_nested_folder_opens_repository_root(
    make_repo: MakeRepo, service: RepositoryService
) -> None:
    root = make_repo("project")
    nested = root / "src" / "package" / "deep"
    nested.mkdir(parents=True)

    repository = service.open_repository(nested)

    assert repository.path.samefile(root)
    assert repository.name == "project"


def test_open_path_given_as_string(make_repo: MakeRepo, service: RepositoryService) -> None:
    root = make_repo()

    assert service.open_repository(str(root)).path.samefile(root)


def test_open_repository_with_unicode_and_spaces_in_path(
    make_repo: MakeRepo, service: RepositoryService
) -> None:
    root = make_repo("мои проекты — git visualizer")

    repository = service.open_repository(root)

    assert repository.name == "мои проекты — git visualizer"
    assert repository.path.samefile(root)


def test_open_repository_without_commits(make_repo: MakeRepo, service: RepositoryService) -> None:
    root = make_repo(commits=0)

    repository = service.open_repository(root)

    assert repository.is_unborn
    assert repository.head_commit is None
    assert repository.current_branch == "main"


def test_open_repository_with_detached_head(
    make_repo: MakeRepo, git: Git, service: RepositoryService
) -> None:
    root = make_repo(commits=2)
    git(root, "checkout", "-q", "--detach", "HEAD~1")

    repository = service.open_repository(root)

    assert repository.is_detached
    assert repository.current_branch is None
    assert repository.head_commit == git(root, "rev-parse", "HEAD").strip()


def test_open_repository_on_unborn_branch(
    make_repo: MakeRepo, git: Git, service: RepositoryService
) -> None:
    root = make_repo(commits=2)
    git(root, "checkout", "-q", "--orphan", "fresh")

    repository = service.open_repository(root)

    assert repository.is_unborn
    assert repository.current_branch == "fresh"


def test_reopening_reflects_new_state(
    make_repo: MakeRepo, git: Git, service: RepositoryService
) -> None:
    root = make_repo(commits=1)
    first = service.open_repository(root)
    git(root, "checkout", "-q", "-b", "feature")

    second = service.open_repository(root)

    assert (first.current_branch, second.current_branch) == ("main", "feature")
    assert first.head_commit == second.head_commit


# --- remote ---


def test_remotes_of_opened_repository(
    make_repo: MakeRepo, git: Git, service: RepositoryService
) -> None:
    root = make_repo()
    repository = service.open_repository(root)
    assert service.get_remotes(repository) == ()

    git(root, "remote", "add", "origin", "https://token@example.com/r.git")

    assert service.get_remotes(repository) == (
        Remote(
            name="origin",
            fetch_url="https://example.com/r.git",
            push_url="https://example.com/r.git",
        ),
    )


# --- отказы ---


def test_nonexistent_path(tmp_path: Path, service: RepositoryService) -> None:
    with pytest.raises(PathDoesNotExistError) as info:
        service.open_repository(tmp_path / "no-such-folder")

    assert info.value.path == tmp_path / "no-such-folder"


def test_path_to_file(make_repo: MakeRepo, service: RepositoryService) -> None:
    file = make_repo() / "file0.txt"  # файл внутри настоящего репозитория

    with pytest.raises(PathIsNotDirectoryError):
        service.open_repository(file)


def test_plain_folder_keeps_technical_details(tmp_path: Path, service: RepositoryService) -> None:
    plain = tmp_path / "just a folder"
    plain.mkdir()

    with pytest.raises(NotAGitRepositoryError) as info:
        service.open_repository(plain)

    assert isinstance(info.value.__cause__, GitCommandError)
    assert "not a git repository" in info.value.technical_details()
    assert str(plain) in info.value.technical_details()


def test_git_directory_itself_is_rejected(make_repo: MakeRepo, service: RepositoryService) -> None:
    with pytest.raises(NotAGitRepositoryError) as info:
        service.open_repository(make_repo() / ".git")

    assert ".git" in info.value.message


def test_bare_repository_is_rejected(tmp_path: Path, git: Git, service: RepositoryService) -> None:
    bare = tmp_path / "server.git"
    bare.mkdir()
    git(bare, "init", "-q", "--bare")

    with pytest.raises(BareRepositoryNotSupportedError):
        service.open_repository(bare)


def test_folder_inside_bare_repository_is_rejected(
    tmp_path: Path, git: Git, service: RepositoryService
) -> None:
    bare = tmp_path / "server.git"
    bare.mkdir()
    git(bare, "init", "-q", "--bare")

    with pytest.raises(BareRepositoryNotSupportedError):
        service.open_repository(bare / "refs")


def test_missing_git_is_reported_as_git_not_found(
    make_repo: MakeRepo,
) -> None:
    runner = GitRunner(git_executable="git-visualizer-no-such-git")
    service = RepositoryService(lambda path: GitRepository(runner, path))

    with pytest.raises(GitNotFoundError):
        service.open_repository(make_repo())


def _can_create_foreign_owned_repository() -> bool:
    if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False  # смена владельца возможна только от root на POSIX
    try:
        version = GitRunner().get_version()
    except GitNotFoundError:
        return False
    return version >= GitVersion(2, 35, 2)  # проверка владельца появилась в Git 2.35.2


@pytest.mark.skipif(
    not _can_create_foreign_owned_repository(),
    reason="нужен POSIX, root и Git >= 2.35.2, чтобы создать репозиторий с чужим владельцем",
)
def test_repository_with_foreign_owner_is_reported_as_unsafe(
    make_repo: MakeRepo, service: RepositoryService
) -> None:
    root = make_repo()
    for directory, subdirs, files in os.walk(root):
        for name in [".", *subdirs, *files]:
            os.chown(Path(directory) / name, 12345, 12345)

    with pytest.raises(UnsafeRepositoryError) as info:
        service.open_repository(root)

    assert "safe.directory" in info.value.technical_details()
