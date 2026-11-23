"""Сценарии просмотра изменённых файлов коммита и diff одного файла.

Обычный и merge-коммит сравниваются с **первым** родителем (MVP, FR-DIFF-03).
Root-коммит сравнивается с пустым деревом — эту команду выбирает Infrastructure.
Ограничение размера diff задаётся здесь, в Application, а не в виджетах.
"""

from git_visualizer.application.ports import GitRepositoryFactory
from git_visualizer.domain.models import Commit, Diff, FileChange, Repository

# Максимальный объём текста diff, передаваемого в Presentation (FR-DIFF-05).
# 200 000 символов — компромисс между читаемостью и отзывчивостью GUI; полный diff
# остаётся доступным через Git, приложение показывает признак обрезки.
DEFAULT_MAX_DIFF_CHARS = 200_000


class DiffService:
    def __init__(
        self,
        git_repository_factory: GitRepositoryFactory,
        *,
        max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS,
    ) -> None:
        if max_diff_chars < 1:
            raise ValueError(f"Diff limit must be positive, got {max_diff_chars}")
        self._git_repository_factory = git_repository_factory
        self._max_diff_chars = max_diff_chars

    @property
    def max_diff_chars(self) -> int:
        return self._max_diff_chars

    def get_commit_files(
        self, repository: Repository, commit: Commit
    ) -> tuple[FileChange, ...]:
        """Изменённые файлы коммита.

        Для merge-коммита сравнивает с первым родителем; для root — с пустым деревом.
        Порядок файлов — как у Git.

        :raises GitError: ошибка запуска или выполнения Git (пробрасывается без изменений).
        :raises GitOutputParseError: вывод Git не удалось разобрать.
        """
        git = self._git_repository_factory(repository.path)
        return git.get_commit_files(
            commit_hash=commit.hash, parent_hash=_first_parent(commit)
        )

    def get_file_diff(
        self,
        repository: Repository,
        commit: Commit,
        file_change: FileChange,
    ) -> Diff:
        """Unified diff выбранного файла коммита.

        Для переименования/копирования сравниваются оба пути. ``Diff.base_commit_hash``
        содержит родителя, с которым выполнено сравнение (``None`` у root-коммита), —
        Presentation использует его, чтобы явно показать базу сравнения.

        :raises GitError: ошибка запуска или выполнения Git (пробрасывается без изменений).
        """
        git = self._git_repository_factory(repository.path)
        return git.get_commit_file_diff(
            commit_hash=commit.hash,
            parent_hash=_first_parent(commit),
            path=file_change.path,
            old_path=file_change.old_path,
            max_chars=self._max_diff_chars,
        )


def _first_parent(commit: Commit) -> str | None:
    """Родитель для сравнения: первый у merge, единственный у обычного, ``None`` у root."""
    return commit.parent_hashes[0] if commit.parent_hashes else None
