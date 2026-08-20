"""Machine-wide port-pool configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

from .paths import AppPaths
from .utils import WteError

DEFAULT_POOL_START = 35000
DEFAULT_POOL_END = 39999
DEFAULT_CONFIG = """# Inclusive range used for per-worktree port allocation.\nport_range:\n  start: 35000\n  end: 39999\n"""


@dataclass(frozen=True)
class PortPool:
    """Inclusive bounds of the machine-local port pool."""

    start: int
    end: int

    def validate(self) -> None:
        if not 1 <= self.start <= 65535:
            raise WteError(f"port_range.start is outside 1-65535: {self.start}")
        if not 1 <= self.end <= 65535:
            raise WteError(f"port_range.end is outside 1-65535: {self.end}")
        if self.end < self.start:
            raise WteError("port_range.end must be greater than or equal to port_range.start")


def _load_yaml_mapping(path: Path) -> Dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise WteError(f"cannot read YAML file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WteError(f"YAML root must be a mapping: {path}")
    return raw


def load_port_pool(paths: AppPaths) -> PortPool:
    """Load the configured pool, using defaults when config.yaml is absent."""
    if not paths.config.exists():
        pool = PortPool(DEFAULT_POOL_START, DEFAULT_POOL_END)
        pool.validate()
        return pool
    data = _load_yaml_mapping(paths.config)
    if "pool" in data and "port_range" not in data:
        raise WteError("config key 'pool' was renamed to 'port_range'")
    raw_pool = data.get("port_range") or {}
    if not isinstance(raw_pool, dict):
        raise WteError(f"port_range must be a mapping: {paths.config}")
    try:
        start = int(raw_pool.get("start") or DEFAULT_POOL_START)
        end = int(raw_pool.get("end") or DEFAULT_POOL_END)
    except (TypeError, ValueError) as exc:
        raise WteError("port_range.start and port_range.end must be integers") from exc
    pool = PortPool(start, end)
    pool.validate()
    return pool


def initialize_config(paths: AppPaths) -> bool:
    """Create the default config if absent; return whether it was created."""
    paths.ensure()
    if paths.config.exists():
        return False
    paths.config.write_text(DEFAULT_CONFIG)
    return True
