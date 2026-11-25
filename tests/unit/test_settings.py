"""Тесты локальных настроек: значения по умолчанию, недавние репозитории, QSettings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings

from git_visualizer.application.services.history_service import DEFAULT_HISTORY_LIMIT
from git_visualizer.presentation.settings import (
    GRAPH_VIEW_GROUP,
    LEGACY_GRAPH_VIEW_KEYS,
    MAX_HISTORY_LIMIT,
    MAX_RECENT_REPOSITORIES,
    MIN_HISTORY_LIMIT,
    SETTINGS_KEYS,
    AppSettings,
    forget_recent_repository,
    graph_view_group,
    remember_recent_repository,
)
from git_visualizer.presentation.widgets.graph_view import GraphViewState

CONFIG_NAME = "git-visualizer-settings.ini"
REPO = "C:/repo"
OTHER_REPO = "C:/other"
VIEW = GraphViewState(zoom=1.5, center_x=42.5, center_y=-80.25)


def make_storage(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / CONFIG_NAME), QSettings.Format.IniFormat)


def make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(make_storage(tmp_path))


def save_view(settings: AppSettings, path: str, state: GraphViewState = VIEW) -> None:
    """Репозиторий должен быть в «Недавних»: иначе настройки не сохраняются."""
    settings.add_recent_repository(path)
    settings.set_graph_view_state(path, state)


def view_keys(storage: QSettings) -> set[str]:
    return {
        key for key in storage.allKeys() if key.startswith(f"{GRAPH_VIEW_GROUP}/")
    }


# --- значения по умолчанию ---


def test_empty_settings_have_defaults(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    assert settings.recent_repositories() == ()
    assert settings.window_geometry() is None
    assert settings.main_splitter_state() is None
    assert settings.body_splitter_state() is None
    assert settings.graph_view_state(REPO) is None
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


# --- положение графа привязано к репозиторию ---


def test_graph_view_is_independent_for_each_repository(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    save_view(settings, REPO)
    save_view(settings, OTHER_REPO, GraphViewState(zoom=0.25))

    assert settings.graph_view_state(REPO) == VIEW
    assert settings.graph_view_state(OTHER_REPO) == GraphViewState(zoom=0.25)


def test_graph_view_path_comparison_ignores_separator(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    save_view(settings, REPO)

    assert settings.graph_view_state("C:/repo/") == VIEW


def test_graph_view_is_not_saved_for_repository_outside_recent(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    settings.set_recent_repositories([REPO])

    settings.set_graph_view_state(OTHER_REPO, VIEW)
    settings.sync()

    assert settings.graph_view_state(OTHER_REPO) is None
    assert view_keys(make_storage(tmp_path)) == set()


def test_graph_view_is_pruned_when_repository_leaves_recent(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    save_view(settings, "C:/a")
    save_view(settings, "C:/b")
    save_view(settings, "C:/c")

    settings.remove_recent_repository("C:/b")
    settings.sync()

    assert settings.graph_view_state("C:/a") == VIEW
    assert settings.graph_view_state("C:/b") is None
    assert settings.graph_view_state("C:/c") == VIEW


def test_graph_view_is_pruned_when_evicted_from_recent_list(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    for index in range(MAX_RECENT_REPOSITORIES + 1):
        save_view(settings, f"C:/repo-{index}", GraphViewState(zoom=1.0))

    assert settings.graph_view_state("C:/repo-0") is None
    assert (
        settings.graph_view_state(f"C:/repo-{MAX_RECENT_REPOSITORIES}")
        is not None
    )


# --- сохранение и восстановление между запусками ---


def test_all_settings_survive_restart(tmp_path: Path) -> None:
    geometry = QByteArray(b"window-geometry-state")
    main_state = QByteArray(b"main-splitter-state")
    body_state = QByteArray(b"body-splitter-state")

    settings = make_settings(tmp_path)
    save_view(settings, REPO)
    settings.set_window_geometry(geometry)
    settings.set_main_splitter_state(main_state)
    settings.set_body_splitter_state(body_state)
    settings.set_history_limit(1200)
    settings.sync()

    reopened = make_settings(tmp_path)
    assert reopened.recent_repositories() == (REPO,)
    assert reopened.window_geometry() == geometry
    assert reopened.main_splitter_state() == main_state
    assert reopened.body_splitter_state() == body_state
    assert reopened.graph_view_state(REPO) == VIEW
    assert reopened.history_limit() == 1200


def test_only_interface_state_keys_are_saved(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    save_view(settings, REPO)
    settings.set_window_geometry(QByteArray(b"geometry"))
    settings.set_main_splitter_state(QByteArray(b"main"))
    settings.set_body_splitter_state(QByteArray(b"body"))
    settings.set_history_limit(1500)
    settings.sync()

    keys = set(make_storage(tmp_path).allKeys())
    group_prefix = f"{GRAPH_VIEW_GROUP}/"
    for key in keys:
        if key in SETTINGS_KEYS:
            continue
        assert key.startswith(group_prefix), key
        suffix = key[len(group_prefix):].split("/", 1)[1]
        assert suffix in {"path", "zoom", "center_x", "center_y"}
    # Учётных данных в настройках нет: ни паролей, ни токенов, ни ключей.
    assert not any(
        marker in key.lower()
        for key in keys
        for marker in ("password", "token", "credential", "secret")
    )


# --- настройки прежней версии (один масштаб на всё приложение) ---


def test_legacy_view_moves_to_most_recent_repository(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.setValue("recent/repositories", ["C:/big", "C:/small"])
    storage.setValue("graph/zoom", 0.4)
    storage.setValue("graph/center_x", 10.0)
    storage.setValue("graph/center_y", 20.0)
    settings = AppSettings(storage)

    settings.clean_up_graph_view_states(settings.recent_repositories())

    assert settings.graph_view_state("C:/big") == GraphViewState(
        zoom=0.4, center_x=10.0, center_y=20.0
    )
    assert settings.graph_view_state("C:/small") is None
    assert not any(storage.contains(key) for key in LEGACY_GRAPH_VIEW_KEYS)


def test_legacy_zoom_only_view_moves_to_most_recent_repository(
    tmp_path: Path,
) -> None:
    storage = make_storage(tmp_path)
    storage.setValue("recent/repositories", [REPO])
    storage.setValue("graph/zoom", 0.75)
    settings = AppSettings(storage)

    settings.clean_up_graph_view_states(settings.recent_repositories())

    assert settings.graph_view_state(REPO) == GraphViewState(zoom=0.75)


def test_legacy_view_is_discarded_without_recent_repositories(
    tmp_path: Path,
) -> None:
    storage = make_storage(tmp_path)
    storage.setValue("graph/zoom", 0.4)
    storage.setValue("graph/center_x", 10.0)
    storage.setValue("graph/center_y", 20.0)
    settings = AppSettings(storage)

    settings.clean_up_graph_view_states(settings.recent_repositories())

    assert view_keys(storage) == set()
    assert not any(storage.contains(key) for key in LEGACY_GRAPH_VIEW_KEYS)


def test_legacy_view_does_not_overwrite_repository_view(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.setValue("recent/repositories", [REPO])
    settings = AppSettings(storage)
    settings.set_graph_view_state(REPO, VIEW)
    storage.setValue("graph/zoom", 0.1)
    storage.setValue("graph/center_x", 1.0)
    storage.setValue("graph/center_y", 2.0)

    settings.clean_up_graph_view_states(settings.recent_repositories())

    assert settings.graph_view_state(REPO) == VIEW


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


def test_corrupted_graph_view_state_is_ignored(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    settings = AppSettings(storage)
    settings.set_recent_repositories([REPO])
    group = graph_view_group(REPO)

    storage.setValue(f"{group}/zoom", 1.5)
    storage.setValue(f"{group}/center_x", 10.0)
    storage.setValue(f"{group}/center_y", 20.0)
    assert settings.graph_view_state(REPO) == GraphViewState(
        zoom=1.5, center_x=10.0, center_y=20.0
    )

    storage.setValue(f"{group}/zoom", "wide")
    assert settings.graph_view_state(REPO) is None

    storage.setValue(f"{group}/zoom", 100.0)
    assert settings.graph_view_state(REPO) is None

    storage.setValue(f"{group}/zoom", 1.0)
    storage.setValue(f"{group}/center_y", "far away")
    # Неполная/повреждённая пара центра: сохраняем хотя бы масштаб.
    assert settings.graph_view_state(REPO) == GraphViewState(zoom=1.0)


def test_native_settings_string_values_are_parsed(tmp_path: Path) -> None:
    # Нативный QSettings на Windows возвращает сохранённые дробные числа строками.
    storage = make_storage(tmp_path)
    settings = AppSettings(storage)
    settings.set_recent_repositories([REPO])
    group = graph_view_group(REPO)
    storage.setValue(f"{group}/zoom", "0.5")
    storage.setValue(f"{group}/center_x", "-30.25")
    storage.setValue(f"{group}/center_y", "15.5")

    assert settings.graph_view_state(REPO) == GraphViewState(
        zoom=0.5, center_x=-30.25, center_y=15.5
    )


def test_graph_view_state_can_be_reset(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    save_view(settings, REPO)
    settings.set_graph_view_state(REPO, None)
    settings.sync()

    assert settings.graph_view_state(REPO) is None
    assert view_keys(make_storage(tmp_path)) == set()


def test_corrupted_splitter_and_geometry_values_are_ignored(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.setValue("window/geometry", "not a byte array")
    storage.setValue("window/main_splitter_state", 42)

    assert AppSettings(storage).window_geometry() is None
    assert AppSettings(storage).main_splitter_state() is None
