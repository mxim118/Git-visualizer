"""Сценарии состояния рабочей директории: статус, diff staged/unstaged, stage/unstage и Discard Changes.

Сервис получает ``git status`` и diff одного файла отдельно для индекса (staged) и
рабочей директории (unstaged), а также добавляет файлы в индекс, убирает их оттуда
и отменяет незакоммиченные изменения выбранного файла. Ограничение размера diff
задаётся здесь, в Application; презентация только рисует готовый ``Diff``.

Один файл может иметь оба изменения (``MM``/``AM``): ``ChangedFile`` хранит их
независимо. Неотслеживаемый файл diff не имеет (Git о нём ничего не знает до
``git add``), поэтому такой запрос отклоняется ``ValueError`` — Presentation
показывает пояснение вместо запуска Git.

Stage/Unstage не отменяют изменения рабочей директории. Массовые операции
пропускают конфликтующие файлы: ``git add`` снял бы конфликт, записав в индекс
текущее содержимое рабочей директории (возможно, с маркерами), а разрешение
конфликтов не входит в MVP. Если конфликтов нет, используются команды на всё
дерево (``git add -A``, ``git restore --staged :/``): они не упираются в предел
длины командной строки на больших наборах файлов.
"""

from collections.abc import Sequence

from git_visualizer.application.errors import UntrackedFileDeleteError
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

    @staticmethod
    def stageable(changes: Sequence[ChangedFile]) -> tuple[ChangedFile, ...]:
        """Файлы, которые затрагивает Stage All: рабочее изменение или untracked.

        Конфликтующие файлы не входят: их добавление в индекс означало бы
        разрешение конфликта, чего MVP не делает. Уже добавленный в индекс файл
        без рабочего изменения тоже не входит — добавлять нечего.
        """
        return tuple(change for change in changes if change.can_stage)

    @staticmethod
    def staged(changes: Sequence[ChangedFile]) -> tuple[ChangedFile, ...]:
        """Файлы, которые затрагивает Unstage All: у них есть изменение в индексе."""
        return tuple(change for change in changes if change.is_staged)

    def stage_file(self, repository: Repository, changed_file: ChangedFile) -> None:
        """Добавляет файл в индекс (``git add -- <путь>``), не меняя рабочую директорию.

        Для переименования/копирования в индекс добавляются оба пути: иначе Git
        увидит удаление старого файла и новый файл по отдельности.

        :raises ValueError: файл уже в индексе, конфликтует или не имеет изменений.
        :raises GitError: команда Git завершилась с ошибкой (пробрасывается без изменений).
        """
        if not changed_file.can_stage:
            raise ValueError(
                f"File cannot be staged: {changed_file.path!r}"
            )
        git = self._git_repository_factory(repository.path)
        git.stage_files(_operation_paths(changed_file))

    def unstage_file(self, repository: Repository, changed_file: ChangedFile) -> None:
        """Убирает файл из индекса, сохраняя изменения рабочей директории.

        Для переименования/копирования передаются оба пути, чтобы в индексе не
        осталось половины переименования. Команду (``git restore --staged`` или
        ``git rm --cached --force`` без ``HEAD``) выбирает Infrastructure.

        :raises ValueError: у файла нет изменения в индексе (в том числе конфликт).
        :raises GitError: команда Git завершилась с ошибкой (пробрасывается без изменений).
        """
        if not changed_file.is_staged:
            raise ValueError(
                f"File has no staged changes to unstage: {changed_file.path!r}"
            )
        git = self._git_repository_factory(repository.path)
        git.unstage_files(_operation_paths(changed_file))

    def stage_all(self, repository: Repository) -> None:
        """Добавляет в индекс все изменения, удаления и неотслеживаемые файлы.

        Если в рабочей директории есть конфликты, они пропускаются: вместо
        массовой команды на всё дерево в индекс добавляются только остальные
        файлы. Пустой результат — изменений для добавления нет.

        :raises GitError: команда Git завершилась с ошибкой (пробрасывается без изменений).
        """
        git = self._git_repository_factory(repository.path)
        changes = git.get_status()
        stageable = self.stageable(changes)
        if not stageable:
            return
        if any(change.is_conflicted for change in changes):
            git.stage_files(_bulk_paths(stageable))
            return
        git.stage_all()

    def unstage_all(self, repository: Repository) -> None:
        """Очищает индекс, сохраняя содержимое рабочей директории.

        Конфликтующие файлы не затрагиваются: массовая ``git restore --staged``
        сбросила бы их конфликтные стадии к ``HEAD``. Поэтому при наличии
        конфликтов из индекса убираются только уже добавленные файлы. Пустой
        индекс — операция ничего не делает.

        :raises GitError: команда Git завершилась с ошибкой (пробрасывается без изменений).
        """
        git = self._git_repository_factory(repository.path)
        changes = git.get_status()
        staged = self.staged(changes)
        if not staged:
            return
        if any(change.is_conflicted for change in changes):
            git.unstage_files(_bulk_paths(staged))
            return
        git.unstage_all()

    def discard_file(self, repository: Repository, changed_file: ChangedFile) -> None:
        """Отменяет все незакоммиченные изменения выбранного файла.

        Отслеживаемый файл восстанавливается к ``HEAD`` сразу в индексе и рабочей
        директории; при переименовании/копировании передаются оба пути, иначе
        Git оставил бы половину изменения. Неотслеживаемый файл удаляется с диска:
        Git не хранит его содержимое, и восстановить файл из коммита невозможно.
        Другие файлы репозитория не затрагиваются.

        :raises ValueError: файл конфликтует или не имеет незакоммиченных изменений.
        :raises GitError: команда Git завершилась с ошибкой (пробрасывается без изменений).
        :raises UntrackedFileDeleteError: файловую систему не удалось изменить
            (нет прав, файл занят другой программой и т. п.).
        """
        if changed_file.is_conflicted:
            raise ValueError(
                f"Conflicted file cannot be discarded: {changed_file.path!r}"
            )
        if not changed_file.can_discard:
            raise ValueError(
                f"File has no changes to discard: {changed_file.path!r}"
            )
        git = self._git_repository_factory(repository.path)
        if changed_file.is_untracked:
            try:
                git.delete_untracked_path(changed_file.path)
            except OSError as error:
                raise UntrackedFileDeleteError(
                    f"Failed to delete untracked path: {changed_file.path!r}",
                    path=changed_file.path,
                    details=str(error),
                ) from error
            return
        git.restore_files(_operation_paths(changed_file))

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


def _operation_paths(changed_file: ChangedFile) -> tuple[str, ...]:
    """Пути, которые нужно передать Git для операции с одним файлом.

    У переименования/копирования Git должен увидеть оба пути, иначе операция
    затронет только половину изменения. Порядок не важен, но старый путь идёт
    первым — так его проще читать в технических подробностях.
    """
    if changed_file.old_path is None:
        return (changed_file.path,)
    return (changed_file.old_path, changed_file.path)


def _bulk_paths(changes: Sequence[ChangedFile]) -> tuple[str, ...]:
    """Пути массовой операции без повторов, в порядке вывода ``git status``."""
    paths: dict[str, None] = {}
    for change in changes:
        for path in _operation_paths(change):
            paths.setdefault(path)
    return tuple(paths)


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
