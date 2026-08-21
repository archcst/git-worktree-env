"""Allow ``python -m worktree_env`` to behave like ``wte``."""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
