"""История коммитов (``GitRepository.get_history``) на реальных временных репозиториях."""

import random
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest

from git_visualizer.application.services.history_service import HistoryService
from git_visualizer.application.services.repository_service import RepositoryService
from git_visualizer.domain.models import Commit
from git_visualizer.infrastructure.git.git_repository import GitRepository
from git_visualizer.infrastructure.git.git_runner import GitRunner

MakeRepo = Callable[..., Path]
Git = Callable[..., str]
LIMIT = 5000


@pytest.fixture
def history() -> Callable[..., tuple[Commit, ...]]:
    runner = GitRunner(default_timeout=60)

    def get(
        path: Path, *, limit: int = LIMIT, skip: int = 0
    ) -> tuple[Commit, ...]:
        return GitRepository(runner, path).get_history(limit=limit, skip=skip)

    return get


@pytest.fixture
def commit(git: Git, monkeypatch: pytest.MonkeyPatch) -> Callable[..., str]:
    """``commit(repo, message, ...)`` — пустой коммит с точным сообщением; возвращает hash."""

    def create(
        repo: Path,
        message: str | bytes,
        *,
        author: str = "Test <test@example.com>",
        committer: tuple[str, str] = ("Test", "test@example.com"),
        author_date: str | None = None,
        committer_date: str | None = None,
        config: tuple[str, ...] = (),
    ) -> str:
        for variable, value in (
            ("GIT_AUTHOR_DATE", author_date),
            ("GIT_COMMITTER_DATE", committer_date),
        ):
            if value is None:
                monkeypatch.delenv(variable, raising=False)
            else:
                monkeypatch.setenv(variable, value)
        args = [
            "-c", f"user.name={committer[0]}",
            "-c", f"user.email={committer[1]}",
            "-c", "commit.gpgsign=false",
        ]
        for setting in config:
            args += ["-c", setting]
        args += [
            "commit", "-q", "--allow-empty", "--cleanup=verbatim",
            "--author", author, "-F", "-",
        ]
        runner = GitRunner(default_timeout=60)
        runner.run(args, cwd=repo, stdin_data=message)
        return git(repo, "rev-parse", "HEAD").strip()

    return create


def hashes(commits: tuple[Commit, ...]) -> list[str]:
    return [c.hash for c in commits]


def assert_topological(commits: tuple[Commit, ...]) -> None:
    """Никакой родитель не стоит раньше своего потомка."""
    position = {c.hash: index for index, c in enumerate(commits)}
    for c in commits:
        for parent in c.parent_hashes:
            if parent in position:
                assert position[parent] > position[c.hash], f"{parent} раньше потомка {c.hash}"


# --- пустые состояния ---


def test_repository_without_commits_has_empty_history(make_repo: MakeRepo, history) -> None:
    repo = make_repo("empty", commits=0)

    assert history(repo) == ()


def test_unborn_branch_with_other_branches_shows_their_history(
    make_repo: MakeRepo, git: Git, history
) -> None:
    repo = make_repo(commits=2)
    git(repo, "checkout", "-q", "--orphan", "fresh")  # HEAD не указывает на коммит

    commits = history(repo)

    assert [c.subject for c in commits] == ["commit 1", "commit 0"]
    assert all("HEAD" not in c.refs for c in commits)
    assert commits[0].refs == ("refs/heads/main",)


# --- обычные коммиты ---


def test_root_commit_has_all_fields(make_repo: MakeRepo, commit, git: Git, history) -> None:
    repo = make_repo(commits=0)
    expected = commit(
        repo,
        "Add feature\n\nLonger explanation\nover two lines\n",
        author="Alice Author <alice@example.org>",
        committer=("Carl Committer", "carl@example.org"),
        author_date="2024-03-05T10:20:30+0200",
        committer_date="2024-03-06T01:02:03-0530",
    )

    (c,) = history(repo)

    assert c.hash == expected
    assert expected.startswith(c.short_hash) and len(c.short_hash) >= 4
    assert c.parent_hashes == ()
    assert c.is_root and not c.is_merge
    assert (c.author_name, c.author_email) == ("Alice Author", "alice@example.org")
    assert (c.committer_name, c.committer_email) == ("Carl Committer", "carl@example.org")
    assert c.authored_at.utcoffset() == timedelta(hours=2)
    assert (c.authored_at.year, c.authored_at.month, c.authored_at.day) == (2024, 3, 5)
    assert (c.authored_at.hour, c.authored_at.minute, c.authored_at.second) == (10, 20, 30)
    assert c.committed_at.utcoffset() == -timedelta(hours=5, minutes=30)
    assert c.subject == "Add feature"
    assert c.body == "Longer explanation\nover two lines"
    assert c.refs == ("HEAD", "refs/heads/main")


def test_linear_history_is_newest_first_with_parent_links(
    make_repo: MakeRepo, git: Git, history
) -> None:
    repo = make_repo(commits=4)
    expected = git(repo, "rev-list", "--topo-order", "HEAD").split()

    commits = history(repo)

    assert hashes(commits) == expected
    assert [c.subject for c in commits] == ["commit 3", "commit 2", "commit 1", "commit 0"]
    for child, parent in zip(commits, commits[1:]):
        assert child.parent_hashes == (parent.hash,)
    assert commits[-1].is_root


def test_merge_commit_has_both_parents_in_order(
    make_repo: MakeRepo, commit, git: Git, history
) -> None:
    repo = make_repo(commits=1)
    git(repo, "switch", "-q", "-c", "feature")
    feature_tip = commit(repo, "feature work")
    git(repo, "switch", "-q", "main")
    main_tip = commit(repo, "main work")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@e.com", "-c", "commit.gpgsign=false",
        "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")

    commits = history(repo)

    merge = commits[0]
    assert merge.is_merge
    assert merge.subject == "Merge feature"
    assert merge.parent_hashes == (main_tip, feature_tip)  # первый родитель — ветка, в которую слили
    assert merge.refs == ("HEAD", "refs/heads/main")
    assert len(commits) == 4
    assert_topological(commits)
    by_hash = {c.hash: c for c in commits}
    assert by_hash[feature_tip].refs == ("refs/heads/feature",)


def test_parent_is_never_listed_before_child_even_with_inverted_dates(
    make_repo: MakeRepo, commit, git: Git, history
) -> None:
    # Корень «новее» своих потомков: порядок по дате вывел бы его раньше потомка.
    repo = make_repo(commits=0)
    root = commit(repo, "root", author_date="2030-01-01T00:00:00+0000",
                  committer_date="2030-01-01T00:00:00+0000")
    a = commit(repo, "a", author_date="2000-01-01T00:00:00+0000",
               committer_date="2000-01-01T00:00:00+0000")
    git(repo, "switch", "-q", "-c", "other", root)
    b = commit(repo, "b", author_date="2001-01-01T00:00:00+0000",
               committer_date="2001-01-01T00:00:00+0000")

    commits = history(repo)

    assert set(hashes(commits)) == {root, a, b}
    assert hashes(commits)[-1] == root, "корень идёт после обоих потомков"
    assert_topological(commits)


# --- какие refs попадают в историю ---


def test_all_local_and_remote_tracking_branches_are_included(
    make_repo: MakeRepo, commit, git: Git, history
) -> None:
    repo = make_repo(commits=1)
    git(repo, "switch", "-q", "-c", "side")
    side = commit(repo, "only on side branch")
    git(repo, "switch", "-q", "main")
    # Коммит, достижимый только из remote-tracking ветки (локальной ветки у него нет).
    git(repo, "switch", "-q", "-c", "tmp")
    remote_only = commit(repo, "only on remote")
    git(repo, "update-ref", "refs/remotes/origin/feature", remote_only)
    git(repo, "switch", "-q", "main")
    git(repo, "branch", "-q", "-D", "tmp")

    commits = history(repo)

    by_hash = {c.hash: c for c in commits}
    assert {side, remote_only} <= set(by_hash)
    assert by_hash[side].refs == ("refs/heads/side",)
    assert by_hash[remote_only].refs == ("refs/remotes/origin/feature",)


def test_refs_use_full_names_and_head_marker(make_repo: MakeRepo, git: Git, history) -> None:
    repo = make_repo(commits=1)
    git(repo, "branch", "feature/x")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    (c,) = history(repo)

    assert set(c.refs) == {
        "HEAD", "refs/heads/main", "refs/heads/feature/x", "refs/remotes/origin/main",
    }
    assert c.refs[0] == "HEAD"


def test_user_decoration_settings_do_not_hide_refs(
    make_repo: MakeRepo, git: Git, history
) -> None:
    repo = make_repo(commits=1)
    git(repo, "branch", "feature")
    git(repo, "config", "log.excludeDecoration", "refs/heads/feature")  # настройка пользователя

    (c,) = history(repo)

    assert c.refs == ("HEAD", "refs/heads/main", "refs/heads/feature")


def test_tags_are_not_part_of_history_refs(make_repo: MakeRepo, git: Git, history) -> None:
    repo = make_repo(commits=1)
    git(repo, "-c", "user.name=T", "-c", "user.email=t@e.com", "tag", "-a", "v1", "-m", "release")
    git(repo, "tag", "light")

    (c,) = history(repo)

    assert c.refs == ("HEAD", "refs/heads/main")


def test_stash_commits_are_not_in_history(make_repo: MakeRepo, git: Git, history) -> None:
    repo = make_repo(commits=2)
    (repo / "file0.txt").write_text("changed\n", encoding="utf-8")
    (repo / "new.txt").write_text("untracked\n", encoding="utf-8")
    git(repo, "add", "--", "new.txt")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@e.com", "stash", "push", "-q")
    assert git(repo, "rev-parse", "--verify", "refs/stash").strip()  # stash действительно есть

    commits = history(repo)

    assert [c.subject for c in commits] == ["commit 1", "commit 0"]
    stash_commit = git(repo, "rev-parse", "refs/stash").strip()
    assert stash_commit not in hashes(commits)
    assert all(not c.subject.startswith(("WIP on", "index on")) for c in commits)


def test_detached_head_commit_without_branch_is_included(
    make_repo: MakeRepo, commit, git: Git, history
) -> None:
    repo = make_repo(commits=2)
    git(repo, "checkout", "-q", "--detach")
    detached = commit(repo, "made on detached HEAD")

    commits = history(repo)

    assert commits[0].hash == detached
    assert commits[0].refs == ("HEAD",)
    assert [c.subject for c in commits[1:]] == ["commit 1", "commit 0"]
    assert commits[1].refs == ("refs/heads/main",)


def test_file_named_head_in_work_tree_does_not_break_log(
    make_repo: MakeRepo, history
) -> None:
    repo = make_repo(commits=2)
    (repo / "HEAD").write_text("not a revision\n", encoding="utf-8")

    assert [c.subject for c in history(repo)] == ["commit 1", "commit 0"]


# --- произвольное содержимое сообщений и имён ---


def test_unicode_and_multiline_message(make_repo: MakeRepo, commit, history) -> None:
    repo = make_repo(commits=0)
    body = (
        "Первый абзац: «ёлка» — 日本語, 😀.\n"
        "\n"
        "Второй абзац\n"
        "  с отступом и\ttabом\n"
        "\n"
        "Последняя строка"
    )
    commit(
        repo,
        f"Исправление ошибки № 42\n\n{body}\n",
        author="Иван Петров 😀 <ivan@пример.рф>",
        committer=("Мария Иванова", "maria@example.com"),
    )

    (c,) = history(repo)

    assert c.subject == "Исправление ошибки № 42"
    assert c.body == body
    assert c.author_name == "Иван Петров 😀"
    assert c.author_email == "ivan@пример.рф"
    assert c.committer_name == "Мария Иванова"


def test_special_characters_in_message(make_repo: MakeRepo, commit, history) -> None:
    repo = make_repo(commits=0)
    subject = 'Fix "quotes" & <tags> 100% %H %x00 \\n {x}'
    body = "line with 'single' quotes\n$HOME `cmd` $(cmd) %s %b\n\\0 and ;|&"
    commit(repo, f"{subject}\n\n{body}\n")

    (c,) = history(repo)

    assert c.subject == subject
    assert c.body == body


def test_control_characters_used_as_separators_do_not_break_parsing(
    make_repo: MakeRepo, commit, history
) -> None:
    # U+001F / U+001E — типичные «разделители»; Git принимает их в сообщении и в имени автора.
    repo = make_repo(commits=0)
    first = commit(
        repo,
        "Subject \x1f with US\n\nbody \x1f US \x1e RS \x1b[31m ESC\n",
        author="A\x1fB <ab@example.com>",
    )
    commit(repo, "plain follow-up")

    newest, older = history(repo)

    assert newest.subject == "plain follow-up"
    assert older.hash == first
    assert older.subject == "Subject \x1f with US"
    assert older.body == "body \x1f US \x1e RS \x1b[31m ESC"
    assert older.author_name == "A\x1fB"


def test_multiline_first_paragraph_becomes_single_line_subject(
    make_repo: MakeRepo, commit, history
) -> None:
    # Так Git определяет subject (%s): первый абзац в одну строку; остальное — body.
    repo = make_repo(commits=0)
    commit(repo, "first line\nsecond line\n\nbody text\n")

    (c,) = history(repo)

    assert c.subject == "first line second line"
    assert c.body == "body text"


def test_leading_indentation_of_body_is_preserved(make_repo: MakeRepo, commit, history) -> None:
    repo = make_repo(commits=0)
    commit(repo, "Subject\n\n    indented first line\n  second\n")

    (c,) = history(repo)

    assert c.body == "    indented first line\n  second"


def test_message_without_body(make_repo: MakeRepo, commit, history) -> None:
    repo = make_repo(commits=0)
    commit(repo, "Just a subject\n")

    (c,) = history(repo)

    assert (c.subject, c.body) == ("Just a subject", "")


def test_crlf_inside_body_is_preserved(make_repo: MakeRepo, commit, history) -> None:
    repo = make_repo(commits=0)
    commit(repo, "Subject\n\nline one\r\nline two\r\n")

    (c,) = history(repo)

    assert c.body == "line one\r\nline two"


def test_commit_with_non_utf8_encoding_is_decoded_correctly(
    make_repo: MakeRepo, commit, git: Git, history
) -> None:
    repo = make_repo(commits=0)
    text = "caf\u00e9 \u00e9t\u00e9"
    commit(
        repo,
        f"Subject {text}\n\nbody {text}\n".encode("latin-1"),
        config=("i18n.commitEncoding=ISO-8859-1",),
    )
    # Пользовательская настройка вывода не должна влиять на разбор.
    git(repo, "config", "i18n.logOutputEncoding", "ISO-8859-1")

    (c,) = history(repo)

    assert c.subject == f"Subject {text}"
    assert c.body == f"body {text}"


# --- лимит ---


def test_limit_returns_newest_commits_in_the_same_order(
    make_repo: MakeRepo, history
) -> None:
    repo = make_repo(commits=6)
    everything = history(repo)

    limited = history(repo, limit=3)

    assert limited == everything[:3]
    assert [c.subject for c in limited] == ["commit 5", "commit 4", "commit 3"]
    # Родитель последнего коммита выборки в неё не вошёл — штатная ситуация обрезанной истории.
    assert limited[-1].parent_hashes == (everything[3].hash,)
    assert everything[3].hash not in hashes(limited)


def test_limit_larger_than_history_returns_everything(make_repo: MakeRepo, history) -> None:
    assert len(history(make_repo(commits=3), limit=100)) == 3


# --- загрузка более старой истории (skip, FR-HIST-07) ---


def test_skip_loads_the_next_older_page(make_repo: MakeRepo, history) -> None:
    repo = make_repo(commits=7)
    everything = history(repo)

    first = history(repo, limit=3)
    second = history(repo, limit=3, skip=3)
    last = history(repo, limit=3, skip=6)

    assert [c.subject for c in first] == ["commit 6", "commit 5", "commit 4"]
    assert [c.subject for c in second] == ["commit 3", "commit 2", "commit 1"]
    assert [c.subject for c in last] == ["commit 0"]
    assert first + second + last == everything


def test_skip_beyond_history_is_empty(make_repo: MakeRepo, history) -> None:
    assert history(make_repo(commits=2), skip=100) == ()


def test_skip_windows_match_git_rev_list_on_branched_history(
    make_repo: MakeRepo, commit, git: Git, history
) -> None:
    repo = make_repo(commits=2)
    git(repo, "switch", "-q", "-c", "feature")
    commit(repo, "feature 1")
    commit(repo, "feature 2")
    git(repo, "switch", "-q", "main")
    commit(repo, "main 1")
    expected = git(
        repo, "rev-list", "--topo-order", "--branches", "--remotes", "HEAD"
    ).split()

    pages: list[str] = []
    for skip in range(0, len(expected), 2):
        page = history(repo, limit=2, skip=skip)
        assert_topological(page)
        pages.extend(hashes(page))

    assert pages == expected


def test_service_loads_pages_until_history_is_complete(make_repo: MakeRepo) -> None:
    root = make_repo("pages", commits=5)
    runner = GitRunner(default_timeout=60)
    factory = lambda path: GitRepository(runner, path)  # noqa: E731
    repository = RepositoryService(factory).open_repository(root)
    service = HistoryService(factory)

    loaded: list[Commit] = []
    has_more = True
    while has_more:
        page = service.get_history(repository, limit=2, skip=len(loaded))
        assert len(page.commits) <= 2
        loaded.extend(page.commits)
        has_more = page.has_more

    assert [c.subject for c in loaded] == [
        "commit 4", "commit 3", "commit 2", "commit 1", "commit 0",
    ]


def test_service_reports_whether_history_is_truncated(make_repo: MakeRepo) -> None:
    root = make_repo("truncated", commits=3)
    runner = GitRunner(default_timeout=60)
    factory = lambda path: GitRepository(runner, path)  # noqa: E731
    repository = RepositoryService(factory).open_repository(root)
    service = HistoryService(factory)

    # Ровно limit коммитов — история полная: len == limit неоднозначен.
    complete = service.get_history(repository, limit=3)
    # На один меньше — история обрезана, и это видно пользователю.
    truncated = service.get_history(repository, limit=2)

    assert complete.has_more is False
    assert len(complete.commits) == 3
    assert truncated.has_more is True
    assert len(truncated.commits) == 2


# --- сверка с независимым источником ---


def test_random_history_matches_git_rev_list(
    make_repo: MakeRepo, commit, git: Git, history
) -> None:
    """Порядок, родители и состав совпадают с ``git rev-list --topo-order --parents``."""
    rng = random.Random(20260721)
    repo = make_repo(commits=0)
    commit(repo, "root", committer_date="2024-01-01T00:00:00+0000")
    branches = ["main"]
    ident = ["-c", "user.name=T", "-c", "user.email=t@e.com", "-c", "commit.gpgsign=false"]

    for step in range(30):
        action = rng.choice(["commit", "commit", "branch", "merge"])
        current = rng.choice(branches)
        git(repo, "switch", "-q", current)
        if action == "branch":
            name = f"b{step}"
            git(repo, "switch", "-q", "-c", name)
            branches.append(name)
        elif action == "merge" and len(branches) > 1:
            other = rng.choice([b for b in branches if b != current])
            git(repo, *ident, "merge", "-q", "--no-ff", "-m", f"merge {other} {step}", other)
            continue
        # даты нарочно случайные: не совпадают с порядком обхода
        date = f"20{rng.randint(10, 29)}-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}T00:00:00+0000"
        commit(repo, f"step {step}", committer_date=date, author_date=date)

    commits = history(repo)
    parents_by_hash = {
        line.split()[0]: tuple(line.split()[1:])
        for line in git(repo, "rev-list", "--topo-order", "--parents",
                        "--branches", "--remotes", "HEAD").splitlines()
    }

    assert len(commits) > 20
    assert hashes(commits) == list(git(repo, "rev-list", "--topo-order", "--branches",
                                       "--remotes", "HEAD").split())
    assert {c.hash: c.parent_hashes for c in commits} == parents_by_hash
    assert_topological(commits)
    assert any(c.is_merge for c in commits), "в случайной истории есть merge-коммиты"


# --- сценарий целиком: сервисы поверх реального Git ---


def test_services_open_repository_and_load_history(make_repo: MakeRepo) -> None:
    root = make_repo("мой проект", commits=3)
    runner = GitRunner(default_timeout=60)
    factory = lambda path: GitRepository(runner, path)  # noqa: E731

    repository = RepositoryService(factory).open_repository(root / "." )
    page = HistoryService(factory).get_history(repository)

    assert [c.subject for c in page.commits] == ["commit 2", "commit 1", "commit 0"]
    assert page.commits[0].hash == repository.head_commit
    assert page.has_more is False
