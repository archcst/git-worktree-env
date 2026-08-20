from pathlib import Path

from git_worktree_env.profiles import find_profile, main_worktree_root, validate_profiles


def test_linked_worktree_matches_its_main_profile(app_paths, git_worktrees):
    main, linked = git_worktrees
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        "match:\n"
        f"  main_worktree: {main}\n"
        "ports:\n"
        "  - id: web\n"
    )

    profile = find_profile(app_paths, linked)

    assert profile is not None
    assert profile["name"] == "example"
    assert main_worktree_root(linked) == main


def test_validation_rejects_duplicate_main_worktrees(app_paths, git_worktrees):
    main, _ = git_worktrees
    for name in ("one", "two"):
        (app_paths.profiles / f"{name}.yaml").write_text(
            f"name: {name}\n"
            f"match:\n  main_worktree: {main}\n"
            "ports:\n  - id: web\n"
        )

    errors, _warnings = validate_profiles(app_paths)

    assert any("also configured" in error for error in errors)
