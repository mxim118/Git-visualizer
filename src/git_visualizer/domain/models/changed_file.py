"""Модели изменённых файлов: в рабочей директории и в диффе между двумя состояниями."""

from dataclasses import dataclass

from git_visualizer.domain.enums import FileChangeType


@dataclass(frozen=True, kw_only=True)
class ChangedFile:
    """Запись о файле в состоянии рабочей директории (результат ``git status``).

    Git ведёт два независимых статуса одного файла, поэтому изменения в индексе и в
    рабочей директории хранятся отдельно: файл может быть добавлен в индекс (``ADDED``)
    и затем изменён ещё раз (``MODIFIED`` в рабочей директории) — это одна запись с
    двумя заполненными полями. В интерфейсе такой файл попадает и в список staged,
    и в список unstaged.

    * ``staged_change`` — изменение в индексе относительно HEAD (``None`` — нет);
    * ``unstaged_change`` — изменение в рабочей директории относительно индекса (``None`` — нет);
    * ``is_untracked`` — файл не отслеживается Git (оба изменения при этом ``None``);
    * ``is_conflicted`` — файл в состоянии unmerged (оба изменения при этом ``None``);
    * ``old_path`` — прежний путь при переименовании или копировании.

    Пути хранятся как есть (``str``), без потери Unicode; пробелы и спецсимволы допустимы.
    """

    path: str
    staged_change: FileChangeType | None = None
    unstaged_change: FileChangeType | None = None
    old_path: str | None = None
    is_untracked: bool = False
    is_conflicted: bool = False

    @property
    def is_staged(self) -> bool:
        return self.staged_change is not None

    @property
    def is_unstaged(self) -> bool:
        return self.unstaged_change is not None

    @property
    def can_stage(self) -> bool:
        """Файл можно добавить в индекс: есть рабочее изменение или он не отслеживается.

        Конфликтующий файл добавить в индекс нельзя: ``git add`` снял бы конфликт,
        записав в индекс текущее содержимое рабочей директории (возможно, с маркерами
        конфликта). Разрешение конфликтов не входит в MVP, поэтому такой файл остаётся
        только для отображения.
        """
        return not self.is_conflicted and (self.is_unstaged or self.is_untracked)


@dataclass(frozen=True, kw_only=True)
class FileChange:
    """Изменение одного файла между двумя состояниями (например, коммит и его родитель).

    Используется в списке файлов выбранного коммита; в отличие от ``ChangedFile``
    не различает индекс и рабочую директорию.
    """

    path: str
    change_type: FileChangeType
    old_path: str | None = None  # для RENAMED и COPIED
