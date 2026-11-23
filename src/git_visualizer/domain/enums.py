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


class RefKind(Enum):
    """Вид ссылки, для которой на графе рисуется метка (``RefLabel``).

    Теги добавятся сюда вместе с реализацией тегов (ТЗ 4.2).
    """

    LOCAL_BRANCH = "local_branch"
    REMOTE_BRANCH = "remote_branch"
    DETACHED_HEAD = "detached_head"
