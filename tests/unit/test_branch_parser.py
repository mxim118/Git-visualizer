"""Разбор вывода ``git for-each-ref`` для веток — без запуска Git."""

import pytest

from git_visualizer.infrastructure.git.git_errors import GitOutputParseError
from git_visualizer.infrastructure.git.git_parser import (
    BRANCH_FORMAT,
    BRANCH_PATTERNS,
    parse_branches,
    parse_upstream,
)

H1, H2, H3 = "1" * 40, "2" * 40, "3" * 40


def line(ref: str, commit: str = H1, head: str = " ", upstream: str = "", symref: str = "") -> str:
    """Одна строка вывода в формате ``BRANCH_FORMAT`` (поля разделены NUL)."""
    return "\0".join((ref, commit, head, upstream, symref))


def parse(*lines: str):
    return parse_branches("".join(entry + "\n" for entry in lines))


# --- базовый разбор ---


def test_empty_output_means_no_branches() -> None:
    assert parse_branches("") == ()


def test_local_branch_fields() -> None:
    (branch,) = parse(line("refs/heads/main", H1))

    assert branch.name == "main"
    assert branch.is_remote is False
    assert branch.commit_hash == H1
    assert branch.is_current is False
    assert branch.upstream is None


def test_current_branch_is_marked_by_asterisk_only() -> None:
    main, feature = parse(
        line("refs/heads/feature", H2, head=" "),
        line("refs/heads/main", H1, head="*"),
    )[::-1]

    assert main.is_current is True
    assert feature.is_current is False


def test_remote_tracking_branch_name_keeps_remote_prefix() -> None:
    (branch,) = parse(line("refs/remotes/origin/main", H2))

    assert branch.name == "origin/main"
    assert branch.is_remote is True
    assert branch.is_current is False
    assert branch.upstream is None
    assert branch.commit_hash == H2


def test_slashes_in_branch_names_are_kept() -> None:
    local, remote = parse(
        line("refs/heads/feature/login/ui"),
        line("refs/remotes/origin/feature/login/ui"),
    )

    assert local.name == "feature/login/ui"
    assert remote.name == "origin/feature/login/ui"


def test_remote_branch_never_reports_current_or_upstream_even_if_git_does() -> None:
    (branch,) = parse(line("refs/remotes/origin/main", head="*", upstream="refs/remotes/x/y"))

    assert branch.is_current is False
    assert branch.upstream is None


def test_order_of_git_output_is_preserved() -> None:
    branches = parse(
        line("refs/heads/b", H1),
        line("refs/heads/a", H2),
        line("refs/remotes/origin/z", H3),
        line("refs/remotes/origin/a", H1),
    )

    assert [b.name for b in branches] == ["b", "a", "origin/z", "origin/a"]


def test_sha256_hashes_are_accepted() -> None:
    (branch,) = parse(line("refs/heads/main", "a" * 64))

    assert branch.commit_hash == "a" * 64


# --- символические ссылки ---


def test_symbolic_refs_are_not_branches() -> None:
    branches = parse(
        line("refs/heads/alias", symref="refs/heads/main"),
        line("refs/heads/main", H1, head="*"),
        line("refs/remotes/origin/HEAD", H1, symref="refs/remotes/origin/main"),
        line("refs/remotes/origin/main", H1),
    )

    assert [b.name for b in branches] == ["main", "origin/main"]


# --- upstream ---


@pytest.mark.parametrize(
    ("full", "expected"),
    [
        ("", None),
        ("refs/remotes/origin/main", "origin/main"),
        ("refs/remotes/origin/feature/x", "origin/feature/x"),
        ("refs/heads/main", "main"),  # upstream может быть локальной веткой (remote = ".")
        ("refs/remotes/", None),
        ("refs/tags/v1", "refs/tags/v1"),  # неожиданная цель остаётся как есть
    ],
)
def test_parse_upstream(full: str, expected: str | None) -> None:
    assert parse_upstream(full) == expected


def test_upstream_is_reported_even_if_the_target_does_not_exist_anymore() -> None:
    # Git отдаёт настроенное имя и для «gone»-upstream; в списке такой ветки может не быть.
    (branch,) = parse(line("refs/heads/feature", upstream="refs/remotes/origin/feature"))

    assert branch.upstream == "origin/feature"


# --- произвольные имена ---


@pytest.mark.parametrize(
    "name",
    [
        "ветка-ё",
        "emoji-😀",
        "with space-like\u00a0nbsp",
        "line\u2028separator",  # str.splitlines() разрезал бы запись по этому символу
        "next\u0085line",
        "percent%x-and-%(refname)",
        "dots.and-dashes_1.0",
    ],
)
def test_unusual_branch_names_survive(name: str) -> None:
    local, remote = parse(line(f"refs/heads/{name}", H1), line(f"refs/remotes/origin/{name}", H2))

    assert local.name == name
    assert remote.name == f"origin/{name}"


def test_crlf_line_endings_are_tolerated() -> None:
    output = line("refs/heads/main", H1, head="*") + "\r\n" + line("refs/heads/dev", H2) + "\r\n"

    main, dev = parse_branches(output)

    assert (main.name, main.is_current) == ("main", True)
    assert dev.name == "dev"


def test_output_without_trailing_newline_is_accepted() -> None:
    (branch,) = parse_branches(line("refs/heads/main"))

    assert branch.name == "main"


# --- некорректный вывод ---


@pytest.mark.parametrize(
    "bad",
    [
        "refs/heads/main\0" + H1 + "\n",  # не хватает полей
        line("refs/heads/main", "xyz") + "\n",  # неверный hash
        line("refs/heads/main", H1[:-1]) + "\n",
        line("refs/tags/v1") + "\n",  # ref вне refs/heads и refs/remotes
        line("HEAD") + "\n",
        line("refs/heads/") + "\n",  # пустое имя
        "garbage\n",
    ],
)
def test_malformed_output_raises_parse_error_with_command(bad: str) -> None:
    with pytest.raises(GitOutputParseError) as info:
        parse_branches(bad, command=("git", "for-each-ref"))

    assert info.value.command == ("git", "for-each-ref")
    assert "Command: git for-each-ref" in info.value.technical_details()


def test_parse_error_names_the_broken_record() -> None:
    with pytest.raises(GitOutputParseError, match="record #1"):
        parse_branches(line("refs/heads/ok") + "\n" + "broken\n")


# --- формат и аргументы ---


def test_format_requests_fields_in_parser_order() -> None:
    fields = BRANCH_FORMAT.split("%00")

    assert fields == ["%(refname)", "%(objectname)", "%(HEAD)", "%(upstream)", "%(symref)"]


def test_format_uses_full_upstream_not_the_ambiguous_short_form() -> None:
    assert "upstream:short" not in BRANCH_FORMAT


def test_only_branch_namespaces_are_requested() -> None:
    # Теги и refs/stash не запрашиваются.
    assert BRANCH_PATTERNS == ("refs/heads", "refs/remotes")
