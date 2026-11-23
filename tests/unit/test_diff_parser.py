"""Unit-тесты разбора списка файлов, бинарного diff и аргументов Git."""

from pathlib import Path

import pytest

from git_visualizer.domain.enums import FileChangeType
from git_visualizer.infrastructure.git.git_errors import GitOutputParseError
from git_visualizer.infrastructure.git.git_parser import (
    is_binary_diff,
    parse_name_status,
)
from git_visualizer.infrastructure.git.git_repository import (
    GitRepository,
    build_commit_diff_arguments,
    build_commit_files_arguments,
    _truncate_diff,
)
from git_visualizer.infrastructure.git.git_runner import GitResult

H1, H2 = "1" * 40, "2" * 40


def test_empty_name_status_is_empty() -> None:
    assert parse_name_status("") == ()


def test_regular_statuses_and_paths_with_spaces_and_unicode() -> None:
    output = (
        "A\0new file.txt\0"
        "M\0папка/файл с пробелом.py\0"
        "D\0old.txt\0"
        "T\0link\0"
    )

    changes = parse_name_status(output)

    assert [(c.path, c.change_type, c.old_path) for c in changes] == [
        ("new file.txt", FileChangeType.ADDED, None),
        ("папка/файл с пробелом.py", FileChangeType.MODIFIED, None),
        ("old.txt", FileChangeType.DELETED, None),
        ("link", FileChangeType.TYPE_CHANGED, None),
    ]


def test_rename_and_copy_keep_both_paths() -> None:
    output = (
        "R100\0old name.txt\0new name.txt\0"
        "C075\0источник.txt\0копия.txt\0"
        "M\0with\nnewline.txt\0"
    )

    changes = parse_name_status(output)

    assert [(c.path, c.change_type, c.old_path) for c in changes] == [
        ("new name.txt", FileChangeType.RENAMED, "old name.txt"),
        ("копия.txt", FileChangeType.COPIED, "источник.txt"),
        ("with\nnewline.txt", FileChangeType.MODIFIED, None),
    ]


@pytest.mark.parametrize(
    "output",
    (
        "X\0file.txt\0",
        "R100\0old.txt\0",
        "M\0\0",
        "Rabc\0old.txt\0new.txt\0",
        "\0",
    ),
)
def test_invalid_name_status_is_parse_error(output: str) -> None:
    with pytest.raises(GitOutputParseError):
        parse_name_status(output, command=("git", "diff-tree"))


def test_binary_diff_is_detected_in_git_message() -> None:
    output = (
        "diff --git a/image.png b/image.png\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "Binary files /dev/null and b/image.png differ\n"
    )

    assert is_binary_diff(output) is True


def test_text_line_that_looks_like_binary_message_is_not_binary() -> None:
    output = (
        "diff --git a/file.txt b/file.txt\n"
        "--- a/file.txt\n"
        "+++ b/file.txt\n"
        "@@ -1 +1 @@\n"
        "+Binary files a/x and b/x differ\n"
    )

    assert is_binary_diff(output) is False


def test_commit_files_arguments_compare_with_explicit_parent() -> None:
    arguments = build_commit_files_arguments(commit_hash=H2, parent_hash=H1)

    assert arguments[:6] == [
        "diff-tree",
        "--no-commit-id",
        "-r",
        "--name-status",
        "-z",
        "--find-renames",
    ]
    assert arguments[6] == "--find-copies-harder"
    assert arguments[-3:] == [H1, H2, "--"]


def test_commit_files_arguments_for_root_use_root_option() -> None:
    arguments = build_commit_files_arguments(commit_hash=H1, parent_hash=None)

    assert "--root" in arguments
    assert arguments[-3:] == ["--root", H1, "--"]


def test_commit_diff_arguments_for_regular_commit() -> None:
    arguments = build_commit_diff_arguments(
        commit_hash=H2,
        parent_hash=H1,
        paths=["old name.txt", "new name.txt"],
    )

    assert arguments[:8] == [
        "-c",
        "core.quotePath=false",
        "diff",
        "--no-ext-diff",
        "--no-color",
        "--no-textconv",
        H1,
        H2,
    ]
    assert arguments[-3:] == ["--", "old name.txt", "new name.txt"]


def test_commit_diff_arguments_for_root_commit_use_diff_tree() -> None:
    arguments = build_commit_diff_arguments(
        commit_hash=H1, parent_hash=None, paths=["file.txt"]
    )

    assert arguments[:3] == ["-c", "core.quotePath=false", "diff-tree"]
    assert "--root" in arguments
    assert arguments[-3:] == [H1, "--", "file.txt"]


def test_commit_diff_arguments_require_a_path() -> None:
    with pytest.raises(ValueError):
        build_commit_diff_arguments(commit_hash=H2, parent_hash=H1, paths=[])


def test_truncate_diff_keeps_line_boundary() -> None:
    text = "line one\nline two\nline three\n"

    cut, truncated = _truncate_diff(text, 15)

    assert truncated is True
    assert cut == "line one\n"
    assert len(cut) <= 15


def test_truncate_diff_does_not_touch_short_text() -> None:
    assert _truncate_diff("short\n", 100) == ("short\n", False)


class FakeRunner:
    def __init__(self, result: GitResult) -> None:
        self.result = result
        self.calls: list[tuple[list[str], Path | None]] = []

    def run(self, args, *, cwd=None, check=True):
        self.calls.append((list(args), cwd))
        return self.result


def fake_repository(output: str, *, returncode: int = 0) -> tuple[GitRepository, FakeRunner]:
    runner = FakeRunner(
        GitResult(
            command=("git", "test"),
            returncode=returncode,
            stdout=output,
            stderr="",
        )
    )
    return GitRepository(runner, Path("repo")), runner


def test_repository_parses_commit_files() -> None:
    repository, runner = fake_repository("R100\0old.txt\0new.txt\0")

    changes = repository.get_commit_files(commit_hash=H2, parent_hash=H1)

    assert changes[0].change_type is FileChangeType.RENAMED
    assert runner.calls[0][0][-3:] == [H1, H2, "--"]


def test_repository_returns_binary_diff_without_text() -> None:
    output = "diff --git a/x b/x\nBinary files a/x and b/x differ\n"
    repository, _ = fake_repository(output)

    diff = repository.get_commit_file_diff(
        commit_hash=H2,
        parent_hash=H1,
        path="x",
        old_path=None,
        max_chars=1000,
    )

    assert diff.is_binary is True
    assert diff.text == ""
    assert diff.base_commit_hash == H1


def test_repository_truncates_long_diff() -> None:
    repository, _ = fake_repository("line1\nline2\nline3\n")

    diff = repository.get_commit_file_diff(
        commit_hash=H2,
        parent_hash=H1,
        path="x",
        old_path=None,
        max_chars=10,
    )

    assert diff.is_truncated is True
    assert diff.text == "line1\n"
    assert len(diff.text) <= 10


def test_repository_passes_both_paths_for_rename() -> None:
    repository, runner = fake_repository("diff --git a/old b/new\n")

    repository.get_commit_file_diff(
        commit_hash=H2,
        parent_hash=H1,
        path="new name.txt",
        old_path="old name.txt",
        max_chars=1000,
    )

    arguments = runner.calls[0][0]
    assert arguments[-3:] == ["--", "old name.txt", "new name.txt"]


def test_repository_rejects_non_positive_diff_limit() -> None:
    repository, _ = fake_repository("")

    with pytest.raises(ValueError):
        repository.get_commit_file_diff(
            commit_hash=H2,
            parent_hash=H1,
            path="x",
            old_path=None,
            max_chars=0,
        )
