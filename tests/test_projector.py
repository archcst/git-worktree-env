import pytest

from git_worktree_env.projector import apply_worktree
from git_worktree_env.registry import load_registry
from git_worktree_env.utils import WteError


def test_projection_links_secrets_and_writes_ports(app_paths, git_worktrees, tmp_path, monkeypatch):
    main, linked = git_worktrees
    secret = tmp_path / "backend.env"
    secret.write_text("TOKEN=test\n")
    (app_paths.profiles / "example.yaml").write_text(
        "name: example\n"
        f"match:\n  main_worktree: {main}\n"
        "ports:\n  - id: frontend\n  - id: backend\n"
        "secrets:\n"
        f"  - source: {secret}\n"
        "    target: app/.env\n"
        "writes:\n"
        "  - path: app/.env.development\n"
        "    body: |\n"
        "      PORT=${frontend}\n"
        "      API=http://127.0.0.1:${backend}\n"
    )
    generated_path = linked / "app/.env.development"
    generated_path.parent.mkdir(parents=True)
    generated_path.write_text("KEEP=old\n")
    monkeypatch.setattr("git_worktree_env.registry.port_is_free", lambda _port: True)

    result = apply_worktree(app_paths, linked)

    assert result is not None
    assert (linked / "app/.env").is_symlink()
    assert (linked / "app/.env").resolve() == secret.resolve()
    generated = generated_path.read_text()
    assert "KEEP=old" not in generated
    assert "PORT=41000" in generated
    assert "API=http://127.0.0.1:41001" in generated
    assert load_registry(app_paths)[str(linked)]["profile"] == "example"


def test_failed_projection_does_not_commit_a_registry_entry(
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

    def fail_writes(*_args):
        raise WteError("write failed")

    monkeypatch.setattr("git_worktree_env.projector.apply_writes", fail_writes)

    with pytest.raises(WteError, match="write failed"):
        apply_worktree(app_paths, linked)

    assert str(linked) not in load_registry(app_paths)
