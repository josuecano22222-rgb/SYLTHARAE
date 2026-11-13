"""Load the same environment as the SYLTHARAE web runtime (.env + config overlay)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def bootstrap_runtime() -> Path:
    """Ensure project root is on sys.path and DB_* from .env are loaded."""
    root_str = str(ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    from core.init import _apply_environment_overlay, _load_dotenv_file

    _load_dotenv_file(ROOT)
    _apply_environment_overlay(ROOT)
    return ROOT


def db_connection_probe() -> dict:
    """Return non-secret connectivity diagnostics."""
    import os

    bootstrap_runtime()
    info = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": os.getenv("DB_PORT", "5432"),
        "database": os.getenv("DB_NAME", "analysis"),
        "user": os.getenv("DB_USER", "postgres"),
        "password_configured": bool(os.getenv("DB_PASSWORD")),
    }
    try:
        from Api.utils import execute_query

        row = execute_query("SELECT 1 AS ok", fetch="one")
        info["connected"] = bool(row and row[0] == 1)
        row = execute_query("SELECT COUNT(*) FROM paths", fetch="one")
        info["path_count"] = int(row[0]) if row else None
    except Exception as exc:
        info["connected"] = False
        info["error"] = str(exc).split("\n")[0]
    return info


if __name__ == "__main__":
    import json

    print(json.dumps(db_connection_probe(), indent=2))
