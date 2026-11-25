"""Аргументы и вызовы сетевых Git-команд: без реального remote."""

from pathlib import Path

import pytest

from git_visualizer.infrastructure.git.git_repository import (
    GitRepository,
    build_fetch_arguments,
    build_pull_ff_only_arguments,
    build_push_arguments,
    build_push_set_upstream_arguments,
)
from git_visualizer.infrastructure.git.git_runner import GitResult


class FakeRunner:
    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def run(self, args: list[str], **kwargs: object) -> GitResult:
        self.calls.append((list(args), dict(kwargs)))
        return GitResult(
            command=("git", *args),
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def test_fetch_arguments_use_remote_name() -> None:
    assert build_fetch_arguments("origin") == ["fetch", "origin"]
    assert build_fetch_arguments("  fork  ") == ["fetch", "fork"]


def test_fetch_arguments_reject_empty_remote() -> None:
    with pytest.raises(ValueError):
        build_fetch_arguments("   ")


def test_pull_is_ff_only_and_never_rebases() -> None:
    arguments = build_pull_ff_only_arguments()

    assert arguments == ["pull", "--ff-only", "--no-rebase"]
    assert "--autostash" not in arguments
    assert "--rebase" not in arguments


def test_push_arguments_never_force() -> None:
    arguments = build_push_arguments()

    assert arguments == ["push"]
    assert "--force" not in arguments
    assert "-f" not in arguments
    assert "--force-with-lease" not in arguments


def test_initial_push_sets_upstream() -> None:
    arguments = build_push_set_upstream_arguments("fork", "main")

    assert arguments == ["push", "--set-upstream", "fork", "main"]
    assert "--force" not in arguments


@pytest.mark.parametrize(("remote", "branch"), [("", "main"), ("origin", "  ")])
def test_initial_push_rejects_empty_values(remote: str, branch: str) -> None:
    with pytest.raises(ValueError):
        build_push_set_upstream_arguments(remote, branch)


def test_fetch_passes_network_timeout_and_combines_output() -> None:
    runner = FakeRunner(stdout="out\n", stderr="progress\n")
    repository = GitRepository(runner, Path("repo"))  # type: ignore[arg-type]

    output = repository.fetch("origin", timeout=42.0)

    assert runner.calls == [
        (["fetch", "origin"], {"cwd": Path("repo"), "check": True, "timeout": 42.0})
    ]
    assert output == "out\nprogress"


def test_pull_and_push_pass_network_timeout() -> None:
    runner = FakeRunner()
    repository = GitRepository(runner, Path("repo"))  # type: ignore[arg-type]

    repository.pull_ff_only(timeout=11.0)
    repository.push(timeout=12.0)
    repository.push_set_upstream("fork", "main", timeout=13.0)

    assert [call[1].get("timeout") for call in runner.calls] == [11.0, 12.0, 13.0]
    assert runner.calls[2][0] == ["push", "--set-upstream", "fork", "main"]


def test_upstream_is_none_when_branch_has_no_upstream() -> None:
    runner = FakeRunner(stdout="\n")
    repository = GitRepository(runner, Path("repo"))  # type: ignore[arg-type]

    assert repository.get_branch_upstream("main") is None
    assert runner.calls[0][0] == [
        "for-each-ref",
        "--format=%(upstream)",
        "refs/heads/main",
    ]


def test_upstream_is_reported_even_when_remote_tracking_ref_is_gone() -> None:
    # for-each-ref отдаёт настроенный upstream, даже если refs/remotes/... удалён.
    runner = FakeRunner(stdout="refs/remotes/origin/main\n")
    repository = GitRepository(runner, Path("repo"))  # type: ignore[arg-type]

    assert repository.get_branch_upstream("main") == "origin/main"


def test_local_upstream_is_shortened() -> None:
    runner = FakeRunner(stdout="refs/heads/base\n")
    repository = GitRepository(runner, Path("repo"))  # type: ignore[arg-type]

    assert repository.get_branch_upstream("main") == "base"


def test_upstream_requires_branch_name() -> None:
    repository = GitRepository(FakeRunner(), Path("repo"))  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        repository.get_branch_upstream("  ")
