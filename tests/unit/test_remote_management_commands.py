"""Аргументы и вызовы Git-команд управления remote: без реального remote."""

from pathlib import Path

import pytest

from git_visualizer.infrastructure.git.git_repository import (
    GitRepository,
    build_remote_add_arguments,
    build_remote_remove_arguments,
    build_remote_rename_arguments,
    build_remote_set_url_arguments,
)
from git_visualizer.infrastructure.git.git_runner import GitResult


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def run(self, args: list[str], **kwargs: object) -> GitResult:
        self.calls.append((list(args), dict(kwargs)))
        return GitResult(command=("git", *args), returncode=0, stdout="", stderr="")


def test_add_remote_arguments_separate_options_from_values() -> None:
    assert build_remote_add_arguments("origin", "https://example.com/r.git") == [
        "remote",
        "add",
        "--",
        "origin",
        "https://example.com/r.git",
    ]


def test_add_remote_arguments_trim_values() -> None:
    assert build_remote_add_arguments(
        "  origin  ", "  git@example.com:me/r.git  "
    ) == [
        "remote",
        "add",
        "--",
        "origin",
        "git@example.com:me/r.git",
    ]


def test_rename_remote_arguments() -> None:
    assert build_remote_rename_arguments("origin", "upstream") == [
        "remote",
        "rename",
        "--",
        "origin",
        "upstream",
    ]


def test_set_remote_url_arguments() -> None:
    assert build_remote_set_url_arguments(
        "origin", "ssh://git@example.com/repo.git"
    ) == [
        "remote",
        "set-url",
        "--",
        "origin",
        "ssh://git@example.com/repo.git",
    ]


def test_remove_remote_arguments() -> None:
    assert build_remote_remove_arguments("origin") == [
        "remote",
        "remove",
        "--",
        "origin",
    ]


@pytest.mark.parametrize(
    ("builder", "values"),
    [
        (build_remote_add_arguments, ("", "https://example.com/r.git")),
        (build_remote_add_arguments, ("origin", "  ")),
        (build_remote_rename_arguments, ("", "upstream")),
        (build_remote_rename_arguments, ("origin", "  ")),
        (build_remote_set_url_arguments, ("", "https://example.com/r.git")),
        (build_remote_set_url_arguments, ("origin", "  ")),
        (build_remote_remove_arguments, ("",)),
    ],
)
def test_remote_builders_reject_empty_values(builder, values) -> None:
    with pytest.raises(ValueError):
        builder(*values)


def test_repository_runs_remote_commands_without_shell_options() -> None:
    runner = FakeRunner()
    repository = GitRepository(runner, Path("repo"))  # type: ignore[arg-type]

    repository.add_remote("origin", "https://example.com/r.git")
    repository.rename_remote("origin", "upstream")
    repository.set_remote_url("upstream", "git@example.com:me/r.git")
    repository.remove_remote("upstream")

    assert [call[0] for call in runner.calls] == [
        ["remote", "add", "--", "origin", "https://example.com/r.git"],
        ["remote", "rename", "--", "origin", "upstream"],
        ["remote", "set-url", "--", "upstream", "git@example.com:me/r.git"],
        ["remote", "remove", "--", "upstream"],
    ]
    assert all(call[1] == {"cwd": Path("repo"), "check": True} for call in runner.calls)
