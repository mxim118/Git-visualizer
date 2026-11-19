"""Модель репозитория."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, kw_only=True)
class Repository:
    """Открытый репозиторий с рабочей директорией.

    Возможные состояния HEAD:

    * обычное: ``current_branch`` и ``head_commit`` заданы;
    * detached HEAD: ``current_branch is None``, ``head_commit`` задан;
    * unborn (нет коммитов): ``head_commit is None``; ``current_branch`` при этом
      содержит имя будущей ветки (например, ``main`` сразу после ``git init``).

    Bare-репозитории в MVP не поддерживаются; поле ``is_bare`` только фиксирует свойство.
    """

    path: Path  # абсолютный путь к корню репозитория
    name: str
    current_branch: str | None
    head_commit: str | None  # полный hash коммита, на который указывает HEAD
    is_bare: bool = False

    @property
    def is_detached(self) -> bool:
        return self.current_branch is None and self.head_commit is not None

    @property
    def is_unborn(self) -> bool:
        return self.head_commit is None
