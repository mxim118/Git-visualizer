"""Тесты доменных моделей: создание, неизменяемость и граничные случаи."""

import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import (
    Branch,
    ChangedFile,
    Commit,
    Diff,
    FileChange,
    GraphEdge,
    GraphLayout,
    GraphNode,
    Repository,
)

H1 = "1" * 40
H2 = "2" * 40
H3 = "3" * 40
MSK = timezone(timedelta(hours=3))


def make_commit(**overrides) -> Commit:
    fields = dict(
        hash=H1,
        short_hash="1111111",
        parent_hashes=(H2,),
        author_name="Иван Петров",
        author_email="ivan@example.com",
        authored_at=datetime(2026, 1, 15, 12, 30, tzinfo=MSK),
        committer_name="Иван Петров",
        committer_email="ivan@example.com",
        committed_at=datetime(2026, 1, 15, 12, 31, tzinfo=MSK),
        subject="Add feature",
        body="",
    )
    fields.update(overrides)
    return Commit(**fields)


# --------------------------------------------------------------------------
# FileChangeType
# --------------------------------------------------------------------------


def test_file_change_types_are_the_expected_set() -> None:
    assert {member.name for member in FileChangeType} == {
        "ADDED", "MODIFIED", "DELETED", "RENAMED", "COPIED", "TYPE_CHANGED",
    }


def test_file_change_type_is_not_a_plain_string() -> None:
    # Строки в коде не должны «случайно» совпадать с перечислением.
    assert FileChangeType.ADDED != "added"
    assert len({member.value for member in FileChangeType}) == len(FileChangeType)


# --------------------------------------------------------------------------
# Repository
# --------------------------------------------------------------------------


def test_repository_on_a_branch() -> None:
    repo = Repository(path=Path("/work/project"), name="project", current_branch="main", head_commit=H1)

    assert repo.path == Path("/work/project")
    assert repo.current_branch == "main"
    assert repo.is_bare is False
    assert not repo.is_detached
    assert not repo.is_unborn


def test_repository_with_detached_head() -> None:
    repo = Repository(path=Path("/work/project"), name="project", current_branch=None, head_commit=H1)

    assert repo.is_detached
    assert not repo.is_unborn


def test_repository_without_commits_is_unborn_but_keeps_branch_name() -> None:
    repo = Repository(path=Path("/work/new"), name="new", current_branch="main", head_commit=None)

    assert repo.is_unborn
    assert not repo.is_detached
    assert repo.current_branch == "main"


def test_repository_without_branch_and_commit_is_not_detached() -> None:
    # Detached HEAD всегда указывает на коммит; без коммита это просто unborn.
    repo = Repository(path=Path("/r"), name="r", current_branch=None, head_commit=None)

    assert repo.is_unborn
    assert not repo.is_detached


def test_repository_path_may_contain_unicode_and_spaces() -> None:
    path = Path("/home/user/мои проекты/git visualizer")

    repo = Repository(path=path, name="git visualizer", current_branch="main", head_commit=H1)

    assert repo.path == path
    assert repo.name == "git visualizer"


# --------------------------------------------------------------------------
# Commit
# --------------------------------------------------------------------------


def test_root_commit_has_no_parents() -> None:
    commit = make_commit(parent_hashes=())

    assert commit.parent_hashes == ()
    assert commit.is_root
    assert not commit.is_merge


def test_regular_commit_has_one_parent() -> None:
    commit = make_commit(parent_hashes=(H2,))

    assert not commit.is_root
    assert not commit.is_merge


def test_merge_commit_keeps_parent_order() -> None:
    commit = make_commit(parent_hashes=(H2, H3))

    assert commit.is_merge
    assert not commit.is_root
    assert commit.parent_hashes[0] == H2  # первый родитель остаётся первым


def test_octopus_merge_with_three_parents_is_merge() -> None:
    assert make_commit(parent_hashes=(H1, H2, H3)).is_merge


def test_commit_refs_default_to_empty_and_can_be_set() -> None:
    assert make_commit().refs == ()
    assert make_commit(refs=("main", "origin/main")).refs == ("main", "origin/main")


def test_commit_message_keeps_newlines_and_unicode() -> None:
    body = "Первая строка\r\n\r\nВторая — с тире\nТретья 🙂"

    commit = make_commit(subject="Тема", body=body)

    assert commit.body == body
    assert commit.subject == "Тема"


def test_commit_dates_keep_timezone() -> None:
    commit = make_commit()

    assert commit.authored_at.utcoffset() == timedelta(hours=3)
    assert commit.authored_at < commit.committed_at


def test_commits_with_equal_fields_are_equal_and_hashable() -> None:
    assert make_commit() == make_commit()
    assert len({make_commit(), make_commit(), make_commit(hash=H3)}) == 2


# --------------------------------------------------------------------------
# Branch
# --------------------------------------------------------------------------


def test_local_branch_without_upstream() -> None:
    branch = Branch(name="feature/x", is_remote=False, is_current=False, commit_hash=H1)

    assert branch.upstream is None


def test_local_branch_with_upstream() -> None:
    branch = Branch(
        name="main", is_remote=False, is_current=True, commit_hash=H1, upstream="origin/main"
    )

    assert branch.upstream == "origin/main"
    assert branch.is_current


def test_remote_branch() -> None:
    branch = Branch(name="origin/main", is_remote=True, is_current=False, commit_hash=H1)

    assert branch.is_remote
    assert not branch.is_current
    assert branch.upstream is None


def test_current_unborn_branch_has_no_commit() -> None:
    branch = Branch(name="main", is_remote=False, is_current=True, commit_hash=None)

    assert branch.commit_hash is None


def test_branch_name_may_contain_slashes_and_unicode() -> None:
    branch = Branch(name="feature/задача-1", is_remote=False, is_current=False, commit_hash=H1)

    assert branch.name == "feature/задача-1"


# --------------------------------------------------------------------------
# ChangedFile / FileChange
# --------------------------------------------------------------------------


def test_file_changed_only_in_working_tree_is_unstaged() -> None:
    file = ChangedFile(path="a.py", unstaged_change=FileChangeType.MODIFIED)

    assert file.is_unstaged
    assert not file.is_staged


def test_file_changed_only_in_index_is_staged() -> None:
    file = ChangedFile(path="a.py", staged_change=FileChangeType.MODIFIED)

    assert file.is_staged
    assert not file.is_unstaged


def test_file_can_be_staged_and_unstaged_at_the_same_time_with_different_types() -> None:
    # Git: статус "AM" — добавлен в индекс, затем изменён в рабочей директории.
    file = ChangedFile(
        path="new.py",
        staged_change=FileChangeType.ADDED,
        unstaged_change=FileChangeType.MODIFIED,
    )

    assert file.is_staged and file.is_unstaged
    assert file.staged_change is FileChangeType.ADDED
    assert file.unstaged_change is FileChangeType.MODIFIED


def test_untracked_file_is_neither_staged_nor_unstaged() -> None:
    file = ChangedFile(path="notes.txt", is_untracked=True)

    assert file.is_untracked
    assert not file.is_staged
    assert not file.is_unstaged
    assert not file.is_conflicted


def test_conflicted_file() -> None:
    file = ChangedFile(path="merge.py", is_conflicted=True)

    assert file.is_conflicted
    assert not file.is_staged and not file.is_unstaged


def test_renamed_file_keeps_old_path() -> None:
    file = ChangedFile(
        path="new_name.py", old_path="old_name.py", staged_change=FileChangeType.RENAMED
    )

    assert file.old_path == "old_name.py"
    assert file.staged_change is FileChangeType.RENAMED


def test_changed_file_defaults() -> None:
    file = ChangedFile(path="a.py")

    assert file.old_path is None
    assert not (file.is_staged or file.is_unstaged or file.is_untracked or file.is_conflicted)


@pytest.mark.parametrize(
    "path",
    [
        "file with spaces.txt",
        "документ №1 (копия).txt",
        "dir/вложенная папка/файл.py",
        "quote'and\"double.txt",
        "名前.txt",
        "-starts-with-dash.txt",
    ],
)
def test_paths_are_stored_unchanged(path: str) -> None:
    assert ChangedFile(path=path, is_untracked=True).path == path
    assert FileChange(path=path, change_type=FileChangeType.ADDED).path == path
    assert Diff(path=path, text="").path == path


def test_file_change_for_added_and_renamed_file() -> None:
    added = FileChange(path="a.py", change_type=FileChangeType.ADDED)
    renamed = FileChange(path="b.py", change_type=FileChangeType.RENAMED, old_path="a.py")

    assert added.old_path is None
    assert renamed.old_path == "a.py"


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------


def test_text_diff_defaults() -> None:
    diff = Diff(path="a.py", text="@@ -1 +1 @@\n-old\n+new\n")

    assert not diff.is_binary
    assert not diff.is_truncated
    assert diff.base_commit_hash is None
    assert diff.old_path is None


def test_binary_diff_has_no_text() -> None:
    diff = Diff(path="logo.png", text="", is_binary=True)

    assert diff.is_binary
    assert diff.text == ""


def test_truncated_diff_is_flagged() -> None:
    assert Diff(path="big.log", text="...", is_truncated=True).is_truncated


def test_diff_records_which_parent_it_was_compared_with() -> None:
    first = Diff(path="a.py", text="x", base_commit_hash=H2)
    second = Diff(path="a.py", text="x", base_commit_hash=H3)

    assert first.base_commit_hash == H2
    assert first != second


def test_diff_text_keeps_crlf_and_unicode() -> None:
    text = "-старая\r\n+новая\r\n"

    assert Diff(path="a.txt", text=text).text == text


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------


def test_graph_node() -> None:
    node = GraphNode(commit_hash=H1, x=0.0, y=40.0, lane=2)

    assert (node.x, node.y, node.lane) == (0.0, 40.0, 2)
    assert node.refs == ()


def test_graph_node_with_refs() -> None:
    assert GraphNode(commit_hash=H1, x=0, y=0, lane=0, refs=("main", "HEAD")).refs == ("main", "HEAD")


def test_merge_commit_has_one_edge_per_parent() -> None:
    to_first = GraphEdge(child_hash=H1, parent_hash=H2, points=((0.0, 0.0), (0.0, 40.0)))
    to_second = GraphEdge(child_hash=H1, parent_hash=H3, points=((0.0, 0.0), (30.0, 40.0)))

    assert to_first != to_second
    assert {to_first, to_second} == {to_first, to_second}  # хэшируемы вместе с points
    assert to_first.points[0] == (0.0, 0.0)


def test_empty_graph_layout_represents_empty_history() -> None:
    layout = GraphLayout()

    assert layout.nodes == ()
    assert layout.edges == ()


def test_graph_layout_holds_nodes_and_edges() -> None:
    node = GraphNode(commit_hash=H1, x=0, y=0, lane=0)
    edge = GraphEdge(child_hash=H1, parent_hash=H2, points=((0, 0), (0, 40)))

    layout = GraphLayout(nodes=(node,), edges=(edge,))

    assert layout.nodes == (node,)
    assert layout.edges == (edge,)


# --------------------------------------------------------------------------
# Общие свойства всех моделей
# --------------------------------------------------------------------------

SAMPLES = [
    Repository(path=Path("/r"), name="r", current_branch="main", head_commit=H1),
    make_commit(),
    Branch(name="main", is_remote=False, is_current=True, commit_hash=H1),
    ChangedFile(path="a.py"),
    FileChange(path="a.py", change_type=FileChangeType.ADDED),
    Diff(path="a.py", text=""),
    GraphNode(commit_hash=H1, x=0, y=0, lane=0),
    GraphEdge(child_hash=H1, parent_hash=H2, points=()),
    GraphLayout(),
]


@pytest.mark.parametrize("model", SAMPLES, ids=lambda m: type(m).__name__)
def test_models_are_immutable(model) -> None:
    field_name = dataclasses.fields(model)[0].name

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(model, field_name, "changed")


@pytest.mark.parametrize("model", SAMPLES, ids=lambda m: type(m).__name__)
def test_models_are_hashable(model) -> None:
    assert hash(model) == hash(dataclasses.replace(model))


@pytest.mark.parametrize("model", SAMPLES, ids=lambda m: type(m).__name__)
def test_models_require_keyword_arguments(model) -> None:
    # Исключает перепутанные позиции однотипных полей (author_name / committer_name, x / y).
    model_class = type(model)

    with pytest.raises(TypeError):
        model_class(*[None] * len(dataclasses.fields(model_class)))
