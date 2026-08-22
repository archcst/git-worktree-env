"""Host-side reconciliation for worktrees created without Git hooks."""

from __future__ import annotations

import fcntl
import os
import plistlib
import platform
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Set, Tuple

from .hooks import resolve_wte_executable
from .paths import AppPaths
from .profiles import configured_main_worktree, load_profiles, worktree_root
from .projector import apply_worktree, print_apply_result
from .registry import load_registry, prune_registry
from .utils import WteError, log, run_git

LAUNCHD_LABEL = "io.github.archcst.wte-reconciler"
PROFILE_LAUNCHD_LABEL = "io.github.archcst.wte-profile-monitor"
PROFILE_FILES_LAUNCHD_LABEL = "io.github.archcst.wte-profile-files-monitor"
SYSTEMD_SERVICE = "wte-reconciler.service"
SYSTEMD_PATH = "wte-reconciler.path"
PROFILE_SYSTEMD_SERVICE = "wte-profile-monitor.service"
PROFILE_SYSTEMD_PATH = "wte-profile-monitor.path"
PROFILE_FILES_SYSTEMD_SERVICE = "wte-profile-files-monitor.service"
PROFILE_FILES_SYSTEMD_PATH = "wte-profile-files-monitor.path"


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


def discover_watch_paths(
    paths: AppPaths, strict: bool = False
) -> Tuple[Path, ...]:
    """Return each configured repository's linked-worktree metadata directory."""
    watched: Set[Path] = set()
    for profile in load_profiles(paths, strict=strict):
        main = configured_main_worktree(profile)
        if main is None or not main.is_dir():
            continue
        common = _common_git_dir(main)
        if common is not None:
            watched.add(common / "worktrees")
    return tuple(sorted(watched))


def discover_profile_paths(paths: AppPaths) -> Tuple[Path, ...]:
    """Return profile files whose in-place changes must be monitored."""
    files: Set[Path] = set()
    for pattern in ("*.yaml", "*.yml", "*.json"):
        files.update(paths.profiles.glob(pattern))
    files.discard(paths.config)
    return tuple(sorted(files))


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


def _reconcile_once_unlocked(paths: AppPaths) -> ReconcileResult:
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


@contextmanager
def _state_lock(paths: AppPaths, name: str) -> Iterator[None]:
    """Serialize one category of host monitor state changes."""
    paths.ensure()
    lock = paths.state / name
    if not lock.exists():
        lock.touch(exist_ok=True)
    with lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reconcile_once(paths: AppPaths) -> ReconcileResult:
    """Serialize and project configuration into unregistered worktrees."""
    with _state_lock(paths, "reconciler.lock"):
        return _reconcile_once_unlocked(paths)


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


def _profile_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PROFILE_LAUNCHD_LABEL}.plist"


def _profile_files_launch_agent_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "LaunchAgents"
        / f"{PROFILE_FILES_LAUNCHD_LABEL}.plist"
    )


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


def _write_profile_launch_agent(paths: AppPaths, executable: Path) -> Path:
    agent = _profile_launch_agent_path()
    agent.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": PROFILE_LAUNCHD_LABEL,
        "ProgramArguments": [str(executable), "_profile_set_changed"],
        "RunAtLoad": False,
        "WatchPaths": [str(paths.profiles)],
        "ProcessType": "Background",
        "ThrottleInterval": 2,
        "StandardOutPath": str(paths.state / "reconciler.log"),
        "StandardErrorPath": str(paths.state / "reconciler.log"),
    }
    with agent.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    return agent


def _write_profile_files_launch_agent(
    paths: AppPaths, executable: Path, watched: Tuple[Path, ...]
) -> Path:
    agent = _profile_files_launch_agent_path()
    agent.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": PROFILE_FILES_LAUNCHD_LABEL,
        "ProgramArguments": [str(executable), "_profiles_changed"],
        "RunAtLoad": False,
        "WatchPaths": [str(path) for path in watched],
        "ProcessType": "Background",
        "ThrottleInterval": 2,
        "StandardOutPath": str(paths.state / "reconciler.log"),
        "StandardErrorPath": str(paths.state / "reconciler.log"),
    }
    with agent.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    return agent


def _bootstrap_launch_agent(label: str, agent: Path) -> None:
    _run(["launchctl", "bootout", f"{_launch_domain()}/{label}"])
    result = _run(["launchctl", "bootstrap", _launch_domain(), str(agent)])
    if result.returncode != 0:
        raise WteError(result.stderr.strip() or "launchctl bootstrap failed")


def _install_launchd(paths: AppPaths, executable: Path, watched: Tuple[Path, ...]) -> MonitorStatus:
    _bootstrap_launch_agent(LAUNCHD_LABEL, _write_launch_agent(paths, executable, watched))
    return monitor_status(paths, watched)


def _install_profile_launchd(paths: AppPaths, executable: Path) -> None:
    _bootstrap_launch_agent(
        PROFILE_LAUNCHD_LABEL, _write_profile_launch_agent(paths, executable)
    )


def _install_profile_files_launchd(
    paths: AppPaths, executable: Path, watched: Tuple[Path, ...]
) -> None:
    _bootstrap_launch_agent(
        PROFILE_FILES_LAUNCHD_LABEL,
        _write_profile_files_launch_agent(paths, executable, watched),
    )


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


def _write_profile_systemd_units(paths: AppPaths, executable: Path) -> Tuple[Path, Path]:
    unit_dir = _systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    service = unit_dir / PROFILE_SYSTEMD_SERVICE
    path_unit = unit_dir / PROFILE_SYSTEMD_PATH
    escaped_executable = str(executable).replace("%", "%%")
    escaped_profiles = str(paths.profiles).replace("%", "%%")
    service.write_text(
        "[Unit]\nDescription=Refresh worktree-env profile monitoring\n\n"
        "[Service]\nType=oneshot\n"
        f'ExecStart="{escaped_executable}" _profile_set_changed\n'
    )
    path_unit.write_text(
        "[Unit]\nDescription=Watch worktree-env profiles\n\n"
        f"[Path]\nPathChanged={escaped_profiles}\nUnit={PROFILE_SYSTEMD_SERVICE}\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    return service, path_unit


def _write_profile_files_systemd_units(
    executable: Path, watched: Tuple[Path, ...]
) -> Tuple[Path, Path]:
    unit_dir = _systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    service = unit_dir / PROFILE_FILES_SYSTEMD_SERVICE
    path_unit = unit_dir / PROFILE_FILES_SYSTEMD_PATH
    escaped_executable = str(executable).replace("%", "%%")
    service.write_text(
        "[Unit]\nDescription=Refresh worktree-env after profile changes\n\n"
        "[Service]\nType=oneshot\n"
        f'ExecStart="{escaped_executable}" _profiles_changed\n'
    )
    path_lines = "\n".join(
        f"PathModified={str(path).replace('%', '%%')}" for path in watched
    )
    path_unit.write_text(
        "[Unit]\nDescription=Watch worktree-env profile files\n\n"
        f"[Path]\n{path_lines}\nUnit={PROFILE_FILES_SYSTEMD_SERVICE}\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    return service, path_unit


def _reload_systemd() -> None:
    result = _run(["systemctl", "--user", "daemon-reload"])
    if result.returncode != 0:
        raise WteError(result.stderr.strip() or "systemd user manager is unavailable")


def _enable_systemd_path(unit: str) -> None:
    result = _run(["systemctl", "--user", "enable", "--now", unit])
    if result.returncode != 0:
        raise WteError(result.stderr.strip() or f"cannot enable {unit}")
    result = _run(["systemctl", "--user", "restart", unit])
    if result.returncode != 0:
        raise WteError(result.stderr.strip() or f"cannot restart {unit}")


def _install_systemd(paths: AppPaths, executable: Path, watched: Tuple[Path, ...]) -> MonitorStatus:
    _write_systemd_units(executable, watched)
    _reload_systemd()
    _enable_systemd_path(SYSTEMD_PATH)
    return monitor_status(paths, watched)


def _install_profile_systemd(paths: AppPaths, executable: Path) -> None:
    _write_profile_systemd_units(paths, executable)
    _reload_systemd()
    _enable_systemd_path(PROFILE_SYSTEMD_PATH)


def _install_profile_files_systemd(
    executable: Path, watched: Tuple[Path, ...]
) -> None:
    _write_profile_files_systemd_units(executable, watched)
    _reload_systemd()
    _enable_systemd_path(PROFILE_FILES_SYSTEMD_PATH)


def _uninstall_launchd_agent(label: str, agent: Path) -> None:
    _run(["launchctl", "bootout", f"{_launch_domain()}/{label}"])
    if agent.exists():
        agent.unlink()


def _uninstall_worktree_launchd() -> None:
    _uninstall_launchd_agent(LAUNCHD_LABEL, _launch_agent_path())


def _uninstall_profile_files_launchd() -> None:
    _uninstall_launchd_agent(
        PROFILE_FILES_LAUNCHD_LABEL, _profile_files_launch_agent_path()
    )


def _uninstall_launchd() -> None:
    _uninstall_worktree_launchd()
    _uninstall_profile_files_launchd()
    _uninstall_launchd_agent(PROFILE_LAUNCHD_LABEL, _profile_launch_agent_path())


def _remove_systemd_units(path_name: str, service_name: str) -> None:
    _run(["systemctl", "--user", "disable", "--now", path_name])
    unit_dir = _systemd_user_dir()
    for name in (path_name, service_name):
        unit = unit_dir / name
        if unit.exists():
            unit.unlink()


def _uninstall_worktree_systemd() -> None:
    _remove_systemd_units(SYSTEMD_PATH, SYSTEMD_SERVICE)
    _run(["systemctl", "--user", "daemon-reload"])


def _uninstall_profile_files_systemd() -> None:
    _remove_systemd_units(PROFILE_FILES_SYSTEMD_PATH, PROFILE_FILES_SYSTEMD_SERVICE)
    _run(["systemctl", "--user", "daemon-reload"])


def _uninstall_systemd() -> None:
    _remove_systemd_units(SYSTEMD_PATH, SYSTEMD_SERVICE)
    _remove_systemd_units(
        PROFILE_FILES_SYSTEMD_PATH, PROFILE_FILES_SYSTEMD_SERVICE
    )
    _remove_systemd_units(PROFILE_SYSTEMD_PATH, PROFILE_SYSTEMD_SERVICE)
    _run(["systemctl", "--user", "daemon-reload"])


def _launchd_watch_paths(agent: Path) -> Tuple[Path, ...]:
    if not agent.is_file():
        return ()
    try:
        with agent.open("rb") as handle:
            raw = plistlib.load(handle).get("WatchPaths", [])
        return tuple(sorted(Path(str(path)) for path in raw))
    except (OSError, ValueError, plistlib.InvalidFileException):
        return ()


def _systemd_watch_paths(path_name: str) -> Tuple[Path, ...]:
    path_unit = _systemd_user_dir() / path_name
    if not path_unit.is_file():
        return ()
    try:
        values = [
            line.split("=", 1)[1].replace("%%", "%")
            for line in path_unit.read_text().splitlines()
            if line.startswith(("PathChanged=", "PathModified="))
        ]
    except OSError:
        return ()
    return tuple(sorted(Path(value) for value in values))


def _configured_watch_paths(
    launch_agent: Path, systemd_path: str
) -> Tuple[Path, ...]:
    system = platform.system()
    if system == "Darwin":
        return _launchd_watch_paths(launch_agent)
    if system == "Linux":
        return _systemd_watch_paths(systemd_path)
    return ()


def _configured_worktree_watch_paths() -> Tuple[Path, ...]:
    """Read linked-worktree paths from the installed platform registration."""
    return _configured_watch_paths(_launch_agent_path(), SYSTEMD_PATH)


def _configured_profile_file_watch_paths() -> Tuple[Path, ...]:
    """Read profile-file paths from the installed platform registration."""
    return _configured_watch_paths(
        _profile_files_launch_agent_path(), PROFILE_FILES_SYSTEMD_PATH
    )


def _refresh_profile_file_monitor(paths: AppPaths, force: bool = False) -> bool:
    """Refresh in-place file watches after profiles are added or replaced."""
    watched = discover_profile_paths(paths)
    if not force and _configured_profile_file_watch_paths() == watched:
        return False
    executable = resolve_wte_executable()
    system = platform.system()
    if system == "Darwin":
        if watched:
            _install_profile_files_launchd(paths, executable, watched)
        else:
            _uninstall_profile_files_launchd()
    elif system == "Linux":
        if watched:
            _install_profile_files_systemd(executable, watched)
        else:
            _uninstall_profile_files_systemd()
    else:
        raise WteError(f"unsupported platform: {system}")
    return True


def _profile_monitor_is_installed() -> bool:
    system = platform.system()
    if system == "Darwin":
        return _profile_launch_agent_path().is_file()
    if system == "Linux":
        return (_systemd_user_dir() / PROFILE_SYSTEMD_PATH).is_file()
    return False


def refresh_profile_file_monitor(paths: AppPaths, force: bool = False) -> bool:
    """Serialize updates to in-place profile-file watches."""
    with _state_lock(paths, "monitor-refresh.lock"):
        if not _profile_monitor_is_installed():
            return False
        return _refresh_profile_file_monitor(paths, force=force)


def _refresh_worktree_monitor(paths: AppPaths) -> bool:
    """Refresh repository watches after a valid profile-set change."""
    watched = discover_watch_paths(paths, strict=True)
    if _configured_worktree_watch_paths() == watched:
        return False
    executable = resolve_wte_executable()
    system = platform.system()
    if system == "Darwin":
        if watched:
            _install_launchd(paths, executable, watched)
        else:
            _uninstall_worktree_launchd()
    elif system == "Linux":
        if watched:
            _install_systemd(paths, executable, watched)
        else:
            _uninstall_worktree_systemd()
    else:
        raise WteError(f"unsupported platform: {system}")
    return True


def refresh_worktree_monitor(paths: AppPaths) -> bool:
    """Serialize updates to linked-worktree metadata watches."""
    with _state_lock(paths, "monitor-refresh.lock"):
        if not _profile_monitor_is_installed():
            return False
        return _refresh_worktree_monitor(paths)


def _install_monitor(paths: AppPaths) -> MonitorStatus:
    """Install profile and linked-worktree filesystem monitors."""
    paths.ensure()
    watched = discover_watch_paths(paths)
    profile_files = discover_profile_paths(paths)
    executable = resolve_wte_executable()
    system = platform.system()
    if system == "Darwin":
        if watched:
            _install_launchd(paths, executable, watched)
        else:
            _uninstall_worktree_launchd()
        if profile_files:
            _install_profile_files_launchd(paths, executable, profile_files)
        else:
            _uninstall_profile_files_launchd()
        _install_profile_launchd(paths, executable)
        return monitor_status(paths, watched)
    if system == "Linux":
        if watched:
            _install_systemd(paths, executable, watched)
        else:
            _uninstall_worktree_systemd()
        if profile_files:
            _install_profile_files_systemd(executable, profile_files)
        else:
            _uninstall_profile_files_systemd()
        _install_profile_systemd(paths, executable)
        return monitor_status(paths, watched)
    return MonitorStatus(False, False, False, watched, f"unsupported platform: {system}")


def install_monitor(paths: AppPaths) -> MonitorStatus:
    """Serialize installation of all filesystem monitors."""
    with _state_lock(paths, "monitor-refresh.lock"):
        return _install_monitor(paths)


def _uninstall_monitor(paths: AppPaths) -> None:
    """Remove all platform-specific monitor registrations."""
    system = platform.system()
    if system == "Darwin":
        _uninstall_launchd()
    elif system == "Linux":
        _uninstall_systemd()


def uninstall_monitor(paths: AppPaths) -> None:
    """Serialize removal of all filesystem monitors."""
    with _state_lock(paths, "monitor-refresh.lock"):
        _uninstall_monitor(paths)


def monitor_status(
    paths: AppPaths,
    watched: Optional[Tuple[Path, ...]] = None,
) -> MonitorStatus:
    """Inspect the current platform's profile and worktree monitors."""
    watched = watched if watched is not None else discover_watch_paths(paths)
    profile_files = discover_profile_paths(paths)
    system = platform.system()
    if system == "Darwin":
        installed = _profile_launch_agent_path().is_file()
        profile_active = _run(
            ["launchctl", "print", f"{_launch_domain()}/{PROFILE_LAUNCHD_LABEL}"]
        ).returncode == 0
        profile_files_active = not profile_files or _run(
            [
                "launchctl",
                "print",
                f"{_launch_domain()}/{PROFILE_FILES_LAUNCHD_LABEL}",
            ]
        ).returncode == 0
        worktree_active = not watched or _run(
            ["launchctl", "print", f"{_launch_domain()}/{LAUNCHD_LABEL}"]
        ).returncode == 0
        return MonitorStatus(
            True,
            installed,
            profile_active and profile_files_active and worktree_active,
            watched,
            "launchd",
        )
    if system == "Linux":
        unit_dir = _systemd_user_dir()
        installed = (unit_dir / PROFILE_SYSTEMD_PATH).is_file()
        profile_active = _run(
            ["systemctl", "--user", "is-active", "--quiet", PROFILE_SYSTEMD_PATH]
        ).returncode == 0
        profile_files_active = not profile_files or _run(
            [
                "systemctl",
                "--user",
                "is-active",
                "--quiet",
                PROFILE_FILES_SYSTEMD_PATH,
            ]
        ).returncode == 0
        worktree_active = not watched or _run(
            ["systemctl", "--user", "is-active", "--quiet", SYSTEMD_PATH]
        ).returncode == 0
        return MonitorStatus(
            True,
            installed,
            profile_active and profile_files_active and worktree_active,
            watched,
            "systemd.path",
        )
    return MonitorStatus(False, False, False, watched, f"unsupported platform: {system}")
