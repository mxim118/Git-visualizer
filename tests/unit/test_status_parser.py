"""Unit-тесты разбора ``git status --porcelain=v2 -z``."""

from __future__ import annotations

import pytest

from git_visualizer.domain.enums import FileChangeType
from git_visualizer.infrastructure.git.git_errors import GitOutputParseError
from git_visualizer.infrastructure.git.git_parser import parse_status
from git_visualizer.infrastructure.git.git_repository import build_status_arguments

# Пример реального вывода Git (Git 2.54) для репозитория с разными изменениями.
_HEADERS = (
    "# branch.oid 481dff93cc0332a30761e474b1077ada46df45c4\n"
    "# branch.head main"
)
_BLOB_A = "a" * 40
_BLOB_B = "b" * 40


def _ordinary(xy: str, path: str) -> str:
    return f"1 {xy} N... 100644 100644 100644 {_BLOB_A} {_BLOB_B} {path}"


def _rename(xy: str, path: str, old_path: str) -> str:
    return (
        f"2 {xy} N... 100644 100644 100644 {_BLOB_A} {_BLOB_B} R100 {path}"
        f"\0{old_path}"
    )


def _unmerged(xy: str, path: str) -> str:
    return (
        f"u {xy} N... 100644 100644 100644 100644 "
        f"{_BLOB_A} {_BLOB_B} {_BLOB_A} {path}"
    )


def test_empty_output_means_no_changes() -> None:
    assert parse_status("") == ()


def test_status_arguments_disable_optional_index_lock() -> None:
    # Без этой опции фоновый `git status` берёт .git/index.lock и ломает
    # одновременный `git add`/`commit` пользователя (exit 128).
    arguments = build_status_arguments()

    assert arguments[:2] == ["--no-optional-locks", "status"]
    assert "--porcelain=v2" in arguments
    assert "-z" in arguments
    assert "--untracked-files=normal" in arguments
    assert "--branch" in arguments


def test_branch_headers_and_ignored_entries_are_skipped() -> None:
    output = "\0".join(
        [
            _HEADERS,
            _ordinary(".M", "modified.txt"),
            "! ignored.log",
            "",
        ]
    )

    files = parse_status(output)

    assert [file.path for file in files] == ["modified.txt"]


def test_unstaged_staged_and_both_changes_are_independent() -> None:
    output = "\0".join(
        [
            _ordinary(".M", "worktree.txt"),
            _ordinary("M.", "index.txt"),
            _ordinary("MM", "both.txt"),
            _ordinary("A.", "added.txt"),
            _ordinary(".D", "deleted.txt"),
            _ordinary("T.", "type.txt"),
            "",
        ]
    )

    files = {file.path: file for file in parse_status(output)}

    assert files["worktree.txt"].staged_change is None
    assert files["worktree.txt"].unstaged_change is FileChangeType.MODIFIED
    assert not files["worktree.txt"].is_staged
    assert files["worktree.txt"].is_unstaged

    assert files["index.txt"].staged_change is FileChangeType.MODIFIED
    assert files["index.txt"].unstaged_change is None

    both = files["both.txt"]
    assert both.staged_change is FileChangeType.MODIFIED
    assert both.unstaged_change is FileChangeType.MODIFIED
    assert both.is_staged and both.is_unstaged

    assert files["added.txt"].staged_change is FileChangeType.ADDED
    assert files["deleted.txt"].unstaged_change is FileChangeType.DELETED
    assert files["type.txt"].staged_change is FileChangeType.TYPE_CHANGED


def test_untracked_file_and_directory() -> None:
    output = "\0".join(["?" + " новый файл.txt", "? untracked dir/", ""])

    files = parse_status(output)

    assert files[0].path == "новый файл.txt"
    assert files[0].is_untracked
    assert files[0].staged_change is None
    assert files[0].unstaged_change is None
    assert files[1].path == "untracked dir/"
    assert files[1].is_untracked


def test_rename_entry_keeps_old_and_new_paths() -> None:
    output = "\0".join(
        [_rename("R.", "новый файл.txt", "старый файл.txt"), ""]
    )

    files = parse_status(output)

    assert len(files) == 1
    assert files[0].path == "новый файл.txt"
    assert files[0].old_path == "старый файл.txt"
    assert files[0].staged_change is FileChangeType.RENAMED
    assert files[0].unstaged_change is None


def test_unmerged_entry_is_conflicted_without_section_changes() -> None:
    output = "\0".join([_unmerged("UU", "conflict.txt"), ""])

    files = parse_status(output)

    assert len(files) == 1
    assert files[0].path == "conflict.txt"
    assert files[0].is_conflicted
    assert files[0].staged_change is None
    assert files[0].unstaged_change is None
    assert not files[0].is_staged
    assert not files[0].is_unstaged


def test_paths_with_spaces_and_unicode_are_not_split() -> None:
    path = "каталог с пробелом/файл.txt"
    output = "\0".join([_ordinary("MM", path), _rename("R.", "b c.txt", "a b.txt"), ""])

    files = parse_status(output)

    assert files[0].path == path
    assert files[1].path == "b c.txt"
    assert files[1].old_path == "a b.txt"


@pytest.mark.parametrize(
    ("output", "problem"),
    [
        ("X strange", "unknown record type"),
        (_ordinary(".Z", "file.txt"), "unknown status character"),
        ("1 MW file.txt", "expected 9 fields"),
        ("2 R. N... 100644 100644 100644 a b R100 new.txt", "missing original path"),
        ("? ", "empty path"),
        (_unmerged("XX", "conflict.txt"), "invalid unmerged status"),
    ],
)
def test_malformed_output_is_rejected(output: str, problem: str) -> None:
    with pytest.raises(GitOutputParseError) as raised:
        parse_status(output, command=("git", "status"))

    assert problem in str(raised.value)
