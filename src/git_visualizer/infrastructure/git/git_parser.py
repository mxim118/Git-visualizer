"""Разбор вывода Git в доменные модели (без запуска процессов)."""

import re
import secrets
from collections.abc import Callable, Sequence
from datetime import datetime

from git_visualizer.domain.models import Commit, Remote
from git_visualizer.infrastructure.git.git_errors import GitOutputParseError

_FETCH_SUFFIX = " (fetch)"
_PUSH_SUFFIX = " (push)"

# Логин и пароль в http(s)-адресе: https://user:secret@host/... Жадное совпадение
# до последнего «@» в authority-части (в пароле может встретиться «@»).
_HTTP_CREDENTIALS = re.compile(r"^(https?://)[^/?#]*@", re.IGNORECASE)


def strip_url_credentials(url: str) -> str:
    """Удаляет логин и пароль из http(s)-адреса.

    SSH-адреса (``git@github.com:org/repo.git``, ``ssh://git@host/...``) не меняются:
    имя пользователя там не секрет.
    """
    return _HTTP_CREDENTIALS.sub(r"\1", url)


def parse_remotes(output: str) -> tuple[Remote, ...]:
    """Разбирает вывод ``git remote -v``.

    Формат строк: ``<name><TAB><url> (fetch)`` и ``<name><TAB><url> (push)``. Remote без
    адресов выводится строкой ``<name><TAB>`` без суффикса. Порядок remote — как в выводе Git.
    Если push-адресов несколько, берётся первый.
    """
    names: dict[str, None] = {}  # dict как упорядоченное множество
    fetch_urls: dict[str, str] = {}
    push_urls: dict[str, str] = {}

    for line in output.split("\n"):
        line = line.rstrip("\r")
        if not line:
            continue
        name, _, rest = line.partition("\t")
        names.setdefault(name)
        if rest.endswith(_FETCH_SUFFIX):
            url = rest.removesuffix(_FETCH_SUFFIX)
            if url:
                fetch_urls.setdefault(name, strip_url_credentials(url))
        elif rest.endswith(_PUSH_SUFFIX):
            url = rest.removesuffix(_PUSH_SUFFIX)
            if url:
                push_urls.setdefault(name, strip_url_credentials(url))

    return tuple(
        Remote(name=name, fetch_url=fetch_urls.get(name), push_url=push_urls.get(name))
        for name in names
    )


# --- История коммитов (``git log``) ---
#
# Формат вывода. Каждый коммит — одна запись; записи завершаются NUL (``git log -z``),
# а NUL не может быть частью сообщения коммита (Git обрезает сообщение по NUL).
# Поля внутри записи разделены СЛУЧАЙНЫМ разделителем, который создаётся заново при каждом
# запросе (``new_history_separator``). Фиксированный разделитель (например, U+001F) был бы
# небезопасен: Git принимает его и в имени автора, и в subject, и такой коммит молча сдвинул бы
# поля. Случайный разделитель нельзя предугадать, поэтому содержимое репозитория не может ни
# сломать разбор, ни подделать запись.
#
# Порядок полей фиксирован и описан в ``_HISTORY_PLACEHOLDERS``.

_HISTORY_PLACEHOLDERS: tuple[str, ...] = (
    "%H",  # полный hash
    "%h",  # сокращённый hash
    "%P",  # hash родителей через пробел
    "%an",  # автор
    "%ae",  # email автора
    "%aI",  # дата автора, ISO 8601 со смещением
    "%cn",  # committer
    "%ce",  # email committer
    "%cI",  # дата коммита, ISO 8601 со смещением
    "%D",  # refs без обрамления; вместе с ``--decorate=full`` — полные имена
    "%s",  # subject
    "%b",  # body — всё после subject (сам subject в него не входит)
)

_HASH_PATTERN = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")  # SHA-1 и SHA-256
_SHORT_HASH_PATTERN = re.compile(r"[0-9a-f]{4,64}")

# Какие refs попадают в ``Commit.refs`` (ТЗ 5.2, FR-HIST-01): локальные и remote-tracking
# ветки и ``HEAD``. Теги добавляются вместе с ``--tags`` (реализация тегов); служебные
# refs (``refs/stash``) и пометка ``grafted`` отбрасываются.
_BRANCH_REF_PREFIXES = ("refs/heads/", "refs/remotes/")
_HEAD_ARROW = "HEAD -> "


def new_history_separator() -> str:
    """Новый случайный разделитель полей для одного запроса истории."""
    return f"\x1f{secrets.token_hex(8)}\x1f"


def history_log_format(separator: str) -> str:
    """Строка ``--format`` для ``git log``, соответствующая ``parse_history``.

    Управляющие символы разделителя записываются как ``%xNN``, чтобы в командной строке
    процесса не было управляющих символов.
    """
    if "%" in separator:
        raise ValueError("Separator must not contain '%'")
    encoded = "".join(f"%x{ord(char):02x}" if ord(char) < 0x20 else char for char in separator)
    return encoded.join(_HISTORY_PLACEHOLDERS)


def parse_history(
    output: str, *, separator: str, command: Sequence[str] = ()
) -> tuple[Commit, ...]:
    """Разбирает вывод ``git log -z --format=<history_log_format(separator)>``.

    Порядок коммитов сохраняется как в выводе Git (топологический — его обеспечивает
    ``--topo-order``); парсер ничего не сортирует. Пустой вывод — пустая история.

    :param separator: тот же разделитель, что был передан в ``history_log_format``.
    :param command: выполнявшаяся команда — только для сообщений об ошибках.
    :raises GitOutputParseError: запись не соответствует формату (число полей, hash,
        даты). Данные не додумываются: одна нечитаемая запись прерывает разбор.
    """
    records = output.split("\0")
    # Каждая запись завершается NUL, поэтому после последней остаётся пустой хвост.
    if records and records[-1] == "":
        records.pop()
    return tuple(
        _parse_history_record(record, index, separator, command)
        for index, record in enumerate(records)
    )


def _parse_history_record(
    record: str, index: int, separator: str, command: Sequence[str]
) -> Commit:
    def fail(problem: str) -> GitOutputParseError:
        return GitOutputParseError(
            f"Cannot parse git log record #{index}: {problem}", command=command
        )

    fields = record.split(separator)
    if len(fields) != len(_HISTORY_PLACEHOLDERS):
        raise fail(f"expected {len(_HISTORY_PLACEHOLDERS)} fields, got {len(fields)}")
    (
        commit_hash,
        short_hash,
        parents,
        author_name,
        author_email,
        author_date,
        committer_name,
        committer_email,
        committer_date,
        decoration,
        subject,
        body,
    ) = fields

    if not _HASH_PATTERN.fullmatch(commit_hash):
        raise fail(f"invalid commit hash {commit_hash!r}")
    if not _SHORT_HASH_PATTERN.fullmatch(short_hash):
        raise fail(f"invalid short hash {short_hash!r} (commit {commit_hash})")
    parent_hashes = tuple(parents.split())
    for parent in parent_hashes:
        if not _HASH_PATTERN.fullmatch(parent):
            raise fail(f"invalid parent hash {parent!r} (commit {commit_hash})")

    return Commit(
        hash=commit_hash,
        short_hash=short_hash,
        parent_hashes=parent_hashes,
        author_name=author_name,
        author_email=author_email,
        authored_at=_parse_iso_date(author_date, "author date", commit_hash, fail),
        committer_name=committer_name,
        committer_email=committer_email,
        committed_at=_parse_iso_date(committer_date, "committer date", commit_hash, fail),
        subject=subject,
        # %b заканчивается переводом строки, который не часть сообщения.
        body=body.rstrip("\r\n"),
        refs=parse_decoration(decoration),
    )


def _parse_iso_date(
    value: str,
    what: str,
    commit_hash: str,
    fail: Callable[[str], GitOutputParseError],
) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise fail(f"invalid {what} {value!r} (commit {commit_hash})") from None
    if parsed.tzinfo is None:
        raise fail(f"{what} {value!r} has no UTC offset (commit {commit_hash})")
    return parsed


def parse_decoration(decoration: str) -> tuple[str, ...]:
    """Разбирает ``%D`` (при ``--decorate=full``) в кортеж имён refs.

    Формат имён: ``HEAD`` и **полные** имена (``refs/heads/main``,
    ``refs/remotes/origin/main``) — по префиксу видно, ветка это или remote-tracking
    ветка. ``HEAD -> refs/heads/main`` даёт два элемента: ``HEAD`` и ветку; при detached
    HEAD остаётся только ``HEAD``. Порядок — как у Git. Пробелов в именах refs быть не
    может, поэтому разделитель ``", "`` однозначен.
    """
    refs: list[str] = []
    for item in decoration.split(", "):
        if item == "HEAD":
            refs.append("HEAD")
        elif item.startswith(_HEAD_ARROW):
            refs.append("HEAD")
            target = item.removeprefix(_HEAD_ARROW)
            if target.startswith(_BRANCH_REF_PREFIXES):
                refs.append(target)
        elif item.startswith(_BRANCH_REF_PREFIXES):
            refs.append(item)
        # Остальное (``tag: ...``, ``refs/stash``, ``grafted``, пусто) не входит в MVP-историю.
    return tuple(refs)
