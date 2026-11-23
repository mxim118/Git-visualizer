"""Модель ветки."""

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class Branch:
    """Локальная или удалённая (remote-tracking) ветка.

    ``name`` — короткое имя без префикса ``refs/heads/`` или ``refs/remotes/``;
    у удалённой ветки оно включает имя remote (``origin/main``).
    ``commit_hash`` равен ``None`` у unborn-ветки (текущая ветка репозитория без коммитов).
    ``upstream`` равен ``None``, если отслеживаемая ветка не настроена; у удалённых веток
    upstream не бывает. Это настроенное имя в том же коротком виде, что и ``name``
    (``origin/main`` для remote-tracking ветки, ``main`` для локальной), и оно не гарантирует,
    что такая ветка сейчас есть: remote-tracking ветку могли удалить (upstream «gone»).
    Detached HEAD веткой не является и в списке веток не представлен.

    Remote-tracking ветка (``origin/main``) — локальная копия состояния ветки на сервере, а не
    настроенный remote (``origin``, см. ``Remote``): это разные сущности.
    """

    name: str
    is_remote: bool
    is_current: bool
    commit_hash: str | None
    upstream: str | None = None

    @property
    def ref_name(self) -> str:
        """Полное имя ref: ``refs/heads/main`` или ``refs/remotes/origin/main``.

        Тот же формат, что у ``Commit.refs`` (см. решение о формате refs).
        """
        prefix = "refs/remotes/" if self.is_remote else "refs/heads/"
        return prefix + self.name
