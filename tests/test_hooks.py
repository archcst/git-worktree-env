from pathlib import Path

from git_worktree_env.hooks import _dispatcher_script


def test_dispatcher_runs_wte_only_for_post_checkout():
    script = _dispatcher_script(Path("/opt/bin/wte"), "/opt/old-hooks")

    assert 'if [[ "$HOOK_NAME" == "post-checkout" ]]' in script
    assert '"$WTE_EXECUTABLE" _hook "$@"' in script
    assert "post-merge" not in script
    assert 'PREVIOUS_HOOKS_PATH=/opt/old-hooks' in script
    assert 'exec "$CHAIN_TARGET" "$@"' in script
