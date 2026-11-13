"""Application path resolution - single source of truth.

Every runtime path used by the application is derived from one application
data root.  The root is resolved with the following precedence:

1. ``APP_DATA_DIR`` environment variable (explicit deployment configuration)
2. ``~/.local/share/file-analysis`` on Linux/macOS, or
   ``~/AppData/Local/file-analysis`` on Windows (user data directory)

The application must behave identically regardless of the directory from
which it is launched; no module may rely on the current working directory.

Subdirectories (created lazily by :func:`ensure_runtime_dirs`):

    APP_DATA_DIR/
        logs/
        checkpoints/
        extracted/
        uploads/
        cache/
        runtime/
        config/
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

_APP_NAME = "file-analysis"

_cached_root: Optional[Path] = None

_SUBDIRS = ("logs", "checkpoints", "extracted", "uploads", "cache", "runtime", "config")


def set_data_root(root: Path | str) -> Path:
    """Explicitly set the application data root (used by tests and embeddings)."""
    global _cached_root
    _cached_root = Path(root).expanduser().resolve()
    return _cached_root


def get_data_root() -> Path:
    """Return the application data root, creating it if necessary."""
    global _cached_root
    if _cached_root is not None:
        root = _cached_root
    else:
        env_root = os.environ.get("APP_DATA_DIR", "").strip()
        if env_root:
            root = Path(env_root).expanduser().resolve()
        else:
            if os.name == "nt":
                base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            else:
                base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
            root = Path(base) / _APP_NAME
        _cached_root = root
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_subdir(name: str) -> Path:
    """Return a standard subdirectory of the data root (created if missing)."""
    if name not in _SUBDIRS:
        raise ValueError(f"Unknown runtime subdirectory: {name!r} (expected one of {_SUBDIRS})")
    path = get_data_root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_logs_dir() -> Path:
    return get_subdir("logs")


def get_checkpoints_dir() -> Path:
    return get_subdir("checkpoints")


def get_extracted_dir() -> Path:
    return get_subdir("extracted")


def get_uploads_dir() -> Path:
    return get_subdir("uploads")


def get_cache_dir() -> Path:
    return get_subdir("cache")


def get_runtime_dir() -> Path:
    return get_subdir("runtime")


def get_config_dir() -> Path:
    return get_subdir("config")


def ensure_runtime_dirs() -> Dict[str, Path]:
    """Create and return all standard runtime directories."""
    return {name: get_subdir(name) for name in _SUBDIRS}


def get_project_root() -> Path:
    """Return the repository/package root (where the sources live).

    This is derived from this file's location, never from the CWD.
    """
    return Path(__file__).resolve().parent.parent


def reset_cache() -> None:
    """Reset the cached root (primarily for tests)."""
    global _cached_root
    _cached_root = None
