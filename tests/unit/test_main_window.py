"""GUI-тесты основного пользовательского потока: стартовый экран → репозиторий."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel

from git_visualizer.application.errors import NotAGitRepositoryError
from git_visualizer.application.services.git_environment_service import GitEnvironment
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import (
    Branch,
    ChangedFile,
    Commit,
    Diff,
    FileChange,
    Remote,
    Repository,
)
from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitNotFoundError,
)
from git_visualizer.main import main
from git_visualizer.presentation.controllers.repository_controller import (
    RepositoryController,
)
from git_visualizer.presentation.error_texts import user_message
from git_visualizer.presentation.widgets.diff_view import (
    ADDED_BACKGROUND,
    DELETED_BACKGROUND,
    DiffLineKind,
    DiffView,
    classify_diff_line,
)
from git_visualizer.presentation.windows.main_window import MainWindow

NOW = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_commit(
    label: str,
    *,
    subject: str = "Subject",
    body: str = "",
    parents: tuple[str, ...] = (),
    refs: tuple[str, ...] = (),
    author_name: str = "Test Author",
    author_email: str = "author@example.com",
    committer_name: str = "Test Committer",
    committer_email: str = "committer@example.com",
) -> Commit:
    commit_hash = label.ljust(40, "0")
    return Commit(
        hash=commit_hash,
        short_hash=commit_hash[:7],
        parent_hashes=parents,
        author_name=author_name,
        author_email=author_email,
        authored_at=NOW,
        committer_name=committer_name,
        committer_email=committer_email,
        committed_at=NOW,
        subject=subject,
        body=body,
        refs=refs,
    )


def make_repository(
    path: Path,
    *,
    head_commit: str | None,
    name: str = "repo",
    branch: str | None = "main",
) -> Repository:
    return Repository(
        path=path, name=name, current_branch=branch, head_commit=head_commit
    )


class FakeEnvironmentService:
    def __init__(
        self,
        *,
        environment: GitEnvironment | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._environment = environment or GitEnvironment(
            version=(2, 54, 0), raw_version="git version 2.54.0.windows.1"
        )
        self._error = error

    def check(self) -> GitEnvironment:
        if self._error is not None:
            raise self._error
        return self._environment


class FakeRepositoryService:
    def __init__(
        self,
        repository: Repository,
        *,
        remotes: tuple[Remote, ...] = (),
        error: BaseException | None = None,
    ) -> None:
        self.repository = repository
        self.remotes = remotes
        self.error = error

    def open_repository(self, path: str) -> Repository:
        if self.error is not None:
            raise self.error
        return self.repository

    def get_remotes(self, repository: Repository) -> tuple[Remote, ...]:
        return self.remotes


class FakeHistoryService:
    def __init__(
        self, commits: tuple[Commit, ...], *, error: BaseException | None = None
    ) -> None:
        self.commits = commits
        self.error = error
        self.requests = 0

    def get_history(self, repository: Repository) -> tuple[Commit, ...]:
        self.requests += 1
        if self.error is not None:
            raise self.error
        return self.commits


class FakeBranchService:
    def __init__(self, branches: tuple[Branch, ...]) -> None:
        self.branches = branches

    def get_branches(self, repository: Repository) -> tuple[Branch, ...]:
        return self.branches


class FakeDiffService:
    def __init__(
        self,
        files: tuple[FileChange, ...] = (),
        diff: Diff | None = None,
        *,
        files_error: BaseException | None = None,
        diff_error: BaseException | None = None,
    ) -> None:
        self.files = files
        self.diff = diff
        self.files_error = files_error
        self.diff_error = diff_error

    def get_commit_files(
        self, repository: Repository, commit: Commit
    ) -> tuple[FileChange, ...]:
        if self.files_error is not None:
            raise self.files_error
        return self.files

    def get_file_diff(
        self, repository: Repository, commit: Commit, file_change: FileChange
    ) -> Diff:
        if self.diff_error is not None:
            raise self.diff_error
        assert self.diff is not None
        return self.diff


class FakeChangesService:
    def __init__(
        self,
        changes: tuple[ChangedFile, ...] = (),
        diff: Diff | None = None,
        *,
        changes_error: BaseException | None = None,
        diff_error: BaseException | None = None,
        operation_error: BaseException | None = None,
    ) -> None:
        self.changes = changes
        self.diff = diff
        self.changes_error = changes_error
        self.diff_error = diff_error
        self.operation_error = operation_error
        self.after_stage: tuple[ChangedFile, ...] | None = None
        self.after_unstage: tuple[ChangedFile, ...] | None = None
        self.after_stage_all: tuple[ChangedFile, ...] | None = None
        self.after_unstage_all: tuple[ChangedFile, ...] | None = None
        self.status_requests = 0
        self.diff_requests: list[tuple[str, bool]] = []
        self.stage_requests: list[ChangedFile] = []
        self.unstage_requests: list[ChangedFile] = []
        self.stage_all_requests = 0
        self.unstage_all_requests = 0

    def get_status(self, repository: Repository) -> tuple[ChangedFile, ...]:
        self.status_requests += 1
        if self.changes_error is not None:
            raise self.changes_error
        return self.changes

    def get_file_diff(
        self, repository: Repository, changed_file: ChangedFile, *, staged: bool
    ) -> Diff:
        self.diff_requests.append((changed_file.path, staged))
        if self.diff_error is not None:
            raise self.diff_error
        assert self.diff is not None
        return self.diff

    def _raise_operation_error(self) -> None:
        if self.operation_error is not None:
            raise self.operation_error

    def stage_file(
        self, repository: Repository, changed_file: ChangedFile
    ) -> None:
        self.stage_requests.append(changed_file)
        self._raise_operation_error()
        if self.after_stage is not None:
            self.changes = self.after_stage

    def unstage_file(
        self, repository: Repository, changed_file: ChangedFile
    ) -> None:
        self.unstage_requests.append(changed_file)
        self._raise_operation_error()
        if self.after_unstage is not None:
            self.changes = self.after_unstage

    def stage_all(self, repository: Repository) -> None:
        self.stage_all_requests += 1
        self._raise_operation_error()
        if self.after_stage_all is not None:
            self.changes = self.after_stage_all

    def unstage_all(self, repository: Repository) -> None:
        self.unstage_all_requests += 1
        self._raise_operation_error()
        if self.after_unstage_all is not None:
            self.changes = self.after_unstage_all


class FakeCommitService:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.requests: list[str] = []

    def commit(self, repository: Repository, message: str) -> None:
        self.requests.append(message)
        if self.error is not None:
            raise self.error


def build_controller(
    *,
    repository: Repository,
    commits: tuple[Commit, ...] = (),
    branches: tuple[Branch, ...] = (),
    remotes: tuple[Remote, ...] = (),
    open_error: BaseException | None = None,
    history_error: BaseException | None = None,
    files: tuple[FileChange, ...] = (),
    diff: Diff | None = None,
    files_error: BaseException | None = None,
    diff_error: BaseException | None = None,
    environment: GitEnvironment | None = None,
    environment_error: BaseException | None = None,
    changes_service: FakeChangesService | None = None,
    commit_service: FakeCommitService | None = None,
) -> tuple[RepositoryController, FakeHistoryService]:
    history = FakeHistoryService(commits, error=history_error)
    controller = RepositoryController(
        repository_service=FakeRepositoryService(
            repository, remotes=remotes, error=open_error
        ),
        history_service=history,
        branch_service=FakeBranchService(branches),
        graph_service=GraphService(),
        diff_service=FakeDiffService(
            files, diff, files_error=files_error, diff_error=diff_error
        ),
        changes_service=changes_service or FakeChangesService(),
        commit_service=commit_service or FakeCommitService(),
        git_environment_service=FakeEnvironmentService(
            environment=environment, error=environment_error
        ),
    )
    return controller, history


def wait_until(
    qt_app,
    predicate: Callable[[], bool],
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("условие не выполнилось за отведённое время")
        qt_app.processEvents()
        time.sleep(0.005)


@contextmanager
def opened_window(controller: RepositoryController) -> Iterator[MainWindow]:
    window = MainWindow(controller)
    try:
        yield window
    finally:
        window.close()


def patch_folder_dialog(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(path)
    )


# --- стартовый экран ---


def test_main_window_starts_on_start_screen(qt_app) -> None:
    commit = make_commit("head")
    controller, _ = build_controller(
        repository=make_repository(Path("repo"), head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(
                name="main",
                is_remote=False,
                is_current=True,
                commit_hash=commit.hash,
            ),
        ),
    )

    with opened_window(controller) as window:
        assert window.windowTitle() == "Git Visualizer"
        assert window.stack.currentWidget() is window.start_screen
        assert window.start_screen.open_button.text() == "Открыть репозиторий"
        assert window.start_screen.open_button.isEnabled()


def test_start_screen_texts_fit_and_do_not_overlap(qt_app) -> None:
    controller, _ = build_controller(
        repository=make_repository(Path("repo"), head_commit=None)
    )

    with opened_window(controller) as window:
        window.resize(1280, 800)
        window.show()
        wait_until(
            qt_app,
            lambda: "поддерживается" in window.start_screen.git_status_label.text(),
        )
        qt_app.processEvents()
        screen = window.start_screen
        card = screen.findChild(QFrame, "start_screen_card")
        subtitle = screen.findChild(QLabel, "start_screen_subtitle")
        hint = screen.findChild(QLabel, "start_screen_hint")
        assert card is not None
        assert subtitle is not None
        assert hint is not None

        for label in (subtitle, hint, screen.git_status_label):
            metrics = QFontMetrics(label.font())
            for line in label.text().splitlines():
                assert metrics.horizontalAdvance(line) <= label.width(), label.text()
            if label.hasHeightForWidth():
                assert label.height() >= label.heightForWidth(label.width())

        assert subtitle.geometry().bottom() < screen.open_button.geometry().top()
        assert hint.geometry().top() > screen.open_button.geometry().bottom()
        assert screen.git_status_label.geometry().top() > hint.geometry().bottom()
        assert card.height() >= card.layout().sizeHint().height()


def test_start_screen_shows_supported_git_version(qt_app) -> None:
    controller, _ = build_controller(repository=make_repository(Path("repo"), head_commit=None))

    with opened_window(controller) as window:
        wait_until(
            qt_app,
            lambda: "2.54.0" in window.start_screen.git_status_label.text(),
        )
        assert "поддерживается" in window.start_screen.git_status_label.text()


def test_start_screen_warns_about_old_git_version(qt_app) -> None:
    controller, _ = build_controller(
        repository=make_repository(Path("repo"), head_commit=None),
        environment=GitEnvironment(
            version=(2, 22, 1), raw_version="git version 2.22.1.windows.1"
        ),
    )

    with opened_window(controller) as window:
        wait_until(
            qt_app,
            lambda: "2.22.1" in window.start_screen.git_status_label.text(),
        )
        text = window.start_screen.git_status_label.text()
        assert "2.23" in text
        assert "Обновите Git" in text


def test_start_screen_shows_git_not_found_instruction(qt_app) -> None:
    controller, _ = build_controller(
        repository=make_repository(Path("repo"), head_commit=None),
        environment_error=GitNotFoundError("git"),
    )

    with opened_window(controller) as window:
        window.resize(1280, 800)
        window.show()
        wait_until(
            qt_app,
            lambda: "Git не найден" in window.start_screen.git_status_label.text(),
        )
        qt_app.processEvents()
        label = window.start_screen.git_status_label
        hint = window.start_screen.findChild(QLabel, "start_screen_hint")
        card = window.start_screen.findChild(QFrame, "start_screen_card")
        assert "git-scm.com" in label.text()
        assert label.height() >= label.heightForWidth(label.width())
        assert hint is not None
        assert card is not None
        assert label.geometry().top() > hint.geometry().bottom()
        assert card.height() >= card.layout().sizeHint().height()


# --- открытие репозитория и загрузка истории ---


def test_open_repository_through_folder_dialog_shows_workspace(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = make_commit("old", subject="Первый коммит")
    new = make_commit("new", subject="Второй коммит", parents=(old.hash,))
    repo_path = tmp_path / "my-repo"
    repo_path.mkdir()
    branches = (
        Branch(name="main", is_remote=False, is_current=True, commit_hash=new.hash),
        Branch(name="origin/main", is_remote=True, is_current=False, commit_hash=new.hash),
    )
    remotes = (Remote(name="origin", fetch_url="https://example.com/repo.git"),)
    controller, _ = build_controller(
        repository=make_repository(repo_path, head_commit=new.hash, name="my-repo"),
        commits=(new, old),
        branches=branches,
        remotes=remotes,
    )
    patch_folder_dialog(monkeypatch, repo_path)

    with opened_window(controller) as window:
        wait_until(
            qt_app,
            lambda: "поддерживается" in window.start_screen.git_status_label.text(),
        )
        window.start_screen.open_button.click()

        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.stack.currentWidget() is window.workspace_page,
        )

        workspace = window.workspace_page
        assert window.windowTitle() == "Git Visualizer — my-repo"
        assert workspace.top_bar.repository_name_label.text() == "my-repo"
        assert workspace.top_bar.branch_label.text() == "Ветка: main"
        assert workspace.top_bar.refresh_button.isEnabled()
        assert workspace.top_bar.close_button.isEnabled()
        assert not workspace.top_bar.fetch_button.isEnabled()
        assert not workspace.top_bar.pull_button.isEnabled()
        assert not workspace.top_bar.push_button.isEnabled()
        assert workspace.graph_view.commit_count == 2
        assert workspace.main_splitter.count() == 2
        assert workspace.body_splitter.count() == 3
        assert workspace.branches_panel.tree.topLevelItemCount() == 3
        assert workspace.details_panel.current_commit is None
        assert workspace.details_panel.placeholder_text == "Коммит не выбран"
        assert workspace.changes_panel.objectName() == "changes_panel"

        data = workspace.workspace_data
        assert data is not None
        assert data.commits == (new, old)
        assert data.branches == branches
        assert data.remotes == remotes
        assert data.layout.nodes
        assert workspace.top_bar.repository_name_label.toolTip() == str(repo_path)


def test_selecting_commit_saves_hash_and_updates_details(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = make_commit("old", subject="Первый коммит")
    new = make_commit(
        "new",
        subject="Второй коммит",
        body="Подробное сообщение\nвторая строка",
        parents=(old.hash,),
        refs=("HEAD", "refs/heads/main", "refs/remotes/origin/main"),
        author_name="Анна Автор",
        author_email="anna@example.com",
        committer_name="Иван Коммиттер",
        committer_email="ivan@example.com",
    )
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=new.hash),
        commits=(new, old),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=new.hash),
        ),
    )
    patch_folder_dialog(monkeypatch, tmp_path)
    selected: list[str] = []

    with opened_window(controller) as window:
        window.commit_selected.connect(selected.append)
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None,
        )
        assert window.selected_commit_hash is None

        window.workspace_page.graph_view.commit_selected.emit(old.hash)

        details = window.workspace_page.details_panel
        assert window.selected_commit_hash == old.hash
        assert details.current_commit is old
        assert selected == [old.hash]

        window.workspace_page.graph_view.commit_selected.emit(new.hash)
        metadata = details.metadata_view.toPlainText()
        assert new.short_hash in metadata
        assert new.hash in metadata
        assert "Анна Автор" in metadata
        assert "anna@example.com" in metadata
        assert "Иван Коммиттер" in metadata
        assert "ivan@example.com" in metadata
        assert "main, origin/main" in metadata
        assert "Второй коммит" in metadata
        assert "Подробное сообщение" in metadata
        assert "Изменения относительно родителя" in metadata


def test_commit_files_and_diffs_are_shown_for_merge_commit(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_parent = make_commit("first-parent", subject="Первый родитель")
    second_parent = make_commit("second-parent", subject="Второй родитель")
    merge = make_commit(
        "merge",
        subject="Merge feature",
        parents=(first_parent.hash, second_parent.hash),
    )
    files = (
        FileChange(path="added.txt", change_type=FileChangeType.ADDED),
        FileChange(path="modified.txt", change_type=FileChangeType.MODIFIED),
        FileChange(
            path="renamed.txt",
            change_type=FileChangeType.RENAMED,
            old_path="old.txt",
        ),
        FileChange(path="deleted.txt", change_type=FileChangeType.DELETED),
        FileChange(
            path="copy.txt",
            change_type=FileChangeType.COPIED,
            old_path="source.txt",
        ),
    )
    diff = Diff(
        path="renamed.txt",
        old_path="old.txt",
        text=(
            "diff --git a/old.txt b/renamed.txt\n"
            "similarity index 100%\n"
            "rename from old.txt\n"
            "rename to renamed.txt\n"
            "@@ -1 +1 @@\n"
            "-old line\n"
            "+new line\n"
        ),
        base_commit_hash=first_parent.hash,
    )
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=merge.hash),
        commits=(merge, first_parent, second_parent),
        branches=(
            Branch(
                name="main",
                is_remote=False,
                is_current=True,
                commit_hash=merge.hash,
            ),
        ),
        files=files,
        diff=diff,
    )
    patch_folder_dialog(monkeypatch, tmp_path)

    with opened_window(controller) as window:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None,
        )
        window.workspace_page.graph_view.commit_selected.emit(merge.hash)
        details = window.workspace_page.details_panel
        wait_until(qt_app, lambda: len(details.files) == len(files))

        metadata = details.metadata_view.toPlainText()
        assert "Merge-коммит" in metadata
        assert first_parent.short_hash in metadata
        assert details.files_tree.topLevelItemCount() == len(files)
        assert details.files_tree.topLevelItem(0).text(1) == "добавлен"
        assert details.files_tree.topLevelItem(2).text(0) == "old.txt → renamed.txt"
        assert details.files_tree.topLevelItem(2).text(1) == "переименован"
        assert details.files_tree.topLevelItem(3).text(1) == "удалён"
        assert details.files_tree.topLevelItem(4).text(1) == "скопирован"

        details.files_tree.setCurrentItem(details.files_tree.topLevelItem(2))
        wait_until(qt_app, lambda: bool(window.diff_overlay.diff_view.text))

        assert details.selected_file == files[2]
        assert window.workspace_page.selected_file == files[2]
        assert not window.diff_overlay.isHidden()
        assert window.diff_overlay.diff_view.text == diff.text
        assert "первым родителем" in window.diff_overlay.diff_view.base_label.text()
        assert first_parent.short_hash in window.diff_overlay.diff_view.base_label.text()
        assert "renamed.txt" in window.diff_overlay.diff_view.header_label.text()


def test_file_diff_opens_in_window_overlay_and_closes_with_cross(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = make_commit("commit")
    file_change = FileChange(path="file.txt", change_type=FileChangeType.MODIFIED)
    diff = Diff(path="file.txt", text="@@ -1 +1 @@\n-old\n+new\n")
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        files=(file_change,),
        diff=diff,
    )
    patch_folder_dialog(monkeypatch, tmp_path)

    with opened_window(controller) as window:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None,
        )
        window.workspace_page.graph_view.commit_selected.emit(commit.hash)

        details = window.workspace_page.details_panel
        wait_until(qt_app, lambda: len(details.files) == 1)
        assert window.diff_overlay.isHidden()
        # Внутреннего diff и его splitter'а в деталях больше нет (исходная жалоба).
        assert not hasattr(details, "diff_view")
        assert not hasattr(details, "details_splitter")

        details.files_tree.setCurrentItem(details.files_tree.topLevelItem(0))
        wait_until(
            qt_app,
            lambda: not window.diff_overlay.isHidden()
            and bool(window.diff_overlay.diff_view.text),
        )
        assert "+new" in window.diff_overlay.diff_view.text
        assert "file.txt" in window.diff_overlay.diff_view.header_label.text()

        window.diff_overlay.close_button.click()

        assert window.diff_overlay.isHidden()
        assert details.files_tree.currentItem() is None
        assert details.selected_file is None
        assert window.workspace_page.selected_file is None

        # После закрытия тот же файл можно открыть повторно.
        details.files_tree.setCurrentItem(details.files_tree.topLevelItem(0))
        wait_until(qt_app, lambda: not window.diff_overlay.isHidden())


def test_root_commit_uses_empty_tree_comparison(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_commit("root", subject="Первый коммит")
    file_change = FileChange(path="file.txt", change_type=FileChangeType.ADDED)
    diff = Diff(
        path="file.txt",
        text=(
            "diff --git a/file.txt b/file.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/file.txt\n"
            "@@ -0,0 +1 @@\n"
            "+content\n"
        ),
        base_commit_hash=None,
    )
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=root.hash),
        commits=(root,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=root.hash),
        ),
        files=(file_change,),
        diff=diff,
    )
    patch_folder_dialog(monkeypatch, tmp_path)

    with opened_window(controller) as window:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None,
        )
        window.workspace_page.graph_view.commit_selected.emit(root.hash)
        details = window.workspace_page.details_panel
        wait_until(qt_app, lambda: len(details.files) == 1)

        metadata = details.metadata_view.toPlainText()
        assert "Корневой коммит" in metadata
        assert "пустого дерева" in metadata

        details.files_tree.setCurrentItem(details.files_tree.topLevelItem(0))
        wait_until(qt_app, lambda: bool(window.diff_overlay.diff_view.text))

        assert "пустым деревом" in window.diff_overlay.diff_view.base_label.text()
        assert "+content" in window.diff_overlay.diff_view.text


def test_diff_view_reports_binary_and_truncated_diff(qt_app) -> None:
    view = DiffView()
    view.show_diff(
        Diff(path="image.png", text="", is_binary=True, base_commit_hash="a" * 40),
        title="image.png — изменён",
        base_text="Сравнение с родителем aaaaaaa.",
    )
    assert "Бинарный файл" in view.message_text
    assert view.is_editor_visible is False

    view.show_diff(
        Diff(
            path="big.txt",
            text="+added\n-removed\n context\n",
            is_truncated=True,
            base_commit_hash="a" * 40,
        ),
        title="big.txt — изменён",
        base_text="Сравнение с родителем aaaaaaa.",
    )
    assert view.is_editor_visible is True
    assert "+added" in view.text
    assert "Diff обрезан" in view.notice_text


def test_diff_line_kinds_distinguish_added_deleted_and_headers() -> None:
    assert classify_diff_line("+added") is DiffLineKind.ADDED
    assert classify_diff_line("-removed") is DiffLineKind.DELETED
    assert classify_diff_line("@@ -1 +1 @@") is DiffLineKind.HUNK
    assert classify_diff_line("+++ b/file.txt") is DiffLineKind.HEADER
    assert classify_diff_line("--- a/file.txt") is DiffLineKind.HEADER
    assert classify_diff_line("diff --git a/x b/x") is DiffLineKind.HEADER
    assert classify_diff_line(" context") is DiffLineKind.CONTEXT


def test_diff_view_paints_added_and_deleted_lines_differently(qt_app) -> None:
    view = DiffView()
    view.show_diff(
        Diff(
            path="file.txt",
            text="@@ -1 +1 @@\n-old\n+new\n context\n",
            base_commit_hash="a" * 40,
        ),
        title="file.txt — изменён",
        base_text="Сравнение с родителем aaaaaaa.",
    )
    qt_app.processEvents()
    document = view.editor.document()

    def background(block_number: int) -> str:
        block = document.findBlockByNumber(block_number)
        formats = block.layout().formats()
        assert formats
        return formats[0].format.background().color().name()

    assert background(1) == DELETED_BACKGROUND.name()
    assert background(2) == ADDED_BACKGROUND.name()
    assert background(1) != background(2)


def test_commit_files_error_is_shown_and_keeps_commit_details(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = make_commit("commit")
    error = GitCommandError(128, command=("git", "diff-tree"), stderr="fatal")
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        files_error=error,
    )
    patch_folder_dialog(monkeypatch, tmp_path)
    shown: list[tuple[str, BaseException]] = []

    with opened_window(controller) as window:
        monkeypatch.setattr(
            window, "_show_error", lambda title, exc: shown.append((title, exc))
        )
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None,
        )
        window.workspace_page.graph_view.commit_selected.emit(commit.hash)
        wait_until(qt_app, lambda: bool(shown))

        details = window.workspace_page.details_panel
        assert shown[0][0] == "Не удалось загрузить файлы коммита"
        assert shown[0][1] is error
        assert details.current_commit is commit
        assert "Не удалось загрузить список файлов" in details.files_status_label.text()


def test_refresh_reloads_history_manually(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = make_commit("old", subject="Первый коммит")
    controller, history = build_controller(
        repository=make_repository(tmp_path, head_commit=old.hash),
        commits=(old,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=old.hash),
        ),
    )
    patch_folder_dialog(monkeypatch, tmp_path)

    with opened_window(controller) as window:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.graph_view.commit_count == 1,
        )

        new = make_commit("new", subject="Новый коммит")
        history.commits = (new, old)
        window.workspace_page.top_bar.refresh_button.click()

        wait_until(
            qt_app,
            lambda: window.workspace_page.graph_view.commit_count == 2,
        )
        assert window.workspace_page.top_bar.refresh_button.isEnabled()
        assert window.selected_commit_hash is None


# --- панель Changes ---

WORKTREE_CHANGES = (
    ChangedFile(path="staged.txt", staged_change=FileChangeType.MODIFIED),
    ChangedFile(
        path="both.txt",
        staged_change=FileChangeType.MODIFIED,
        unstaged_change=FileChangeType.MODIFIED,
    ),
    ChangedFile(path="untracked.txt", is_untracked=True),
    ChangedFile(path="conflict.txt", is_conflicted=True),
)


def open_workspace_with_changes(
    qt_app,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changes_service: FakeChangesService,
    *,
    commit_service: FakeCommitService | None = None,
) -> tuple[MainWindow, FakeChangesService]:
    commit = make_commit("only")
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        changes_service=changes_service,
        commit_service=commit_service,
    )
    patch_folder_dialog(monkeypatch, tmp_path)
    window = MainWindow(controller)
    window.start_screen.open_button.click()
    wait_until(
        qt_app,
        lambda: window.workspace_page.workspace_data is not None
        and window.workspace_page.changes_panel.status_label.text() != "Изменения не загружены",
    )
    return window, changes_service


def test_changes_panel_shows_staged_and_unstaged_and_routes_diff(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = Diff(path="both.txt", text="@@ -1 +1 @@\n-old\n+new\n")
    changes_service = FakeChangesService(WORKTREE_CHANGES, diff)

    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    try:
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == WORKTREE_CHANGES)

        assert [change.path for change in panel.unstaged_files] == [
            "both.txt",
            "untracked.txt",
            "conflict.txt",
        ]
        assert [change.path for change in panel.staged_files] == [
            "staged.txt",
            "both.txt",
        ]
        assert panel.unstaged_group.title() == "Unstaged (3)"
        assert panel.staged_group.title() == "Staged (2)"
        assert "Вне индекса: 3" in panel.status_label.text()
        assert "В индексе: 2" in panel.status_label.text()

        # Файл с обоими изменениями есть в обеих секциях.
        assert panel.unstaged_tree.topLevelItemCount() == 3
        assert panel.staged_tree.topLevelItemCount() == 2
        assert panel.staged_tree.topLevelItem(1).text(0) == "both.txt"

        # Клик по файлу только выбирает его и не открывает diff.
        panel.staged_tree.setCurrentItem(panel.staged_tree.topLevelItem(1))
        assert window.diff_overlay.isHidden()
        assert changes_service.diff_requests == []
        assert panel.selected_file == WORKTREE_CHANGES[1]
        assert panel.selected_is_staged is True
        assert panel.unstaged_tree.currentItem() is None

        # Diff открывается отдельной кнопкой.
        panel.diff_button.click()
        wait_until(
            qt_app,
            lambda: not window.diff_overlay.isHidden()
            and window.diff_overlay.diff_view.is_editor_visible,
        )
        assert changes_service.diff_requests == [("both.txt", True)]
        assert "в индексе" in window.diff_overlay.diff_view.header_label.text()
        assert "Staged diff" in window.diff_overlay.diff_view.base_label.text()
        assert "+new" in window.diff_overlay.diff_view.text
        assert window._diff_overlay_kind == "worktree"

        # Смена выбора закрывает прежний diff; новый открывается кнопкой.
        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))
        assert window.diff_overlay.isHidden()
        assert changes_service.diff_requests == [("both.txt", True)]
        assert panel.selected_is_staged is False
        assert panel.staged_tree.currentItem() is None

        panel.diff_button.click()
        wait_until(
            qt_app,
            lambda: len(changes_service.diff_requests) == 2
            and "Unstaged diff" in window.diff_overlay.diff_view.base_label.text()
            and window.diff_overlay.diff_view.is_editor_visible,
        )
        assert changes_service.diff_requests == [
            ("both.txt", True),
            ("both.txt", False),
        ]
        assert "вне индекса" in window.diff_overlay.diff_view.header_label.text()

        # Крестик закрывает панель, но сохраняет выбор Changes: Stage/Unstage
        # доступны сразу, а тот же diff можно открыть кнопкой повторно.
        window.diff_overlay.close_button.click()
        assert window.diff_overlay.isHidden()
        assert panel.selected_file == WORKTREE_CHANGES[1]
        assert panel.diff_button.isEnabled()
        assert window._diff_overlay_kind is None

        panel.diff_button.click()
        wait_until(
            qt_app,
            lambda: len(changes_service.diff_requests) == 3
            and window.diff_overlay.diff_view.is_editor_visible,
        )
    finally:
        window.close()


def test_untracked_file_shows_notice_without_git_diff(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changes_service = FakeChangesService(WORKTREE_CHANGES)
    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    try:
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == WORKTREE_CHANGES)

        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(1))

        assert window.diff_overlay.isHidden()
        assert changes_service.diff_requests == []
        assert panel.selected_file is not None
        assert panel.selected_file.path == "untracked.txt"

        panel.diff_button.click()

        assert not window.diff_overlay.isHidden()
        assert "не отслеживается" in window.diff_overlay.diff_view.message_text
        assert "untracked.txt" in window.diff_overlay.diff_view.header_label.text()
        assert window._diff_overlay_kind == "worktree"
    finally:
        window.close()


def test_changes_empty_state_is_explicit(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changes_service = FakeChangesService(())
    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    try:
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.is_clean)
        assert panel.status_label.text() == "Изменений нет — рабочая директория чиста."
        assert panel.unstaged_tree.topLevelItem(0).text(0) == "Нет незакоммиченных изменений"
        assert panel.staged_tree.topLevelItem(0).text(0) == "Нет файлов в индексе"
        assert panel.unstaged_group.title() == "Unstaged (0)"
        assert panel.staged_group.title() == "Staged (0)"
        assert "Изменений нет" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_changes_refresh_button_reloads_status(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changes_service = FakeChangesService(())
    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    try:
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.is_clean)
        requests_before = changes_service.status_requests

        changes_service.changes = (ChangedFile(path="new.txt", is_untracked=True),)
        panel.refresh_button.click()

        wait_until(qt_app, lambda: panel.changes == changes_service.changes)
        assert changes_service.status_requests == requests_before + 1
        assert panel.unstaged_tree.topLevelItem(0).text(0) == "new.txt"
        assert panel.unstaged_tree.topLevelItem(0).text(1) == "не отслеживается"
    finally:
        window.close()


def test_changes_failure_is_reported_and_panel_shows_error(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = GitCommandError(128, command=("git", "status"), stderr="fatal")
    changes_service = FakeChangesService(changes_error=error)
    commit = make_commit("only")
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        changes_service=changes_service,
    )
    patch_folder_dialog(monkeypatch, tmp_path)
    shown: list[tuple[str, BaseException]] = []

    with opened_window(controller) as window:
        monkeypatch.setattr(
            window, "_show_error", lambda title, exc: shown.append((title, exc))
        )
        window.start_screen.open_button.click()
        wait_until(qt_app, lambda: bool(shown))

        panel = window.workspace_page.changes_panel
        assert shown[0][0] == "Не удалось загрузить изменения"
        assert shown[0][1] is error
        assert panel.status_label.text() == "Не удалось загрузить изменения."
        assert panel.changes == ()
        assert panel.refresh_button.isEnabled()


# --- Stage/Unstage через главное окно ---


def test_stage_selected_file_works_without_opening_diff(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unstaged = (
        ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED),
    )
    staged = (
        ChangedFile(path="file.txt", staged_change=FileChangeType.MODIFIED),
    )
    changes_service = FakeChangesService(unstaged)
    changes_service.after_stage = staged

    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    try:
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == unstaged)
        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))

        # Клик по файлу только выбирает его: diff не запрашивается.
        assert window.diff_overlay.isHidden()
        assert changes_service.diff_requests == []

        panel.stage_button.click()

        wait_until(qt_app, lambda: panel.changes == staged)
        assert changes_service.stage_requests == [unstaged[0]]
        assert changes_service.diff_requests == []
        assert window.diff_overlay.isHidden()
    finally:
        window.close()


def test_stage_selected_file_refreshes_status_and_shows_staged_diff(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unstaged = (
        ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED),
    )
    staged = (
        ChangedFile(path="file.txt", staged_change=FileChangeType.MODIFIED),
    )
    diff = Diff(path="file.txt", text="@@ -1 +1 @@\n-old\n+new\n")
    changes_service = FakeChangesService(unstaged, diff)
    changes_service.after_stage = staged

    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    try:
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == unstaged)
        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))
        # Diff открываем только явно, чтобы проверить его переключение после stage.
        assert window.diff_overlay.isHidden()
        panel.diff_button.click()
        wait_until(
            qt_app,
            lambda: not window.diff_overlay.isHidden()
            and changes_service.diff_requests == [("file.txt", False)],
        )

        panel.stage_button.click()

        wait_until(qt_app, lambda: panel.changes == staged)
        wait_until(
            qt_app,
            lambda: changes_service.diff_requests[-1:] == [("file.txt", True)]
            and "Staged diff" in window.diff_overlay.diff_view.base_label.text(),
        )
        assert changes_service.stage_requests == [unstaged[0]]
        assert panel.staged_files == staged
        assert panel.unstaged_files == ()
        assert window._changes_operation is None
    finally:
        window.close()


def test_unstage_selected_file_keeps_worktree_changes_and_shows_unstaged_diff(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = (
        ChangedFile(path="file.txt", staged_change=FileChangeType.MODIFIED),
    )
    unstaged = (
        ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED),
    )
    diff = Diff(path="file.txt", text="@@ -1 +1 @@\n-old\n+new\n")
    changes_service = FakeChangesService(staged, diff)
    changes_service.after_unstage = unstaged

    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    try:
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == staged)
        panel.staged_tree.setCurrentItem(panel.staged_tree.topLevelItem(0))
        assert window.diff_overlay.isHidden()
        panel.diff_button.click()
        wait_until(
            qt_app,
            lambda: not window.diff_overlay.isHidden()
            and changes_service.diff_requests == [("file.txt", True)],
        )

        panel.unstage_button.click()

        wait_until(qt_app, lambda: panel.changes == unstaged)
        wait_until(
            qt_app,
            lambda: changes_service.diff_requests[-1:] == [("file.txt", False)]
            and "Unstaged diff" in window.diff_overlay.diff_view.base_label.text(),
        )
        assert changes_service.unstage_requests == [staged[0]]
        assert panel.unstaged_files == unstaged
        assert panel.staged_files == ()
    finally:
        window.close()


def test_stage_all_confirmation_shows_scope_and_skips_conflicts(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changes = (
        ChangedFile(path="modified.txt", unstaged_change=FileChangeType.MODIFIED),
        ChangedFile(path="новый файл.txt", is_untracked=True),
        ChangedFile(path="conflict.txt", is_conflicted=True),
    )
    staged = (
        ChangedFile(path="modified.txt", staged_change=FileChangeType.MODIFIED),
        ChangedFile(path="новый файл.txt", staged_change=FileChangeType.ADDED),
        ChangedFile(path="conflict.txt", is_conflicted=True),
    )
    changes_service = FakeChangesService(changes)
    changes_service.after_stage_all = staged

    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    try:
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == changes)
        confirmations: list[tuple[str, tuple[ChangedFile, ...], tuple[ChangedFile, ...]]] = []

        def confirm(operation, files, conflicts):
            confirmations.append((operation, files, conflicts))
            return True

        monkeypatch.setattr(window, "_confirm_bulk_operation", confirm)

        panel.stage_all_button.click()

        wait_until(qt_app, lambda: panel.changes == staged)
        assert len(confirmations) == 1
        operation, files, conflicts = confirmations[0]
        assert operation == "stage"
        assert {change.path for change in files} == {
            "modified.txt",
            "новый файл.txt",
        }
        assert [change.path for change in conflicts] == ["conflict.txt"]
        assert changes_service.stage_all_requests == 1
        # Добавлять больше нечего: остались только staged-файлы и конфликт.
        assert not panel.stage_all_button.isEnabled()
    finally:
        window.close()


def test_unstage_all_cancel_does_not_run_git(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changes = (
        ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),
    )
    changes_service = FakeChangesService(changes)

    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    try:
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == changes)
        monkeypatch.setattr(
            window, "_confirm_bulk_operation", lambda *args: False
        )

        panel.unstage_all_button.click()

        assert changes_service.unstage_all_requests == 0
        assert panel.changes == changes
    finally:
        window.close()


def test_stage_operation_failure_is_reported_and_status_is_refreshed(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED)
    error = GitCommandError(128, command=("git", "add"), stderr="fatal")
    changes_service = FakeChangesService((changed,), operation_error=error)

    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    shown: list[tuple[str, BaseException]] = []
    try:
        panel = window.workspace_page.changes_panel
        monkeypatch.setattr(
            window, "_show_error", lambda title, exc: shown.append((title, exc))
        )
        wait_until(qt_app, lambda: panel.changes == (changed,))
        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))
        assert window.diff_overlay.isHidden()

        panel.stage_button.click()

        wait_until(qt_app, lambda: bool(shown))
        assert shown[0][0] == "Не удалось добавить файл в индекс"
        assert shown[0][1] is error
        assert window.diff_overlay.isHidden()
        assert not panel.is_busy
    finally:
        window.close()


# --- создание коммита через главное окно ---


def test_commit_success_clears_message_and_reloads_history_and_changes(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = (
        ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),
    )
    changes_service = FakeChangesService(staged)
    commit_service = FakeCommitService()
    commit = make_commit("only")
    controller, history = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        changes_service=changes_service,
        commit_service=commit_service,
    )
    patch_folder_dialog(monkeypatch, tmp_path)
    window = MainWindow(controller)
    shown: list[tuple[str, BaseException]] = []
    try:
        monkeypatch.setattr(
            window, "_show_error", lambda title, exc, **kwargs: shown.append((title, exc))
        )
        window.start_screen.open_button.click()
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == staged)

        panel.commit_message_edit.setPlainText("Новый коммит")
        # После коммита индекс чист: новый статус придёт при повторной загрузке.
        changes_service.changes = ()
        requests_before = changes_service.status_requests
        history_before = history.requests
        panel.commit_button.click()

        wait_until(qt_app, lambda: commit_service.requests == ["Новый коммит"])
        wait_until(qt_app, lambda: panel.commit_message == "")
        wait_until(qt_app, lambda: panel.changes == ())
        wait_until(qt_app, lambda: history.requests == history_before + 1)
        wait_until(
            qt_app, lambda: changes_service.status_requests > requests_before
        )
        wait_until(qt_app, lambda: panel.is_clean)

        assert shown == []
        assert panel.commit_button.isEnabled() is False
        assert not panel.is_busy
        assert panel.is_clean
    finally:
        window.close()


def test_commit_failure_shows_error_details_and_keeps_message(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = (
        ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),
    )
    error = GitCommandError(
        128,
        command=("git", "commit", "-F", "-"),
        stderr="Author identity unknown\nfatal: unable to auto-detect email address\n",
    )
    changes_service = FakeChangesService(staged)
    commit_service = FakeCommitService(error=error)
    commit = make_commit("only")
    controller, history = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
        changes_service=changes_service,
        commit_service=commit_service,
    )
    patch_folder_dialog(monkeypatch, tmp_path)
    window = MainWindow(controller)
    shown: list[tuple[str, BaseException, dict[str, object]]] = []
    try:
        monkeypatch.setattr(
            window,
            "_show_error",
            lambda title, exc, **kwargs: shown.append((title, exc, kwargs)),
        )
        window.start_screen.open_button.click()
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == staged)
        panel.commit_message_edit.setPlainText("Останется в поле")
        history_before = history.requests

        panel.commit_button.click()

        wait_until(qt_app, lambda: bool(shown))
        wait_until(qt_app, lambda: panel.staged_files == staged)
        wait_until(qt_app, lambda: not panel.is_busy)
        title, shown_error, kwargs = shown[0]
        assert title == "Не удалось создать коммит"
        assert shown_error is error
        assert "user.name" in kwargs["message"]
        # Поле не очищается при ошибке: пользователь может исправить и повторить.
        assert panel.commit_message == "Останется в поле"
        assert panel.staged_files == staged
        assert history.requests == history_before
    finally:
        window.close()


# --- ошибки и пустой репозиторий ---


def test_open_failure_shows_russian_error_and_stays_on_start_screen(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = NotAGitRepositoryError(
        "Path is not inside a Git repository", path=tmp_path
    )
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=None),
        open_error=error,
    )
    patch_folder_dialog(monkeypatch, tmp_path)
    shown: list[tuple[str, BaseException]] = []

    with opened_window(controller) as window:
        monkeypatch.setattr(
            window,
            "_show_error",
            lambda title, exc: shown.append((title, exc)),
        )
        window.start_screen.open_button.click()
        wait_until(qt_app, lambda: bool(shown))

        title, shown_error = shown[0]
        assert title == "Не удалось открыть репозиторий"
        assert shown_error is error
        assert "нет Git-репозитория" in user_message(shown_error)
        assert window.stack.currentWidget() is window.start_screen
        assert window.start_screen.open_button.isEnabled()


def test_empty_repository_shows_empty_graph_and_explanation(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=None),
        commits=(),
        branches=(
            Branch(
                name="main",
                is_remote=False,
                is_current=True,
                commit_hash=None,
            ),
        ),
    )
    patch_folder_dialog(monkeypatch, tmp_path)

    with opened_window(controller) as window:
        wait_until(
            qt_app,
            lambda: "поддерживается" in window.start_screen.git_status_label.text(),
        )
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None,
        )

        assert window.workspace_page.graph_view.is_empty
        assert window.workspace_page.details_panel.current_commit is None
        assert "нет коммитов" in window.workspace_page.details_panel.placeholder_text
        assert "Ветка: main (коммитов нет)" == (
            window.workspace_page.top_bar.branch_label.text()
        )
        assert "нет коммитов" in window.statusBar().currentMessage()


def test_history_failure_is_reported_without_closing_workspace(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = GitCommandError(128, command=("git", "log"), stderr="fatal: bad object")
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit="a" * 40),
        history_error=error,
    )
    patch_folder_dialog(monkeypatch, tmp_path)
    shown: list[tuple[str, BaseException]] = []

    with opened_window(controller) as window:
        monkeypatch.setattr(
            window,
            "_show_error",
            lambda title, exc: shown.append((title, exc)),
        )
        window.start_screen.open_button.click()
        wait_until(qt_app, lambda: bool(shown))

        assert shown[0][0] == "Не удалось загрузить историю"
        assert shown[0][1] is error
        assert window.stack.currentWidget() is window.workspace_page
        assert window.workspace_page.graph_view.is_empty
        assert window.workspace_page.top_bar.refresh_button.isEnabled()


def test_close_repository_returns_to_start_screen(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = make_commit("only")
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(name="main", is_remote=False, is_current=True, commit_hash=commit.hash),
        ),
    )
    patch_folder_dialog(monkeypatch, tmp_path)

    with opened_window(controller) as window:
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None,
        )
        window.workspace_page.graph_view.commit_selected.emit(commit.hash)
        assert window.selected_commit_hash == commit.hash

        window.workspace_page.top_bar.close_button.click()

        assert window.stack.currentWidget() is window.start_screen
        assert window.selected_commit_hash is None
        assert window.workspace_page.graph_view.is_empty
        assert window.workspace_page.details_panel.current_commit is None
        assert window.workspace_page.branches_panel.tree.topLevelItemCount() == 0
        assert window.windowTitle() == "Git Visualizer"
        assert controller.repository is None
        assert "Репозиторий закрыт" in window.statusBar().currentMessage()


def test_entry_point_is_callable() -> None:
    assert callable(main)
