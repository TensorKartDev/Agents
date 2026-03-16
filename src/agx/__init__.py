"""AGX framework package."""

from __future__ import annotations

import subprocess
from importlib import metadata
from pathlib import Path

from .cli import app


def _resolve_version() -> str:
    try:
        return metadata.version("agx-framework")
    except metadata.PackageNotFoundError:
        pass

    root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        version = result.stdout.strip()
        if version:
            return version
    except Exception:
        pass
    return "0.0.0"


__version__ = _resolve_version()
print(__version__)
__all__ = ["app", "__version__"]
