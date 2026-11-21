import pytest

from git_visualizer.domain.models import Remote
from git_visualizer.infrastructure.git.git_parser import parse_remotes, strip_url_credentials

# --- strip_url_credentials ---


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://user:secret@example.com/org/repo.git", "https://example.com/org/repo.git"),
        ("https://TOKEN@github.com/org/repo.git", "https://github.com/org/repo.git"),
        ("http://user:pa@ss@host/repo.git", "http://host/repo.git"),  # «@» в пароле
        ("HTTPS://user:secret@Example.com/r.git", "HTTPS://Example.com/r.git"),
        # без изменений:
        ("https://example.com/org/repo.git", "https://example.com/org/repo.git"),
        ("https://example.com/path@version/repo.git", "https://example.com/path@version/repo.git"),
        ("git@github.com:me/repo.git", "git@github.com:me/repo.git"),
        ("ssh://git@host.example/me/repo.git", "ssh://git@host.example/me/repo.git"),
        ("/srv/git/repo with spaces.git", "/srv/git/repo with spaces.git"),
        ("C:\\repos\\проект.git", "C:\\repos\\проект.git"),
        ("", ""),
    ],
)
def test_strip_url_credentials(url: str, expected: str) -> None:
    assert strip_url_credentials(url) == expected


# --- parse_remotes ---


def test_no_remotes() -> None:
    assert parse_remotes("") == ()


def test_single_remote_with_fetch_and_push() -> None:
    output = (
        "origin\thttps://example.com/org/repo.git (fetch)\n"
        "origin\thttps://example.com/org/repo.git (push)\n"
    )

    assert parse_remotes(output) == (
        Remote(
            name="origin",
            fetch_url="https://example.com/org/repo.git",
            push_url="https://example.com/org/repo.git",
        ),
    )


def test_remote_with_different_push_url() -> None:
    output = (
        "fork\tgit@github.com:me/repo.git (fetch)\n"
        "fork\tgit@github.com:me/repo-push.git (push)\n"
    )

    (remote,) = parse_remotes(output)

    assert remote.fetch_url == "git@github.com:me/repo.git"
    assert remote.push_url == "git@github.com:me/repo-push.git"


def test_remote_without_urls_is_still_listed() -> None:
    # Так Git выводит remote, у которого есть только refspec: имя, табуляция, ничего больше.
    (remote,) = parse_remotes("nourl\t\n")

    assert remote == Remote(name="nourl", fetch_url=None, push_url=None)


def test_order_of_remotes_is_preserved() -> None:
    output = "b\t/x (fetch)\nb\t/x (push)\na\t/y (fetch)\na\t/y (push)\n"

    assert [remote.name for remote in parse_remotes(output)] == ["b", "a"]


def test_unicode_and_spaces_in_name_and_url() -> None:
    output = (
        "локальный\t/tmp/путь с пробелами/r.git (fetch)\n"
        "локальный\t/tmp/путь с пробелами/r.git (push)\n"
    )

    (remote,) = parse_remotes(output)

    assert remote.name == "локальный"
    assert remote.fetch_url == "/tmp/путь с пробелами/r.git"


def test_credentials_are_removed_from_both_urls() -> None:
    output = (
        "origin\thttps://user:s3cr3t@example.com/r.git (fetch)\n"
        "origin\thttps://user:s3cr3t@example.com/r.git (push)\n"
    )

    (remote,) = parse_remotes(output)

    assert "s3cr3t" not in str(remote)
    assert remote.fetch_url == remote.push_url == "https://example.com/r.git"


def test_url_that_contains_fetch_marker_text_is_not_confused() -> None:
    output = "o\t/srv/a (fetch) b (fetch)\no\t/srv/a (fetch) b (push)\n"

    (remote,) = parse_remotes(output)

    assert remote.fetch_url == "/srv/a (fetch) b"
    assert remote.push_url == "/srv/a (fetch) b"


def test_first_push_url_wins_and_crlf_is_tolerated() -> None:
    output = "o\t/a (fetch)\r\no\t/p1 (push)\r\no\t/p2 (push)\r\n"

    (remote,) = parse_remotes(output)

    assert remote.fetch_url == "/a"
    assert remote.push_url == "/p1"
