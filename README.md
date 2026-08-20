# git-worktree-env (`wte`)

[中文文档](README.zh-CN.md)

Per-worktree ports, local secrets, and development environment setup for Git.

`wte` makes linked worktrees runnable without giving every checkout the same
hard-coded ports or copying secrets into Git. A project profile identifies one
dedicated main worktree; every linked worktree created from it receives a
sticky port block and its declared local configuration.

## Features

- Allocates contiguous, machine-wide port blocks with file locking.
- Keeps allocations stable for the lifetime of a worktree path.
- Recognizes linked and agent-created worktrees through their main worktree.
- Symlinks secrets from files outside the repository.
- Generates complete per-worktree configuration files from port templates.
- Starts optional dependency setup tasks after checkout without blocking Git.
- Preserves repository hooks behind a global hook dispatcher.

## Requirements

- macOS or Linux
- Python 3.9+
- Git

Windows is not currently supported because wte uses POSIX file locks, symlinks,
and Bash hooks.

## Installation

With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv tool install git-worktree-env
```

From a source checkout:

```bash
uv tool install --editable .
```

`pipx install git-worktree-env` is also supported.

## Quick start

```bash
wte setup
cp ~/.config/wte/project.example.yaml.template ~/.config/wte/my-project.yaml
$EDITOR ~/.config/wte/my-project.yaml
cd /path/to/a/linked-worktree
wte sync
wte doctor
```

Each project must have a dedicated main worktree:

```yaml
name: example-fullstack

match:
  main_worktree: $HOME/code/example-app

ports:
  - id: frontend
  - id: backend

secrets:
  - source: $HOME/.config/example-app/backend.env
    target: apps/backend/.env

writes:
  - path: apps/frontend/.env.development
    body: |
      VITE_PORT=${frontend}
      VITE_API_URL=http://127.0.0.1:${backend}

init:
  - command: npm install
    cwd: .
    skip_if: node_modules
```

See [`examples/fullstack.yaml`](examples/fullstack.yaml) for a complete profile.

## Commands

```text
wte setup       Create config and the project template, then install Git hooks
wte sync        Synchronize the current worktree with its project profile
wte list        List live worktree port allocations
wte doctor      Diagnose config, profiles, registry, secrets, and hooks
wte uninstall   Remove Git hooks while preserving config and state
```

Run `wte sync` from inside the worktree that needs repair or refresh. It reuses
or allocates ports, recreates secret symlinks, and regenerates declared files;
it does not run dependency initializers.

The dispatcher invokes wte only for `post-checkout`. Other installed hook names
exist solely to forward an earlier global hook or a repository-local hook.
`wte setup` refuses to replace an existing global `core.hooksPath` unless
`--force` is supplied; forced setup records and chains it. The internal hook
entry point is intentionally omitted from the public CLI and documentation.

## Configuration and state

By default all machine-local files are kept together:

```text
~/.config/wte/
├── config.yaml
├── project.example.yaml.template
├── my-project.yaml
├── another-project.yaml
├── hooks/
└── state/
    ├── ports.json
    ├── ports.lock
    └── hooks-state.json
```

Set `WTE_CONFIG_HOME` to override this location. `XDG_CONFIG_HOME` is respected
when no explicit override is present.

Every root-level YAML file except `config.yaml` is loaded as a project profile.
The `state/` directory is managed by wte and should not be edited or synchronized.
The machine-wide allocation range is configured in `config.yaml`:

```yaml
port_range:
  start: 20000
  end: 29999
```

The default range is `20000-29999`, below common OS ephemeral ranges and the
Kubernetes NodePort range. Deleted worktree paths are reclaimed during
the next synchronization or automatic checkout projection. Registry writes are
atomic, and a corrupt registry is reported rather than silently replaced.

## Security model

Profiles are trusted local configuration. Secret contents are never stored in
the registry and secret sources remain outside the repository. Write and secret
targets are restricted to the matched worktree. Initializer commands are passed
to Bash and therefore must not come from untrusted profiles.

## Development

```bash
uv sync
uv run pytest
```

## License

[MIT](LICENSE)
