"""Filesystem locations used by wte.

Application code and user data are intentionally separate. The package can be
installed anywhere, while mutable configuration stays under the user's XDG
configuration directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    """Resolved paths for one wte configuration directory."""

    root: Path
    config: Path
    profiles: Path
    state: Path
    registry: Path
    lock: Path
    hooks: Path
    hooks_state: Path

    @classmethod
    def discover(cls) -> "AppPaths":
        """Resolve paths from the environment.

        ``WTE_CONFIG_HOME`` is primarily useful for tests and portable setups.
        Otherwise wte follows ``XDG_CONFIG_HOME`` and falls back to
        ``~/.config/wte``.
        """
        override = os.environ.get("WTE_CONFIG_HOME")
        if override:
            root = Path(os.path.expanduser(override)).resolve()
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME")
            base = Path(os.path.expanduser(xdg)) if xdg else Path.home() / ".config"
            root = (base / "wte").resolve()
        state = root / "state"
        return cls(
            root=root,
            config=root / "config.yaml",
            profiles=root,
            state=state,
            registry=state / "ports.json",
            lock=state / "ports.lock",
            hooks=root / "hooks",
            hooks_state=state / "hooks-state.json",
        )

    def ensure(self) -> None:
        """Create private configuration and state directories."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        for directory in (self.root, self.state):
            try:
                directory.chmod(0o700)
            except OSError:
                # Some network filesystems do not expose POSIX permission bits.
                pass
