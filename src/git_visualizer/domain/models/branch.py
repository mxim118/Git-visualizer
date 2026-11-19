"""Модель ветки."""

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class Branch:
    """Локальная или удалённая (remote-tracking) ветка.

    ``name`` — короткое имя без префикса ``refs/heads/`` или ``refs/remotes/``;
    у удалённой ветки оно включает имя remote (``origin/main``).
    ``commit_hash`` равен ``None`` у unborn-ветки (текущая ветка репозитория без коммитов).
    ``upstream`` равен ``None``, если отслеживаемая ветка не настроена; у удалённых веток
    upstream не бывает. Detached HEAD веткой не является и в списке веток не представлен.
    """

    name: str
    is_remote: bool
    is_current: bool
    commit_hash: str | None
    upstream: str | None = None
