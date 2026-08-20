# git-worktree-env (`wte`)

[中文文档](README.zh-CN.md)

Automatically prepare an isolated, runnable local development environment for every
Git worktree. Lightweight, minimal, no magic.

## The problem

Git worktrees are widely used for parallel development and task isolation. However,
a new worktree usually contains only code, not a development environment that is
ready to run:

- The frontend, backend, database, and debugger still use the same fixed ports,
  preventing multiple worktrees from running at the same time.
- `.env` files, private keys, and other local secrets must be copied repeatedly and
  can easily be committed by mistake.
- URLs and port settings shared by services within a project must be kept in sync
  manually.

Common solutions often require adding extra scripts to the project, changing how it
is started, modifying `AGENTS.md` or `CLAUDE.md`, or creating skills.
These approaches are intrusive to some degree: they must either be adopted across
the team or affect how other team members work.

`wte` uses a Git `post-checkout` hook to assign stable, conflict-free ports to a
project's worktrees, link environment variables, and generate local configuration.
The hook is not committed to the repository. It is available globally on the local
machine and does not modify any project code.

## Comparison with existing tools

- [Portless](https://github.com/vercel-labs/portless): Requires applications to be
  started through `portless`, introducing a reverse proxy, a local CA, and a
  background service.
- [devports](https://github.com/bendechrai/devports): Wraps worktree creation and
  removal in `devports` commands; worktrees created directly by an agent or IDE are
  not handled automatically.
- [Worktrunk](https://worktrunk.dev/): Replaces the native Git workflow with `wt`,
  does not participate in environment setup, and does not automatically handle
  worktrees created directly by an agent or IDE.
- [workz](https://github.com/rohansx/workz): Uses `.workz.toml`,
  `workz sync`/`workz start`, or separately configured hooks for Cursor, Claude Code,
  and Worktrunk.
- [Hyve](https://github.com/eladkishon/hyve): Adopts a
  `hyve create`/`hyve run` workflow and depends on Docker, database containers, and
  service orchestration.

`wte` does not take over how worktrees are created or how a project is started.
Instead, it automatically projects a complete local development environment after a
worktree is created. It requires no changes to project code, start commands, or agent
prompts; no scripts need to be added to the project, and no traffic proxy or resident
process is required. Worktrees created by Git, an IDE, or a coding agent can all be
handled automatically.

All rules are declared explicitly in profiles stored outside the repository. For each
worktree, `wte` assigns stable ports, mounts secrets, generates local configuration,
and can initialize dependencies in the background, making the worktree ready
immediately after creation.

## Features

- Allocates contiguous port blocks from a machine-wide shared pool and keeps them
  stable for the lifetime of the worktree path.
- Mounts secrets stored outside the repository into the worktree as symlinks,
  preserving a single source of truth and avoiding copy and paste.
- Organizes configuration by repository rather than by service. Supports monorepos
  and multiple port requests.
- Optionally monitors host directories to discover newly added linked worktrees,
  including those created within coding agent sandboxes.
- Preserves the project's own Git hooks. After the `post-checkout` hook for `wte`
  finishes, it invokes the project's own executable Git hook.
- Zero intrusion: no wrapper, no skill, and no changes to coding agent prompts or
  project start commands.

## Requirements

- macOS or Linux
- Python 3.9+
- Git
- Bash

## Installation

```bash
uv tool install git-worktree-env
```

## Upgrading

```bash
uv tool upgrade git-worktree-env
```

## Getting started

```bash
wte init
```

This command:

- Initializes the personal configuration directory at `~/.config/wte/`.
- Runs `git config --global core.hooksPath ~/.config/wte/hooks` to install the global
  Git hook dispatcher.

> If the global `core.hooksPath` already points somewhere else, `wte` displays the
> current value and refuses to change it. Confirm its purpose and migrate or remove
> it as appropriate before retrying `wte init`.

## `~/.config/wte/`

Personal profiles, hooks, and runtime state are stored together in:

```text
~/.config/wte/
├── config.yaml                       # Machine-wide port pool
├── project_a.yaml                    # Project A profile
├── project_b.yaml                    # Project B profile
├── hooks/                            # Global Git hook dispatcher
└── state/                            # Managed by wte; do not edit manually.
    ├── ports.json                    # Port registry
    ├── ports.lock                    # Concurrency lock
    ├── hooks-state.json              # Hook installation state
    └── reconciler.log                # Monitor log (present only when the optional Monitor is enabled; see the Monitor section)
```

Every root-level `*.yaml` file except `config.yaml` is loaded as a project profile.

Set `WTE_CONFIG_HOME` to change this directory. When it is not set,
`XDG_CONFIG_HOME` is respected.

## Configuration examples

### Port range

The machine-wide port range is configured in `~/.config/wte/config.yaml`:

```yaml
port_range:
  start: 20000
  end: 29999
```

The default range is `20000-29999`. You can change it manually; new worktrees will
be allocated from the new range, while existing allocations remain unchanged.

### Project profile

Copy the configuration template:

```bash
cp ~/.config/wte/project.example.yaml.template \
   ~/.config/wte/example-project.yaml
```

The following profile describes a project with separate frontend and backend services:

```yaml
name: example-project

match:
  # Points to the project's main worktree directory.
  main_worktree: $HOME/code/example-app

ports:
  # Names of the ports to request. Add as many as the project needs;
  # each id must be unique within the profile.
  - id: frontend_port
  - id: backend_port

secrets:
  # Environment files shared through symlinks.
  # source points to the original environment file, while target is a path
  # relative to the worktree root.
  - source: $HOME/path/to/your/frontend.env
    target: frontend-dir/.env
  - source: $HOME/path/to/your/backend.env
    target: backend-dir/.env

writes:
  # Frontend configuration:
  - path: frontend-dir/.env.development
    body: |
      VITE_PORT=${frontend_port}
      SERVER_URL=http://127.0.0.1:${backend_port}

  # Backend configuration:
  - path: backend-dir/.env.development
    body: |
      PORT=${backend_port}
```

> This example applies when both the frontend and backend can load `.env.{env name}`
> files. Adjust it to match how your project loads environment variables.
>
> After a worktree directory is deleted, its ports are reclaimed the next time `wte`
> is triggered.

## Monitor

Some coding agents create worktrees inside a sandbox, which can prevent the `wte` hook from running.
To support these tools, enable the Monitor:

```bash
wte monitor enable
```

It watches the `.git/worktrees/` metadata directory associated with each configured
main worktree:

- macOS uses a LaunchAgent with `WatchPaths`.
- Linux uses a systemd user path unit.

When the directory changes, the operating system starts a short-lived Reconciler.
_It is not a resident daemon and does not poll on a timer_, so its resource usage is
minimal.

The Reconciler:

1. Runs `git worktree list --porcelain` to retrieve the actual list of worktrees.
2. Compares it with `ports.json`, then assigns ports, mounts secrets, and generates
   files for unregistered worktrees.

After adding a profile or changing a `main_worktree` path, run this command again:

```bash
wte monitor enable
```

To disable the Monitor, run:

```bash
wte monitor disable
```

Afterward, filesystem changes will no longer be monitored.

## Asynchronous initialization

`wte` can automatically run commands after a worktree is created, allowing the
environment to be initialized in the background:

```yaml
init:
  - command: npm install
    cwd: frontend-dir  # Use "." to run from the worktree root.
    skip_if: node_modules  # Skip this command if the file or directory exists.

  - command: uv sync
    cwd: backend-dir  # Use "." to run from the worktree root.
    skip_if: .venv  # Skip this command if the file or directory exists.
```

A typical timeline looks like this:

```text
Create a worktree
  → wte projects the environment and starts npm install in the background
  → The user describes the task; the AI reads, analyzes, and modifies the code
  → By the time the user or AI starts the project, dependencies are usually ready
```

Asynchronous initialization is started only by the normal `post-checkout` hook.
Neither `wte sync` nor the Monitor Reconciler runs these commands, preventing manual
synchronization or background reconciliation from repeatedly starting expensive
tasks.

## Commands supported by `wte`

```text
wte init              Create personal configuration and templates, and install core Git hooks
wte sync              Synchronize ports, secrets, and generated files for the current worktree
wte list              List port allocations for worktrees that still exist
wte doctor            Diagnose configuration, profiles, registry, secrets, hooks, and Monitor
wte monitor enable    Install or refresh optional host monitoring
wte monitor disable   Remove host monitoring only, preserving Git hooks
wte uninstall         Remove hooks and the Monitor, preserving configuration and runtime state
```

Run `wte sync` from inside the target worktree. It reuses existing ports, recreates
secret symlinks, and regenerates configuration files.
You may need it when a worktree is created through an unconventional method.

## License

[MIT](LICENSE)
