import plistlib
from pathlib import Path

from git_worktree_env.reconciler import (
    LAUNCHD_LABEL,
    _listed_worktrees,
    _write_launch_agent,
    _write_systemd_units,
    discover_watch_paths,
    reconcile_once,
)
from git_worktree_env.registry import load_registry


def test_git_porcelain_discovery_returns_main_and_linked_worktrees(git_worktrees):
    main, linked = git_worktrees

    assert set(_listed_worktrees(main)) == {main, linked}


def test_monitor_discovers_the_common_git_metadata_directory(
    app_paths, git_worktrees
):
    main, _linked = git_worktrees
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        f"match:\n  main_worktree: {main}\n"
        "ports:\n  - id: web\n"
    )

    assert discover_watch_paths(app_paths) == (main / ".git/worktrees",)


def test_reconciler_projects_only_unregistered_worktrees(
    app_paths, git_worktrees, monkeypatch
):
    main, linked = git_worktrees
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        f"match:\n  main_worktree: {main}\n"
        "ports:\n  - id: web\n"
        "writes:\n  - path: generated.env\n    body: PORT=${web}\n"
    )
    monkeypatch.setattr("git_worktree_env.registry.port_is_free", lambda _port: True)

    first = reconcile_once(app_paths)
    second = reconcile_once(app_paths)

    assert first.discovered == 2
    assert first.applied == 2
    assert first.pending == 0
    assert second.applied == 0
    assert set(load_registry(app_paths)) == {str(main), str(linked)}
    assert (main / "generated.env").exists()
    assert (linked / "generated.env").exists()


def test_launch_agent_watches_common_git_metadata(
    app_paths, tmp_path, monkeypatch
):
    agent = tmp_path / "wte.plist"
    watched = (tmp_path / "repo/.git/worktrees",)
    monkeypatch.setattr(
        "git_worktree_env.reconciler._launch_agent_path", lambda: agent
    )

    _write_launch_agent(app_paths, Path("/opt/bin/wte"), watched)
    payload = plistlib.loads(agent.read_bytes())

    assert payload["Label"] == LAUNCHD_LABEL
    assert payload["ProgramArguments"] == ["/opt/bin/wte", "_reconcile"]
    assert payload["WatchPaths"] == [str(watched[0])]
    assert payload["RunAtLoad"] is True


def test_systemd_path_unit_uses_one_shot_reconciliation(tmp_path, monkeypatch):
    unit_dir = tmp_path / "systemd/user"
    watched = (tmp_path / "repo/.git/worktrees",)
    monkeypatch.setattr(
        "git_worktree_env.reconciler._systemd_user_dir", lambda: unit_dir
    )

    service, path_unit = _write_systemd_units(Path("/opt/bin/wte"), watched)

    assert 'ExecStart="/opt/bin/wte" _reconcile' in service.read_text()
    assert f"PathChanged={watched[0]}" in path_unit.read_text()
    assert "WantedBy=default.target" in path_unit.read_text()
