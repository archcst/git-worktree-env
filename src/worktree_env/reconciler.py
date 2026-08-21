"""Host-side reconciliation for worktrees created without Git hooks."""

from __future__ import annotations

import os
import plistlib
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

from .hooks import resolve_wte_executable
from .paths import AppPaths
from .profiles import configured_main_worktree, load_profiles, worktree_root
from .projector import apply_worktree, print_apply_result
from .registry import load_registry, prune_registry
from .utils import WteError, log, run_git

LAUNCHD_LABEL = "io.github.archcst.wte-reconciler"
SYSTEMD_SERVICE = "wte-reconciler.service"
SYSTEMD_PATH = "wte-reconciler.path"


@dataclass(frozen=True)
class MonitorStatus:
    """Installation and runtime state of the host filesystem monitor."""

    supported: bool
    installed: bool
    active: bool
    watch_paths: Tuple[Path, ...]
    detail: str


@dataclass(frozen=True)
class ReconcileResult:
    """Summary of one scan across all configured Git repositories."""

    discovered: int
    applied: int
    pending: int


def _run(command: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )


def _common_git_dir(main: Path) -> Optional[Path]:
    try:
        raw = Path(run_git("rev-parse", "--git-common-dir", cwd=main))
    except WteError:
        return None
    return (main / raw).resolve() if not raw.is_absolute() else raw.resolve()


def discover_watch_paths(paths: AppPaths) -> Tuple[Path, ...]:
    """Return each configured repository's linked-worktree metadata directory."""
    watched: Set[Path] = set()
    for profile in load_profiles(paths, strict=False):
        main = configured_main_worktree(profile)
        if main is None or not main.is_dir():
            continue
        common = _common_git_dir(main)
        if common is not None:
            watched.add(common / "worktrees")
    return tuple(sorted(watched))


def _listed_worktrees(main: Path) -> List[Path]:
    """Read worktree paths from Git's NUL-delimited porcelain output."""
    output = run_git("worktree", "list", "--porcelain", "-z", cwd=main)
    roots: List[Path] = []
    for field in output.split("\0"):
        if field.startswith("worktree "):
            roots.append(Path(field[len("worktree ") :]).resolve())
    return roots


def _candidate_worktrees(paths: AppPaths) -> Set[Path]:
    candidates: Set[Path] = set()
    for profile in load_profiles(paths):
        main = configured_main_worktree(profile)
        if main is None or not main.is_dir():
            continue
        try:
            candidates.update(_listed_worktrees(main))
        except WteError as exc:
            log(f"reconcile skipped repository {main}: {exc}")
    return candidates


def reconcile_once(paths: AppPaths) -> ReconcileResult:
    """Project configuration into worktrees missing from the port registry."""
    registered = set(prune_registry(load_registry(paths)))
    candidates = _candidate_worktrees(paths)
    applied = 0
    pending = 0

    for candidate in sorted(candidates):
        if str(candidate) in registered:
            continue
        try:
            # Git may publish common-dir metadata before checkout is complete.
            if worktree_root(candidate) != candidate:
                raise WteError(f"worktree root is not stable yet: {candidate}")
            # The reconciler is the fallback for sandboxed Git clients that
            # cannot invoke the host's post-checkout hook, so it must perform
            # the same asynchronous initialization for a new worktree.
            result = apply_worktree(paths, candidate, setup=True)
            if result is None:
                continue
            print_apply_result(result)
            registered.add(str(candidate))
            applied += 1
        except (OSError, WteError) as exc:
            pending += 1
            log(f"reconcile will retry {candidate}: {exc}")

    return ReconcileResult(len(candidates), applied, pending)


def reconcile_with_retry(paths: AppPaths) -> ReconcileResult:
    """Retry briefly while a newly announced checkout becomes readable."""
    result = reconcile_once(paths)
    for delay in (0.5, 1.0, 2.0):
        if result.pending == 0:
            break
        time.sleep(delay)
        result = reconcile_once(paths)
    return result


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _launch_domain() -> str:
    return f"gui/{os.getuid()}"


def _write_launch_agent(paths: AppPaths, executable: Path, watched: Tuple[Path, ...]) -> Path:
    agent = _launch_agent_path()
    agent.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [str(executable), "_reconcile"],
        "RunAtLoad": True,
        "WatchPaths": [str(path) for path in watched],
        "ProcessType": "Background",
        "ThrottleInterval": 2,
        "StandardOutPath": str(paths.state / "reconciler.log"),
        "StandardErrorPath": str(paths.state / "reconciler.log"),
    }
    with agent.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    return agent


def _install_launchd(paths: AppPaths, executable: Path, watched: Tuple[Path, ...]) -> MonitorStatus:
    agent = _write_launch_agent(paths, executable, watched)
    _run(["launchctl", "bootout", f"{_launch_domain()}/{LAUNCHD_LABEL}"])
    result = _run(["launchctl", "bootstrap", _launch_domain(), str(agent)])
    if result.returncode != 0:
        raise WteError(result.stderr.strip() or "launchctl bootstrap failed")
    return monitor_status(paths, watched)


def _systemd_user_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(os.path.expanduser(xdg)) if xdg else Path.home() / ".config"
    return base / "systemd" / "user"


def _write_systemd_units(executable: Path, watched: Tuple[Path, ...]) -> Tuple[Path, Path]:
    unit_dir = _systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    service = unit_dir / SYSTEMD_SERVICE
    path_unit = unit_dir / SYSTEMD_PATH
    escaped_executable = str(executable).replace("%", "%%")
    service.write_text(
        "[Unit]\nDescription=Reconcile worktree-env projects\n\n"
        "[Service]\nType=oneshot\n"
        f'ExecStart="{escaped_executable}" _reconcile\n'
    )
    path_lines = "\n".join(
        f"PathChanged={str(path).replace('%', '%%')}" for path in watched
    )
    path_unit.write_text(
        "[Unit]\nDescription=Watch Git linked-worktree metadata for wte\n\n"
        f"[Path]\n{path_lines}\nUnit={SYSTEMD_SERVICE}\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    return service, path_unit


def _install_systemd(paths: AppPaths, executable: Path, watched: Tuple[Path, ...]) -> MonitorStatus:
    _write_systemd_units(executable, watched)
    reload_result = _run(["systemctl", "--user", "daemon-reload"])
    if reload_result.returncode != 0:
        raise WteError(reload_result.stderr.strip() or "systemd user manager is unavailable")
    result = _run(["systemctl", "--user", "enable", "--now", SYSTEMD_PATH])
    if result.returncode != 0:
        raise WteError(result.stderr.strip() or "cannot enable wte-reconciler.path")
    return monitor_status(paths, watched)


def install_monitor(paths: AppPaths) -> MonitorStatus:
    """Install or refresh the OS-managed directory monitor for current profiles."""
    watched = discover_watch_paths(paths)
    if not watched:
        uninstall_monitor(paths)
        return MonitorStatus(
            True,
            False,
            False,
            (),
            "no configured main worktrees; add a profile and run `wte monitor enable`",
        )
    executable = resolve_wte_executable()
    system = platform.system()
    if system == "Darwin":
        return _install_launchd(paths, executable, watched)
    if system == "Linux":
        return _install_systemd(paths, executable, watched)
    return MonitorStatus(False, False, False, watched, f"unsupported platform: {system}")


def _uninstall_launchd() -> None:
    _run(["launchctl", "bootout", f"{_launch_domain()}/{LAUNCHD_LABEL}"])
    agent = _launch_agent_path()
    if agent.exists():
        agent.unlink()


def _uninstall_systemd() -> None:
    _run(["systemctl", "--user", "disable", "--now", SYSTEMD_PATH])
    unit_dir = _systemd_user_dir()
    for name in (SYSTEMD_PATH, SYSTEMD_SERVICE):
        unit = unit_dir / name
        if unit.exists():
            unit.unlink()
    _run(["systemctl", "--user", "daemon-reload"])


def uninstall_monitor(paths: AppPaths) -> None:
    """Remove any platform-specific reconciler registration."""
    system = platform.system()
    if system == "Darwin":
        _uninstall_launchd()
    elif system == "Linux":
        _uninstall_systemd()


def monitor_status(
    paths: AppPaths,
    watched: Optional[Tuple[Path, ...]] = None,
) -> MonitorStatus:
    """Inspect the current platform's reconciler registration."""
    watched = watched if watched is not None else discover_watch_paths(paths)
    system = platform.system()
    if system == "Darwin":
        installed = _launch_agent_path().is_file()
        active = _run(["launchctl", "print", f"{_launch_domain()}/{LAUNCHD_LABEL}"]).returncode == 0
        return MonitorStatus(True, installed, active, watched, "launchd")
    if system == "Linux":
        path_unit = _systemd_user_dir() / SYSTEMD_PATH
        active = _run(["systemctl", "--user", "is-active", "--quiet", SYSTEMD_PATH]).returncode == 0
        return MonitorStatus(True, path_unit.is_file(), active, watched, "systemd.path")
    return MonitorStatus(False, False, False, watched, f"unsupported platform: {system}")
