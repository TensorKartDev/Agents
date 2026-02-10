"""AGX framework package."""

from importlib import metadata

from .cli import app

try:
    __version__ = metadata.version("agx-framework")
except metadata.PackageNotFoundError:  # pragma: no cover - fallback for dev
    __version__ = "0.0.0"

__all__ = ["app", "__version__"]
