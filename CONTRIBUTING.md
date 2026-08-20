# Contributing

Thank you for improving git-worktree-env.

## Development setup

```bash
git clone https://github.com/archcst/git-worktree-env.git
cd git-worktree-env
uv sync
uv run pytest
```

## Pull requests

- Keep behavior changes covered by tests.
- Keep code, comments, commit messages, and the primary README in English.
- Update `README.zh-CN.md` when user-facing behavior changes.
- Never commit real profiles, secret paths, or `ports.json` data.
- Preserve Python 3.9 and POSIX compatibility unless a release explicitly changes it.

Before opening a pull request, run:

```bash
uv run pytest
wte validate
```
