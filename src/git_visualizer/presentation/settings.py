"""Локальные настройки приложения на ``QSettings``.

Здесь хранится только состояние интерфейса и предпочтения пользователя: список
недавних репозиториев, размер и положение главного окна, состояние сплиттеров,
положение графа каждого репозитория (масштаб и центр видимой области) и лимит
первоначальной загрузки коммитов.

Никаких учётных данных здесь нет и быть не должно: пути к локальным каталогам,
числа и состояние Qt-виджетов. Пароли, токены, URL с логином/паролем и приватные
ключи в настройки не попадают (см. ``SETTINGS_KEYS`` и группу ``graph_views``).

Все значения имеют значения по умолчанию; повреждённые значения читаются как
значения по умолчанию, а не приводят к падению.
"""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Iterable, Sequence

from PySide6.QtCore import QByteArray, QSettings

from git_visualizer.application.services.history_service import DEFAULT_HISTORY_LIMIT
from git_visualizer.presentation.widgets.graph_view import (
    MAX_ZOOM,
    MIN_ZOOM,
    GraphViewState,
)

# Сколько недавних репозиториев хранится: список не должен расти бесконечно.
MAX_RECENT_REPOSITORIES = 10

# Разумный диапазон лимита первоначальной загрузки коммитов (ТЗ FR-HIST-07).
MIN_HISTORY_LIMIT = 100
MAX_HISTORY_LIMIT = 20_000
HISTORY_LIMIT_STEP = 100

# Полный перечень постоянных ключей QSettings. Положение графа хранится отдельно,
# группами по репозиториям (см. ``GRAPH_VIEW_GROUP``): тест проверяет, что ничего
# другого приложение не сохраняет и учётных данных там нет.
_KEY_RECENT_REPOSITORIES = "recent/repositories"
_KEY_WINDOW_GEOMETRY = "window/geometry"
_KEY_MAIN_SPLITTER_STATE = "window/main_splitter_state"
_KEY_BODY_SPLITTER_STATE = "window/body_splitter_state"
_KEY_HISTORY_LIMIT = "history/limit"

SETTINGS_KEYS = (
    _KEY_RECENT_REPOSITORIES,
    _KEY_WINDOW_GEOMETRY,
    _KEY_MAIN_SPLITTER_STATE,
    _KEY_BODY_SPLITTER_STATE,
    _KEY_HISTORY_LIMIT,
)

# Группа с положениями графа: graph_views/<sha1 пути>/path|zoom|center_x|center_y.
GRAPH_VIEW_GROUP = "graph_views"
_KEY_VIEW_PATH = "path"
_KEY_VIEW_ZOOM = "zoom"
_KEY_VIEW_CENTER_X = "center_x"
_KEY_VIEW_CENTER_Y = "center_y"

# Ключи прежней версии: один масштаб/центр на всё приложение. Читаются при
# обновлении и переносятся в настройки самого свежего репозитория.
_LEGACY_KEY_GRAPH_ZOOM = "graph/zoom"
_LEGACY_KEY_GRAPH_CENTER_X = "graph/center_x"
_LEGACY_KEY_GRAPH_CENTER_Y = "graph/center_y"
LEGACY_GRAPH_VIEW_KEYS = (
    _LEGACY_KEY_GRAPH_ZOOM,
    _LEGACY_KEY_GRAPH_CENTER_X,
    _LEGACY_KEY_GRAPH_CENTER_Y,
)


def normalize_path_key(path: str) -> str:
    """Ключ сравнения путей: регистр и разделители зависят от ОС."""
    return os.path.normcase(os.path.normpath(path))


def graph_view_group(path: str) -> str:
    """Группа QSettings с положением графа конкретного репозитория."""
    digest = hashlib.sha1(normalize_path_key(path).encode("utf-8")).hexdigest()
    return f"{GRAPH_VIEW_GROUP}/{digest}"


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


def _read_float(value: object) -> float | None:
    """Значение QSettings как конечное число; иначе ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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
        cleaned = _clean_paths(paths)[:MAX_RECENT_REPOSITORIES]
        self._settings.setValue(_KEY_RECENT_REPOSITORIES, cleaned)
        # Для репозиториев вне «Недавних» положения графа больше не нужны.
        self.prune_graph_view_states(cleaned)

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

    def graph_view_state(self, repository_path: str) -> GraphViewState | None:
        """Положение графа конкретного репозитория или ``None``.

        У каждого репозитория своё положение: маленький и большой графы не должны
        делить масштаб и сдвиг. Повреждённый масштаб означает «вписать граф
        автоматически», а не падение.
        """
        group = graph_view_group(repository_path)
        self._settings.beginGroup(group)
        try:
            zoom = _read_float(self._settings.value(_KEY_VIEW_ZOOM))
            if zoom is None or not (MIN_ZOOM <= zoom <= MAX_ZOOM):
                return None
            center_x = _read_float(self._settings.value(_KEY_VIEW_CENTER_X))
            center_y = _read_float(self._settings.value(_KEY_VIEW_CENTER_Y))
        finally:
            self._settings.endGroup()
        if center_x is None or center_y is None:
            return GraphViewState(zoom=zoom)
        return GraphViewState(zoom=zoom, center_x=center_x, center_y=center_y)

    def set_graph_view_state(
        self, repository_path: str, state: GraphViewState | None
    ) -> None:
        """Сохраняет положение графа репозитория; ``None`` удаляет запись.

        Для репозиториев вне «Недавних» настройки не сохраняются: такие записи
        удаляются, даже если репозиторий был в списке раньше.
        """
        group = graph_view_group(repository_path)
        if state is None:
            self._settings.remove(group)
            return
        recent = {
            normalize_path_key(path) for path in self.recent_repositories()
        }
        if normalize_path_key(repository_path) not in recent:
            self._settings.remove(group)
            return
        self._settings.beginGroup(group)
        try:
            self._settings.setValue(
                _KEY_VIEW_PATH, normalize_path_key(repository_path)
            )
            self._settings.setValue(_KEY_VIEW_ZOOM, float(state.zoom))
            if state.center_x is None or state.center_y is None:
                # Центра нет — удаляем неполную пару, чтобы не смешивать записи.
                self._settings.remove(_KEY_VIEW_CENTER_X)
                self._settings.remove(_KEY_VIEW_CENTER_Y)
            else:
                self._settings.setValue(
                    _KEY_VIEW_CENTER_X, float(state.center_x)
                )
                self._settings.setValue(
                    _KEY_VIEW_CENTER_Y, float(state.center_y)
                )
        finally:
            self._settings.endGroup()

    def prune_graph_view_states(self, recent_paths: Sequence[str]) -> None:
        """Удаляет положения графов репозиториев, которых нет в «Недавних»."""
        recent = {normalize_path_key(path) for path in _clean_paths(recent_paths)}
        for group in self._graph_view_groups():
            full_group = f"{GRAPH_VIEW_GROUP}/{group}"
            self._settings.beginGroup(full_group)
            try:
                stored_path = self._settings.value(_KEY_VIEW_PATH)
            finally:
                self._settings.endGroup()
            if (
                not isinstance(stored_path, str)
                or normalize_path_key(stored_path) not in recent
            ):
                self._settings.remove(full_group)

    def clean_up_graph_view_states(self, recent_paths: Sequence[str]) -> None:
        """Переносит прежние общие настройки графа и чистит записи вне «Недавних».

        Прежняя версия хранила один масштаб/центр на всё приложение. Переносим его
        самому свежему репозиторию из «Недавних» (последний, с которым работали),
        а затем удаляем записи репозиториев, которых в списке уже нет.
        """
        self._migrate_legacy_graph_view(recent_paths)
        self.prune_graph_view_states(recent_paths)

    def _graph_view_groups(self) -> list[str]:
        self._settings.beginGroup(GRAPH_VIEW_GROUP)
        try:
            return list(self._settings.childGroups())
        finally:
            self._settings.endGroup()

    def _migrate_legacy_graph_view(self, recent_paths: Sequence[str]) -> None:
        if not any(
            self._settings.contains(key) for key in LEGACY_GRAPH_VIEW_KEYS
        ):
            return
        zoom = _read_float(self._settings.value(_LEGACY_KEY_GRAPH_ZOOM))
        center_x = _read_float(self._settings.value(_LEGACY_KEY_GRAPH_CENTER_X))
        center_y = _read_float(self._settings.value(_LEGACY_KEY_GRAPH_CENTER_Y))
        repositories = _clean_paths(recent_paths)

        if (
            repositories
            and zoom is not None
            and MIN_ZOOM <= zoom <= MAX_ZOOM
            and self.graph_view_state(repositories[0]) is None
        ):
            state = GraphViewState(zoom=zoom)
            if center_x is not None and center_y is not None:
                state = GraphViewState(
                    zoom=zoom, center_x=center_x, center_y=center_y
                )
            self.set_graph_view_state(repositories[0], state)

        for key in LEGACY_GRAPH_VIEW_KEYS:
            self._settings.remove(key)

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
