"""SQLite registry for portable map layer metadata (lives under map_data/)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from services import map_packages

_DB_NAME = "registry.db"


def registry_path() -> Path:
    return map_packages.map_data_root() / _DB_NAME


def _connect() -> sqlite3.Connection:
    path = registry_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_registry() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS layer_catalog (
                id TEXT PRIMARY KEY,
                layer_kind TEXT NOT NULL,
                map_type TEXT,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS package_registry (
                id TEXT PRIMARY KEY,
                installed INTEGER NOT NULL DEFAULT 0,
                installed_at TEXT,
                manifest_json TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tile_cache (
                layer_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                z INTEGER NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                codec TEXT NOT NULL DEFAULT 'webp',
                image_blob BLOB NOT NULL,
                raw_bytes INTEGER NOT NULL DEFAULT 0,
                stored_bytes INTEGER NOT NULL DEFAULT 0,
                cached_at TEXT NOT NULL,
                PRIMARY KEY (layer_id, source_id, z, x, y)
            );
            CREATE INDEX IF NOT EXISTS idx_tile_cache_layer
                ON tile_cache(layer_id);
            """
        )
        conn.commit()


def sync_from_catalog() -> None:
    """Mirror catalog.json + installed manifests into the SQLite registry."""
    init_registry()
    catalog = map_packages.load_catalog()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        for layer in catalog.get("online_layers") or []:
            if not isinstance(layer, dict) or not layer.get("id"):
                continue
            conn.execute(
                """
                INSERT INTO layer_catalog (id, layer_kind, map_type, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    layer_kind=excluded.layer_kind,
                    map_type=excluded.map_type,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    layer["id"],
                    layer.get("layer_kind") or "standard",
                    layer.get("map_type"),
                    json.dumps(layer, ensure_ascii=False),
                    now,
                ),
            )
        for pkg in map_packages.list_packages():
            pid = pkg.get("id")
            if not pid:
                continue
            manifest = pkg.get("local_manifest")
            conn.execute(
                """
                INSERT INTO package_registry (id, installed, installed_at, manifest_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    installed=excluded.installed,
                    installed_at=excluded.installed_at,
                    manifest_json=excluded.manifest_json,
                    updated_at=excluded.updated_at
                """,
                (
                    pid,
                    1 if pkg.get("installed") else 0,
                    (manifest or {}).get("installed_at"),
                    json.dumps(manifest, ensure_ascii=False) if manifest else None,
                    now,
                ),
            )
        conn.commit()


def registry_summary() -> Dict[str, Any]:
    sync_from_catalog()
    with _connect() as conn:
        layer_count = conn.execute("SELECT COUNT(*) FROM layer_catalog").fetchone()[0]
        pkg_count = conn.execute("SELECT COUNT(*) FROM package_registry").fetchone()[0]
        installed = conn.execute(
            "SELECT COUNT(*) FROM package_registry WHERE installed = 1"
        ).fetchone()[0]
        tile_row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(stored_bytes), 0) FROM tile_cache"
        ).fetchone()
        tile_count = int(tile_row[0]) if tile_row else 0
        tile_bytes = int(tile_row[1]) if tile_row else 0
    return {
        "database_path": str(registry_path()),
        "layer_rows": layer_count,
        "package_rows": pkg_count,
        "installed_packages": installed,
        "cached_tiles": tile_count,
        "cached_tile_bytes": tile_bytes,
    }
