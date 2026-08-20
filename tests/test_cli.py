import argparse

import pytest

from git_worktree_env.cli import _build_parser


def test_public_cli_contains_only_the_five_user_commands():
    parser = _build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )

    assert set(subparsers.choices) == {"setup", "sync", "list", "doctor", "uninstall"}


def test_sync_does_not_accept_a_worktree_path():
    parser = _build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "/tmp/another-worktree"])
