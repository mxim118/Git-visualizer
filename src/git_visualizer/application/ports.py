"""Абстракции доступа к Git, которые нужны сервисам Application.

Реализует их слой Infrastructure (``GitRepository``); сервисы зависят только от этого
описания, поэтому их можно тестировать с подменой, без запуска Git.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from git_visualizer.domain.models import Branch, Commit, Remote


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


# Создаёт доступ к репозиторию для каталога; связывается с реализацией при сборке приложения.
GitRepositoryFactory = Callable[[Path], GitRepositoryPort]
