"""Общая подготовка integration-тестов: изолированное окружение и временные репозитории."""

import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from git_visualizer.application.services.git_environment_service import (
    GitEnvironmentService,
)
from git_visualizer.infrastructure.git.git_runner import GitRunner


@pytest.fixture(autouse=True)
def _require_git() -> None:
    if shutil.which("git") is None:
        pytest.skip("Git is not installed")


@pytest.fixture(scope="session", autouse=True)
def _require_supported_git() -> None:
    """Сценарий 24 ТЗ: на Git старее 2.23 integration-тесты не запускаются."""
    if shutil.which("git") is None:
        pytest.skip("Git is not installed")
    environment = GitEnvironmentService(GitRunner()).check()
    if not environment.is_supported:
        pytest.skip(
            f"Git {environment.version_text} is older than the required "
            f"{environment.minimum_text}"
        )


@pytest.fixture(autouse=True)
def _isolated_git_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Тесты не зависят от настроек и окружения машины, на которой запущены."""
    # Если pytest запущен из git-хука, эти переменные перенаправили бы Git на чужой репозиторий.
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"):
        monkeypatch.delenv(name, raising=False)
    # Глобальный и системный конфиг (алиасы, подпись коммитов, safe.directory) не читаются.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    home = str(tmp_path / "home")
    for variable in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(variable, home)
    # Git не ищет репозиторий выше временной папки: «обычная папка» остаётся обычной,
    # даже если системный temp случайно лежит внутри чужого репозитория.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


@pytest.fixture
def git() -> Callable[..., str]:
    """``git(cwd, *args, stdin=None)`` — выполняет команду Git и возвращает stdout."""
    runner = GitRunner(default_timeout=60)

    def run(cwd: Path, *args: str, stdin: str | None = None) -> str:
        return runner.run(list(args), cwd=cwd, stdin_data=stdin).stdout

    return run


@pytest.fixture
def make_repo(tmp_path: Path, git: Callable[..., str]) -> Callable[..., Path]:
    """``make_repo(name, commits=1)`` — создаёт репозиторий с веткой ``main`` и коммитами."""

    def create(name: str = "repo", *, commits: int = 1) -> Path:
        path = tmp_path / name
        path.mkdir(parents=True)
        git(path, "init", "-q")
        # Имя первой ветки задаётся явно: не зависит от init.defaultBranch и версии Git.
        git(path, "symbolic-ref", "HEAD", "refs/heads/main")
        for number in range(commits):
            (path / f"file{number}.txt").write_text(f"{number}\n", encoding="utf-8")
            git(path, "add", "--", ".")
            git(
                path,
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "-c", "commit.gpgsign=false",
                "commit", "-q", "-m", f"commit {number}",
            )
        return path

    return create
