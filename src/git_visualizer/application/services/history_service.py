"""Сценарий получения истории коммитов.

История загружается страницами (ТЗ FR-HIST-07): при открытии репозитория и
обновлении запрашиваются последние ``limit`` коммитов, а более старые
догружаются повторным запросом с ``skip``. Признак «загружена не вся история»
считается здесь, без дополнительных команд Git: у Infrastructure запрашивается
на один коммит больше лимита, и лишний коммит означает, что страница не
последняя. ``len == limit`` такой однозначности не даёт.
"""

from dataclasses import dataclass

from git_visualizer.application.ports import GitRepositoryFactory
from git_visualizer.domain.models import Commit, Repository

# Сколько коммитов загружается при первоначальном открытии и — такой же
# страницей — при каждом «Загрузить ещё» (ТЗ FR-HIST-07: 2 000–5 000).
# Единственное место, где задано это число: позже оно станет пользовательской
# настройкой (итерация 8), и менять придётся только здесь.
# Infrastructure значения по умолчанию не имеет.
DEFAULT_HISTORY_LIMIT = 3000


@dataclass(frozen=True, kw_only=True)
class HistoryPage:
    """Страница истории: коммиты страницы и признак, что есть более старые.

    ``commits`` — не больше запрошенного лимита, в топологическом порядке.
    ``has_more`` — среди refs репозитория есть коммиты старше последнего
    загруженного, то есть история обрезана лимитом и её можно догрузить.
    """

    commits: tuple[Commit, ...]
    has_more: bool


class HistoryService:
    def __init__(self, git_repository_factory: GitRepositoryFactory) -> None:
        self._git_repository_factory = git_repository_factory

    def get_history(
        self,
        repository: Repository,
        *,
        limit: int = DEFAULT_HISTORY_LIMIT,
        skip: int = 0,
    ) -> HistoryPage:
        """Страница истории открытого репозитория: все локальные и remote-tracking
        ветки и ``HEAD``.

        Порядок топологический (потомок раньше родителей), новые коммиты — первыми;
        результат нельзя пересортировывать по дате. ``skip`` пропускает уже
        загруженные коммиты: ``skip=len(страницы)`` возвращает следующую, более
        старую страницу того же порядка. Если коммитов больше ``limit``, у
        последних коммитов страницы родителей в ней нет: это обычная ситуация
        для потребителя (граф). Пустая страница — коммитов нет (или ``skip``
        больше всей истории).

        :raises ValueError: ``limit`` меньше 1 или ``skip`` отрицателен.
        :raises GitError: ошибка запуска или выполнения Git (пробрасывается без изменений).
        """
        if limit < 1:
            raise ValueError(f"History limit must be positive, got {limit}")
        if skip < 0:
            raise ValueError(f"History skip must not be negative, got {skip}")
        # На один коммит больше лимита: так видно, что страница не последняя.
        requested = self._git_repository_factory(repository.path).get_history(
            limit=limit + 1, skip=skip
        )
        return HistoryPage(
            commits=tuple(requested[:limit]),
            has_more=len(requested) > limit,
        )
