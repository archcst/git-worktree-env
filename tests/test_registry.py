import json
from pathlib import Path

import pytest

from worktree_env.config import PortPool
from worktree_env.registry import (
    allocate_ports,
    load_registry,
    prune_registry,
    save_registry,
)
from worktree_env.utils import WteError


def test_allocation_is_sticky_and_contiguous(app_paths, tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    root.mkdir()
    profile = {
        "name": "example",
        "_file": str(tmp_path / "example.yaml"),
        "ports": [{"id": "frontend"}, {"id": "backend"}],
    }
    monkeypatch.setattr("worktree_env.registry.port_is_free", lambda _port: True)

    start, ports = allocate_ports(profile, root, {}, PortPool(42000, 42010))
    registry = {
        str(root): {
            "profile": "example",
            "file": "example.yaml",
            "ports": ports,
        }
    }
    second_start, second_ports = allocate_ports(profile, root, registry, PortPool(42000, 42010))

    assert start == second_start == 42000
    assert ports == second_ports == {"frontend": 42000, "backend": 42001}


def test_atomic_registry_round_trip_and_corruption_error(app_paths):
    save_registry(app_paths, {"/tmp/example": {"ports": {"web": 42000}}})
    assert load_registry(app_paths)["/tmp/example"]["ports"]["web"] == 42000

    app_paths.registry.write_text("not json")
    with pytest.raises(WteError, match="cannot read port registry"):
        load_registry(app_paths)


def test_prune_drops_missing_directories(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    registry = {str(live): {}, str(tmp_path / "missing"): {}}

    assert list(prune_registry(registry)) == [str(live)]
