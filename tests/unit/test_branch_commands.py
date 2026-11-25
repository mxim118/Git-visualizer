"""Аргументы Git-команд для проверки, создания и переключения веток."""

from git_visualizer.infrastructure.git.git_repository import (
    build_create_branch_arguments,
    build_switch_branch_arguments,
    build_validate_branch_name_arguments,
)


def test_validate_branch_name_uses_check_ref_format() -> None:
    assert build_validate_branch_name_arguments("feature/x") == [
        "check-ref-format",
        "--branch",
        "feature/x",
    ]


def test_create_branch_from_head_uses_switch_c() -> None:
    assert build_create_branch_arguments(name="feature/x") == [
        "switch",
        "-c",
        "feature/x",
    ]


def test_create_branch_from_selected_commit_appends_start_point() -> None:
    assert build_create_branch_arguments(
        name="feature/x", start_point="a" * 40
    ) == ["switch", "-c", "feature/x", "a" * 40]


def test_create_branch_never_forces_or_discards_changes() -> None:
    arguments = build_create_branch_arguments(
        name="feature/x", start_point="a" * 40
    )

    assert "--force" not in arguments
    assert "--discard-changes" not in arguments


def test_switch_branch_disables_remote_branch_guessing() -> None:
    assert build_switch_branch_arguments("feature/x") == [
        "switch",
        "--no-guess",
        "feature/x",
    ]


def test_switch_branch_never_forces_or_discards_changes() -> None:
    arguments = build_switch_branch_arguments("main")

    assert "--force" not in arguments
    assert "--discard-changes" not in arguments
