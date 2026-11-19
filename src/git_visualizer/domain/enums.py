"""Перечисления предметной области."""

from enum import Enum


class FileChangeType(Enum):
    """Тип изменения файла.

    Сопоставление с буквами статусов Git (A, M, D, ...) выполняет слой Infrastructure;
    Domain о буквенных кодах не знает. Состояния «не отслеживается» и «конфликт»
    сюда не входят: это не изменение относительно предыдущего состояния, они выражаются
    флагами ``ChangedFile.is_untracked`` и ``ChangedFile.is_conflicted``.
    """

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type_changed"
