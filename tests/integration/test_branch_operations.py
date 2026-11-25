"""Integration-тесты создания и переключения локальных веток на реальном Git."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog

from git_visualizer.application.services.branch_service import BranchService
from git_visualizer.application.services.changes_service import ChangesService
from git_visualizer.application.services.commit_service import CommitService
from git_visualizer.application.services.diff_service import DiffService
from git_visualizer.application.services.git_environment_service import (
    GitEnvironmentService,
)
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.application.services.history_service import HistoryService
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


def branch_service() -> BranchService:
    return BranchService(make_factory())


def configure_author(git: Git, path: Path) -> None:
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "commit.gpgsign", "false")


def rev(git: Git, repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref).strip()


def snapshot(git: Git, repo: Path) -> tuple[str, str, str]:
    """HEAD, текущая ветка и все refs — для проверки «состояние не изменилось»."""
    return (
        rev(git, repo, "HEAD"),
        git(repo, "branch", "--show-current").strip(),
        git(repo, "for-each-ref", "--format=%(refname) %(objectname)").strip(),
    )


# --- создание ветки ---


def test_create_branch_from_current_head(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("create-head", commits=2)
    head_before = rev(git, path, "HEAD")
    repository = open_repository(path)

    branch_service().create_branch(repository, "feature/from-head")

    assert git(path, "branch", "--show-current").strip() == "feature/from-head"
    assert rev(git, path, "HEAD") == head_before
    assert git(path, "status", "--porcelain") == ""

    reopened = open_repository(path)
    branches = {branch.name: branch for branch in branch_service().get_branches(reopened)}
    assert branches["feature/from-head"].is_current
    assert branches["feature/from-head"].commit_hash == head_before
    assert not branches["main"].is_current


def test_create_branch_from_selected_commit(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("create-from-commit", commits=3)
    selected = rev(git, path, "HEAD~1")
    main_head = rev(git, path, "main")
    repository = open_repository(path)

    branch_service().create_branch(repository, "from-old", start_point=selected)

    assert git(path, "branch", "--show-current").strip() == "from-old"
    assert rev(git, path, "HEAD") == selected
    assert rev(git, path, "main") == main_head  # старая ветка не сдвинулась
    assert git(path, "status", "--porcelain") == ""


def test_create_branch_with_existing_name_keeps_state(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("create-existing", commits=1)
    git(path, "branch", "dev")
    before = snapshot(git, path)
    repository = open_repository(path)

    with pytest.raises(GitCommandError) as info:
        branch_service().create_branch(repository, "dev")

    assert info.value.kind is GitErrorKind.BRANCH_ALREADY_EXISTS
    assert snapshot(git, path) == before


# --- переключение ветки ---


def test_switch_to_existing_local_branch(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("switch-local", commits=3)
    git(path, "branch", "dev", "HEAD~1")
    target = rev(git, path, "dev")
    repository = open_repository(path)

    branch_service().switch_branch(repository, "dev")

    assert git(path, "branch", "--show-current").strip() == "dev"
    assert rev(git, path, "HEAD") == target
    assert git(path, "status", "--porcelain") == ""
    assert open_repository(path).current_branch == "dev"


def test_switch_to_missing_branch_reports_ref_not_found(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("switch-missing", commits=1)
    before = snapshot(git, path)

    with pytest.raises(GitCommandError) as info:
        branch_service().switch_branch(open_repository(path), "missing")

    assert info.value.kind is GitErrorKind.REF_NOT_FOUND
    assert snapshot(git, path) == before


# --- некорректное имя ---


@pytest.mark.parametrize(
    ("name", "expected_error"),
    [
        ("", ValueError),
        ("   ", ValueError),
        ("-bad", ValueError),
        ("feature@{1}", ValueError),
        ("bad name", GitCommandError),
        ("bad..name", GitCommandError),
        ("HEAD", GitCommandError),
        ("trailing.", GitCommandError),
        ("feature/", GitCommandError),
        ("feature.lock", GitCommandError),
    ],
)
def test_invalid_branch_name_is_rejected_and_nothing_changes(
    make_repo: MakeRepo,
    git: Git,
    name: str,
    expected_error: type[BaseException],
) -> None:
    path = make_repo("invalid-name", commits=1)
    before = snapshot(git, path)

    with pytest.raises(expected_error) as info:
        branch_service().create_branch(open_repository(path), name)

    if isinstance(info.value, GitCommandError):
        assert info.value.kind is GitErrorKind.INVALID_BRANCH_NAME
    assert snapshot(git, path) == before
    assert git(path, "status", "--porcelain") == ""


# --- безопасность рабочей директории ---


def make_conflicting_branches(git: Git, path: Path) -> str:
    """Ветки main и other по-разному меняют один файл; возвращает hash other."""
    configure_author(git, path)
    git(path, "switch", "-q", "-c", "other")
    (path / "file0.txt").write_text("other version\n", encoding="utf-8")
    git(path, "commit", "-q", "-am", "other version")
    other_head = rev(git, path, "HEAD")
    git(path, "switch", "-q", "main")
    (path / "file0.txt").write_text("main version\n", encoding="utf-8")
    git(path, "commit", "-q", "-am", "main version")
    (path / "file0.txt").write_text("dirty worktree\n", encoding="utf-8")
    return other_head


def test_switch_refuses_when_local_changes_would_be_overwritten(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("switch-dirty", commits=1)
    make_conflicting_branches(git, path)
    before = snapshot(git, path)
    status_before = git(path, "status", "--porcelain")

    with pytest.raises(GitCommandError) as info:
        branch_service().switch_branch(open_repository(path), "other")

    assert info.value.kind is GitErrorKind.DIRTY_WORKTREE
    assert snapshot(git, path) == before
    status_after = git(path, "status", "--porcelain")
    assert status_after == status_before
    assert status_after.splitlines() == [" M file0.txt"]
    assert (path / "file0.txt").read_text(encoding="utf-8") == "dirty worktree\n"


def test_create_from_selected_commit_refuses_without_creating_branch(
    make_repo: MakeRepo, git: Git
) -> None:
    path = make_repo("create-dirty", commits=1)
    other_head = make_conflicting_branches(git, path)
    before = snapshot(git, path)
    status_before = git(path, "status", "--porcelain")

    with pytest.raises(GitCommandError) as info:
        branch_service().create_branch(
            open_repository(path), "from-other", start_point=other_head
        )

    assert info.value.kind is GitErrorKind.DIRTY_WORKTREE
    # git switch -c создаёт ref только после успешной проверки рабочей директории:
    # ветки нет, HEAD и изменения на месте.
    assert "refs/heads/from-other" not in git(path, "for-each-ref")
    assert snapshot(git, path) == before
    status_after = git(path, "status", "--porcelain")
    assert status_after == status_before
    assert status_after.splitlines() == [" M file0.txt"]
    assert (path / "file0.txt").read_text(encoding="utf-8") == "dirty worktree\n"


# --- GUI-поток: панель веток, обновление HEAD/графа/refs/Changes ---


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
        git_environment_service=GitEnvironmentService(runner),
    )


def wait_until(
    qt_app,
    predicate: Callable[[], bool],
    *,
    timeout: float = 15.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("условие не выполнилось за отведённое время")
        qt_app.processEvents()
        time.sleep(0.01)
    qt_app.processEvents()


def open_window(qt_app, path: Path, monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )
    window = MainWindow(build_controller())
    window.start_screen.open_button.click()
    wait_until(qt_app, lambda: window.workspace_page.workspace_data is not None)
    return window


def test_gui_creates_and_switches_branch_and_refreshes_graph_refs_changes(
    qt_app, make_repo: MakeRepo, git: Git, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_repo("gui-branches", commits=2)
    window = open_window(qt_app, path, monkeypatch)
    panel = window.workspace_page.branches_panel
    try:
        panel.branch_name_edit.setText("feature/gui")
        panel.create_button.click()

        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.workspace_page.workspace_data.repository.current_branch
            == "feature/gui",
        )
        wait_until(qt_app, lambda: panel.branch_name == "" and not panel.create_button.isEnabled())

        data = window.workspace_page.workspace_data
        assert data is not None
        assert data.repository.current_branch == "feature/gui"
        head = rev(git, path, "HEAD")
        assert data.repository.head_commit == head
        assert any(
            branch.name == "feature/gui" and branch.is_current
            for branch in data.branches
        )
        assert window.workspace_page.graph_view.commit_count == 2
        assert any(
            label.name == "feature/gui"
            for label in data.ref_labels[data.repository.head_commit]
        )
        wait_until(qt_app, lambda: not window.workspace_page.changes_panel.changes)

        assert panel.select_branch("main")
        panel.switch_button.click()

        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.workspace_page.workspace_data.repository.current_branch
            == "main",
        )
        data = window.workspace_page.workspace_data
        assert data is not None
        assert git(path, "branch", "--show-current").strip() == "main"
        assert any(
            label.name == "main"
            for label in data.ref_labels[data.repository.head_commit]
        )
        assert window.workspace_page.graph_view.commit_count == 2
        wait_until(qt_app, lambda: not window.workspace_page.changes_panel.changes)
    finally:
        window.close()


def test_gui_creates_branch_from_selected_commit(
    qt_app, make_repo: MakeRepo, git: Git, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_repo("gui-branch-from-commit", commits=3)
    selected = rev(git, path, "HEAD~1")
    window = open_window(qt_app, path, monkeypatch)
    panel = window.workspace_page.branches_panel
    try:
        assert window.workspace_page.graph_view.select_commit(
            selected, emit_signal=True
        )
        wait_until(
            qt_app,
            lambda: panel.from_commit_checkbox.isEnabled()
            and selected[:7] in panel.from_commit_checkbox.text(),
        )
        panel.from_commit_checkbox.setChecked(True)
        panel.branch_name_edit.setText("from-selected")
        panel.create_button.click()

        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.workspace_page.workspace_data.repository.current_branch
            == "from-selected",
        )
        data = window.workspace_page.workspace_data
        assert data is not None
        assert data.repository.head_commit == selected
        assert rev(git, path, "HEAD") == selected
        assert rev(git, path, "main") != selected
        assert any(
            branch.name == "from-selected" and branch.is_current
            for branch in data.branches
        )
        assert window.workspace_page.graph_view.commit_count == 3
        wait_until(qt_app, lambda: not window.workspace_page.changes_panel.changes)
    finally:
        window.close()
