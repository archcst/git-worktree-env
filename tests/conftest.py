from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

import pytest

from worktree_env.paths import AppPaths


def run(command: Sequence[str], cwd: Path) -> str:
    result = subprocess.run(
        list(command),
        cwd=str(cwd),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    return result.stdout.strip()


@pytest.fixture
def app_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppPaths:
    root = tmp_path / "config"
    monkeypatch.setenv("WTE_CONFIG_HOME", str(root))
    paths = AppPaths.discover()
    paths.ensure()
    paths.config.write_text("port_range:\n  start: 41000\n  end: 41099\n")
    return paths


@pytest.fixture
def git_worktrees(tmp_path: Path):
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    main.mkdir()
    run(["git", "init"], main)
    run(["git", "config", "user.name", "Test User"], main)
    run(["git", "config", "user.email", "test@example.com"], main)
    (main / "README.md").write_text("test\n")
    run(["git", "add", "README.md"], main)
    run(["git", "commit", "-m", "initial"], main)
    run(["git", "worktree", "add", "-b", "feature", str(linked)], main)
    return main.resolve(), linked.resolve()
