"""Разбор вывода ``git log`` и сборка его аргументов — без запуска Git."""

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from git_visualizer.infrastructure.git.git_errors import GitOutputParseError
from git_visualizer.infrastructure.git.git_parser import (
    _HISTORY_PLACEHOLDERS,
    history_log_format,
    new_history_separator,
    parse_decoration,
    parse_history,
)
from git_visualizer.infrastructure.git.git_repository import (
    GitRepository,
    build_history_arguments,
)

SEP = "\x1fabc123\x1f"  # в unit-тестах разделитель фиксирован, чтобы строить записи вручную
H1, H2, H3 = "1" * 40, "2" * 40, "3" * 40


def record(**overrides: str) -> str:
    """Одна запись в том виде, в каком её выводит ``git log -z`` (с завершающим NUL)."""
    fields = {
        "hash": H1,
        "short": H1[:7],
        "parents": "",
        "an": "Ann",
        "ae": "ann@example.com",
        "ad": "2024-01-02T03:04:05+01:00",
        "cn": "Carl",
        "ce": "carl@example.com",
        "cd": "2024-01-03T10:00:00-05:30",
        "refs": "",
        "subject": "Subject",
        "body": "",
    }
    fields.update(overrides)
    return SEP.join(fields.values()) + "\0"


def parse(output: str):
    return parse_history(output, separator=SEP)


# --- базовый разбор ---


def test_empty_output_is_empty_history() -> None:
    assert parse("") == ()


def test_single_record_fills_all_fields() -> None:
    (commit,) = parse(
        record(
            parents=H2,
            refs="HEAD -> refs/heads/main",
            subject="Fix bug",
            body="Details\n",
        )
    )

    assert commit.hash == H1
    assert commit.short_hash == H1[:7]
    assert commit.parent_hashes == (H2,)
    assert (commit.author_name, commit.author_email) == ("Ann", "ann@example.com")
    assert (commit.committer_name, commit.committer_email) == ("Carl", "carl@example.com")
    assert commit.subject == "Fix bug"
    assert commit.body == "Details"
    assert commit.refs == ("HEAD", "refs/heads/main")


def test_dates_are_aware_and_keep_their_offsets() -> None:
    (commit,) = parse(record())

    assert commit.authored_at == datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=1)))
    assert commit.authored_at.utcoffset() == timedelta(hours=1)
    assert commit.committed_at.utcoffset() == -timedelta(hours=5, minutes=30)


def test_root_commit_has_no_parents() -> None:
    (commit,) = parse(record(parents=""))

    assert commit.parent_hashes == ()
    assert commit.is_root


def test_merge_commit_keeps_parent_order() -> None:
    (commit,) = parse(record(parents=f"{H3} {H2}"))

    assert commit.parent_hashes == (H3, H2)
    assert commit.is_merge


def test_sha256_hashes_are_accepted() -> None:
    long_hash = "a" * 64

    (commit,) = parse(record(hash=long_hash, short=long_hash[:10], parents="b" * 64))

    assert commit.hash == long_hash
    assert commit.parent_hashes == ("b" * 64,)


def test_order_of_records_is_preserved_and_not_sorted_by_date() -> None:
    # Родитель (H2) «новее» потомка (H1) по дате: сортировка по дате поставила бы его первым.
    output = (
        record(hash=H1, parents=H2, cd="2020-01-01T00:00:00+00:00")
        + record(hash=H2, parents="", cd="2030-01-01T00:00:00+00:00")
        + record(hash=H3, parents="", cd="2010-01-01T00:00:00+00:00")
    )

    assert [commit.hash for commit in parse(output)] == [H1, H2, H3]


# --- subject и body: произвольное содержимое ---


def test_body_trailing_newlines_are_removed_but_inner_formatting_is_kept() -> None:
    body = "First paragraph\n\n  indented code\n\tтаб\n\nLast line\n"

    (commit,) = parse(record(body=body))

    assert commit.body == "First paragraph\n\n  indented code\n\tтаб\n\nLast line"


def test_crlf_body_is_not_corrupted() -> None:
    (commit,) = parse(record(body="line1\r\nline2\r\n"))

    assert commit.body == "line1\r\nline2"


def test_empty_body() -> None:
    (commit,) = parse(record(subject="Only subject", body=""))

    assert commit.subject == "Only subject"
    assert commit.body == ""


@pytest.mark.parametrize(
    "text",
    [
        "Исправлена ошибка «Привет, мир» — 日本語 😀",
        'quotes " \' backslash \\ percent %H %x00 %% <tag> & {braces}',
        "tab\tinside and trailing spaces   ",
        "\x1b[31mansi\x1b[0m",
        "RS \x1e and US \x1f in text",  # управляющие символы, которые часто берут в разделители
    ],
)
def test_arbitrary_text_in_subject_body_and_names_survives(text: str) -> None:
    (commit,) = parse(record(subject=text, body=text + "\n", an=text, ae=text, cn=text, ce=text))

    assert commit.subject == text
    assert commit.body == text
    assert commit.author_name == text
    assert commit.author_email == text
    assert commit.committer_name == text
    assert commit.committer_email == text


def test_text_equal_to_a_fixed_separator_does_not_break_parsing() -> None:
    # Раньше фиксированный разделитель сдвигал бы поля; случайный разделитель — нет.
    (commit,) = parse(record(an="A\x1fB", subject="S\x1fT", body="x\x1fy\x1fz\n"))

    assert commit.author_name == "A\x1fB"
    assert commit.subject == "S\x1fT"
    assert commit.body == "x\x1fy\x1fz"


def test_separator_is_unique_per_request() -> None:
    separators = {new_history_separator() for _ in range(50)}

    assert len(separators) == 50
    assert all(s.startswith("\x1f") and s.endswith("\x1f") for s in separators)


# --- refs ---


@pytest.mark.parametrize(
    ("decoration", "expected"),
    [
        ("", ()),
        ("HEAD", ("HEAD",)),  # detached HEAD
        ("HEAD -> refs/heads/main", ("HEAD", "refs/heads/main")),
        (
            "HEAD -> refs/heads/main, refs/remotes/origin/main, refs/heads/feature/x",
            ("HEAD", "refs/heads/main", "refs/remotes/origin/main", "refs/heads/feature/x"),
        ),
        ("refs/remotes/origin/HEAD", ("refs/remotes/origin/HEAD",)),
        # теги и служебные refs в историю MVP не входят
        ("tag: refs/tags/v1, refs/heads/main", ("refs/heads/main",)),
        ("refs/stash", ()),
        ("grafted, HEAD -> refs/heads/main", ("HEAD", "refs/heads/main")),
        ("refs/heads/ветка-ё", ("refs/heads/ветка-ё",)),
    ],
)
def test_parse_decoration(decoration: str, expected: tuple[str, ...]) -> None:
    assert parse_decoration(decoration) == expected


# --- некорректный вывод: ошибка, а не «угаданные» данные ---


@pytest.mark.parametrize(
    "bad",
    [
        record().replace(SEP, "|", 1),  # не хватает полей
        record() + "garbage\0",  # запись, не похожая на коммит
        record(hash="xyz"),
        record(hash=H1[:-1]),
        record(short="zz"),
        record(parents=f"{H2} not-a-hash"),
        record(ad="yesterday"),
        record(cd=""),
        record(ad="2024-01-02T03:04:05"),  # без смещения
    ],
)
def test_malformed_output_raises_parse_error_with_command(bad: str) -> None:
    with pytest.raises(GitOutputParseError) as info:
        parse_history(bad, separator=SEP, command=("git", "log"))

    assert info.value.command == ("git", "log")
    assert "Command: git log" in info.value.technical_details()


def test_parse_error_names_the_broken_record() -> None:
    with pytest.raises(GitOutputParseError, match="record #1"):
        parse(record() + record(hash="broken"))


# --- формат и аргументы git log ---


def test_format_matches_parser_field_count() -> None:
    separator = new_history_separator()

    fmt = history_log_format(separator)

    assert fmt.count("%x1f") == 2 * (len(_HISTORY_PLACEHOLDERS) - 1)
    assert all(ord(char) >= 0x20 for char in fmt), "в аргументах нет управляющих символов"
    assert fmt.startswith("%H%x1f")
    assert fmt.endswith("%s" + _encoded(separator) + "%b")


def _encoded(separator: str) -> str:
    return "".join(f"%x{ord(c):02x}" if ord(c) < 0x20 else c for c in separator)


def test_format_rejects_separator_with_percent() -> None:
    with pytest.raises(ValueError):
        history_log_format("%x1f")


def test_arguments_with_head() -> None:
    args = build_history_arguments(include_head=True, limit=2500, separator=SEP)

    assert args[:4] == ["log", "--branches", "--remotes", "HEAD"]
    assert args[-1] == "--"
    assert args[-2] == f"--format={history_log_format(SEP)}"
    assert "--topo-order" in args
    assert "--max-count=2500" in args
    assert "--decorate=full" in args
    assert {"--decorate-refs=HEAD", "--decorate-refs=refs/heads/", "--decorate-refs=refs/remotes/"} <= set(args)
    assert "--encoding=UTF-8" in args
    assert "-z" in args


def test_arguments_without_head() -> None:
    args = build_history_arguments(include_head=False, limit=10, separator=SEP)

    assert "HEAD" not in args
    assert args[:3] == ["log", "--branches", "--remotes"]


def test_arguments_never_use_all_refs() -> None:
    # --all добавил бы refs/stash и служебные коммиты stash в граф (ТЗ FR-HIST-01).
    for include_head in (True, False):
        args = build_history_arguments(include_head=include_head, limit=5, separator=SEP)
        assert "--all" not in args
        assert not any(arg.startswith("--tags") for arg in args)


@pytest.mark.parametrize("limit", [0, -1])
def test_arguments_reject_non_positive_limit(limit: int) -> None:
    with pytest.raises(ValueError):
        build_history_arguments(include_head=True, limit=limit, separator=SEP)


def test_infrastructure_has_no_default_limit() -> None:
    # Лимит задаётся в одном месте (application.services.history_service).
    parameter = inspect.signature(GitRepository.get_history).parameters["limit"]

    assert parameter.default is inspect.Parameter.empty
