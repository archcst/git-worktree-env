"""Project-profile loading, validation, and worktree matching."""

from __future__ import annotations

import json
import re
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from .paths import AppPaths
from .utils import WteError, expand_home, run_git

Profile = Dict[str, Any]
PORT_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_profile(path: Path) -> Profile:
    """Parse one YAML or JSON profile and attach its source path."""
    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            raw = yaml.safe_load(path.read_text()) or {}
        else:
            raw = json.loads(path.read_text() or "{}")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise WteError(f"cannot parse profile {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WteError(f"profile root must be a mapping: {path}")
    raw["_file"] = str(path)
    return raw


def load_profiles(paths: AppPaths, strict: bool = True) -> List[Profile]:
    """Load profiles, preferring YAML when files share the same stem."""
    if not paths.profiles.is_dir():
        return []
    rank = {".yaml": 0, ".yml": 1, ".json": 2}
    files: List[Path] = []
    for pattern in ("*.yaml", "*.yml", "*.json"):
        files.extend(paths.profiles.glob(pattern))
    files = [path for path in files if path.resolve() != paths.config.resolve()]

    profiles: List[Profile] = []
    seen = set()
    for path in sorted(files, key=lambda item: (item.stem, rank[item.suffix.lower()])):
        if path.stem in seen:
            continue
        seen.add(path.stem)
        try:
            profile = parse_profile(path)
        except WteError:
            if strict:
                raise
            continue
        if profile.get("name"):
            profiles.append(profile)
        elif strict:
            raise WteError(f"profile has no name: {path}")
    return profiles


def worktree_root(cwd: Optional[Path] = None) -> Path:
    """Return the root of the Git worktree containing ``cwd``."""
    return Path(run_git("rev-parse", "--show-toplevel", cwd=cwd)).resolve()


def main_worktree_root(root: Path) -> Path:
    """Return the main worktree associated with ``root``.

    Normal and linked worktrees share the main worktree's ``.git`` directory.
    The porcelain listing is a fallback for repositories whose common Git
    directory uses a non-standard location.
    """
    try:
        common = Path(run_git("rev-parse", "--git-common-dir", cwd=root))
    except WteError:
        return root.resolve()
    common = (root / common).resolve() if not common.is_absolute() else common.resolve()
    if common.name == ".git":
        return common.parent

    try:
        listing = run_git("worktree", "list", "--porcelain", cwd=root)
        first = next(
            line[len("worktree ") :]
            for line in listing.splitlines()
            if line.startswith("worktree ")
        )
        return Path(first).resolve()
    except (WteError, StopIteration):
        return root.resolve()


def configured_main_worktree(profile: Profile) -> Optional[Path]:
    """Return the normalized main-worktree path configured by a profile."""
    match = profile.get("match") or {}
    raw = match.get("main_worktree") if isinstance(match, dict) else None
    if not raw:
        return None
    return expand_home(str(raw)).resolve()


def find_profile(paths: AppPaths, root: Path) -> Optional[Profile]:
    """Find the unique profile whose main worktree owns ``root``."""
    actual = main_worktree_root(root)
    hits = [
        profile
        for profile in load_profiles(paths)
        if configured_main_worktree(profile) == actual
    ]
    if not hits:
        return None
    if len(hits) > 1:
        sources = ", ".join(Path(item["_file"]).name for item in hits)
        raise WteError(f"multiple profiles match main worktree {actual}: {sources}")
    return hits[0]


def _port_claims(profile: Profile) -> Sequence[Dict[str, Any]]:
    raw = profile.get("ports") or profile.get("services") or []
    return raw if isinstance(raw, list) else []


def validate_profiles(paths: AppPaths) -> Tuple[List[str], List[str]]:
    """Validate all profiles without requiring their worktrees to exist."""
    errors: List[str] = []
    warnings: List[str] = []
    try:
        profiles = load_profiles(paths)
    except WteError as exc:
        return [str(exc)], warnings

    names: Dict[str, Path] = {}
    main_roots: Dict[Path, Path] = {}
    for profile in profiles:
        source = Path(profile["_file"])
        label = source.name
        name = profile.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}: name must be a non-empty string")
        elif name in names:
            errors.append(f"{label}: duplicate profile name {name!r} (also in {names[name].name})")
        else:
            names[name] = source

        match = profile.get("match") or {}
        main_raw = match.get("main_worktree") if isinstance(match, dict) else None
        main_root = configured_main_worktree(profile)
        if main_root is None:
            errors.append(f"{label}: match.main_worktree is required")
        elif not expand_home(str(main_raw)).is_absolute():
            errors.append(f"{label}: match.main_worktree must be an absolute or home-relative path")
        elif main_root in main_roots:
            errors.append(
                f"{label}: main worktree is also configured by {main_roots[main_root].name}"
            )
        else:
            main_roots[main_root] = source
            if not main_root.exists():
                warnings.append(f"{label}: main worktree does not exist: {main_root}")

        claims = _port_claims(profile)
        if not claims:
            errors.append(f"{label}: ports must contain at least one claim")
            continue
        ids: List[str] = []
        for index, claim in enumerate(claims):
            port_id = claim.get("id") if isinstance(claim, dict) else None
            if not isinstance(port_id, str) or not PORT_ID.match(port_id):
                errors.append(f"{label}: ports[{index}].id is invalid")
            elif port_id in ids:
                errors.append(f"{label}: duplicate port id {port_id!r}")
            else:
                ids.append(port_id)

        mapping = {port_id: "0" for port_id in ids}
        writes = profile.get("writes") or []
        if not isinstance(writes, list):
            errors.append(f"{label}: writes must be a list")
            continue
        for index, spec in enumerate(writes):
            if not isinstance(spec, dict):
                errors.append(f"{label}: writes[{index}] must be a mapping")
                continue
            write_path = spec.get("path")
            if not write_path:
                errors.append(f"{label}: writes[{index}].path is required")
            elif Path(str(write_path)).is_absolute() or ".." in Path(str(write_path)).parts:
                errors.append(f"{label}: writes[{index}].path must stay inside the worktree")
            try:
                Template(str(spec.get("body") or "")).substitute(mapping)
            except (KeyError, ValueError) as exc:
                errors.append(f"{label}: writes[{index}].body has an invalid variable: {exc}")

        secrets = profile.get("secrets") or []
        if not isinstance(secrets, list):
            errors.append(f"{label}: secrets must be a list")
        else:
            for index, entry in enumerate(secrets):
                if not isinstance(entry, dict):
                    errors.append(f"{label}: secrets[{index}] must be a mapping")
                    continue
                if not entry.get("source"):
                    errors.append(f"{label}: secrets[{index}].source is required")
                target = entry.get("target")
                if not target:
                    errors.append(f"{label}: secrets[{index}].target is required")
                elif Path(str(target)).is_absolute() or ".." in Path(str(target)).parts:
                    errors.append(f"{label}: secrets[{index}].target must stay inside the worktree")

        initializers = profile.get("init")
        if initializers is None:
            initializers = []
        if not isinstance(initializers, list):
            errors.append(f"{label}: init must be a list")
        else:
            for index, entry in enumerate(initializers):
                if not isinstance(entry, dict):
                    errors.append(f"{label}: init[{index}] must be a mapping")
                    continue
                command = entry.get("command")
                if (
                    not isinstance(command, list)
                    or not command
                    or not all(isinstance(value, str) for value in command)
                    or not command[0].strip()
                ):
                    errors.append(
                        f"{label}: init[{index}].command must be a non-empty string list"
                    )
                args = entry.get("args", [])
                if not isinstance(args, list) or not all(
                    isinstance(value, str) for value in args
                ):
                    errors.append(f"{label}: init[{index}].args must be a string list")
                cwd = entry.get("cwd")
                if not isinstance(cwd, str) or not cwd.strip():
                    errors.append(f"{label}: init[{index}].cwd is required")

    if not profiles:
        warnings.append(f"no profiles found in {paths.profiles}")
    return errors, warnings
