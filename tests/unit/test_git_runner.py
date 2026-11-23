"""Тесты GitRunner.

Три группы:
1. С подменой process_runner — проверяют, КАК запускается процесс (аргументы, env, stdin).
2. С реальными процессами, где в роли «git» выступает интерпретатор Python
   (sys.executable) — детерминированно проверяют код выхода, stdin, timeout и т. п.
3. С настоящим Git (пропускаются, если Git не установлен).
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitLaunchError,
    GitNotFoundError,
    GitTimeoutError,
)
from git_visualizer.infrastructure.git.git_runner import (
    GIT_ENV_OVERRIDES,
    GitResult,
    GitRunner,
    GitVersion,
)

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="Git is not installed")


class FakeProcess:
    """Подменяет subprocess.run: запоминает вызовы и возвращает/бросает заданное."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        raises: BaseException | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raises = raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            command, self.returncode, stdout=self.stdout, stderr=self.stderr
        )

    @property
    def command(self) -> list[str]:
        return self.calls[-1][0]

    @property
    def kwargs(self) -> dict:
        return self.calls[-1][1]


@pytest.fixture
def fake() -> FakeProcess:
    return FakeProcess()


@pytest.fixture
def runner(fake: FakeProcess) -> GitRunner:
    return GitRunner(process_runner=fake)


@pytest.fixture
def python_runner() -> GitRunner:
    """Раннер, запускающий Python вместо Git: реальный процесс, управляемое поведение."""
    return GitRunner(git_executable=sys.executable, default_timeout=20)


# --------------------------------------------------------------------------
# 1. Как запускается процесс (подмена process_runner)
# --------------------------------------------------------------------------


def test_command_is_list_starting_with_executable(runner: GitRunner, fake: FakeProcess) -> None:
    runner.run(["status", "--porcelain=v2"])

    assert fake.command == ["git", "status", "--porcelain=v2"]
    assert isinstance(fake.command, list)


def test_shell_is_never_enabled(runner: GitRunner, fake: FakeProcess) -> None:
    runner.run(["status"])

    assert fake.kwargs["shell"] is False


def test_arguments_are_not_joined_into_one_string(runner: GitRunner, fake: FakeProcess) -> None:
    tricky = ["a b", "c;d", "$(whoami)", "&& echo hacked", "--format=%H %s", "'q'", '"dq"', ""]

    runner.run(["log", *tricky])

    # Каждый аргумент остался отдельным элементом списка без изменений.
    assert fake.command == ["git", "log", *tricky]


@pytest.mark.parametrize("bad_args", ["status", b"status"])
def test_single_string_instead_of_list_is_rejected(
    runner: GitRunner, fake: FakeProcess, bad_args
) -> None:
    with pytest.raises(TypeError):
        runner.run(bad_args)

    assert fake.calls == []  # процесс не запускался


def test_non_string_argument_is_rejected(runner: GitRunner, fake: FakeProcess) -> None:
    with pytest.raises(TypeError):
        runner.run(["add", Path("file.txt")])  # type: ignore[list-item]

    assert fake.calls == []


def test_environment_forces_c_locale_and_disables_terminal_prompt(
    runner: GitRunner, fake: FakeProcess, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    monkeypatch.setenv("GV_TEST_MARKER", "kept")

    runner.run(["status"])

    env = fake.kwargs["env"]
    assert env["LC_ALL"] == "C"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GV_TEST_MARKER"] == "kept"  # остальное окружение унаследовано
    # Окружение самого приложения не изменяется.
    assert os.environ["LC_ALL"] == "ru_RU.UTF-8"
    assert dict(GIT_ENV_OVERRIDES) == {"LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}


def test_stdin_is_closed_when_no_data(runner: GitRunner, fake: FakeProcess) -> None:
    runner.run(["status"])

    assert fake.kwargs["stdin"] is subprocess.DEVNULL
    assert "input" not in fake.kwargs


def test_stdin_text_is_encoded_as_utf8(runner: GitRunner, fake: FakeProcess) -> None:
    runner.run(["commit", "-F", "-"], stdin_data="Привет\n")

    assert fake.kwargs["input"] == "Привет\n".encode("utf-8")
    assert "stdin" not in fake.kwargs


def test_stdin_bytes_are_passed_unchanged(runner: GitRunner, fake: FakeProcess) -> None:
    runner.run(["hash-object", "--stdin"], stdin_data=b"\x00\xff")

    assert fake.kwargs["input"] == b"\x00\xff"


def test_cwd_is_passed_per_call(runner: GitRunner, fake: FakeProcess, tmp_path: Path) -> None:
    runner.run(["status"], cwd=tmp_path)
    assert fake.kwargs["cwd"] == str(tmp_path)

    runner.run(["status"])
    assert fake.kwargs["cwd"] is None


def test_timeout_default_and_override(fake: FakeProcess) -> None:
    runner = GitRunner(default_timeout=12.5, process_runner=fake)

    runner.run(["status"])
    assert fake.kwargs["timeout"] == 12.5

    runner.run(["status"], timeout=3)
    assert fake.kwargs["timeout"] == 3


def test_timeout_can_be_disabled_through_constructor(fake: FakeProcess) -> None:
    GitRunner(default_timeout=None, process_runner=fake).run(["status"])

    assert fake.kwargs["timeout"] is None


def test_no_console_window_flag_is_set_only_on_windows(
    runner: GitRunner, fake: FakeProcess, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    runner.run(["status"])
    assert "creationflags" not in fake.kwargs

    monkeypatch.setattr(sys, "platform", "win32")
    runner.run(["status"])
    assert fake.kwargs["creationflags"] == 0x08000000


# --------------------------------------------------------------------------
# 2. Результат и ошибки (подмена process_runner)
# --------------------------------------------------------------------------


def test_successful_result_contains_command_output_and_exit_code() -> None:
    fake = FakeProcess(returncode=0, stdout="вывод\n".encode(), stderr=b"warn\n")

    result = GitRunner(process_runner=fake).run(["status"])

    assert result == GitResult(
        command=("git", "status"), returncode=0, stdout="вывод\n", stderr="warn\n"
    )


def test_nonzero_exit_raises_command_error_with_technical_details() -> None:
    fake = FakeProcess(returncode=128, stdout=b"o", stderr=b"fatal: oops\n")

    with pytest.raises(GitCommandError) as info:
        GitRunner(process_runner=fake).run(["status"])

    error = info.value
    assert error.returncode == 128
    assert error.stdout == "o"
    assert error.stderr == "fatal: oops\n"
    assert error.command == ("git", "status")
    details = error.technical_details()
    assert "128" in details and "fatal: oops" in details and "git status" in details


def test_check_false_returns_nonzero_result_instead_of_raising() -> None:
    fake = FakeProcess(returncode=1)

    result = GitRunner(process_runner=fake).run(["rev-parse", "--verify", "-q", "HEAD"], check=False)

    assert result.returncode == 1


def test_timeout_expired_becomes_git_timeout_error_with_partial_output() -> None:
    original = subprocess.TimeoutExpired(
        cmd="git", timeout=5, output=b"partial", stderr="err".encode()
    )
    fake = FakeProcess(raises=original)

    with pytest.raises(GitTimeoutError) as info:
        GitRunner(process_runner=fake).run(["fetch"])

    error = info.value
    assert error.timeout == 5
    assert error.stdout == "partial"
    assert error.stderr == "err"
    assert error.command == ("git", "fetch")
    assert error.__cause__ is original


def test_missing_executable_becomes_git_not_found_error() -> None:
    original = FileNotFoundError(2, "No such file or directory", "git")
    fake = FakeProcess(raises=original)

    with pytest.raises(GitNotFoundError) as info:
        GitRunner(process_runner=fake).run(["status"])

    assert info.value.executable == "git"
    assert info.value.__cause__ is original


@pytest.mark.parametrize(
    "failure",
    [PermissionError(13, "Permission denied"), OSError(22, "Invalid argument"), ValueError("embedded null byte")],
)
def test_other_launch_failures_become_git_launch_error(failure: BaseException) -> None:
    fake = FakeProcess(raises=failure)

    with pytest.raises(GitLaunchError) as info:
        GitRunner(process_runner=fake).run(["status"])

    assert info.value.__cause__ is failure
    assert not isinstance(info.value, GitNotFoundError)


def test_missing_cwd_is_launch_error_not_git_not_found(
    runner: GitRunner, fake: FakeProcess, tmp_path: Path
) -> None:
    with pytest.raises(GitLaunchError) as info:
        runner.run(["status"], cwd=tmp_path / "does-not-exist")

    assert not isinstance(info.value, GitNotFoundError)
    assert "does-not-exist" in info.value.message
    assert fake.calls == []


def test_file_instead_of_cwd_is_launch_error(runner: GitRunner, tmp_path: Path) -> None:
    a_file = tmp_path / "file.txt"
    a_file.write_text("x")

    with pytest.raises(GitLaunchError):
        runner.run(["status"], cwd=a_file)


# --------------------------------------------------------------------------
# 3. get_version (подмена process_runner)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (b"git version 2.43.0\n", GitVersion(2, 43, 0)),
        (b"git version 2.43.0.windows.1\n", GitVersion(2, 43, 0)),
        (b"git version 2.39.3 (Apple Git-145)\n", GitVersion(2, 39, 3)),
        (b"git version 2.40\n", GitVersion(2, 40, 0)),
    ],
)
def test_get_version_parses_known_formats(output: bytes, expected: GitVersion) -> None:
    version = GitRunner(process_runner=FakeProcess(stdout=output)).get_version()

    assert version == expected
    assert version.raw == output.decode().strip()


def test_get_version_runs_version_flag(runner: GitRunner, fake: FakeProcess) -> None:
    fake.stdout = b"git version 2.43.0\n"

    runner.get_version()

    assert fake.command == ["git", "--version"]


def test_get_version_rejects_output_that_is_not_git() -> None:
    runner = GitRunner(process_runner=FakeProcess(stdout=b"Python 3.12.3\n"))

    with pytest.raises(GitNotFoundError) as info:
        runner.get_version()

    assert "does not look like Git" in info.value.message


def test_get_version_reports_missing_git() -> None:
    runner = GitRunner(process_runner=FakeProcess(raises=FileNotFoundError(2, "No such file")))

    with pytest.raises(GitNotFoundError):
        runner.get_version()


def test_versions_are_comparable_and_ignore_raw_text() -> None:
    assert GitVersion(2, 43, 0) >= GitVersion(2, 23)
    assert GitVersion(2, 9, 5) < GitVersion(2, 23)
    assert GitVersion(2, 43, 0, raw="a") == GitVersion(2, 43, 0, raw="b")


# --------------------------------------------------------------------------
# 4. Реальные процессы (Python в роли «git»)
# --------------------------------------------------------------------------


def test_real_process_success(python_runner: GitRunner) -> None:
    result = python_runner.run(
        ["-c", "import sys; sys.stdout.write('out'); sys.stderr.write('err')"]
    )

    assert (result.returncode, result.stdout, result.stderr) == (0, "out", "err")


def test_real_process_nonzero_exit(python_runner: GitRunner) -> None:
    script = "import sys; sys.stdout.write('o'); sys.stderr.write('e'); sys.exit(3)"

    with pytest.raises(GitCommandError) as info:
        python_runner.run(["-c", script])

    assert (info.value.returncode, info.value.stdout, info.value.stderr) == (3, "o", "e")


def test_real_process_nonzero_exit_with_check_false(python_runner: GitRunner) -> None:
    result = python_runner.run(["-c", "import sys; sys.exit(7)"], check=False)

    assert result.returncode == 7


def test_real_process_timeout_is_enforced(python_runner: GitRunner) -> None:
    started = time.monotonic()

    with pytest.raises(GitTimeoutError) as info:
        python_runner.run(["-c", "import time; time.sleep(30)"], timeout=0.5)

    assert time.monotonic() - started < 10
    assert info.value.timeout == 0.5


def test_real_process_receives_stdin_unchanged(python_runner: GitRunner) -> None:
    echo = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
    message = "Тема коммита\r\n\r\nТело — ёжик 🙂\n"

    result = python_runner.run(["-c", echo], stdin_data=message)

    assert result.stdout == message  # включая \r\n и символы вне BMP


def test_real_process_without_stdin_data_sees_closed_stdin(python_runner: GitRunner) -> None:
    script = "import sys; sys.stdout.write(str(len(sys.stdin.buffer.read())))"

    result = python_runner.run(["-c", script], timeout=10)

    assert result.stdout == "0"  # EOF сразу, а не ожидание ввода


def test_real_process_output_keeps_crlf_and_utf8(python_runner: GitRunner) -> None:
    script = "import sys; sys.stdout.buffer.write('а\\r\\nб\\n'.encode('utf-8'))"

    assert python_runner.run(["-c", script]).stdout == "а\r\nб\n"


def test_real_process_invalid_utf8_is_replaced_not_raised(python_runner: GitRunner) -> None:
    script = "import sys; sys.stdout.buffer.write(b'ok\\xff')"

    assert python_runner.run(["-c", script]).stdout == "ok\ufffd"


def test_real_process_receives_arguments_exactly_as_given(python_runner: GitRunner) -> None:
    """Главная проверка «не shell»: метасимволы, пробелы и кавычки доходят буквально."""
    script = "import sys, json; print(json.dumps(sys.argv[1:]))"
    tricky = ["a b", "c;d", "$(echo injected)", "&& echo injected", "`id`", "'q'", '"dq"', "é ё", "*"]

    result = python_runner.run(["-c", script, *tricky])

    assert json.loads(result.stdout) == tricky


def test_real_process_sees_forced_environment(
    python_runner: GitRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
    script = "import os; print(os.environ['LC_ALL'], os.environ['GIT_TERMINAL_PROMPT'])"

    assert python_runner.run(["-c", script]).stdout.strip() == "C 0"


def test_real_missing_executable_is_git_not_found() -> None:
    runner = GitRunner(git_executable="git-visualizer-no-such-executable")

    with pytest.raises(GitNotFoundError) as info:
        runner.run(["status"])

    assert info.value.executable == "git-visualizer-no-such-executable"
    assert isinstance(info.value.__cause__, FileNotFoundError)


def test_real_cwd_is_applied(python_runner: GitRunner, tmp_path: Path) -> None:
    result = python_runner.run(["-c", "import os; print(os.getcwd())"], cwd=tmp_path)

    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()


# --------------------------------------------------------------------------
# 5. Настоящий Git
# --------------------------------------------------------------------------


@requires_git
def test_real_git_version_is_detected() -> None:
    version = GitRunner().get_version()

    assert version.major >= 2
    assert version.raw.startswith("git version")


@requires_git
def test_real_git_error_is_english_regardless_of_system_locale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    monkeypatch.setenv("LANGUAGE", "ru")
    monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
    # pytest может создать tmp_path внутри рабочего Git-репозитория проекта.
    # Запускаем Git во вложенном каталоге и не даём подняться выше tmp_path:
    # иначе git status найдёт родительский репозиторий и завершится успешно.
    work = tmp_path / "not-a-repository"
    work.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    with pytest.raises(GitCommandError) as info:
        GitRunner().run(["status"], cwd=work)  # work не является репозиторием

    assert info.value.returncode == 128
    assert "not a git repository" in info.value.stderr


@requires_git
def test_real_git_commit_message_via_stdin_survives_unicode(tmp_path: Path) -> None:
    runner = GitRunner()
    runner.run(["init", "-q"], cwd=tmp_path)
    message = "Тема коммита\n\nТело с переносом — ёжик"

    runner.run(
        [
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "-c", "commit.gpgsign=false",
            "commit", "-q", "--allow-empty", "-F", "-",
        ],
        cwd=tmp_path,
        stdin_data=message,
    )
    logged = runner.run(["log", "-1", "--format=%B"], cwd=tmp_path)

    assert logged.stdout.strip() == message
