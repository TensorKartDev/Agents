"""Workspace path resolution for runtime-discoverable assets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class WorkspacePaths:
    """Resolved filesystem paths used by the runtime."""

    base_dir: Path
    agents_dir: Path
    registry_path: Path
    runs_dir: Path


def resolve_workspace_paths(base_dir: Optional[Path] = None) -> WorkspacePaths:
    """Resolve runtime paths without coupling the framework to one repo layout."""

    resolved_base = (base_dir or Path(__file__).resolve().parents[2]).resolve()

    agents_dir_value = os.getenv("AGX_AGENTS_DIR", "").strip()
    agents_dir = Path(agents_dir_value).expanduser().resolve() if agents_dir_value else resolved_base / "agents"

    registry_value = os.getenv("AGX_AGENT_REGISTRY", "").strip()
    registry_path = (
        Path(registry_value).expanduser().resolve()
        if registry_value
        else _discover_registry_path(agents_dir)
    )

    runs_dir_value = os.getenv("AGX_RUNS_DIR", "").strip()
    runs_dir = Path(runs_dir_value).expanduser().resolve() if runs_dir_value else resolved_base / ".agx" / "runs"

    return WorkspacePaths(
        base_dir=resolved_base,
        agents_dir=agents_dir,
        registry_path=registry_path,
        runs_dir=runs_dir,
    )


def _discover_registry_path(agents_dir: Path) -> Path:
    for candidate in ("agents.yaml", "Agents.yaml", "Agents.YAML", "registry.yaml"):
        path = agents_dir / candidate
        if path.exists():
            return path
    return agents_dir / "agents.yaml"
