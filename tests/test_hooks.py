from pathlib import Path

import pytest

from git_worktree_env.hooks import _dispatcher_script, install_hooks
from git_worktree_env.utils import WteError


def test_dispatcher_runs_wte_only_for_post_checkout():
    script = _dispatcher_script(Path("/opt/bin/wte"), "/opt/old-hooks")

    assert 'if [[ "$HOOK_NAME" == "post-checkout" ]]' in script
    assert '"$WTE_EXECUTABLE" _hook "$@"' in script
    assert "post-merge" not in script
    assert 'PREVIOUS_HOOKS_PATH=/opt/old-hooks' in script
    assert 'exec "$CHAIN_TARGET" "$@"' in script


def test_install_refuses_to_replace_an_existing_global_hooks_path(
    app_paths, tmp_path, monkeypatch
):
    existing = tmp_path / "other-hooks"
    existing.mkdir()
    monkeypatch.setattr(
        "git_worktree_env.hooks.current_hooks_path", lambda: str(existing)
    )

    with pytest.raises(WteError, match="wte did not change it"):
        install_hooks(app_paths)

    assert not app_paths.hooks.exists()
