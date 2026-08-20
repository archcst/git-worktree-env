import argparse

import pytest

from git_worktree_env.cli import _build_parser


def test_public_cli_contains_only_the_six_user_commands():
    parser = _build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )

    assert set(subparsers.choices) == {
        "init",
        "sync",
        "list",
        "doctor",
        "monitor",
        "uninstall",
    }
    monitor_parser = subparsers.choices["monitor"]
    monitor_subparsers = next(
        action
        for action in monitor_parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert set(monitor_subparsers.choices) == {"enable", "disable"}


def test_sync_does_not_accept_a_worktree_path():
    parser = _build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "/tmp/another-worktree"])
