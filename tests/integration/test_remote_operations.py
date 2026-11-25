"""Integration-тесты Fetch/Pull/Push на локальном bare-remote (без сети)."""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog

from git_visualizer.application.errors import UnknownRemoteError
from git_visualizer.application.services.branch_service import BranchService
from git_visualizer.application.services.changes_service import ChangesService
from git_visualizer.application.services.commit_service import CommitService
from git_visualizer.application.services.diff_service import DiffService
from git_visualizer.application.services.git_environment_service import (
    GitEnvironmentService,
)
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.application.services.history_service import HistoryService
from git_visualizer.application.services.remote_service import RemoteService
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.infrastructure.git.git_errors import GitCommandError, GitErrorKind
from git_visualizer.infrastructure.git.git_repository import GitRepository
from git_visualizer.infrastructure.git.git_runner import GitRunner
from git_visualizer.presentation.controllers.repository_controller import (
    RepositoryController,
)
from git_visualizer.presentation.windows.main_window import MainWindow

Git = Callable[..., str]
MakeRepo = Callable[..., Path]


def make_factory():
    runner = GitRunner(default_timeout=60)

    def factory(path: Path) -> GitRepository:
        return GitRepository(runner, path)

    return factory


def open_repository(path: Path):
    return RepositoryService(make_factory()).open_repository(path)


def remote_service() -> RemoteService:
    return RemoteService(make_factory())


def configure_author(git: Git, path: Path) -> None:
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "commit.gpgsign", "false")


def rev(git: Git, repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref).strip()


def commit_file(
    git: Git, repo: Path, content: str, message: str, *, name: str = "file0.txt"
) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-q", "-m", message)
    return rev(git, repo, "HEAD")


def make_bare_remote(tmp_path: Path, git: Git, name: str = "remote.git") -> Path:
    path = tmp_path / name
    git(tmp_path, "init", "--bare", "-q", str(path))
    return path


def make_seed_with_bare_remote(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> tuple[Path, Path]:
    """Seed-репозиторий с origin на локальный bare-remote и опубликованным main."""
    seed = make_repo("seed", commits=1)
    configure_author(git, seed)
    remote = make_bare_remote(tmp_path, git)
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "-u", "origin", "main")
    # HEAD bare-репозитория по умолчанию указывает на master: выравниваем с main.
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    return seed, remote


def clone_local(tmp_path: Path, git: Git, remote: Path) -> Path:
    local = tmp_path / "local"
    # У bare-remote HEAD по умолчанию смотрит на master, а публикуется main:
    # без -b main клон остался бы на пустой (unborn) ветке master.
    git(tmp_path, "clone", "-q", "-b", "main", str(remote), str(local))
    configure_author(git, local)
    return local


def snapshot(git: Git, repo: Path) -> tuple[str, str, str]:
    """HEAD, рабочая директория и статус — для проверки «ничего не изменилось»."""
    return (
        rev(git, repo, "HEAD"),
        git(repo, "branch", "--show-current").strip(),
        git(repo, "status", "--porcelain"),
    )


# --- успешные операции ---


def test_fetch_updates_remote_tracking_refs_without_moving_head(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    seed, remote = make_seed_with_bare_remote(tmp_path, git, make_repo)
    local = clone_local(tmp_path, git, remote)
    old_head = rev(git, local, "HEAD")
    new_head = commit_file(git, seed, "remote change\n", "remote commit")
    git(seed, "push", "-q", "origin", "main")
    assert rev(git, local, "refs/remotes/origin/main") == old_head

    result = remote_service().fetch(open_repository(local), "origin")

    assert result.operation == "fetch"
    assert result.remote == "origin"
    assert rev(git, local, "refs/remotes/origin/main") == new_head
    assert rev(git, local, "HEAD") == old_head
    assert git(local, "status", "--porcelain") == ""
    # История после fetch видит коммит из remote-tracking ветки.
    history = HistoryService(make_factory()).get_history(open_repository(local))
    assert new_head in {commit.hash for commit in history}


def test_pull_fast_forwards_current_branch(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    seed, remote = make_seed_with_bare_remote(tmp_path, git, make_repo)
    local = clone_local(tmp_path, git, remote)
    new_head = commit_file(git, seed, "remote change\n", "remote commit")
    git(seed, "push", "-q", "origin", "main")

    result = remote_service().pull(open_repository(local))

    assert result.operation == "pull"
    assert result.branch == "main"
    assert result.upstream == "origin/main"
    assert "Fast-forward" in result.output
    assert rev(git, local, "HEAD") == new_head
    assert (local / "file0.txt").read_text(encoding="utf-8") == "remote change\n"
    assert git(local, "status", "--porcelain") == ""
    assert open_repository(local).head_commit == new_head
    assert ChangesService(make_factory()).get_status(open_repository(local)) == ()


def test_pull_reports_already_up_to_date(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    _seed, remote = make_seed_with_bare_remote(tmp_path, git, make_repo)
    local = clone_local(tmp_path, git, remote)
    before = snapshot(git, local)

    result = remote_service().pull(open_repository(local))

    assert "Already up to date" in result.output
    assert snapshot(git, local) == before


def test_push_updates_remote_upstream(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    _seed, remote = make_seed_with_bare_remote(tmp_path, git, make_repo)
    local = clone_local(tmp_path, git, remote)
    local_head = commit_file(git, local, "local change\n", "local commit")
    assert rev(git, remote, "refs/heads/main") != local_head

    result = remote_service().push(open_repository(local))

    assert result.operation == "push"
    assert result.branch == "main"
    assert result.upstream == "origin/main"
    assert rev(git, remote, "refs/heads/main") == local_head
    assert rev(git, local, "refs/remotes/origin/main") == local_head


def test_initial_push_sets_upstream(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    remote = make_bare_remote(tmp_path, git)
    local = make_repo("local-unlinked", commits=1)
    configure_author(git, local)
    git(local, "remote", "add", "origin", str(remote))
    local_head = rev(git, local, "HEAD")
    repository = open_repository(local)
    assert make_factory()(local).get_branch_upstream("main") is None

    result = remote_service().push_initial(repository, "origin")

    assert result.operation == "push"
    assert result.remote == "origin"
    assert result.upstream == "origin/main"
    assert rev(git, remote, "refs/heads/main") == local_head
    assert git(local, "config", "branch.main.remote").strip() == "origin"
    assert git(local, "config", "branch.main.merge").strip() == "refs/heads/main"
    assert make_factory()(local).get_branch_upstream("main") == "origin/main"


# --- ошибки, воспроизводимые без внешней сети ---


def test_pull_non_fast_forward_changes_nothing_but_refs(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    seed, remote = make_seed_with_bare_remote(tmp_path, git, make_repo)
    local = clone_local(tmp_path, git, remote)
    local_head = commit_file(git, local, "local change\n", "local commit")
    remote_head = commit_file(git, seed, "remote change\n", "remote commit")
    git(seed, "push", "-q", "origin", "main")
    before = snapshot(git, local)

    with pytest.raises(GitCommandError) as info:
        remote_service().pull(open_repository(local))

    assert info.value.kind is GitErrorKind.NON_FAST_FORWARD
    # HEAD и рабочая директория не изменены; обновились только remote-tracking refs.
    assert snapshot(git, local) == before
    assert rev(git, local, "HEAD") == local_head
    assert rev(git, local, "refs/remotes/origin/main") == remote_head
    assert (local / "file0.txt").read_text(encoding="utf-8") == "local change\n"


def test_push_non_fast_forward_is_rejected_without_force(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    seed, remote = make_seed_with_bare_remote(tmp_path, git, make_repo)
    local = clone_local(tmp_path, git, remote)
    local_head = commit_file(git, local, "local change\n", "local commit")
    remote_head = commit_file(git, seed, "remote change\n", "remote commit")
    git(seed, "push", "-q", "origin", "main")
    local_before = snapshot(git, local)

    with pytest.raises(GitCommandError) as info:
        remote_service().push(open_repository(local))

    assert info.value.kind is GitErrorKind.NON_FAST_FORWARD
    assert rev(git, remote, "refs/heads/main") == remote_head
    assert rev(git, local, "HEAD") == local_head
    assert snapshot(git, local) == local_before


def test_fetch_from_missing_remote_reports_remote_not_found(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    local = make_repo("missing-remote", commits=1)
    configure_author(git, local)
    git(local, "remote", "add", "origin", str(tmp_path / "nowhere.git"))
    before = snapshot(git, local)

    with pytest.raises(GitCommandError) as info:
        remote_service().fetch(open_repository(local), "origin")

    assert info.value.kind is GitErrorKind.REMOTE_NOT_FOUND
    assert snapshot(git, local) == before


def test_fetch_from_closed_local_port_reports_network_error(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    """Сетевой сбой воспроизводится локально: порт занят, но никто не слушает."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    local = make_repo("network-error", commits=1)
    configure_author(git, local)
    git(local, "remote", "add", "origin", f"http://127.0.0.1:{port}/repo.git")
    before = snapshot(git, local)

    with pytest.raises(GitCommandError) as info:
        remote_service().fetch(open_repository(local), "origin")

    assert info.value.kind is GitErrorKind.NETWORK_ERROR
    assert snapshot(git, local) == before


def test_push_to_checked_out_remote_branch_reports_permission_denied(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    # Non-bare «remote» с checked-out main: Git отказывает в обновлении этой ветки.
    remote_repo = make_repo("nonbare-remote", commits=1)
    configure_author(git, remote_repo)
    remote_before = rev(git, remote_repo, "HEAD")
    local = clone_local(tmp_path, git, remote_repo)
    local_head = commit_file(git, local, "local change\n", "local commit")

    with pytest.raises(GitCommandError) as info:
        remote_service().push(open_repository(local))

    assert info.value.kind is GitErrorKind.PERMISSION_DENIED
    assert rev(git, remote_repo, "HEAD") == remote_before
    assert rev(git, local, "HEAD") == local_head


def test_pull_refuses_to_overwrite_dirty_worktree(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    seed, remote = make_seed_with_bare_remote(tmp_path, git, make_repo)
    local = clone_local(tmp_path, git, remote)
    commit_file(git, seed, "remote version\n", "remote commit")
    git(seed, "push", "-q", "origin", "main")
    (local / "file0.txt").write_text("local dirty version\n", encoding="utf-8")
    before_head = rev(git, local, "HEAD")

    with pytest.raises(GitCommandError) as info:
        remote_service().pull(open_repository(local))

    assert info.value.kind is GitErrorKind.DIRTY_WORKTREE
    assert rev(git, local, "HEAD") == before_head
    assert (local / "file0.txt").read_text(encoding="utf-8") == "local dirty version\n"
    assert git(local, "status", "--porcelain").splitlines() == [" M file0.txt"]


def test_initial_push_without_remote_fails_with_unknown_remote(
    tmp_path: Path, make_repo: MakeRepo
) -> None:
    local = make_repo("no-remote", commits=1)

    with pytest.raises(UnknownRemoteError):
        remote_service().push_initial(open_repository(local), "origin")


# --- GUI-поток: fetch → pull → push с обновлением refs/графа/ветки/Changes ---


def build_controller() -> RepositoryController:
    runner = GitRunner(default_timeout=60)

    def factory(path: Path) -> GitRepository:
        return GitRepository(runner, path)

    return RepositoryController(
        repository_service=RepositoryService(factory),
        history_service=HistoryService(factory),
        branch_service=BranchService(factory),
        graph_service=GraphService(),
        diff_service=DiffService(factory),
        changes_service=ChangesService(factory),
        commit_service=CommitService(factory),
        remote_service=RemoteService(factory),
        git_environment_service=GitEnvironmentService(runner),
    )


def wait_until(
    qt_app,
    predicate: Callable[[], bool],
    *,
    timeout: float = 20.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("условие не выполнилось за отведённое время")
        qt_app.processEvents()
        time.sleep(0.01)
    qt_app.processEvents()


def test_gui_fetch_pull_push_flow_updates_graph_branch_and_changes(
    qt_app,
    tmp_path: Path,
    git: Git,
    make_repo: MakeRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed, remote = make_seed_with_bare_remote(tmp_path, git, make_repo)
    local = clone_local(tmp_path, git, remote)
    old_head = rev(git, local, "HEAD")
    remote_head = commit_file(git, seed, "remote change\n", "remote commit")
    git(seed, "push", "-q", "origin", "main")

    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(local)
    )
    window = MainWindow(build_controller())
    shown: list[tuple[str, BaseException]] = []
    monkeypatch.setattr(
        window, "_show_error", lambda title, exc, **kwargs: shown.append((title, exc))
    )
    try:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.workspace_page.workspace_data.repository.head_commit == old_head,
        )

        # 1. Fetch обновляет remote-tracking refs, историю и граф; HEAD не двигается.
        window.workspace_page.top_bar.fetch_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.workspace_page.workspace_data.repository.head_commit == old_head
            and any(
                branch.name == "origin/main"
                and branch.commit_hash == remote_head
                for branch in window.workspace_page.workspace_data.branches
            ),
        )
        data = window.workspace_page.workspace_data
        assert data is not None
        assert data.repository.current_branch == "main"
        assert data.repository.head_commit == old_head
        assert window.workspace_page.graph_view.commit_count == 2
        assert any(
            label.name == "origin/main" for label in data.ref_labels[remote_head]
        )
        wait_until(
            qt_app,
            lambda: "Fetch «origin»: выполнено."
            in window.workspace_page.top_bar.remote_status_label.text(),
        )

        # 2. Pull fast-forward: текущая ветка, HEAD, граф и Changes обновляются.
        window.workspace_page.top_bar.pull_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.workspace_page.workspace_data.repository.head_commit
            == remote_head,
        )
        assert (local / "file0.txt").read_text(encoding="utf-8") == "remote change\n"
        wait_until(
            qt_app,
            lambda: "fast-forward выполнен"
            in window.workspace_page.top_bar.remote_status_label.text(),
        )
        wait_until(qt_app, lambda: not window.controller.is_loading)
        wait_until(
            qt_app,
            lambda: window.workspace_page.changes_panel.changes == ()
            and not window.workspace_page.changes_panel.is_busy,
        )
        assert window.workspace_page.changes_panel.changes == ()
        assert window.workspace_page.top_bar.branch_label.text() == "Ветка: main"
        data = window.workspace_page.workspace_data
        assert data is not None
        assert any(label.name == "main" for label in data.ref_labels[remote_head])

        # 3. Push отправляет новый локальный коммит в настроенный upstream.
        local_head = commit_file(git, local, "local change\n", "local commit")
        window.workspace_page.top_bar.push_button.click()
        wait_until(
            qt_app,
            lambda: rev(git, remote, "refs/heads/main") == local_head,
        )
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.workspace_page.workspace_data.repository.head_commit
            == local_head,
        )
        wait_until(
            qt_app,
            lambda: "Push «main»" in window.workspace_page.top_bar.remote_status_label.text(),
        )
        data = window.workspace_page.workspace_data
        assert data is not None
        assert data.repository.current_branch == "main"
        assert window.workspace_page.graph_view.commit_count == 3
        assert shown == []
    finally:
        window.close()


def test_push_works_when_remote_tracking_ref_is_gone(
    tmp_path: Path, git: Git, make_repo: MakeRepo
) -> None:
    """Настроенный upstream не зависит от существования refs/remotes/... (gone)."""
    _seed, remote = make_seed_with_bare_remote(tmp_path, git, make_repo)
    local = clone_local(tmp_path, git, remote)
    git(local, "update-ref", "-d", "refs/remotes/origin/main")
    # rev-parse @{upstream} здесь дал бы «unknown revision»; for-each-ref — имя.
    assert make_factory()(local).get_branch_upstream("main") == "origin/main"
    local_head = commit_file(git, local, "local change\n", "local commit")

    result = remote_service().push(open_repository(local))

    assert result.operation == "push"
    assert result.upstream == "origin/main"
    assert rev(git, remote, "refs/heads/main") == local_head
