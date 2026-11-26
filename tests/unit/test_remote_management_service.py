"""RemoteService: конфигурация remote с подменой порта, без запуска Git."""

from pathlib import Path

import pytest

from git_visualizer.application.errors import (
    InvalidRemoteNameError,
    InvalidRemoteUrlError,
    UnknownRemoteError,
)
from git_visualizer.application.services.remote_service import (
    RemoteConfigurationResult,
    RemoteService,
)
from git_visualizer.domain.models import Remote, Repository
from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitErrorKind,
)


class FakeGit:
    def __init__(
        self,
        *,
        remotes: tuple[Remote, ...] = (),
        add_error: BaseException | None = None,
        rename_error: BaseException | None = None,
        set_url_error: BaseException | None = None,
        remove_error: BaseException | None = None,
    ) -> None:
        self.remotes = remotes
        self.add_error = add_error
        self.rename_error = rename_error
        self.set_url_error = set_url_error
        self.remove_error = remove_error
        self.calls: list[tuple[object, ...]] = []

    def get_remotes(self) -> tuple[Remote, ...]:
        return self.remotes

    def add_remote(self, name: str, url: str) -> None:
        self.calls.append(("add", name, url))
        if self.add_error is not None:
            raise self.add_error
        self.remotes = (
            *self.remotes,
            Remote(name=name, fetch_url=url, push_url=url),
        )

    def rename_remote(self, name: str, new_name: str) -> None:
        self.calls.append(("rename", name, new_name))
        if self.rename_error is not None:
            raise self.rename_error
        self.remotes = tuple(
            Remote(
                name=new_name if remote.name == name else remote.name,
                fetch_url=remote.fetch_url,
                push_url=remote.push_url,
            )
            for remote in self.remotes
        )

    def set_remote_url(self, name: str, url: str) -> None:
        self.calls.append(("set-url", name, url))
        if self.set_url_error is not None:
            raise self.set_url_error
        self.remotes = tuple(
            Remote(
                name=remote.name,
                fetch_url=url if remote.name == name else remote.fetch_url,
                push_url=url if remote.name == name else remote.push_url,
            )
            for remote in self.remotes
        )

    def remove_remote(self, name: str) -> None:
        self.calls.append(("remove", name))
        if self.remove_error is not None:
            raise self.remove_error
        self.remotes = tuple(
            remote for remote in self.remotes if remote.name != name
        )


def make_repository(tmp_path: Path) -> Repository:
    return Repository(
        path=tmp_path, name="repo", current_branch="main", head_commit="a" * 40
    )


def service_for(
    fake: FakeGit, requested: list[Path] | None = None
) -> RemoteService:
    def factory(path: Path) -> FakeGit:
        if requested is not None:
            requested.append(path)
        return fake

    return RemoteService(factory)


def test_add_remote_trims_values_and_returns_fresh_remotes(tmp_path: Path) -> None:
    fake = FakeGit()
    requested: list[Path] = []

    result = service_for(fake, requested).add_remote(
        make_repository(tmp_path),
        "  origin  ",
        "\thttps://example.com/repo.git\n",
    )

    assert requested == [tmp_path]
    assert fake.calls == [("add", "origin", "https://example.com/repo.git")]
    assert result == RemoteConfigurationResult(
        operation="add",
        remote_name="origin",
        remotes=(
            Remote(
                name="origin",
                fetch_url="https://example.com/repo.git",
                push_url="https://example.com/repo.git",
            ),
        ),
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/user/repo.git",
        "http://example.com/user/repo.git",
        "git@example.com:user/repo.git",
        "ssh://git@example.com:22/user/repo.git",
        "file:///tmp/repo.git",
        "C:/projects/local-repo",
        "./relative/repo.git",
    ],
)
def test_add_remote_accepts_https_ssh_and_local_urls(
    tmp_path: Path, url: str
) -> None:
    fake = FakeGit()

    service_for(fake).add_remote(make_repository(tmp_path), "origin", url)

    assert fake.calls == [("add", "origin", url)]


@pytest.mark.parametrize(
    "url",
    ["", "   ", "https://", "ssh://", "://example.com/repo", "https:///repo"],
)
def test_add_remote_rejects_invalid_url_before_git(
    tmp_path: Path, url: str
) -> None:
    fake = FakeGit()

    with pytest.raises(InvalidRemoteUrlError):
        service_for(fake).add_remote(make_repository(tmp_path), "origin", url)

    assert fake.calls == []


@pytest.mark.parametrize("name", ["", "   ", "bad\x00name"])
def test_add_remote_rejects_invalid_name_before_git(
    tmp_path: Path, name: str
) -> None:
    fake = FakeGit()

    with pytest.raises(InvalidRemoteNameError):
        service_for(fake).add_remote(
            make_repository(tmp_path), name, "https://example.com/repo.git"
        )

    assert fake.calls == []


def test_add_remote_propagates_duplicate_name_error(tmp_path: Path) -> None:
    error = GitCommandError(
        3, stderr="error: remote origin already exists.\n"
    )

    with pytest.raises(GitCommandError) as info:
        service_for(FakeGit(add_error=error)).add_remote(
            make_repository(tmp_path), "origin", "https://example.com/repo.git"
        )

    assert info.value is error
    assert error.kind is GitErrorKind.REMOTE_ALREADY_EXISTS


def test_edit_remote_renames_and_updates_url_in_order(tmp_path: Path) -> None:
    fake = FakeGit(
        remotes=(
            Remote(
                name="origin",
                fetch_url="https://old.example.com/repo.git",
                push_url="https://old.example.com/repo.git",
            ),
        )
    )

    result = service_for(fake).edit_remote(
        make_repository(tmp_path),
        "origin",
        new_name="upstream",
        url="git@example.com:user/repo.git",
    )

    assert fake.calls == [
        ("rename", "origin", "upstream"),
        ("set-url", "upstream", "git@example.com:user/repo.git"),
    ]
    assert result.operation == "edit"
    assert result.remote_name == "origin"
    assert result.new_name == "upstream"
    assert result.remotes[0].name == "upstream"
    assert result.remotes[0].fetch_url == "git@example.com:user/repo.git"


def test_edit_remote_skips_unchanged_url(tmp_path: Path) -> None:
    fake = FakeGit(
        remotes=(Remote(name="origin", fetch_url="https://example.com/r.git"),)
    )

    service_for(fake).edit_remote(
        make_repository(tmp_path),
        "origin",
        new_name="upstream",
        url="https://example.com/r.git",
    )

    assert fake.calls == [("rename", "origin", "upstream")]


def test_edit_remote_skips_rename_for_same_name(tmp_path: Path) -> None:
    fake = FakeGit(
        remotes=(Remote(name="origin", fetch_url="https://example.com/r.git"),)
    )

    result = service_for(fake).edit_remote(
        make_repository(tmp_path),
        "origin",
        new_name="origin",
        url="https://example.com/new.git",
    )

    assert fake.calls == [
        ("set-url", "origin", "https://example.com/new.git")
    ]
    assert result.new_name == "origin"


def test_edit_remote_without_changes_runs_no_git_commands(
    tmp_path: Path,
) -> None:
    fake = FakeGit(
        remotes=(Remote(name="origin", fetch_url="https://example.com/r.git"),)
    )

    service_for(fake).edit_remote(
        make_repository(tmp_path),
        "origin",
        new_name="origin",
        url="https://example.com/r.git",
    )

    assert fake.calls == []


def test_edit_remote_requires_configured_remote(tmp_path: Path) -> None:
    fake = FakeGit()

    with pytest.raises(UnknownRemoteError) as info:
        service_for(fake).edit_remote(
            make_repository(tmp_path),
            "origin",
            new_name="upstream",
            url="https://example.com/r.git",
        )

    assert info.value.remote_name == "origin"
    assert fake.calls == []


def test_edit_remote_validates_new_name_and_url_before_git(
    tmp_path: Path,
) -> None:
    fake = FakeGit(
        remotes=(Remote(name="origin", fetch_url="https://example.com/r.git"),)
    )
    service = service_for(fake)
    repository = make_repository(tmp_path)

    with pytest.raises(InvalidRemoteNameError):
        service.edit_remote(
            repository, "origin", new_name="  ", url="https://example.com/r.git"
        )
    with pytest.raises(InvalidRemoteUrlError):
        service.edit_remote(
            repository, "origin", new_name="upstream", url="https://"
        )

    assert fake.calls == []


def test_edit_remote_propagates_git_errors_unchanged(tmp_path: Path) -> None:
    error = GitCommandError(
        3, stderr="error: remote upstream already exists.\n"
    )
    fake = FakeGit(
        remotes=(Remote(name="origin", fetch_url="https://example.com/r.git"),),
        rename_error=error,
    )

    with pytest.raises(GitCommandError) as info:
        service_for(fake).edit_remote(
            make_repository(tmp_path),
            "origin",
            new_name="upstream",
            url="https://example.com/new.git",
        )

    assert info.value is error
    # До set-url дело не дошло.
    assert fake.calls == [("rename", "origin", "upstream")]


def test_edit_remote_propagates_set_url_error_after_rename(
    tmp_path: Path,
) -> None:
    error = GitCommandError(2, stderr="error: No such remote: 'upstream'\n")
    fake = FakeGit(
        remotes=(Remote(name="origin", fetch_url="https://example.com/r.git"),),
        set_url_error=error,
    )

    with pytest.raises(GitCommandError) as info:
        service_for(fake).edit_remote(
            make_repository(tmp_path),
            "origin",
            new_name="upstream",
            url="https://example.com/new.git",
        )

    assert info.value is error
    # Переименование уже выполнено; контроллер перечитает состояние после ошибки.
    assert fake.calls == [
        ("rename", "origin", "upstream"),
        ("set-url", "upstream", "https://example.com/new.git"),
    ]


def test_remove_remote_returns_remaining_remotes(tmp_path: Path) -> None:
    fake = FakeGit(
        remotes=(
            Remote(name="fork", fetch_url="https://example.com/fork.git"),
            Remote(name="origin", fetch_url="https://example.com/r.git"),
        )
    )

    result = service_for(fake).remove_remote(
        make_repository(tmp_path), "origin"
    )

    assert fake.calls == [("remove", "origin")]
    assert result.operation == "remove"
    assert result.remote_name == "origin"
    assert tuple(remote.name for remote in result.remotes) == ("fork",)


def test_remove_remote_requires_configured_remote(tmp_path: Path) -> None:
    fake = FakeGit()

    with pytest.raises(UnknownRemoteError):
        service_for(fake).remove_remote(make_repository(tmp_path), "origin")

    assert fake.calls == []


def test_remove_remote_rejects_empty_name(tmp_path: Path) -> None:
    fake = FakeGit()

    with pytest.raises(InvalidRemoteNameError):
        service_for(fake).remove_remote(make_repository(tmp_path), "  ")

    assert fake.calls == []


def test_remove_remote_propagates_git_errors(tmp_path: Path) -> None:
    error = GitCommandError(2, stderr="error: No such remote: 'origin'\n")
    fake = FakeGit(
        remotes=(Remote(name="origin", fetch_url="https://example.com/r.git"),),
        remove_error=error,
    )

    with pytest.raises(GitCommandError) as info:
        service_for(fake).remove_remote(make_repository(tmp_path), "origin")

    assert info.value is error
