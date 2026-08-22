import plistlib
from pathlib import Path

from worktree_env.reconciler import (
    LAUNCHD_LABEL,
    PROFILE_FILES_LAUNCHD_LABEL,
    PROFILE_FILES_SYSTEMD_SERVICE,
    PROFILE_LAUNCHD_LABEL,
    PROFILE_SYSTEMD_SERVICE,
    _listed_worktrees,
    _write_launch_agent,
    _write_profile_files_launch_agent,
    _write_profile_files_systemd_units,
    _write_profile_launch_agent,
    _write_profile_systemd_units,
    _write_systemd_units,
    discover_watch_paths,
    install_monitor,
    reconcile_once,
    refresh_profile_file_monitor,
    refresh_worktree_monitor,
)
from worktree_env.registry import load_registry


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
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)

    first = reconcile_once(app_paths)
    second = reconcile_once(app_paths)

    assert first.discovered == 2
    assert first.applied == 2
    assert first.pending == 0
    assert second.applied == 0
    assert set(load_registry(app_paths)) == {str(main), str(linked)}
    assert (main / "generated.env").exists()
    assert (linked / "generated.env").exists()


def test_reconciler_runs_initializers_for_unregistered_worktrees(
    app_paths, git_worktrees, monkeypatch
):
    main, linked = git_worktrees
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        f"match:\n  main_worktree: {main}\n"
        "ports:\n  - id: web\n"
        "init:\n  - command: [tool]\n    cwd: .\n"
    )
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)
    initialized = []
    monkeypatch.setattr(
        "worktree_env.projector.run_initializers",
        lambda _profile, root: initialized.append(root),
    )

    reconcile_once(app_paths)
    reconcile_once(app_paths)

    assert set(initialized) == {main.resolve(), linked.resolve()}
    assert len(initialized) == 2


def test_launch_agent_watches_common_git_metadata(
    app_paths, tmp_path, monkeypatch
):
    agent = tmp_path / "wte.plist"
    watched = (tmp_path / "repo/.git/worktrees",)
    monkeypatch.setattr(
        "worktree_env.reconciler._launch_agent_path", lambda: agent
    )

    _write_launch_agent(app_paths, Path("/opt/bin/wte"), watched)
    payload = plistlib.loads(agent.read_bytes())

    assert payload["Label"] == LAUNCHD_LABEL
    assert payload["ProgramArguments"] == ["/opt/bin/wte", "_reconcile"]
    assert payload["WatchPaths"] == [str(watched[0])]
    assert payload["RunAtLoad"] is True


def test_launch_agent_watches_profile_directory_for_automatic_refresh(
    app_paths, tmp_path, monkeypatch
):
    agent = tmp_path / "profiles.plist"
    monkeypatch.setattr(
        "worktree_env.reconciler._profile_launch_agent_path", lambda: agent
    )

    _write_profile_launch_agent(app_paths, Path("/opt/bin/wte"))
    payload = plistlib.loads(agent.read_bytes())

    assert payload["Label"] == PROFILE_LAUNCHD_LABEL
    assert payload["ProgramArguments"] == ["/opt/bin/wte", "_profile_set_changed"]
    assert payload["WatchPaths"] == [str(app_paths.profiles)]


def test_launch_agent_watches_profile_files_for_in_place_changes(
    app_paths, tmp_path, monkeypatch
):
    agent = tmp_path / "profile-files.plist"
    profile = app_paths.profiles / "example.yaml"
    profile.write_text("name: example\n")
    monkeypatch.setattr(
        "worktree_env.reconciler._profile_files_launch_agent_path", lambda: agent
    )

    _write_profile_files_launch_agent(
        app_paths, Path("/opt/bin/wte"), (profile,)
    )
    payload = plistlib.loads(agent.read_bytes())

    assert payload["Label"] == PROFILE_FILES_LAUNCHD_LABEL
    assert payload["ProgramArguments"] == ["/opt/bin/wte", "_profiles_changed"]
    assert payload["WatchPaths"] == [str(profile)]


def test_monitor_stays_enabled_to_discover_profiles_added_later(
    app_paths, monkeypatch
):
    calls = []
    monkeypatch.setattr("worktree_env.reconciler.platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        "worktree_env.reconciler.resolve_wte_executable", lambda: Path("/opt/bin/wte")
    )
    monkeypatch.setattr(
        "worktree_env.reconciler._uninstall_worktree_launchd",
        lambda: calls.append("remove-worktree-monitor"),
    )
    monkeypatch.setattr(
        "worktree_env.reconciler._uninstall_profile_files_launchd",
        lambda: calls.append("remove-profile-files-monitor"),
    )
    monkeypatch.setattr(
        "worktree_env.reconciler._install_profile_launchd",
        lambda paths, executable: calls.append((paths, executable)),
    )
    monkeypatch.setattr(
        "worktree_env.reconciler.monitor_status",
        lambda paths, watched: type(
            "Status", (), {"installed": True, "active": True}
        )(),
    )

    status = install_monitor(app_paths)

    assert status.installed is True
    assert calls == [
        "remove-worktree-monitor",
        "remove-profile-files-monitor",
        (app_paths, Path("/opt/bin/wte")),
    ]


def test_profile_change_refreshes_repository_watch_paths(
    app_paths, git_worktrees, tmp_path, monkeypatch
):
    main, _linked = git_worktrees
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        f"match:\n  main-worktree: {main}\n"
        "port-claims:\n  - id: web\n"
    )
    agent = tmp_path / "wte.plist"
    agent.write_bytes(
        plistlib.dumps({"WatchPaths": [str(tmp_path / "old/.git/worktrees")]})
    )
    monkeypatch.setattr("worktree_env.reconciler.platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        "worktree_env.reconciler._profile_monitor_is_installed", lambda: True
    )
    monkeypatch.setattr("worktree_env.reconciler._launch_agent_path", lambda: agent)
    monkeypatch.setattr(
        "worktree_env.reconciler.resolve_wte_executable", lambda: Path("/opt/bin/wte")
    )
    installed = []
    monkeypatch.setattr(
        "worktree_env.reconciler._install_launchd",
        lambda paths, executable, watched: installed.append(
            (paths, executable, watched)
        ),
    )

    assert refresh_worktree_monitor(app_paths) is True
    assert installed == [
        (app_paths, Path("/opt/bin/wte"), (main / ".git/worktrees",))
    ]


def test_systemd_path_unit_uses_one_shot_reconciliation(tmp_path, monkeypatch):
    unit_dir = tmp_path / "systemd/user"
    watched = (tmp_path / "repo/.git/worktrees",)
    monkeypatch.setattr(
        "worktree_env.reconciler._systemd_user_dir", lambda: unit_dir
    )

    service, path_unit = _write_systemd_units(Path("/opt/bin/wte"), watched)

    assert 'ExecStart="/opt/bin/wte" _reconcile' in service.read_text()
    assert f"PathChanged={watched[0]}" in path_unit.read_text()
    assert "WantedBy=default.target" in path_unit.read_text()


def test_systemd_profile_path_triggers_automatic_refresh(app_paths, tmp_path, monkeypatch):
    unit_dir = tmp_path / "systemd/user"
    monkeypatch.setattr(
        "worktree_env.reconciler._systemd_user_dir", lambda: unit_dir
    )

    service, path_unit = _write_profile_systemd_units(
        app_paths, Path("/opt/bin/wte")
    )

    assert 'ExecStart="/opt/bin/wte" _profile_set_changed' in service.read_text()
    assert f"PathChanged={app_paths.profiles}" in path_unit.read_text()
    assert f"Unit={PROFILE_SYSTEMD_SERVICE}" in path_unit.read_text()


def test_systemd_profile_files_trigger_in_place_change_refresh(
    app_paths, tmp_path, monkeypatch
):
    unit_dir = tmp_path / "systemd/user"
    profile = app_paths.profiles / "example.yaml"
    monkeypatch.setattr(
        "worktree_env.reconciler._systemd_user_dir", lambda: unit_dir
    )

    service, path_unit = _write_profile_files_systemd_units(
        Path("/opt/bin/wte"), (profile,)
    )

    assert 'ExecStart="/opt/bin/wte" _profiles_changed' in service.read_text()
    assert f"PathModified={profile}" in path_unit.read_text()
    assert f"Unit={PROFILE_FILES_SYSTEMD_SERVICE}" in path_unit.read_text()

    profile.write_text("name: example\n")
    monkeypatch.setattr("worktree_env.reconciler.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "worktree_env.reconciler._profile_monitor_is_installed", lambda: True
    )
    assert refresh_profile_file_monitor(app_paths) is False
