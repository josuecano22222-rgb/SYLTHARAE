"""Automatic on-view tile cache: compressed images in map_data/registry.db."""

from __future__ import annotations

import io
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image

from services import map_layers, map_packages, map_registry

logger = logging.getLogger(__name__)

_LAYER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.I)
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.I)
_WEBP_QUALITY = 82

_HOST_MIN_INTERVAL = {
    "tile.openstreetmap.org": 0.5,
    "tile.opentopomap.org": 0.5,
    "basemaps.cartocdn.com": 0.2,
    "server.arcgisonline.com": 0.1,
}
_host_lock = threading.Lock()
_host_last_fetch: Dict[str, float] = {}


def _legacy_cache_root() -> Path:
    return map_packages.map_data_root() / "cache"


def _validate_tile_coords(z: int, x: int, y: int) -> None:
    if z < 0 or z > 22 or x < 0 or y < 0:
        raise ValueError("Invalid tile coordinates")


def _validate_ids(layer_id: str, source_id: str) -> None:
    if not _LAYER_ID.match(layer_id or ""):
        raise ValueError("Invalid layer id")
    if not _SOURCE_ID.match(source_id or ""):
        raise ValueError("Invalid source id")


def _compress_image(raw: bytes) -> Tuple[bytes, int]:
    """Encode tile as WebP for compact BLOB storage."""
    with Image.open(io.BytesIO(raw)) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=_WEBP_QUALITY, method=4)
        blob = out.getvalue()
    return blob, len(raw)


def _decompress_to_png(blob: bytes, codec: str) -> bytes:
    if codec != "webp":
        raise ValueError(f"Unsupported tile codec {codec!r}")
    with Image.open(io.BytesIO(blob)) as img:
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()


def _find_layer_source(layer_id: str, source_id: str) -> Tuple[str, Dict[str, Any]]:
    catalog = map_packages.load_catalog()
    layers = map_layers._merge_catalog_online(catalog)
    for layer in layers:
        if layer.get("id") != layer_id:
            continue
        for src in layer.get("sources") or []:
            if not isinstance(src, dict):
                continue
            sid = src.get("id") or "base"
            if sid != source_id:
                continue
            tiles = src.get("tiles")
            if not isinstance(tiles, list) or not tiles:
                raise ValueError("Source has no tile URL template")
            template = str(tiles[0])
            if "{z}" not in template:
                raise ValueError("Unsupported tile template")
            return template, src
    raise ValueError(f"Unknown layer/source: {layer_id}/{source_id}")


def _format_tile_url(template: str, z: int, x: int, y: int) -> str:
    return (
        template.replace("{z}", str(z))
        .replace("{x}", str(x))
        .replace("{y}", str(y))
    )


def _read_tile_row(layer_id: str, source_id: str, z: int, x: int, y: int):
    map_registry.init_registry()
    with map_registry._connect() as conn:
        return conn.execute(
            """
            SELECT codec, image_blob FROM tile_cache
            WHERE layer_id = ? AND source_id = ? AND z = ? AND x = ? AND y = ?
            """,
            (layer_id, source_id, z, x, y),
        ).fetchone()


def read_cached_tile(layer_id: str, source_id: str, z: int, x: int, y: int) -> Optional[bytes]:
    """Return PNG bytes for MapLibre (decompressed from DB WebP)."""
    _validate_ids(layer_id, source_id)
    _validate_tile_coords(z, x, y)
    row = _read_tile_row(layer_id, source_id, z, x, y)
    if row is None:
        legacy = _read_legacy_disk_tile(layer_id, source_id, z, x, y)
        if legacy is not None:
            store_cached_tile(layer_id, source_id, z, x, y, legacy)
            return legacy
        return None
    try:
        return _decompress_to_png(row["image_blob"], row["codec"])
    except (OSError, ValueError) as exc:
        logger.warning("Corrupt cached tile %s/%s %s/%s/%s: %s", layer_id, source_id, z, x, y, exc)
        return None


def store_cached_tile(layer_id: str, source_id: str, z: int, x: int, y: int, raw_png: bytes) -> None:
    _validate_ids(layer_id, source_id)
    _validate_tile_coords(z, x, y)
    if not raw_png:
        raise ValueError("Empty tile data")
    blob, raw_bytes = _compress_image(raw_png)
    now = datetime.now(timezone.utc).isoformat()
    map_registry.init_registry()
    with map_registry._connect() as conn:
        conn.execute(
            """
            INSERT INTO tile_cache (
                layer_id, source_id, z, x, y, codec, image_blob,
                raw_bytes, stored_bytes, cached_at
            ) VALUES (?, ?, ?, ?, ?, 'webp', ?, ?, ?, ?)
            ON CONFLICT(layer_id, source_id, z, x, y) DO UPDATE SET
                codec = excluded.codec,
                image_blob = excluded.image_blob,
                raw_bytes = excluded.raw_bytes,
                stored_bytes = excluded.stored_bytes,
                cached_at = excluded.cached_at
            """,
            (layer_id, source_id, z, x, y, blob, raw_bytes, len(blob), now),
        )
        conn.commit()
    _remove_legacy_disk_tile(layer_id, source_id, z, x, y)


def _legacy_tile_path(layer_id: str, source_id: str, z: int, x: int, y: int) -> Path:
    return _legacy_cache_root() / layer_id / source_id / str(z) / str(x) / f"{y}.png"


def _read_legacy_disk_tile(layer_id: str, source_id: str, z: int, x: int, y: int) -> Optional[bytes]:
    path = _legacy_tile_path(layer_id, source_id, z, x, y)
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _remove_legacy_disk_tile(layer_id: str, source_id: str, z: int, x: int, y: int) -> None:
    path = _legacy_tile_path(layer_id, source_id, z, x, y)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def migrate_legacy_disk_cache() -> int:
    """Import map_data/cache/*.png into registry.db (one-time cleanup)."""
    root = _legacy_cache_root()
    if not root.is_dir():
        return 0
    imported = 0
    for path in root.rglob("*.png"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
            if len(rel.parts) < 5:
                continue
            layer_id, source_id, z_s, x_s, y_name = rel.parts[:5]
            y_s = y_name.replace(".png", "")
            z, x, y = int(z_s), int(x_s), int(y_s)
            if _read_tile_row(layer_id, source_id, z, x, y) is not None:
                path.unlink(missing_ok=True)
                continue
            store_cached_tile(layer_id, source_id, z, x, y, path.read_bytes())
            imported += 1
        except (OSError, ValueError) as exc:
            logger.debug("Skip legacy tile %s: %s", path, exc)
    return imported


def _throttle_upstream(url: str) -> None:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        host = ""
    interval = _HOST_MIN_INTERVAL.get(host, 0.15)
    with _host_lock:
        now = time.monotonic()
        last = _host_last_fetch.get(host, 0.0)
        wait = interval - (now - last)
        if wait > 0:
            time.sleep(wait)
        _host_last_fetch[host] = time.monotonic()


def fetch_upstream_tile(layer_id: str, source_id: str, z: int, x: int, y: int) -> bytes:
    template, _src = _find_layer_source(layer_id, source_id)
    url = _format_tile_url(template, z, x, y)
    _throttle_upstream(url)
    req = urllib.request.Request(url, headers={"User-Agent": "SYLTHARAE-MapCache/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read()
    if not data:
        raise ValueError("Empty tile response")
    return data


def get_or_fetch_tile(layer_id: str, source_id: str, z: int, x: int, y: int, *, allow_fetch: bool) -> Optional[bytes]:
    cached = read_cached_tile(layer_id, source_id, z, x, y)
    if cached is not None:
        return cached
    if not allow_fetch:
        return None
    try:
        data = fetch_upstream_tile(layer_id, source_id, z, x, y)
        store_cached_tile(layer_id, source_id, z, x, y, data)
        return read_cached_tile(layer_id, source_id, z, x, y)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.info("Tile fetch failed %s/%s %s/%s/%s: %s", layer_id, source_id, z, x, y, exc)
        return None


def layer_cache_summary(layer_id: str) -> Dict[str, Any]:
    map_registry.init_registry()
    with map_registry._connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(stored_bytes), 0), COALESCE(SUM(raw_bytes), 0)
            FROM tile_cache WHERE layer_id = ?
            """,
            (layer_id,),
        ).fetchone()
    tiles = int(row[0]) if row else 0
    stored = int(row[1]) if row else 0
    raw = int(row[2]) if row else 0
    return {"layer_id": layer_id, "tiles": tiles, "bytes": stored, "raw_bytes": raw}


def cache_storage_summary() -> Dict[str, Any]:
    migrate_legacy_disk_cache()
    map_registry.init_registry()
    with map_registry._connect() as conn:
        rows = conn.execute(
            """
            SELECT layer_id, COUNT(*), COALESCE(SUM(stored_bytes), 0)
            FROM tile_cache GROUP BY layer_id
            """
        ).fetchall()
        total_row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(stored_bytes), 0), COALESCE(SUM(raw_bytes), 0) FROM tile_cache"
        ).fetchone()
    by_layer: Dict[str, Dict[str, int]] = {}
    for layer_id, count, nbytes in rows:
        by_layer[str(layer_id)] = {"tiles": int(count), "bytes": int(nbytes)}
    tiles = int(total_row[0]) if total_row else 0
    stored = int(total_row[1]) if total_row else 0
    raw = int(total_row[2]) if total_row else 0
    return {
        "cache_bytes": stored,
        "cache_tiles": tiles,
        "cache_raw_bytes": raw,
        "compression_ratio": round(raw / stored, 2) if stored else None,
        "storage": "registry.db",
        "layers": by_layer,
    }
