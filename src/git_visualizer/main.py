"""Точка входа и composition root приложения Git Visualizer.

Здесь создаются инфраструктурные объекты и Application Services; Presentation
получает готовый контроллер и не зависит от Infrastructure напрямую.
"""

import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from git_visualizer.application.ports import GitRepositoryFactory
from git_visualizer.application.services.branch_service import BranchService
from git_visualizer.application.services.changes_service import ChangesService
from git_visualizer.application.services.commit_service import CommitService
from git_visualizer.application.services.diff_service import DiffService
from git_visualizer.application.services.git_environment_service import (
    GitEnvironmentService,
)
from git_visualizer.application.services.graph_service import GraphService
from git_visualizer.application.services.history_service import HistoryService
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.application.services.remote_service import RemoteService
from git_visualizer.infrastructure.git.git_repository import GitRepository
from git_visualizer.infrastructure.git.git_runner import GitRunner
from git_visualizer.presentation.controllers.repository_controller import (
    RepositoryController,
)
from git_visualizer.presentation.settings import AppSettings
from git_visualizer.presentation.windows.main_window import MainWindow


def build_repository_controller() -> RepositoryController:
    """Создаёт контроллер с настоящими Git-сервисами."""
    runner = GitRunner()

    def factory(path: Path) -> GitRepository:
        return GitRepository(runner, path)

    git_repository_factory: GitRepositoryFactory = factory
    return RepositoryController(
        repository_service=RepositoryService(git_repository_factory),
        history_service=HistoryService(git_repository_factory),
        branch_service=BranchService(git_repository_factory),
        graph_service=GraphService(),
        diff_service=DiffService(git_repository_factory),
        changes_service=ChangesService(git_repository_factory),
        commit_service=CommitService(git_repository_factory),
        remote_service=RemoteService(git_repository_factory),
        git_environment_service=GitEnvironmentService(runner),
    )


def main() -> int:
    """Создаёт QApplication, показывает главное окно и запускает цикл событий."""
    app = QApplication(sys.argv)
    # Организация и приложение задают платформенное место хранения QSettings.
    app.setOrganizationName("GitVisualizer")
    app.setApplicationName("GitVisualizer")

    window = MainWindow(build_repository_controller(), AppSettings.from_default())
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
