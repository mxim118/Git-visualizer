"""Сценарии состояния рабочей директории: списки изменений и diff staged/unstaged.

Сервис получает ``git status`` и diff одного файла отдельно для индекса (staged) и
рабочей директории (unstaged). Ограничение размера diff задаётся здесь, в Application;
презентация только рисует готовый ``Diff``.

Один файл может иметь оба изменения (``MM``/``AM``): ``ChangedFile`` хранит их
независимо. Неотслеживаемый файл diff не имеет (Git о нём ничего не знает до
``git add``), поэтому такой запрос отклоняется ``ValueError`` — Presentation
показывает пояснение вместо запуска Git.
"""

from git_visualizer.application.ports import GitRepositoryFactory
from git_visualizer.application.services.diff_service import DEFAULT_MAX_DIFF_CHARS
from git_visualizer.domain.models import ChangedFile, Diff, Repository


class ChangesService:
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

    def get_status(self, repository: Repository) -> tuple[ChangedFile, ...]:
        """Изменения индекса и рабочей директории.

        Порядок файлов — как у Git. Одна запись описывает файл целиком: индексное
        изменение (``staged_change``) и рабочее (``unstaged_change``) могут быть
        заполнены одновременно, ``is_untracked``/``is_conflicted`` — отдельные флаги.

        :raises GitError: ошибка запуска или выполнения Git (пробрасывается без изменений).
        :raises GitOutputParseError: вывод Git не удалось разобрать.
        """
        git = self._git_repository_factory(repository.path)
        return git.get_status()

    def get_file_diff(
        self,
        repository: Repository,
        changed_file: ChangedFile,
        *,
        staged: bool,
    ) -> Diff:
        """Diff файла в выбранной секции: ``staged=True`` — индекс, иначе рабочая директория.

        :raises ValueError: у файла нет изменений в запрошенной секции (``staged`` —
        нужен ``staged_change``; ``staged=False`` — ``unstaged_change`` или конфликт).
        Неотслеживаемый файл diff не имеет.
        :raises GitError: ошибка запуска или выполнения Git (пробрасывается без изменений).
        """
        _check_section(changed_file, staged=staged)
        git = self._git_repository_factory(repository.path)
        return git.get_worktree_diff(
            path=changed_file.path,
            old_path=changed_file.old_path,
            staged=staged,
            max_chars=self._max_diff_chars,
        )


def _check_section(changed_file: ChangedFile, *, staged: bool) -> None:
    if staged:
        if not changed_file.is_staged:
            raise ValueError(
                f"File has no staged changes: {changed_file.path!r}"
            )
        return
    if changed_file.is_untracked:
        raise ValueError(
            f"Untracked file has no diff until staged: {changed_file.path!r}"
        )
    if not (changed_file.is_unstaged or changed_file.is_conflicted):
        raise ValueError(
            f"File has no unstaged changes: {changed_file.path!r}"
        )
