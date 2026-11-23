"""Абстракции доступа к Git, которые нужны сервисам Application.

Реализует их слой Infrastructure (``GitRepository``); сервисы зависят только от этого
описания, поэтому их можно тестировать с подменой, без запуска Git.
"""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from git_visualizer.domain.models import Branch, Commit, Diff, FileChange, Remote


class GitRepositoryPort(Protocol):
    """Запросы к репозиторию в заданном каталоге."""

    def is_inside_work_tree(self) -> bool: ...

    def is_bare_repository(self) -> bool: ...

    def get_toplevel(self) -> Path: ...

    def get_current_branch(self) -> str | None: ...

    def get_head_commit(self) -> str | None: ...

    def get_branches(self) -> tuple[Branch, ...]: ...

    def get_remotes(self) -> tuple[Remote, ...]: ...

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
