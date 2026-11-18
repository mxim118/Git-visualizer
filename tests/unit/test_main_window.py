from git_visualizer.main import main
from git_visualizer.presentation.windows.main_window import MainWindow


def test_main_window_can_be_created(qt_app) -> None:
    window = MainWindow()

    assert window.windowTitle() == "Git Visualizer"
    assert window.centralWidget() is not None


def test_entry_point_is_callable() -> None:
    assert callable(main)
