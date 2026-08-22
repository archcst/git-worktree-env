"""Concurrent, sticky allocation of per-worktree port blocks."""

from __future__ import annotations

import fcntl
import json
import os
import socket
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Set, Tuple

from .config import PortPool
from .paths import AppPaths
from .profiles import Profile, port_claims_value
from .utils import WteError

Registry = Dict[str, Any]


def load_registry(paths: AppPaths) -> Registry:
    """Load the registry and reject corruption instead of silently resetting it."""
    if not paths.registry.exists():
        return {}
    try:
        raw = json.loads(paths.registry.read_text() or "{}")
    except (OSError, json.JSONDecodeError) as exc:
        raise WteError(f"cannot read port registry {paths.registry}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WteError(f"port registry root must be an object: {paths.registry}")
    return raw


def save_registry(paths: AppPaths, registry: Registry) -> None:
    """Atomically replace the registry while preserving a valid old copy on crash."""
    paths.root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(registry, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".ports-", suffix=".tmp", dir=paths.root)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, paths.registry)
        try:
            directory_fd = os.open(str(paths.root), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Directory fsync is not available on every supported filesystem.
            pass
    finally:
        if temp_path.exists():
            temp_path.unlink()


@contextmanager
def registry_lock(paths: AppPaths) -> Iterator[None]:
    """Serialize registry reads, allocation, garbage collection, and writes."""
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.lock.touch(exist_ok=True)
    try:
        os.chmod(paths.lock, 0o600)
    except OSError:
        pass
    with paths.lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def is_live_worktree(path: str) -> bool:
    """Return whether a registered worktree directory still exists."""
    return Path(path).is_dir()


def prune_registry(registry: Registry) -> Registry:
    """Drop entries whose worktree directory no longer exists."""
    return {
        path: metadata
        for path, metadata in registry.items()
        if isinstance(metadata, dict) and is_live_worktree(path)
    }


def port_is_free(port: int) -> bool:
    """Return whether a new process can bind the port on loopback."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _used_by_other_worktrees(registry: Registry, current: str) -> Set[int]:
    used: Set[int] = set()
    for path, metadata in registry.items():
        if path == current or not isinstance(metadata, dict) or not Path(path).exists():
            continue
        ports = metadata.get("ports") or {}
        if not isinstance(ports, dict):
            continue
        for raw_port in ports.values():
            try:
                used.add(int(raw_port))
            except (TypeError, ValueError):
                continue
    return used


def allocate_ports(
    profile: Profile,
    root: Path,
    registry: Registry,
    pool: PortPool,
) -> Tuple[int, Dict[str, int]]:
    """Reuse a valid sticky allocation or claim the first free contiguous block."""
    claims = port_claims_value(profile)
    if not isinstance(claims, list) or not claims:
        raise WteError(f"profile {profile.get('name')} has no ports to claim")
    try:
        ids = [str(item["id"]) for item in claims]
    except (TypeError, KeyError) as exc:
        raise WteError(f"profile {profile.get('name')} has an invalid port claim") from exc

    count = len(ids)
    current = str(root)
    used = _used_by_other_worktrees(registry, current)
    existing = registry.get(current) if isinstance(registry.get(current), dict) else None
    profile_file = Path(profile.get("_file") or "").name
    if existing:
        same_profile = existing.get("profile") == profile.get("name")
        # File identity preserves allocations created before profiles had unique names.
        same_file = bool(profile_file) and existing.get("file") == profile_file
        saved = existing.get("ports") or {}
        if (same_profile or same_file) and isinstance(saved, dict):
            try:
                values = [int(saved[port_id]) for port_id in ids]
            except (KeyError, TypeError, ValueError):
                values = []
            if (
                len(values) == count
                and all(pool.start <= port <= pool.end for port in values)
                and not (set(values) & used)
            ):
                return min(values), dict(zip(ids, values))

    last_start = pool.end - count + 1
    for start in range(pool.start, last_start + 1):
        ports = list(range(start, start + count))
        if set(ports) & used:
            continue
        if all(port_is_free(port) for port in ports):
            return start, dict(zip(ids, ports))
    raise WteError(f"no free block of {count} ports in pool {pool.start}-{pool.end}")
