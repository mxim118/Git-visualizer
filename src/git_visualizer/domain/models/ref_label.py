"""Модель метки ссылки на графе."""

from dataclasses import dataclass

from git_visualizer.domain.enums import RefKind


@dataclass(frozen=True, kw_only=True)
class RefLabel:
    """Одна метка, которую граф рисует возле коммита.

    ``name`` — текст для показа (``main``, ``origin/main``, ``HEAD``); ``full_name`` — полное
    имя ref (``refs/heads/main``, ``refs/remotes/origin/main``) либо ``HEAD``: тот же формат,
    что у ``Commit.refs``. ``is_current`` — метка текущей ветки либо ``HEAD`` при detached HEAD.
    Если на один коммит указывает несколько ссылок, у него несколько меток: ни одна не теряется.
    """

    name: str
    full_name: str
    kind: RefKind
    is_current: bool = False
