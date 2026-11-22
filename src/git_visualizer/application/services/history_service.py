"""Сценарий получения истории коммитов."""

from git_visualizer.application.ports import GitRepositoryFactory
from git_visualizer.domain.models import Commit, Repository

# Сколько коммитов загружается при первоначальном открытии (ТЗ FR-HIST-07: 2 000–5 000).
# Единственное место, где задано это число: позже оно станет пользовательской настройкой
# (итерация 8), и менять придётся только здесь. Infrastructure значения по умолчанию не имеет.
DEFAULT_HISTORY_LIMIT = 3000


class HistoryService:
    def __init__(self, git_repository_factory: GitRepositoryFactory) -> None:
        self._git_repository_factory = git_repository_factory

    def get_history(
        self, repository: Repository, *, limit: int = DEFAULT_HISTORY_LIMIT
    ) -> tuple[Commit, ...]:
        """История открытого репозитория: все локальные и remote-tracking ветки и ``HEAD``.

        Порядок топологический (потомок раньше родителей), новые коммиты — первыми;
        результат нельзя пересортировывать по дате. Если коммитов больше ``limit``,
        возвращаются самые новые, а у последних коммитов родителей в результате нет.
        Пустой кортеж — в репозитории нет коммитов.

        :raises ValueError: ``limit`` меньше 1.
        :raises GitError: ошибка запуска или выполнения Git (пробрасывается без изменений).
        """
        if limit < 1:
            raise ValueError(f"History limit must be positive, got {limit}")
        return self._git_repository_factory(repository.path).get_history(limit=limit)
