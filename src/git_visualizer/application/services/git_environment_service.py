"""Проверка окружения Git при запуске приложения (ТЗ 5.10).

Сервис определяет, установлен ли Git и достаточна ли его версия. Сравнение версий
выполняется здесь, в Application; Presentation только показывает результат. Ошибка
отсутствия Git (``GitNotFoundError``) пробрасывается без изменений.
"""

from dataclasses import dataclass

from git_visualizer.application.ports import GitVersionProvider

# Минимальная поддерживаемая версия Git (ТЗ 3.2, MVP-21, решение 11).
MINIMUM_GIT_VERSION: tuple[int, int] = (2, 23)
MINIMUM_GIT_VERSION_TEXT = "2.23"


@dataclass(frozen=True, kw_only=True)
class GitEnvironment:
    """Результат проверки версии Git.

    ``version`` — числа ``(major, minor, patch)``; ``raw_version`` — исходная строка
    вывода ``git --version`` (может содержать суффикс вроде ``windows.1``).
    """

    version: tuple[int, int, int]
    raw_version: str
    minimum: tuple[int, int] = MINIMUM_GIT_VERSION

    @property
    def version_text(self) -> str:
        return ".".join(str(part) for part in self.version)

    @property
    def minimum_text(self) -> str:
        return ".".join(str(part) for part in self.minimum)

    @property
    def is_supported(self) -> bool:
        """Версия не ниже минимальной; суффикс вывода на сравнение не влияет."""
        return self.version[:2] >= self.minimum


class GitEnvironmentService:
    """Проверяет наличие и версию Git через провайдер версии."""

    def __init__(self, git_version_provider: GitVersionProvider) -> None:
        self._git_version_provider = git_version_provider

    def check(self) -> GitEnvironment:
        """Возвращает найденную версию Git.

        :raises GitNotFoundError: Git не найден или его вывод не похож на Git.
        :raises GitError: ошибка запуска Git (пробрасывается без изменений).
        """
        version = self._git_version_provider.get_version()
        return GitEnvironment(
            version=(int(version.major), int(version.minor), int(version.patch)),
            raw_version=version.raw,
        )
