"""Абстракции доступа к Git, которые нужны сервисам Application.

Реализует их слой Infrastructure (``GitRepository``); сервисы зависят только от этого
описания, поэтому их можно тестировать с подменой, без запуска Git.
"""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from git_visualizer.domain.models import (
    Branch,
    ChangedFile,
    Commit,
    Diff,
    FileChange,
    Remote,
)


class GitRepositoryPort(Protocol):
    """Запросы к репозиторию в заданном каталоге."""

    def is_inside_work_tree(self) -> bool: ...

    def is_bare_repository(self) -> bool: ...

    def get_toplevel(self) -> Path: ...

    def get_current_branch(self) -> str | None: ...

    def get_head_commit(self) -> str | None: ...

    def get_status(self) -> tuple[ChangedFile, ...]: ...

    def stage_files(self, paths: Sequence[str]) -> None: ...

    def stage_all(self) -> None: ...

    def unstage_files(self, paths: Sequence[str]) -> None: ...

    def unstage_all(self) -> None: ...

    def commit(self, message: str) -> None: ...

    def get_worktree_diff(
        self,
        *,
        path: str,
        old_path: str | None,
        staged: bool,
        max_chars: int,
    ) -> Diff: ...

    def get_branches(self) -> tuple[Branch, ...]: ...

    def validate_branch_name(self, name: str) -> None: ...

    def create_branch(self, name: str, *, start_point: str | None = None) -> None: ...

    def switch_branch(self, name: str) -> None: ...

    def get_remotes(self) -> tuple[Remote, ...]: ...

    def get_branch_upstream(self, branch_name: str) -> str | None: ...

    def fetch(self, remote_name: str, *, timeout: float) -> str: ...

    def pull_ff_only(self, *, timeout: float) -> str: ...

    def push(self, *, timeout: float) -> str: ...

    def push_set_upstream(
        self, remote_name: str, branch_name: str, *, timeout: float
    ) -> str: ...

    def get_history(self, *, limit: int) -> tuple[Commit, ...]: ...

    def get_commit_files(
        self, *, commit_hash: str, parent_hash: str | None
    ) -> tuple[FileChange, ...]: ...

    def get_commit_file_diff(
        self,
        *,
        commit_hash: str,
        parent_hash: str | None,
        path: str,
        old_path: str | None,
        max_chars: int,
    ) -> Diff: ...


# Создаёт доступ к репозиторию для каталога; связывается с реализацией при сборке приложения.
GitRepositoryFactory = Callable[[Path], GitRepositoryPort]


class GitVersionLike(Protocol):
    """Версия Git в том виде, в каком её возвращает низкоуровневый провайдер."""

    major: int
    minor: int
    patch: int
    raw: str


class GitVersionProvider(Protocol):
    """Определяет наличие Git и возвращает его версию (``git --version``)."""

    def get_version(self) -> GitVersionLike: ...
