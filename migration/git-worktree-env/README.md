# `git-worktree-env` has moved

> **This package has been renamed to [`worktree-env`](https://pypi.org/project/worktree-env/).**

This is the final transition release of `git-worktree-env`. It installs the renamed
package so existing users can continue to run `wte`, but future releases will only be
published as `worktree-env`.

## Migrate an existing installation

For an installation managed by `uv`:

```bash
uv tool uninstall git-worktree-env
uv tool install worktree-env
wte init
# If you previously enabled the optional Monitor:
wte monitor enable
```

For an installation managed by `pipx`:

```bash
pipx uninstall git-worktree-env
pipx install worktree-env
wte init
# If you previously enabled the optional Monitor:
wte monitor enable
```

For an installation managed by `pip`:

```bash
python -m pip uninstall -y git-worktree-env
python -m pip install --upgrade --force-reinstall worktree-env
wte init
# If you previously enabled the optional Monitor:
wte monitor enable
```

Running `wte init` refreshes the Git hook's absolute executable path. The `wte`
command, the `git_worktree_env` Python import, and all existing configuration under
`~/.config/wte/` remain compatible.
