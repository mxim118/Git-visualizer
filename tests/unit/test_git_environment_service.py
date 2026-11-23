"""Unit-тесты проверки окружения Git (наличие и минимальная версия)."""

from dataclasses import dataclass

import pytest

from git_visualizer.application.services.git_environment_service import (
    MINIMUM_GIT_VERSION,
    MINIMUM_GIT_VERSION_TEXT,
    GitEnvironment,
    GitEnvironmentService,
)
from git_visualizer.infrastructure.git.git_errors import GitNotFoundError


@dataclass(frozen=True)
class FakeVersion:
    major: int
    minor: int
    patch: int = 0
    raw: str = ""


class FakeProvider:
    def __init__(self, version: FakeVersion | None = None, error: BaseException | None = None):
        self._version = version or FakeVersion(2, 54, 0, "git version 2.54.0")
        self._error = error

    def get_version(self) -> FakeVersion:
        if self._error is not None:
            raise self._error
        return self._version


def check(version: FakeVersion) -> GitEnvironment:
    return GitEnvironmentService(FakeProvider(version)).check()


def test_minimum_version_is_two_twenty_three() -> None:
    assert MINIMUM_GIT_VERSION == (2, 23)
    assert MINIMUM_GIT_VERSION_TEXT == "2.23"


@pytest.mark.parametrize(
    ("version", "supported"),
    (
        (FakeVersion(2, 22, 9, "git version 2.22.9"), False),
        (FakeVersion(2, 23, 0, "git version 2.23.0"), True),
        (FakeVersion(2, 43, 0, "git version 2.43.0"), True),
        (FakeVersion(2, 43, 0, "git version 2.43.0.windows.1"), True),
        (FakeVersion(2, 39, 3, "git version 2.39.3 (Apple Git-145)"), True),
        (FakeVersion(2, 40, 0, "git version 2.40"), True),
        (FakeVersion(1, 9, 5, "git version 1.9.5"), False),
        (FakeVersion(3, 0, 0, "git version 3.0.0"), True),
    ),
)
def test_version_is_compared_by_numbers(version: FakeVersion, supported: bool) -> None:
    environment = check(version)

    assert environment.is_supported is supported
    assert environment.version == (version.major, version.minor, version.patch)
    assert environment.version_text == (
        f"{version.major}.{version.minor}.{version.patch}"
    )
    assert environment.raw_version == version.raw
    assert environment.minimum_text == "2.23"


def test_missing_git_error_is_propagated() -> None:
    error = GitNotFoundError("git")
    service = GitEnvironmentService(FakeProvider(error=error))

    with pytest.raises(GitNotFoundError) as raised:
        service.check()

    assert raised.value is error
