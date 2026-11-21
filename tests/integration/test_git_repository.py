"""GitRepository на реальных временных репозиториях."""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from git_visualizer.domain.models import Remote
from git_visualizer.infrastructure.git.git_errors import GitCommandError, GitErrorKind
from git_visualizer.infrastructure.git.git_repository import GitRepository
from git_visualizer.infrastructure.git.git_runner import GitRunner

MakeRepo = Callable[..., Path]
Git = Callable[..., str]


@pytest.fixture
def open_git() -> Callable[[Path], GitRepository]:
    runner = GitRunner(default_timeout=60)
    return lambda path: GitRepository(runner, path)


# --- проверка репозитория ---


def test_repository_root_is_inside_work_tree(make_repo: MakeRepo, open_git) -> None:
    assert open_git(make_repo()).is_inside_work_tree() is True


def test_nested_folder_is_inside_work_tree_and_toplevel_is_the_root(
    make_repo: MakeRepo, open_git
) -> None:
    root = make_repo()
    nested = root / "src" / "deep"
    nested.mkdir(parents=True)

    git = open_git(nested)

    assert git.is_inside_work_tree() is True
    assert git.get_toplevel().samefile(root)


def test_plain_folder_raises_not_a_repository_error(tmp_path: Path, open_git) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(GitCommandError) as info:
        open_git(plain).is_inside_work_tree()

    assert info.value.kind is GitErrorKind.NOT_A_REPOSITORY
    assert "not a git repository" in info.value.stderr


def test_bare_repository_is_not_a_work_tree(tmp_path: Path, git: Git, open_git) -> None:
    bare = tmp_path / "bare.git"
    bare.mkdir()
    git(bare, "init", "-q", "--bare")

    repository = open_git(bare)

    assert repository.is_inside_work_tree() is False
    assert repository.is_bare_repository() is True


def test_git_directory_is_neither_work_tree_nor_bare(make_repo: MakeRepo, open_git) -> None:
    repository = open_git(make_repo() / ".git")

    assert repository.is_inside_work_tree() is False
    assert repository.is_bare_repository() is False


def test_toplevel_of_unicode_path_with_spaces(make_repo: MakeRepo, open_git) -> None:
    root = make_repo("мой проект с пробелами")

    toplevel = open_git(root).get_toplevel()

    assert toplevel.name == "мой проект с пробелами"
    assert toplevel.samefile(root)


@pytest.mark.skipif(os.name == "nt", reason="создание symlink на Windows требует прав")
def test_toplevel_resolves_symlinks(make_repo: MakeRepo, tmp_path: Path, open_git) -> None:
    root = make_repo()
    (root / "src").mkdir()
    link = tmp_path / "link"
    link.symlink_to(root / "src")

    toplevel = open_git(link).get_toplevel()

    assert toplevel.samefile(root)
    assert toplevel != link


# --- ветка и HEAD ---


def test_current_branch_and_head_commit(make_repo: MakeRepo, git: Git, open_git) -> None:
    root = make_repo(commits=2)
    repository = open_git(root)

    assert repository.get_current_branch() == "main"
    assert repository.get_head_commit() == git(root, "rev-parse", "HEAD").strip()
    assert repository.has_head() is True


def test_branch_name_with_slash_and_unicode(make_repo: MakeRepo, git: Git, open_git) -> None:
    root = make_repo()
    git(root, "checkout", "-q", "-b", "feature/задача-1")

    assert open_git(root).get_current_branch() == "feature/задача-1"


def test_repository_without_commits(make_repo: MakeRepo, open_git) -> None:
    repository = open_git(make_repo(commits=0))

    assert repository.get_head_commit() is None
    assert repository.has_head() is False
    # Имя будущей ветки известно и до первого коммита.
    assert repository.get_current_branch() == "main"


def test_detached_head_has_no_branch_but_has_commit(make_repo: MakeRepo, git: Git, open_git) -> None:
    root = make_repo(commits=2)
    git(root, "checkout", "-q", "--detach", "HEAD~1")

    repository = open_git(root)

    assert repository.get_current_branch() is None
    assert repository.get_head_commit() == git(root, "rev-parse", "HEAD").strip()


def test_unborn_branch_in_repository_with_history(make_repo: MakeRepo, git: Git, open_git) -> None:
    root = make_repo(commits=2)
    git(root, "checkout", "-q", "--orphan", "other")

    repository = open_git(root)

    assert repository.get_current_branch() == "other"
    assert repository.get_head_commit() is None


def test_head_commit_failure_other_than_unborn_is_raised(make_repo: MakeRepo, open_git) -> None:
    # Каталог без репозитория: rev-parse завершается кодом 128, а не «1 = нет коммитов».
    plain = make_repo().parent / "plain-for-head"
    plain.mkdir()

    with pytest.raises(GitCommandError) as info:
        open_git(plain).get_head_commit()

    assert info.value.returncode == 128


# --- remote ---


def test_repository_without_remotes(make_repo: MakeRepo, open_git) -> None:
    assert open_git(make_repo()).get_remotes() == ()


def test_configured_remotes_are_read(make_repo: MakeRepo, git: Git, open_git) -> None:
    root = make_repo()
    git(root, "remote", "add", "origin", "https://user:s3cr3t@example.com/org/repo.git")
    git(root, "remote", "add", "fork", "git@github.com:me/repo.git")
    git(root, "remote", "set-url", "--push", "fork", "git@github.com:me/repo-push.git")
    git(root, "remote", "add", "локальный", "/srv/пути с пробелами/r.git")

    remotes = open_git(root).get_remotes()

    assert remotes == (
        Remote(
            name="fork",
            fetch_url="git@github.com:me/repo.git",
            push_url="git@github.com:me/repo-push.git",
        ),
        Remote(
            name="origin",
            fetch_url="https://example.com/org/repo.git",  # пароль в отображаемом URL скрыт
            push_url="https://example.com/org/repo.git",
        ),
        Remote(
            name="локальный",
            fetch_url="/srv/пути с пробелами/r.git",
            push_url="/srv/пути с пробелами/r.git",
        ),
    )


def test_remote_without_url_is_listed(make_repo: MakeRepo, git: Git, open_git) -> None:
    root = make_repo()
    git(root, "config", "remote.nourl.fetch", "+refs/heads/*:refs/remotes/nourl/*")

    assert open_git(root).get_remotes() == (Remote(name="nourl"),)
