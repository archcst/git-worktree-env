"""Command-line interface for wte."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from .config import initialize_config, load_port_pool
from .hooks import hooks_status, install_hooks, uninstall_hooks
from .paths import AppPaths
from .profiles import load_profiles, validate_profiles, worktree_root
from .projector import apply_worktree, print_apply_result
from .registry import load_registry, prune_registry, registry_lock, save_registry
from .utils import WteError, expand_profile_path, log, run_git


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wte",
        description="Per-worktree ports, secrets, and local environment setup.",
    )
    parser.add_argument("--version", action="version", version=f"wte {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="create the user configuration directory")

    apply_parser = commands.add_parser("apply", help="apply a profile to a worktree")
    apply_parser.add_argument("path", nargs="?", help="worktree path; defaults to the current repository")

    commands.add_parser("list", help="list live worktree port allocations")
    commands.add_parser("gc", help="remove stale or invalid worktree allocations")

    release_parser = commands.add_parser("release", help="release one worktree allocation")
    release_parser.add_argument("path", help="worktree path recorded in the registry")

    commands.add_parser("validate", help="validate configuration and project profiles")
    commands.add_parser("doctor", help="diagnose configuration and hook installation")

    hook_parser = commands.add_parser("hook", help=argparse.SUPPRESS)
    hook_parser.add_argument("hook_args", nargs="*")

    hooks_parser = commands.add_parser("hooks", help="manage the global Git hook dispatcher")
    hooks_commands = hooks_parser.add_subparsers(dest="hooks_command", required=True)
    install_parser = hooks_commands.add_parser("install", help="install the dispatcher")
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="replace and chain an existing global core.hooksPath",
    )
    hooks_commands.add_parser("uninstall", help="remove the dispatcher and restore hooksPath")
    hooks_commands.add_parser("status", help="show dispatcher status")
    return parser


def _resolve_apply_root(raw: Optional[str]) -> Path:
    return Path(raw).resolve() if raw else worktree_root()


def _cmd_init(paths: AppPaths) -> int:
    created = initialize_config(paths)
    print(f"[wte] config directory: {paths.root}")
    print(f"[wte] {'created' if created else 'kept existing'}: {paths.config}")
    print(f"[wte] profiles: {paths.profiles}/*.yaml")
    return 0


def _cmd_apply(paths: AppPaths, raw_path: Optional[str], setup: bool = False) -> int:
    result = apply_worktree(paths, _resolve_apply_root(raw_path), setup=setup)
    if result is not None:
        print_apply_result(result)
    return 0


def _cmd_list(paths: AppPaths) -> int:
    registry = prune_registry(load_registry(paths))
    print(json.dumps(registry, indent=2, sort_keys=True) if registry else "(empty)")
    return 0


def _cmd_gc(paths: AppPaths) -> int:
    with registry_lock(paths):
        registry = load_registry(paths)
        cleaned = prune_registry(registry, verify_git=True)
        removed = len(registry) - len(cleaned)
        save_registry(paths, cleaned)
    print(f"[wte] removed {removed} stale allocation(s); {len(cleaned)} remain")
    return 0


def _cmd_release(paths: AppPaths, raw_path: str) -> int:
    target = str(Path(raw_path).resolve())
    with registry_lock(paths):
        registry = load_registry(paths)
        existed = target in registry
        registry.pop(target, None)
        if existed:
            save_registry(paths, registry)
    if existed:
        print(f"[wte] released: {target}")
        return 0
    raise WteError(f"worktree is not registered: {target}")


def _print_validation(paths: AppPaths) -> int:
    load_port_pool(paths)
    errors, warnings = validate_profiles(paths)
    for warning in warnings:
        print(f"[wte] warning: {warning}")
    for error in errors:
        print(f"[wte] error: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"[wte] configuration is valid ({len(load_profiles(paths))} profile(s))")
    return 0


def _cmd_doctor(paths: AppPaths) -> int:
    failures = 0
    print(f"[wte] config: {paths.root}")
    try:
        print(f"[wte] git: {run_git('--version')}")
    except WteError as exc:
        print(f"[wte] error: {exc}", file=sys.stderr)
        failures += 1

    validation_status = _print_validation(paths)
    failures += int(validation_status != 0)
    try:
        registry = load_registry(paths)
        print(f"[wte] registry: {paths.registry} ({len(registry)} allocation(s))")
    except WteError as exc:
        print(f"[wte] error: {exc}", file=sys.stderr)
        failures += 1

    status = hooks_status(paths)
    if status["installed"]:
        print(f"[wte] hooks: installed at {status['expected_hooks_path']}")
        print(f"[wte] hook executable: {status.get('wte_executable')}")
    else:
        print(f"[wte] warning: hooks are not installed (current: {status['current_hooks_path']})")

    for profile in load_profiles(paths, strict=False):
        for entry in profile.get("secrets") or []:
            if not isinstance(entry, dict) or not entry.get("source"):
                continue
            source = expand_profile_path(str(entry["source"]), profile).resolve()
            if not source.is_file():
                print(f"[wte] warning: missing secret source: {source}")
    return 1 if failures else 0


def _cmd_hook(paths: AppPaths) -> int:
    """Run from post-checkout and never block the Git operation."""
    try:
        return _cmd_apply(paths, None, setup=True)
    except Exception as exc:  # Hooks must remain fail-open, including unexpected errors.
        log(f"hook skipped: {exc}")
        return 0


def _cmd_hooks(paths: AppPaths, command: str, force: bool = False) -> int:
    if command == "install":
        dispatcher = install_hooks(paths, force=force)
        print(f"[wte] hooks installed: {dispatcher.parent}")
        return 0
    if command == "uninstall":
        previous = uninstall_hooks(paths)
        print("[wte] hooks uninstalled")
        print(f"[wte] restored core.hooksPath: {previous or '(unset)'}")
        return 0
    status = hooks_status(paths)
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse arguments and return a process exit status."""
    args = _build_parser().parse_args(argv)
    paths = AppPaths.discover()
    try:
        if args.command == "init":
            return _cmd_init(paths)
        if args.command == "apply":
            return _cmd_apply(paths, args.path)
        if args.command == "list":
            return _cmd_list(paths)
        if args.command == "gc":
            return _cmd_gc(paths)
        if args.command == "release":
            return _cmd_release(paths, args.path)
        if args.command == "validate":
            return _print_validation(paths)
        if args.command == "doctor":
            return _cmd_doctor(paths)
        if args.command == "hook":
            return _cmd_hook(paths)
        if args.command == "hooks":
            return _cmd_hooks(paths, args.hooks_command, getattr(args, "force", False))
        raise WteError(f"unknown command: {args.command}")
    except WteError as exc:
        log(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
