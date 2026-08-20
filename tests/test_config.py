import pytest

from git_worktree_env.config import initialize_config, load_port_pool
from git_worktree_env.paths import AppPaths
from git_worktree_env.utils import WteError


def test_init_generates_documented_port_range(tmp_path, monkeypatch):
    monkeypatch.setenv("WTE_CONFIG_HOME", str(tmp_path / "config"))
    paths = AppPaths.discover()

    assert initialize_config(paths) is True
    assert paths.config.read_text() == (
        "# Inclusive range used for per-worktree port allocation.\n"
        "port_range:\n"
        "  start: 35000\n"
        "  end: 39999\n"
    )


def test_legacy_pool_key_is_rejected(app_paths):
    app_paths.config.write_text("pool:\n  start: 41000\n  end: 41099\n")

    with pytest.raises(WteError, match="renamed to 'port_range'"):
        load_port_pool(app_paths)
