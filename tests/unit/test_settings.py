"""Тесты локальных настроек: значения по умолчанию, недавние репозитории, QSettings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings

from git_visualizer.application.services.history_service import DEFAULT_HISTORY_LIMIT
from git_visualizer.presentation.settings import (
    MAX_HISTORY_LIMIT,
    MAX_RECENT_REPOSITORIES,
    MIN_HISTORY_LIMIT,
    SETTINGS_KEYS,
    AppSettings,
    forget_recent_repository,
    remember_recent_repository,
)

CONFIG_NAME = "git-visualizer-settings.ini"


def make_storage(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / CONFIG_NAME), QSettings.Format.IniFormat)


def make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(make_storage(tmp_path))


# --- значения по умолчанию ---


def test_empty_settings_have_defaults(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    assert settings.recent_repositories() == ()
    assert settings.window_geometry() is None
    assert settings.main_splitter_state() is None
    assert settings.body_splitter_state() is None
    assert settings.graph_zoom() is None
    assert settings.history_limit() == DEFAULT_HISTORY_LIMIT


# --- недавние репозитории: чистая логика ---


def test_recent_repository_is_remembered_once_and_first() -> None:
    paths = remember_recent_repository(["C:/old", "C:/new"], "C:/new")

    assert paths == ("C:/new", "C:/old")


def test_recent_repository_comparison_ignores_trailing_separator() -> None:
    paths = remember_recent_repository(["C:/repo"], "C:/repo/")

    assert paths == ("C:/repo/",)


def test_recent_repository_list_is_bounded() -> None:
    paths: tuple[str, ...] = ()
    for index in range(MAX_RECENT_REPOSITORIES + 5):
        paths = remember_recent_repository(paths, f"C:/repo-{index}")

    assert len(paths) == MAX_RECENT_REPOSITORIES
    # Первый — последний использованный, самый старый вытеснен.
    assert paths[0] == f"C:/repo-{MAX_RECENT_REPOSITORIES + 4}"
    assert "C:/repo-0" not in paths


def test_empty_recent_paths_are_dropped() -> None:
    paths = remember_recent_repository(["", "  ", "C:/repo"], " ")

    assert paths == ("C:/repo",)


def test_forget_recent_repository_keeps_order() -> None:
    paths = forget_recent_repository(("C:/a", "C:/b", "C:/c"), "C:/b")

    assert paths == ("C:/a", "C:/c")


# --- недавние репозитории: сохранение ---


def test_add_and_remove_recent_repositories_are_saved(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.add_recent_repository("C:/first")
    settings.add_recent_repository("C:/second")
    settings.sync()

    reopened = make_settings(tmp_path)
    assert reopened.recent_repositories() == ("C:/second", "C:/first")

    reopened.remove_recent_repository("C:/second")
    reopened.sync()
    assert make_settings(tmp_path).recent_repositories() == ("C:/first",)


def test_saved_recent_repositories_are_capped(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.set_recent_repositories(
        [f"C:/repo-{index}" for index in range(MAX_RECENT_REPOSITORIES + 3)]
    )
    settings.sync()

    assert (
        len(make_settings(tmp_path).recent_repositories())
        == MAX_RECENT_REPOSITORIES
    )


# --- сохранение и восстановление между запусками ---


def test_all_settings_survive_restart(tmp_path: Path) -> None:
    geometry = QByteArray(b"window-geometry-state")
    main_state = QByteArray(b"main-splitter-state")
    body_state = QByteArray(b"body-splitter-state")

    settings = make_settings(tmp_path)
    settings.add_recent_repository("C:/repo")
    settings.set_window_geometry(geometry)
    settings.set_main_splitter_state(main_state)
    settings.set_body_splitter_state(body_state)
    settings.set_graph_zoom(1.5)
    settings.set_history_limit(1200)
    settings.sync()

    reopened = make_settings(tmp_path)
    assert reopened.recent_repositories() == ("C:/repo",)
    assert reopened.window_geometry() == geometry
    assert reopened.main_splitter_state() == main_state
    assert reopened.body_splitter_state() == body_state
    assert reopened.graph_zoom() == 1.5
    assert reopened.history_limit() == 1200


def test_only_interface_state_keys_are_saved(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.add_recent_repository("C:/repo")
    settings.set_window_geometry(QByteArray(b"geometry"))
    settings.set_main_splitter_state(QByteArray(b"main"))
    settings.set_body_splitter_state(QByteArray(b"body"))
    settings.set_graph_zoom(1.0)
    settings.set_history_limit(1500)
    settings.sync()

    keys = set(make_storage(tmp_path).allKeys())
    assert keys == set(SETTINGS_KEYS)
    # Учётных данных в настройках нет: ни паролей, ни токенов, ни ключей.
    assert not any(
        marker in key.lower()
        for key in keys
        for marker in ("password", "token", "credential", "secret", "key")
    )


# --- некорректные значения ---


def test_corrupted_history_limit_falls_back_to_default(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.setValue("history/limit", "not a number")

    assert AppSettings(storage).history_limit() == DEFAULT_HISTORY_LIMIT


def test_history_limit_is_clamped_to_supported_range(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    settings.set_history_limit(5)
    assert settings.history_limit() == MIN_HISTORY_LIMIT

    settings.set_history_limit(10_000_000)
    assert settings.history_limit() == MAX_HISTORY_LIMIT


def test_corrupted_graph_zoom_is_ignored(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.setValue("graph/zoom", "wide")

    assert AppSettings(storage).graph_zoom() is None

    storage.setValue("graph/zoom", 100.0)
    assert AppSettings(storage).graph_zoom() is None

    storage.setValue("graph/zoom", 0.0)
    assert AppSettings(storage).graph_zoom() is None


def test_graph_zoom_can_be_reset(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.set_graph_zoom(2.0)
    settings.set_graph_zoom(None)

    assert settings.graph_zoom() is None


def test_corrupted_splitter_and_geometry_values_are_ignored(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.setValue("window/geometry", "not a byte array")
    storage.setValue("window/main_splitter_state", 42)

    assert AppSettings(storage).window_geometry() is None
    assert AppSettings(storage).main_splitter_state() is None
