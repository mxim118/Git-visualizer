"""TopBar: выбор remote, доступность кнопок и индикация сетевых операций."""

from pathlib import Path

from git_visualizer.domain.models import Remote, Repository
from git_visualizer.presentation.widgets.top_bar import TopBar

HEAD = "a" * 40


def repository(
    *, branch: str | None = "main", head: str | None = HEAD
) -> Repository:
    return Repository(
        path=Path("C:/projects/repo"), name="repo", current_branch=branch, head_commit=head
    )


def test_remote_actions_are_disabled_before_remotes_are_known(qt_app) -> None:
    bar = TopBar()

    bar.set_repository(repository())

    assert not bar.fetch_button.isEnabled()
    assert not bar.pull_button.isEnabled()
    assert not bar.push_button.isEnabled()
    assert bar.selected_remote is None


def test_remotes_fill_combo_and_enable_actions(qt_app) -> None:
    bar = TopBar()
    bar.set_repository(repository())

    bar.set_remotes(
        (
            Remote(name="fork", fetch_url="git@example.com:me/fork.git"),
            Remote(name="origin", fetch_url="https://example.com/r.git"),
        )
    )

    assert bar.remote_names == ("fork", "origin")
    assert bar.remote_combo.count() == 2
    # origin выбирается по умолчанию, если он есть.
    assert bar.selected_remote == "origin"
    assert bar.fetch_button.isEnabled()
    assert bar.pull_button.isEnabled()
    assert bar.push_button.isEnabled()


def test_selected_remote_is_preserved_when_remotes_are_reloaded(qt_app) -> None:
    bar = TopBar()
    bar.set_repository(repository())
    bar.set_remotes((Remote(name="origin"), Remote(name="fork")))

    bar.remote_combo.setCurrentIndex(bar.remote_combo.findText("fork"))
    bar.set_remotes((Remote(name="fork"), Remote(name="origin")))

    assert bar.selected_remote == "fork"


def test_no_remotes_disables_actions_with_clear_tooltips(qt_app) -> None:
    bar = TopBar()
    bar.set_repository(repository())

    bar.set_remotes(())

    assert not bar.fetch_button.isEnabled()
    assert "ни один remote" in bar.fetch_button.toolTip()
    assert not bar.push_button.isEnabled()


def test_detached_head_and_unborn_repository_disable_pull_and_push(qt_app) -> None:
    bar = TopBar()
    bar.set_remotes((Remote(name="origin"),))

    bar.set_repository(repository(branch=None))
    assert not bar.pull_button.isEnabled()
    assert not bar.push_button.isEnabled()
    assert "detached HEAD" in bar.pull_button.toolTip()

    bar.set_repository(repository(head=None))
    assert not bar.pull_button.isEnabled()
    assert not bar.push_button.isEnabled()
    assert "нет коммитов" in bar.pull_button.toolTip()


def test_busy_state_blocks_all_actions_and_shows_message(qt_app) -> None:
    bar = TopBar()
    bar.set_repository(repository())
    bar.set_remotes((Remote(name="origin"),))

    bar.set_busy(True, "Fetch «origin»: выполняется…")

    assert not bar.fetch_button.isEnabled()
    assert not bar.pull_button.isEnabled()
    assert not bar.push_button.isEnabled()
    assert not bar.refresh_button.isEnabled()
    assert not bar.status_label.isHidden()
    assert "Fetch" in bar.status_label.text()

    bar.set_busy(False)
    assert bar.fetch_button.isEnabled()


def test_remote_result_survives_busy_cycle(qt_app) -> None:
    bar = TopBar()
    bar.set_repository(repository())
    bar.set_remotes((Remote(name="origin"),))

    bar.set_busy(True, "Push: выполняется…")
    bar.show_remote_result("Push «main»: выполнено.", details="To origin")
    bar.set_busy(False)

    assert not bar.remote_status_label.isHidden()
    assert bar.remote_status_label.text() == "Push «main»: выполнено."
    assert bar.remote_status_label.toolTip() == "To origin"

    bar.clear_remote_status()
    assert bar.remote_status_label.isHidden()


def test_buttons_emit_signals(qt_app) -> None:
    bar = TopBar()
    bar.set_repository(repository())
    bar.set_remotes((Remote(name="origin"),))
    calls: list[str] = []
    bar.fetch_requested.connect(lambda: calls.append("fetch"))
    bar.pull_requested.connect(lambda: calls.append("pull"))
    bar.push_requested.connect(lambda: calls.append("push"))

    bar.fetch_button.click()
    bar.pull_button.click()
    bar.push_button.click()

    assert calls == ["fetch", "pull", "push"]


def test_remote_settings_button_requires_open_repository(qt_app) -> None:
    bar = TopBar()

    assert not bar.remote_settings_button.isEnabled()
    assert bar.remote_settings_button.text() == "Настройки remote"

    bar.set_repository(repository())

    assert bar.remote_settings_button.isEnabled()
    assert "Git CLI" in bar.remote_settings_button.toolTip()


def test_remote_settings_button_is_available_without_remotes(qt_app) -> None:
    bar = TopBar()
    bar.set_repository(repository())

    bar.set_remotes(())

    # Даже без remote окно настроек нужно, чтобы добавить первый remote.
    assert bar.remote_settings_button.isEnabled()
    assert not bar.fetch_button.isEnabled()


def test_remote_settings_button_is_disabled_while_busy_and_emits_signal(
    qt_app,
) -> None:
    bar = TopBar()
    bar.set_repository(repository())
    calls: list[bool] = []
    bar.remote_settings_requested.connect(lambda: calls.append(True))

    bar.set_busy(True, "Операция Git…")
    assert not bar.remote_settings_button.isEnabled()

    bar.set_busy(False)
    bar.remote_settings_button.click()

    assert calls == [True]


def test_clear_disables_remote_settings_button(qt_app) -> None:
    bar = TopBar()
    bar.set_repository(repository())

    bar.clear()

    assert not bar.remote_settings_button.isEnabled()
    assert "не открыт" in bar.remote_settings_button.toolTip()


def test_clear_resets_remotes_and_status(qt_app) -> None:
    bar = TopBar()
    bar.set_repository(repository())
    bar.set_remotes((Remote(name="origin"),))
    bar.show_remote_result("Fetch «origin»: выполнено.")

    bar.clear()

    assert bar.remote_combo.count() == 0
    assert bar.selected_remote is None
    assert bar.remote_status_label.isHidden()
    assert not bar.fetch_button.isEnabled()
