"""Console-script bridge from git-worktree-env to worktree-env."""


def main() -> int:
    """Delegate to the CLI supplied by the renamed distribution."""
    from git_worktree_env.cli import main as worktree_env_main

    return worktree_env_main()
