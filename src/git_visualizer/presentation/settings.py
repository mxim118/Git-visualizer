"""Локальные настройки приложения на ``QSettings``.

Здесь хранится только состояние интерфейса и предпочтения пользователя: список
недавних репозиториев, размер и положение главного окна, состояние сплиттеров,
предпочтительный масштаб графа и лимит первоначальной загрузки коммитов.

Никаких учётных данных здесь нет и быть не должно: пути к локальным каталогам,
числа и состояние Qt-виджетов. Пароли, токены, URL с логином/паролем и приватные
ключи в настройки не попадают (см. ``SETTINGS_KEYS`` — полный перечень ключей).

Все значения имеют значения по умолчанию; повреждённые значения читаются как
значения по умолчанию, а не приводят к падению.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterable, Sequence

from PySide6.QtCore import QByteArray, QSettings

from git_visualizer.application.services.history_service import DEFAULT_HISTORY_LIMIT
from git_visualizer.presentation.widgets.graph_view import MAX_ZOOM, MIN_ZOOM

# Сколько недавних репозиториев хранится: список не должен расти бесконечно.
MAX_RECENT_REPOSITORIES = 10

# Разумный диапазон лимита первоначальной загрузки коммитов (ТЗ FR-HIST-07).
MIN_HISTORY_LIMIT = 100
MAX_HISTORY_LIMIT = 20_000
HISTORY_LIMIT_STEP = 100

# Полный перечень ключей QSettings, которые использует приложение. Тест проверяет,
# что ничего другого не сохраняется: учётных данных в настройках нет.
_KEY_RECENT_REPOSITORIES = "recent/repositories"
_KEY_WINDOW_GEOMETRY = "window/geometry"
_KEY_MAIN_SPLITTER_STATE = "window/main_splitter_state"
_KEY_BODY_SPLITTER_STATE = "window/body_splitter_state"
_KEY_GRAPH_ZOOM = "graph/zoom"
_KEY_HISTORY_LIMIT = "history/limit"

SETTINGS_KEYS = (
    _KEY_RECENT_REPOSITORIES,
    _KEY_WINDOW_GEOMETRY,
    _KEY_MAIN_SPLITTER_STATE,
    _KEY_BODY_SPLITTER_STATE,
    _KEY_GRAPH_ZOOM,
    _KEY_HISTORY_LIMIT,
)


def normalize_path_key(path: str) -> str:
    """Ключ сравнения путей: регистр и разделители зависят от ОС."""
    return os.path.normcase(os.path.normpath(path))


def _clean_paths(paths: Iterable[str]) -> list[str]:
    return [item.strip() for item in paths if isinstance(item, str) and item.strip()]


def remember_recent_repository(
    paths: Sequence[str],
    path: str,
    *,
    limit: int = MAX_RECENT_REPOSITORIES,
) -> tuple[str, ...]:
    """Возвращает список недавних, где ``path`` — первый, без дублей и длиннее ``limit``.

    Порядок — по последнему использованию: только что открытый репозиторий в
    начале, самый старый отбрасывается.
    """
    target = path.strip()
    cleaned = _clean_paths(paths)
    if not target:
        return tuple(cleaned[:limit])
    key = normalize_path_key(target)
    ordered = [target]
    ordered.extend(item for item in cleaned if normalize_path_key(item) != key)
    return tuple(ordered[:limit])


def forget_recent_repository(
    paths: Sequence[str],
    path: str,
) -> tuple[str, ...]:
    """Возвращает список недавних без ``path`` (сравнение путей — по ОС)."""
    key = normalize_path_key(path.strip())
    return tuple(
        item for item in _clean_paths(paths) if normalize_path_key(item) != key
    )


def _read_paths(value: object) -> tuple[str, ...]:
    """Приводит значение QSettings к кортежу путей, не доверяя его типу."""
    if isinstance(value, str):
        items: Iterable[object] = (value,)
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return ()
    return tuple(
        item.strip() for item in items if isinstance(item, str) and item.strip()
    )


def _read_byte_array(value: object) -> QByteArray | None:
    if isinstance(value, QByteArray) and not value.isEmpty():
        return value
    return None


def _valid_history_limit(value: object) -> int:
    """Значение лимита из настроек: неверный тип — умолчание, выход за диапазон — clamp."""
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_HISTORY_LIMIT
    return max(MIN_HISTORY_LIMIT, min(MAX_HISTORY_LIMIT, value))


class AppSettings:
    """Типизированный доступ к ``QSettings`` с умолчаниями и валидацией.

    Класс не привязан к главному окну: он принимает готовый ``QSettings``,
    поэтому в тестах используется временный INI-файл, а в приложении — обычное
    хранилище платформы (``QSettings()``).
    """

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings

    @classmethod
    def from_default(cls) -> "AppSettings":
        """Настройки текущего пользователя (организация и приложение заданы в ``main``)."""
        return cls(QSettings())

    # --- недавние репозитории ---

    def recent_repositories(self) -> tuple[str, ...]:
        """Недавние репозитории по последнему использованию (не больше лимита)."""
        return _read_paths(
            self._settings.value(_KEY_RECENT_REPOSITORIES)
        )[:MAX_RECENT_REPOSITORIES]

    def set_recent_repositories(self, paths: Sequence[str]) -> None:
        self._settings.setValue(
            _KEY_RECENT_REPOSITORIES,
            list(_clean_paths(paths)[:MAX_RECENT_REPOSITORIES]),
        )

    def add_recent_repository(self, path: str) -> tuple[str, ...]:
        """Добавляет путь первым и сохраняет; возвращает обновлённый список."""
        repositories = remember_recent_repository(
            self.recent_repositories(), path
        )
        self.set_recent_repositories(repositories)
        return repositories

    def remove_recent_repository(self, path: str) -> tuple[str, ...]:
        """Убирает путь из списка и сохраняет; возвращает обновлённый список."""
        repositories = forget_recent_repository(self.recent_repositories(), path)
        self.set_recent_repositories(repositories)
        return repositories

    # --- окно и панели ---

    def window_geometry(self) -> QByteArray | None:
        """Сохранённая геометрия окна (``QMainWindow.saveGeometry``)."""
        return _read_byte_array(self._settings.value(_KEY_WINDOW_GEOMETRY))

    def set_window_geometry(self, geometry: QByteArray) -> None:
        self._settings.setValue(_KEY_WINDOW_GEOMETRY, geometry)

    def main_splitter_state(self) -> QByteArray | None:
        return _read_byte_array(self._settings.value(_KEY_MAIN_SPLITTER_STATE))

    def set_main_splitter_state(self, state: QByteArray) -> None:
        self._settings.setValue(_KEY_MAIN_SPLITTER_STATE, state)

    def body_splitter_state(self) -> QByteArray | None:
        return _read_byte_array(self._settings.value(_KEY_BODY_SPLITTER_STATE))

    def set_body_splitter_state(self, state: QByteArray) -> None:
        self._settings.setValue(_KEY_BODY_SPLITTER_STATE, state)

    # --- граф ---

    def graph_zoom(self) -> float | None:
        """Предпочтительный масштаб графа или ``None`` — вписывать автоматически."""
        value = self._settings.value(_KEY_GRAPH_ZOOM)
        if value is None or isinstance(value, bool):
            return None
        try:
            zoom = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(zoom) or not (MIN_ZOOM <= zoom <= MAX_ZOOM):
            return None
        return zoom

    def set_graph_zoom(self, zoom: float | None) -> None:
        if zoom is None:
            self._settings.remove(_KEY_GRAPH_ZOOM)
            return
        self._settings.setValue(_KEY_GRAPH_ZOOM, float(zoom))

    # --- история ---

    def history_limit(self) -> int:
        """Лимит первоначальной загрузки коммитов; по умолчанию ``DEFAULT_HISTORY_LIMIT``."""
        return _valid_history_limit(
            self._settings.value(_KEY_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT)
        )

    def set_history_limit(self, limit: int) -> None:
        self._settings.setValue(
            _KEY_HISTORY_LIMIT, _valid_history_limit(limit)
        )

    # --- служебное ---

    def sync(self) -> None:
        """Сбрасывает настройки на диск (переживают перезапуск)."""
        self._settings.sync()
