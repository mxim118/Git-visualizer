"""RepositoryService с подменой порта доступа к Git: проверяется оркестрация, без запуска Git."""

from pathlib import Path

import pytest

from git_visualizer.application.errors import (
    BareRepositoryNotSupportedError,
    NotAGitRepositoryError,
    PathDoesNotExistError,
    PathIsNotDirectoryError,
    RepositoryOpenError,
    UnsafeRepositoryError,
)
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.domain.models import Remote, Repository
from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitNotFoundError,
    GitTimeoutError,
)

HEAD = "a" * 40
NOT_A_REPOSITORY = "fatal: not a git repository (or any of the parent directories): .git\n"
DUBIOUS_OWNERSHIP = (
    "fatal: detected dubious ownership in repository at '/x'\n"
    "To add an exception for this directory, call:\n\n"
    "\tgit config --global --add safe.directory /x\n"
)


class FakeGit:
    """Подмена GitRepositoryPort с заданными ответами."""

    def __init__(
        self,
        *,
        inside: bool = True,
        bare: bool = False,
        toplevel: Path | None = None,
        branch: str | None = "main",
        head: str | None = HEAD,
        remotes: tuple[Remote, ...] = (),
        inside_error: Exception | None = None,
    ) -> None:
        self.inside = inside
        self.bare = bare
        self.toplevel = toplevel
        self.branch = branch
        self.head = head
        self.remotes = remotes
        self.inside_error = inside_error

    def is_inside_work_tree(self) -> bool:
        if self.inside_error is not None:
            raise self.inside_error
        return self.inside

    def is_bare_repository(self) -> bool:
        return self.bare

    def get_toplevel(self) -> Path:
        assert self.toplevel is not None
        return self.toplevel

    def get_current_branch(self) -> str | None:
        return self.branch

    def get_head_commit(self) -> str | None:
        return self.head

    def get_remotes(self) -> tuple[Remote, ...]:
        return self.remotes


class FakeFactory:
    """Фабрика: запоминает, для каких каталогов запрашивался доступ к Git."""

    def __init__(self, git: FakeGit) -> None:
        self.git = git
        self.requested: list[Path] = []

    def __call__(self, path: Path) -> FakeGit:
        self.requested.append(path)
        return self.git


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "my repo"
    (path / "src" / "deep").mkdir(parents=True)
    return path


def make_service(git: FakeGit) -> tuple[RepositoryService, FakeFactory]:
    factory = FakeFactory(git)
    return RepositoryService(factory), factory


# --- проверка пути ---


def test_nonexistent_path_is_rejected_before_git_is_used(tmp_path: Path) -> None:
    service, factory = make_service(FakeGit())

    with pytest.raises(PathDoesNotExistError) as info:
        service.open_repository(tmp_path / "missing")

    assert info.value.path == tmp_path / "missing"
    assert factory.requested == []


@pytest.mark.parametrize("empty", ["", "   "])
def test_empty_path_is_not_treated_as_current_directory(empty: str) -> None:
    service, factory = make_service(FakeGit())

    with pytest.raises(PathDoesNotExistError):
        service.open_repository(empty)

    assert factory.requested == []


def test_file_path_is_rejected(tmp_path: Path) -> None:
    file = tmp_path / "file.txt"
    file.write_text("x")
    service, factory = make_service(FakeGit())

    with pytest.raises(PathIsNotDirectoryError):
        service.open_repository(file)

    assert factory.requested == []


def test_all_open_errors_share_a_base_class() -> None:
    for error in (
        PathDoesNotExistError,
        PathIsNotDirectoryError,
        NotAGitRepositoryError,
        BareRepositoryNotSupportedError,
        UnsafeRepositoryError,
    ):
        assert issubclass(error, RepositoryOpenError)


# --- успешное открытие ---


def test_opening_a_subfolder_returns_the_repository_root(root: Path) -> None:
    service, factory = make_service(FakeGit(toplevel=root))

    repository = service.open_repository(root / "src" / "deep")

    assert repository == Repository(
        path=root, name="my repo", current_branch="main", head_commit=HEAD
    )
    # Сначала проверяется выбранная папка, остальные запросы идут уже к корню.
    assert factory.requested == [root / "src" / "deep", root]


def test_str_and_pathlike_are_both_accepted(root: Path) -> None:
    service, _ = make_service(FakeGit(toplevel=root))

    assert service.open_repository(str(root)) == service.open_repository(root)


def test_detached_head_is_reported_without_branch(root: Path) -> None:
    service, _ = make_service(FakeGit(toplevel=root, branch=None))

    repository = service.open_repository(root)

    assert repository.is_detached
    assert repository.head_commit == HEAD


def test_repository_without_commits_has_no_head(root: Path) -> None:
    service, _ = make_service(FakeGit(toplevel=root, head=None))

    repository = service.open_repository(root)

    assert repository.is_unborn
    assert repository.current_branch == "main"


def test_repository_at_filesystem_root_gets_a_readable_name(tmp_path: Path) -> None:
    drive_root = Path(tmp_path.anchor)
    service, _ = make_service(FakeGit(toplevel=drive_root))

    assert service.open_repository(tmp_path).name == str(drive_root)


# --- отказы ---


def test_bare_repository_is_rejected(root: Path) -> None:
    service, _ = make_service(FakeGit(inside=False, bare=True))

    with pytest.raises(BareRepositoryNotSupportedError) as info:
        service.open_repository(root)

    assert info.value.path == root


def test_git_directory_of_regular_repository_is_not_a_repository(root: Path) -> None:
    service, _ = make_service(FakeGit(inside=False, bare=False))

    with pytest.raises(NotAGitRepositoryError) as info:
        service.open_repository(root)

    assert ".git" in info.value.message


def test_git_not_a_repository_error_is_translated_with_details(root: Path) -> None:
    original = GitCommandError(
        128, command=["git", "rev-parse"], stderr=NOT_A_REPOSITORY
    )
    service, _ = make_service(FakeGit(inside_error=original))

    with pytest.raises(NotAGitRepositoryError) as info:
        service.open_repository(root)

    assert info.value.__cause__ is original
    details = info.value.technical_details()
    assert "not a git repository" in details  # технические подробности не потеряны
    assert str(root) in details


def test_dubious_ownership_is_translated(root: Path) -> None:
    original = GitCommandError(128, command=["git", "rev-parse"], stderr=DUBIOUS_OWNERSHIP)
    service, _ = make_service(FakeGit(inside_error=original))

    with pytest.raises(UnsafeRepositoryError) as info:
        service.open_repository(root)

    assert info.value.__cause__ is original
    assert "safe.directory" in info.value.technical_details()


def test_unrecognized_git_failure_is_propagated_unchanged(root: Path) -> None:
    original = GitCommandError(128, stderr="fatal: something unexpected\n")
    service, _ = make_service(FakeGit(inside_error=original))

    with pytest.raises(GitCommandError) as info:
        service.open_repository(root)

    assert info.value is original


@pytest.mark.parametrize(
    "failure", [GitNotFoundError("git"), GitTimeoutError(30, command=["git", "rev-parse"])]
)
def test_git_not_found_and_timeout_are_propagated(root: Path, failure: Exception) -> None:
    service, _ = make_service(FakeGit(inside_error=failure))

    with pytest.raises(type(failure)):
        service.open_repository(root)


# --- remote ---


def test_remotes_are_read_from_the_repository_root(root: Path) -> None:
    remotes = (Remote(name="origin", fetch_url="https://example.com/r.git"),)
    service, factory = make_service(FakeGit(toplevel=root, remotes=remotes))
    repository = service.open_repository(root)
    factory.requested.clear()

    assert service.get_remotes(repository) == remotes
    assert factory.requested == [root]


def test_repository_without_remotes_gives_empty_tuple(root: Path) -> None:
    service, _ = make_service(FakeGit(toplevel=root))

    assert service.get_remotes(service.open_repository(root)) == ()
