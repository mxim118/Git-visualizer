"""GUI-тесты основного пользовательского потока: стартовый экран → репозиторий."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QSize
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel

from git_visualizer.application.errors import NotAGitRepositoryError
from git_visualizer.application.services.git_environment_service import GitEnvironment
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.application.services.remote_service import RemoteOperationResult
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
from git_visualizer.presentation.dialogs.settings_dialog import SettingsDialog
from git_visualizer.presentation.error_texts import user_message
from git_visualizer.presentation.settings import AppSettings
from git_visualizer.presentation.widgets.diff_view import (
    ADDED_BACKGROUND,
    DELETED_BACKGROUND,
    DiffLineKind,
    DiffView,
    classify_diff_line,
)
from git_visualizer.presentation.widgets.graph_view import GraphViewState
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
        self.limits: list[int] = []

    def get_history(
        self, repository: Repository, *, limit: int
    ) -> tuple[Commit, ...]:
        self.requests += 1
        self.limits.append(limit)
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
        self.after_discard: tuple[ChangedFile, ...] | None = None
        self.status_requests = 0
        self.diff_requests: list[tuple[str, bool]] = []
        self.stage_requests: list[ChangedFile] = []
        self.unstage_requests: list[ChangedFile] = []
        self.stage_all_requests = 0
        self.unstage_all_requests = 0
        self.discard_requests: list[ChangedFile] = []

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

    def discard_file(
        self, repository: Repository, changed_file: ChangedFile
    ) -> None:
        self.discard_requests.append(changed_file)
        self._raise_operation_error()
        if self.after_discard is not None:
            self.changes = self.after_discard


class FakeCommitService:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.requests: list[str] = []

    def commit(self, repository: Repository, message: str) -> None:
        self.requests.append(message)
        if self.error is not None:
            raise self.error


class FakeRemoteService:
    def __init__(
        self,
        *,
        error: BaseException | None = None,
        gate: threading.Event | None = None,
        started: threading.Event | None = None,
    ) -> None:
        self.error = error
        self._gate = gate
        self._started = started
        self.fetch_requests: list[str] = []
        self.pull_requests = 0
        self.push_requests = 0
        self.initial_push_requests: list[str] = []

    def _wait(self) -> None:
        if self._started is not None:
            self._started.set()
        if self._gate is not None:
            assert self._gate.wait(5.0)

    def fetch(self, repository: Repository, remote_name: str):
        self.fetch_requests.append(remote_name)
        self._wait()
        if self.error is not None:
            raise self.error
        return RemoteOperationResult(operation="fetch", remote=remote_name)

    def pull(self, repository: Repository):
        self.pull_requests += 1
        self._wait()
        if self.error is not None:
            raise self.error
        return RemoteOperationResult(
            operation="pull",
            branch=repository.current_branch,
            upstream="origin/main",
            output="Already up to date.",
        )

    def push(self, repository: Repository):
        self.push_requests += 1
        self._wait()
        if self.error is not None:
            raise self.error
        return RemoteOperationResult(
            operation="push",
            branch=repository.current_branch,
            upstream="origin/main",
        )

    def push_initial(self, repository: Repository, remote_name: str):
        self.initial_push_requests.append(remote_name)
        self._wait()
        if self.error is not None:
            raise self.error
        return RemoteOperationResult(
            operation="push",
            remote=remote_name,
            branch=repository.current_branch,
            upstream=f"{remote_name}/{repository.current_branch}",
        )


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
    remote_service: FakeRemoteService | None = None,
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
        remote_service=remote_service or FakeRemoteService(),
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


class MemorySettingsStorage:
    """Замена ``QSettings`` для тестов, которым не важно сохранение на диск.

    Поддерживает используемую приложением часть API: группы, ``contains`` и
    ``remove`` вместе с вложенными ключами.
    """

    def __init__(self) -> None:
        self._values: dict[str, object] = {}
        self._prefix = ""

    def _key(self, key: str) -> str:
        key = key.strip("/")
        if not self._prefix:
            return key
        return f"{self._prefix}/{key}" if key else self._prefix

    def beginGroup(self, prefix: str) -> None:  # noqa: N802 (QSettings API)
        self._prefix = self._key(prefix)

    def endGroup(self) -> None:  # noqa: N802 (QSettings API)
        self._prefix = (
            self._prefix.rsplit("/", 1)[0] if "/" in self._prefix else ""
        )

    def value(self, key: str, default: object = None) -> object:
        return self._values.get(self._key(key), default)

    def setValue(self, key: str, value: object) -> None:  # noqa: N802 (QSettings API)
        self._values[self._key(key)] = value

    def remove(self, key: str) -> None:
        full_key = self._key(key)
        self._values.pop(full_key, None)
        nested_prefix = f"{full_key}/"
        for nested in [
            item for item in self._values if item.startswith(nested_prefix)
        ]:
            self._values.pop(nested, None)

    def contains(self, key: str) -> bool:
        return self._key(key) in self._values

    def childGroups(self) -> list[str]:  # noqa: N802 (QSettings API)
        prefix = f"{self._prefix}/" if self._prefix else ""
        return sorted(
            {
                item[len(prefix):].split("/", 1)[0]
                for item in self._values
                if item.startswith(prefix) and "/" in item[len(prefix):]
            }
        )

    def allKeys(self) -> list[str]:  # noqa: N802 (QSettings API)
        return list(self._values)

    def sync(self) -> None:
        pass


def memory_settings() -> AppSettings:
    """Настройки без файла: тесты не трогают конфигурацию пользователя."""
    return AppSettings(MemorySettingsStorage())


@contextmanager
def opened_window(
    controller: RepositoryController,
    settings: AppSettings | None = None,
) -> Iterator[MainWindow]:
    window = MainWindow(controller, settings or memory_settings())
    try:
        yield window
    finally:
        window.close()


def make_settings(tmp_path: Path) -> AppSettings:
    """Настройки во временном INI-файле: проверяют сохранение между запусками."""
    return AppSettings(
        QSettings(
            str(tmp_path / "git-visualizer-settings.ini"),
            QSettings.Format.IniFormat,
        )
    )


def assert_view_state_close(
    actual: GraphViewState | None,
    expected: GraphViewState,
    *,
    tolerance: float = 1e-6,
) -> None:
    assert actual is not None
    assert abs(actual.zoom - expected.zoom) < 1e-9
    assert abs(actual.center_x - expected.center_x) < tolerance
    assert abs(actual.center_y - expected.center_y) < tolerance


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
        assert workspace.top_bar.remote_combo.count() == 1
        assert workspace.top_bar.selected_remote == "origin"
        assert workspace.top_bar.fetch_button.isEnabled()
        assert workspace.top_bar.pull_button.isEnabled()
        assert workspace.top_bar.push_button.isEnabled()
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
    window = MainWindow(controller, memory_settings())
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


# --- Discard Changes через главное окно ---


def test_discard_tracked_file_confirms_warns_and_refreshes(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED)
    other = ChangedFile(path="other.txt", unstaged_change=FileChangeType.MODIFIED)
    diff = Diff(path="file.txt", text="@@ -1 +1 @@\n-old\n+new\n")
    changes_service = FakeChangesService((changed, other), diff)
    changes_service.after_discard = (other,)

    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    try:
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == (changed, other))
        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))
        panel.diff_button.click()
        wait_until(qt_app, lambda: not window.diff_overlay.isHidden())

        confirmations: list[tuple[ChangedFile, bool]] = []

        def confirm(changed_file, *, has_head):
            confirmations.append((changed_file, has_head))
            return True

        monkeypatch.setattr(window, "_confirm_discard", confirm)

        panel.discard_button.click()

        wait_until(qt_app, lambda: panel.changes == (other,))
        # Подтверждение показано до запуска Git, для репозитория с HEAD.
        assert confirmations == [(changed, True)]
        assert changes_service.discard_requests == [changed]
        # Открытый diff относился к старому содержимому файла.
        assert window.diff_overlay.isHidden()
        assert window._changes_operation is None
        assert panel.selected_file is None
        assert not panel.is_busy
    finally:
        window.close()


def test_discard_cancel_does_not_run_git(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED)
    changes_service = FakeChangesService((changed,))

    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    try:
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == (changed,))
        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))
        monkeypatch.setattr(window, "_confirm_discard", lambda *args, **kwargs: False)

        panel.discard_button.click()

        assert changes_service.discard_requests == []
        assert panel.changes == (changed,)
        assert panel.selected_file == changed
    finally:
        window.close()


def test_discard_untracked_file_uses_separate_confirmation(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    untracked = ChangedFile(path="untracked.txt", is_untracked=True)
    changes_service = FakeChangesService((untracked,))
    changes_service.after_discard = ()

    window, _ = open_workspace_with_changes(
        qt_app, tmp_path, monkeypatch, changes_service
    )
    try:
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == (untracked,))
        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))
        confirmations: list[tuple[ChangedFile, bool]] = []

        def confirm(changed_file, *, has_head):
            confirmations.append((changed_file, has_head))
            return True

        monkeypatch.setattr(window, "_confirm_discard", confirm)

        panel.discard_button.click()

        wait_until(qt_app, lambda: panel.is_clean)
        # Для untracked готовится отдельное подтверждение удаления самого файла.
        assert confirmations == [(untracked, True)]
        assert changes_service.discard_requests == [untracked]
    finally:
        window.close()


def test_discard_operation_failure_is_reported_and_status_is_refreshed(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = ChangedFile(path="file.txt", unstaged_change=FileChangeType.MODIFIED)
    error = GitCommandError(128, command=("git", "restore"), stderr="fatal")
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
        monkeypatch.setattr(window, "_confirm_discard", lambda *args, **kwargs: True)
        wait_until(qt_app, lambda: panel.changes == (changed,))
        panel.unstaged_tree.setCurrentItem(panel.unstaged_tree.topLevelItem(0))

        panel.discard_button.click()

        wait_until(qt_app, lambda: bool(shown))
        assert shown[0][0] == "Не удалось отменить изменения файла"
        assert shown[0][1] is error
        assert window.diff_overlay.isHidden()
        assert not panel.is_busy
        assert window._changes_operation is None
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
    window = MainWindow(controller, memory_settings())
    shown: list[tuple[str, BaseException]] = []
    try:
        monkeypatch.setattr(
            window, "_show_error", lambda title, exc, **kwargs: shown.append((title, exc))
        )
        window.start_screen.open_button.click()
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == staged)

        panel.commit_button.click()
        wait_until(qt_app, lambda: not window.commit_overlay.isHidden())
        assert "Введите сообщение" in window.commit_overlay.hint_label.text()
        window.commit_overlay.message_edit.setPlainText("Новый коммит")
        assert "Staged (1)" in window.commit_overlay.hint_label.text()
        # После коммита индекс чист: новый статус придёт при повторной загрузке.
        changes_service.changes = ()
        requests_before = changes_service.status_requests
        history_before = history.requests
        window.commit_overlay.commit_button.click()

        wait_until(qt_app, lambda: commit_service.requests == ["Новый коммит"])
        wait_until(qt_app, lambda: window.commit_overlay.isHidden())
        wait_until(qt_app, lambda: window.commit_overlay.message == "")
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
    window = MainWindow(controller, memory_settings())
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
        panel.commit_button.click()
        wait_until(qt_app, lambda: not window.commit_overlay.isHidden())
        window.commit_overlay.message_edit.setPlainText("Останется в окне")
        history_before = history.requests

        window.commit_overlay.commit_button.click()

        wait_until(qt_app, lambda: bool(shown))
        wait_until(qt_app, lambda: panel.staged_files == staged)
        wait_until(qt_app, lambda: not panel.is_busy)
        title, shown_error, kwargs = shown[0]
        assert title == "Не удалось создать коммит"
        assert shown_error is error
        assert "user.name" in kwargs["message"]
        # Окно не закрывается при ошибке: сообщение можно исправить и повторить.
        assert not window.commit_overlay.isHidden()
        assert window.commit_overlay.message == "Останется в окне"
        assert window.commit_overlay.commit_button.isEnabled()
        assert panel.staged_files == staged
        assert history.requests == history_before
    finally:
        window.close()


def test_cancel_commit_overlay_does_not_start_commit(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = (
        ChangedFile(path="staged.txt", staged_change=FileChangeType.ADDED),
    )
    changes_service = FakeChangesService(staged)
    commit_service = FakeCommitService()
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
    window = MainWindow(controller, memory_settings())
    try:
        window.start_screen.open_button.click()
        panel = window.workspace_page.changes_panel
        wait_until(qt_app, lambda: panel.changes == staged)
        panel.commit_button.click()
        wait_until(qt_app, lambda: not window.commit_overlay.isHidden())
        window.commit_overlay.message_edit.setPlainText("Не отправлять")

        window.commit_overlay.cancel_button.click()

        assert window.commit_overlay.isHidden()
        assert window.commit_overlay.message == ""
        assert commit_service.requests == []
        assert panel.changes == staged
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


# --- сетевые операции через главное окно ---


def open_workspace_with_remote(
    qt_app,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote_service: FakeRemoteService,
    *,
    upstream: str | None = "origin/main",
) -> MainWindow:
    commit = make_commit("only")
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
        branches=(
            Branch(
                name="main",
                is_remote=False,
                is_current=True,
                commit_hash=commit.hash,
                upstream=upstream,
            ),
            Branch(
                name="origin/main",
                is_remote=True,
                is_current=False,
                commit_hash=commit.hash,
            ),
        ),
        remotes=(Remote(name="origin", fetch_url="https://example.com/r.git"),),
        remote_service=remote_service,
    )
    patch_folder_dialog(monkeypatch, tmp_path)
    window = MainWindow(controller, memory_settings())
    window.start_screen.open_button.click()
    wait_until(qt_app, lambda: window.workspace_page.workspace_data is not None)
    return window


def test_fetch_shows_progress_and_result(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    gate = threading.Event()
    remote_service = FakeRemoteService(gate=gate, started=started)
    window = open_workspace_with_remote(
        qt_app, tmp_path, monkeypatch, remote_service
    )
    shown: list[tuple[str, BaseException]] = []
    monkeypatch.setattr(window, "_show_error", lambda title, exc: shown.append((title, exc)))
    try:
        bar = window.workspace_page.top_bar
        bar.fetch_button.click()
        wait_until(qt_app, started.is_set)

        assert remote_service.fetch_requests == ["origin"]
        assert not bar.fetch_button.isEnabled()
        assert not bar.pull_button.isEnabled()
        assert "получение изменений" in bar.remote_status_label.text()
        assert "Fetch" in window.statusBar().currentMessage()

        gate.set()
        wait_until(
            qt_app,
            lambda: "выполнено" in bar.remote_status_label.text(),
        )
        assert bar.remote_status_label.text() == "Fetch «origin»: выполнено."
        assert bar.fetch_button.isEnabled()
        assert shown == []
    finally:
        gate.set()
        window.close()


def test_pull_uses_upstream_and_reports_result(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote_service = FakeRemoteService()
    window = open_workspace_with_remote(
        qt_app, tmp_path, monkeypatch, remote_service
    )
    try:
        window.workspace_page.top_bar.pull_button.click()
        wait_until(qt_app, lambda: remote_service.pull_requests == 1)
        wait_until(
            qt_app,
            lambda: "уже актуально" in window.workspace_page.top_bar.remote_status_label.text(),
        )
        assert "Pull «main»" in window.workspace_page.top_bar.remote_status_label.text()
    finally:
        window.close()


def test_push_uses_upstream_and_reports_result(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote_service = FakeRemoteService()
    window = open_workspace_with_remote(
        qt_app, tmp_path, monkeypatch, remote_service
    )
    try:
        window.workspace_page.top_bar.push_button.click()
        wait_until(qt_app, lambda: remote_service.push_requests == 1)
        wait_until(
            qt_app,
            lambda: "Push «main»" in window.workspace_page.top_bar.remote_status_label.text(),
        )
        assert remote_service.initial_push_requests == []
    finally:
        window.close()


def test_push_without_upstream_offers_explicit_initial_push(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote_service = FakeRemoteService()
    window = open_workspace_with_remote(
        qt_app, tmp_path, monkeypatch, remote_service, upstream=None
    )
    confirmations: list[tuple[str, str]] = []
    monkeypatch.setattr(
        window,
        "_confirm_initial_push",
        lambda branch, remote: confirmations.append((branch, remote)) or True,
    )
    try:
        window.workspace_page.top_bar.push_button.click()
        wait_until(qt_app, lambda: remote_service.initial_push_requests == ["origin"])
        wait_until(
            qt_app,
            lambda: "Upstream: origin/main" in window.workspace_page.top_bar.remote_status_label.text(),
        )
        assert confirmations == [("main", "origin")]
        assert remote_service.push_requests == 0
    finally:
        window.close()


def test_push_without_upstream_cancel_does_not_run_git(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote_service = FakeRemoteService()
    window = open_workspace_with_remote(
        qt_app, tmp_path, monkeypatch, remote_service, upstream=None
    )
    monkeypatch.setattr(window, "_confirm_initial_push", lambda *args: False)
    try:
        window.workspace_page.top_bar.push_button.click()
        qt_app.processEvents()
        assert remote_service.initial_push_requests == []
        assert remote_service.push_requests == 0
    finally:
        window.close()


def test_pull_without_upstream_shows_notice_and_runs_nothing(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote_service = FakeRemoteService()
    window = open_workspace_with_remote(
        qt_app, tmp_path, monkeypatch, remote_service, upstream=None
    )
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(
        window, "_show_notice", lambda title, message: notices.append((title, message))
    )
    try:
        window.workspace_page.top_bar.pull_button.click()
        qt_app.processEvents()
        assert notices
        assert notices[0][0] == "Pull невозможен"
        assert "upstream" in notices[0][1]
        assert remote_service.pull_requests == 0
        assert "upstream" in window.workspace_page.top_bar.remote_status_label.text()
    finally:
        window.close()


def test_network_failure_is_reported_and_ui_is_not_busy(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = GitCommandError(
        128,
        command=("git", "fetch", "origin"),
        stderr="fatal: unable to access 'https://example.com/r.git/': "
        "Could not resolve host: example.com\n",
    )
    remote_service = FakeRemoteService(error=error)
    window = open_workspace_with_remote(
        qt_app, tmp_path, monkeypatch, remote_service
    )
    shown: list[tuple[str, BaseException]] = []
    reloaded: list[object] = []
    window.controller.repository_loaded.connect(reloaded.append)
    monkeypatch.setattr(
        window,
        "_show_error",
        lambda title, exc, **kwargs: shown.append((title, exc)),
    )
    try:
        window.workspace_page.top_bar.fetch_button.click()
        wait_until(qt_app, lambda: bool(shown))
        assert shown[0][0] == "Не удалось выполнить Fetch"
        assert shown[0][1] is error
        assert "Сетевая ошибка" in window.workspace_page.top_bar.remote_status_label.toolTip()
        # После ошибки контроллер перечитывает состояние Git и снимает блокировку.
        wait_until(qt_app, lambda: bool(reloaded))
        wait_until(qt_app, lambda: not window.workspace_page.top_bar.is_busy)
    finally:
        window.close()


# --- локальные настройки: недавние репозитории, окно, панели, лимит ---


def test_recent_repository_is_shown_and_reopened(
    qt_app, tmp_path: Path, app_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_path = tmp_path / "recent-repo"
    repo_path.mkdir()
    commit = make_commit("head")
    controller, _ = build_controller(
        repository=make_repository(repo_path, head_commit=commit.hash),
        commits=(commit,),
    )
    app_settings.add_recent_repository(str(repo_path))
    app_settings.sync()

    with opened_window(controller, app_settings) as window:
        screen = window.start_screen
        assert screen.recent_repositories == (str(repo_path),)
        assert not screen.recent_section.isHidden()

        screen.recent_list.itemActivated.emit(screen.recent_list.item(0))

        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.stack.currentWidget() is window.workspace_page,
        )
        assert controller.repository is not None
        assert controller.repository.path == repo_path
        assert app_settings.recent_repositories() == (str(repo_path),)


def test_missing_recent_repository_is_removed_without_crash(
    qt_app, tmp_path: Path, app_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "moved-repo"
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=None)
    )
    app_settings.add_recent_repository(str(missing))
    app_settings.sync()

    with opened_window(controller, app_settings) as window:
        notices: list[tuple[str, str]] = []
        monkeypatch.setattr(
            window,
            "_show_notice",
            lambda title, message: notices.append((title, message)),
        )

        window.start_screen.recent_list.itemActivated.emit(
            window.start_screen.recent_list.item(0)
        )

        assert notices and notices[0][0] == "Репозиторий недоступен"
        assert str(missing) in notices[0][1]
        assert controller.repository is None
        assert window.stack.currentWidget() is window.start_screen
        # Перемещённый путь убран из настроек и из списка на стартовом экране.
        assert app_settings.recent_repositories() == ()
        assert window.start_screen.recent_repositories == ()
        assert window.start_screen.recent_section.isHidden()


def test_opened_repository_becomes_the_first_recent(
    qt_app, tmp_path: Path, app_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = make_commit("head")
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
    )
    app_settings.set_recent_repositories(["C:/old-place"])
    patch_folder_dialog(monkeypatch, tmp_path)

    with opened_window(controller, app_settings) as window:
        window.start_screen.open_button.click()
        wait_until(qt_app, lambda: window.workspace_page.workspace_data is not None)

        assert app_settings.recent_repositories() == (str(tmp_path), "C:/old-place")
        assert window.start_screen.recent_repositories == (
            str(tmp_path),
            "C:/old-place",
        )


def test_window_panel_graph_position_and_history_settings_survive_restart(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = make_commit("head")
    patch_folder_dialog(monkeypatch, tmp_path)
    first_controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
    )
    first_settings = make_settings(tmp_path)
    first_settings.set_history_limit(700)
    first_settings.sync()
    window = MainWindow(first_controller, first_settings)
    try:
        window.resize(1000, 640)
        window.show()
        window.start_screen.open_button.click()
        wait_until(qt_app, lambda: window.workspace_page.workspace_data is not None)
        qt_app.processEvents()
        window.workspace_page.main_splitter.setSizes([420, 210])
        window.workspace_page.body_splitter.setSizes([220, 560, 400])
        window.workspace_page.graph_view.restore_view_state(
            GraphViewState(zoom=0.7, center_x=48.0, center_y=24.0)
        )
        qt_app.processEvents()

        expected_size = window.size()
        workspace = window.workspace_page
        expected_main = workspace.main_splitter.sizes()
        expected_body = workspace.body_splitter.sizes()
        expected_view = workspace.graph_view.view_state()
        assert expected_view is not None
    finally:
        window.close()

    # Второй запуск: новые объекты читают тот же файл настроек.
    second_controller, history = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
    )
    second_settings = make_settings(tmp_path)
    window = MainWindow(second_controller, second_settings)
    try:
        window.show()
        window.start_screen.open_button.click()
        wait_until(qt_app, lambda: window.workspace_page.workspace_data is not None)
        qt_app.processEvents()

        assert window.size() == QSize(expected_size)
        assert window.workspace_page.main_splitter.sizes() == expected_main
        assert window.workspace_page.body_splitter.sizes() == expected_body
        restored_view = window.workspace_page.graph_view.view_state()
        assert restored_view is not None
        assert abs(restored_view.zoom - expected_view.zoom) < 1e-9
        assert abs(restored_view.center_x - expected_view.center_x) < 1e-6
        assert abs(restored_view.center_y - expected_view.center_y) < 1e-6
        # Восстановленное положение сохраняется и в самом файле настроек.
        assert second_settings.graph_view_state(str(tmp_path)) == expected_view
        # Лимит истории применился к открытию репозитория во втором запуске.
        assert history.limits == [700]
    finally:
        window.close()


def test_history_limit_from_settings_is_applied_to_open(
    qt_app, tmp_path: Path, app_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = make_commit("head")
    app_settings.set_history_limit(800)
    controller, history = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
    )
    patch_folder_dialog(monkeypatch, tmp_path)

    with opened_window(controller, app_settings) as window:
        window.start_screen.open_button.click()
        wait_until(qt_app, lambda: window.workspace_page.workspace_data is not None)

        assert history.limits == [800]


def test_applying_history_limit_saves_it_and_updates_controller(
    qt_app, tmp_path: Path, app_settings
) -> None:
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=None)
    )

    with opened_window(controller, app_settings) as window:
        window._apply_history_limit(1200)

        assert controller.history_limit == 1200
        assert app_settings.history_limit() == 1200
        assert make_settings(tmp_path).history_limit() == 1200


def test_settings_dialog_returns_selected_history_limit(qt_app) -> None:
    dialog = SettingsDialog(history_limit=3000)
    try:
        assert dialog.history_limit() == 3000

        dialog.history_limit_spin.setValue(500)
        dialog.accept()

        assert dialog.history_limit() == 500
    finally:
        dialog.close()


def test_graph_position_survives_history_refresh(
    qt_app, tmp_path: Path, app_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = make_commit("head")
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
    )
    patch_folder_dialog(monkeypatch, tmp_path)

    with opened_window(controller, app_settings) as window:
        window.start_screen.open_button.click()
        wait_until(qt_app, lambda: window.workspace_page.workspace_data is not None)

        graph = window.workspace_page.graph_view
        graph.restore_view_state(
            GraphViewState(zoom=0.7, center_x=48.0, center_y=24.0)
        )
        expected = graph.view_state()
        assert expected is not None
        reloaded: list[object] = []
        controller.repository_loaded.connect(reloaded.append)

        window.workspace_page.refresh_requested.emit()
        wait_until(qt_app, lambda: bool(reloaded))
        qt_app.processEvents()

        current = graph.view_state()
        assert current is not None
        assert abs(current.zoom - expected.zoom) < 1e-9
        assert abs(current.center_x - expected.center_x) < 1e-6
        assert abs(current.center_y - expected.center_y) < 1e-6


def test_closing_on_start_screen_keeps_saved_graph_position(
    qt_app, tmp_path: Path, app_settings
) -> None:
    saved = GraphViewState(zoom=0.8, center_x=20.0, center_y=-10.0)
    app_settings.add_recent_repository(str(tmp_path))
    app_settings.set_graph_view_state(str(tmp_path), saved)
    app_settings.sync()
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=None)
    )

    with opened_window(controller, app_settings) as window:
        assert window.workspace_page.graph_view.view_state() is None

    # Графа не было: сохранённое положение не затёрто пустым значением.
    assert make_settings(tmp_path).graph_view_state(str(tmp_path)) == saved


def test_legacy_zoom_only_settings_restore_zoom_on_open(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Настройки прежней версии хранили только масштаб — он не должен теряться."""
    commit = make_commit("head")
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
    )
    storage = QSettings(
        str(tmp_path / "git-visualizer-settings.ini"),
        QSettings.Format.IniFormat,
    )
    storage.setValue("recent/repositories", [str(tmp_path)])
    storage.setValue("graph/zoom", 0.75)
    storage.sync()
    patch_folder_dialog(monkeypatch, tmp_path)

    with opened_window(controller, AppSettings(storage)) as window:
        window.start_screen.open_button.click()
        wait_until(qt_app, lambda: window.workspace_page.workspace_data is not None)
        qt_app.processEvents()

        assert abs(window.workspace_page.graph_view.zoom_factor - 0.75) < 1e-6

    # После закрытия сохраняется уже полное положение — с центром.
    saved = make_settings(tmp_path).graph_view_state(str(tmp_path))
    assert saved is not None
    assert saved.center_x is not None
    assert saved.center_y is not None


def test_graph_position_is_saved_when_repository_is_closed(
    qt_app, tmp_path: Path, app_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = make_commit("head")
    controller, _ = build_controller(
        repository=make_repository(tmp_path, head_commit=commit.hash),
        commits=(commit,),
    )
    patch_folder_dialog(monkeypatch, tmp_path)

    with opened_window(controller, app_settings) as window:
        window.start_screen.open_button.click()
        wait_until(qt_app, lambda: window.workspace_page.workspace_data is not None)

        graph = window.workspace_page.graph_view
        graph.restore_view_state(
            GraphViewState(zoom=0.7, center_x=48.0, center_y=24.0)
        )
        expected = graph.view_state()
        assert expected is not None

        window.workspace_page.top_bar.close_button.click()
        wait_until(
            qt_app,
            lambda: window.stack.currentWidget() is window.start_screen,
        )

    # Выход из приложения уже на стартовом экране не должен терять положение.
    saved = make_settings(tmp_path).graph_view_state(str(tmp_path))
    assert saved is not None
    assert abs(saved.zoom - expected.zoom) < 1e-9
    assert saved.center_x == expected.center_x
    assert saved.center_y == expected.center_y


def test_graph_view_settings_are_independent_for_each_repository(
    qt_app, tmp_path: Path, app_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Вид большого и маленького графа хранится отдельно и не переносится."""
    big_path = tmp_path / "big-repo"
    small_path = tmp_path / "small-repo"
    big_path.mkdir()
    small_path.mkdir()
    commit = make_commit("head")
    selected_path = {"value": big_path}
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(selected_path["value"]),
    )

    def open_repository(window: MainWindow, path: Path) -> None:
        selected_path["value"] = path
        window.start_screen.open_button.click()
        wait_until(
            qt_app,
            lambda: window.workspace_page.workspace_data is not None
            and window.workspace_page.workspace_data.repository.path == path,
        )
        qt_app.processEvents()

    def build_window(path: Path) -> MainWindow:
        controller, _ = build_controller(
            repository=make_repository(path, head_commit=commit.hash),
            commits=(commit,),
        )
        window = MainWindow(controller, app_settings)
        window.show()
        return window

    big_view = GraphViewState(zoom=0.2, center_x=120.0, center_y=240.0)
    window = build_window(big_path)
    try:
        open_repository(window, big_path)
        window.workspace_page.graph_view.restore_view_state(big_view)
        saved_big = window.workspace_page.graph_view.view_state()
    finally:
        window.close()

    small_view = GraphViewState(zoom=3.0, center_x=-10.0, center_y=-20.0)
    window = build_window(small_path)
    try:
        open_repository(window, small_path)
        initial = window.workspace_page.graph_view.view_state()
        # В маленьком репозитории нет вида большого графа: граф вписан заново.
        assert initial is not None
        assert abs(initial.zoom - big_view.zoom) > 1e-6
        window.workspace_page.graph_view.restore_view_state(small_view)
        saved_small = window.workspace_page.graph_view.view_state()
    finally:
        window.close()

    # Повторное открытие большого репозитория возвращает именно его вид.
    window = build_window(big_path)
    try:
        open_repository(window, big_path)
        assert_view_state_close(
            window.workspace_page.graph_view.view_state(), saved_big
        )
    finally:
        window.close()

    stored = make_settings(tmp_path)
    assert_view_state_close(stored.graph_view_state(str(big_path)), saved_big)
    assert_view_state_close(stored.graph_view_state(str(small_path)), saved_small)
