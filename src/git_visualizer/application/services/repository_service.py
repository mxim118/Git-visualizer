"""Сценарий открытия репозитория.

Сервис проверяет выбранный путь, определяет корень репозитория и собирает модель
``Repository``. Ожидаемые отказы оформляются типами из ``application.errors``.
Остальные ошибки Git (Git не найден, timeout, неопознанный сбой команды) не скрываются
и пробрасываются как ``GitError``: Application намеренно импортирует только модуль
``git_errors`` слоя Infrastructure, потому что типы ошибок — часть контракта доступа к Git.
"""

import os
from pathlib import Path

from git_visualizer.application.errors import (
    BareRepositoryNotSupportedError,
    NotAGitRepositoryError,
    PathDoesNotExistError,
    PathIsNotDirectoryError,
    RepositoryOpenError,
    UnsafeRepositoryError,
)
from git_visualizer.application.ports import GitRepositoryFactory
from git_visualizer.domain.models import Remote, Repository
from git_visualizer.infrastructure.git.git_errors import GitCommandError, GitErrorKind


class RepositoryService:
    def __init__(self, git_repository_factory: GitRepositoryFactory) -> None:
        self._git_repository_factory = git_repository_factory

    def open_repository(self, path: str | os.PathLike[str]) -> Repository:
        """Открывает репозиторий по пути к его корню или к любой вложенной папке.

        :returns: модель с путём к корню репозитория, текущей веткой (``None`` при
            detached HEAD) и hash HEAD (``None``, если коммитов ещё нет).
        :raises RepositoryOpenError: путь не подходит (подтипы описаны в ``application.errors``).
        :raises GitError: Git не найден, timeout или неожиданный сбой команды.
        """
        selected = self._check_path(path)
        git = self._git_repository_factory(selected)

        try:
            inside_work_tree = git.is_inside_work_tree()
        except GitCommandError as error:
            translated = _translate_command_error(error, selected)
            if translated is None:
                raise
            raise translated from error

        if not inside_work_tree:
            # Git сообщил «не рабочая директория»: это bare-репозиторий либо каталог .git.
            if git.is_bare_repository():
                raise BareRepositoryNotSupportedError(
                    "Bare repositories are not supported", path=selected
                )
            raise NotAGitRepositoryError(
                "Path is inside a .git directory, not inside a working tree", path=selected
            )

        root = git.get_toplevel()
        root_git = self._git_repository_factory(root)
        return Repository(
            path=root,
            name=root.name or str(root),  # у корня диска имени нет
            current_branch=root_git.get_current_branch(),
            head_commit=root_git.get_head_commit(),
        )

    def get_remotes(self, repository: Repository) -> tuple[Remote, ...]:
        """Настроенные remote открытого репозитория; пустой кортеж, если их нет."""
        return self._git_repository_factory(repository.path).get_remotes()

    @staticmethod
    def _check_path(path: str | os.PathLike[str]) -> Path:
        raw = os.fspath(path)
        selected = Path(raw)
        # Пустая строка превратилась бы в «.» (текущий каталог) — открывать его нельзя.
        if not raw.strip() or not selected.exists():
            raise PathDoesNotExistError("Path does not exist", path=selected)
        if not selected.is_dir():
            raise PathIsNotDirectoryError("Path is not a directory", path=selected)
        return selected


def _translate_command_error(error: GitCommandError, path: Path) -> RepositoryOpenError | None:
    """Известные отказы Git при проверке пути; для остальных — ``None`` (ошибку пробросить)."""
    if error.kind is GitErrorKind.NOT_A_REPOSITORY:
        return NotAGitRepositoryError(
            "Path is not inside a Git repository", path=path, details=error.technical_details()
        )
    if error.kind is GitErrorKind.UNSAFE_REPOSITORY:
        return UnsafeRepositoryError(
            "Git refuses to use this directory because of its ownership",
            path=path,
            details=error.technical_details(),
        )
    return None
