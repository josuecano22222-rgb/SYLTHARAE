"""Offline map package catalog, download, and install under the project ``map_data/`` tree."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tarfile
import threading
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.app_paths import get_project_root

logger = logging.getLogger(__name__)

_PACKAGE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.I)
_active_downloads: Dict[str, threading.Thread] = {}


#: Where the map tree lives. A deployment keeps it beside the sources (the
#: default). It is overridable because the suite must not write into the
#: checkout: ``map_data/registry.db`` holds the tile cache, and a test run that
#: opened it dirtied a tracked, 15 MB binary on every run.
MAP_DATA_DIR_ENV = "MAP_DATA_DIR"


def map_data_root() -> Path:
    override = (os.environ.get(MAP_DATA_DIR_ENV) or "").strip()
    root = (Path(override).expanduser().resolve() if override
            else get_project_root() / "map_data")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("packages", "installed", "runtime"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def catalog_path() -> Path:
    return map_data_root() / "catalog.json"


def load_catalog() -> Dict[str, Any]:
    path = catalog_path()
    if not path.is_file():
        return {"version": 1, "packages": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Invalid map catalog JSON: %s", exc)
        return {"version": 1, "packages": []}
    if not isinstance(data, dict):
        return {"version": 1, "packages": []}
    data.setdefault("packages", [])
    return data


def package_by_id(package_id: str) -> Optional[Dict[str, Any]]:
    if not _PACKAGE_ID.match(package_id or ""):
        return None
    for entry in load_catalog().get("packages") or []:
        if isinstance(entry, dict) and entry.get("id") == package_id:
            return entry
    return None


def installed_manifest_path(package_id: str) -> Path:
    return map_data_root() / "installed" / package_id / "manifest.json"


def read_installed_manifest(package_id: str) -> Optional[Dict[str, Any]]:
    path = installed_manifest_path(package_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def list_packages() -> List[Dict[str, Any]]:
    catalog = load_catalog()
    rows: List[Dict[str, Any]] = []
    for entry in catalog.get("packages") or []:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("id")
        if not pid:
            continue
        installed = read_installed_manifest(str(pid))
        rows.append({
            **entry,
            "installed": installed is not None,
            "installed_at": (installed or {}).get("installed_at"),
            "local_manifest": installed,
        })
    return rows


def download_status_path(package_id: str) -> Path:
    return map_data_root() / "runtime" / f"download-{package_id}.json"


def read_download_status(package_id: str) -> Dict[str, Any]:
    path = download_status_path(package_id)
    if not path.is_file():
        return {"state": "idle", "package_id": package_id}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {"state": "idle", "package_id": package_id}


def _write_download_status(package_id: str, payload: Dict[str, Any]) -> None:
    path = download_status_path(package_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _safe_extract(archive_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path, "r:*") as tar:
            for member in tar.getmembers():
                target = (dest / member.name).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise ValueError("Archive path escapes destination")
            tar.extractall(dest, filter="data")
        return
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as zf:
            for name in zf.namelist():
                target = (dest / name).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise ValueError("Archive path escapes destination")
            zf.extractall(dest)
        return
    raise ValueError("Unsupported archive format (expected .tar.gz or .zip)")


def _finalize_install(package_id: str, staging: Path, entry: Dict[str, Any]) -> None:
    manifest_src = staging / "manifest.json"
    if not manifest_src.is_file():
        raise ValueError("Package archive must contain manifest.json at its root")
    manifest = json.loads(manifest_src.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must be a JSON object")

    install_dir = map_data_root() / "installed" / package_id
    if install_dir.exists():
        shutil.rmtree(install_dir)
    shutil.copytree(staging, install_dir)

    manifest["id"] = package_id
    manifest["installed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["catalog_name"] = entry.get("name")
    installed_manifest_path(package_id).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")


def _download_worker(package_id: str, entry: Dict[str, Any]) -> None:
    url = (entry.get("download_url") or "").strip()
    if not url:
        _write_download_status(package_id, {
            "state": "error",
            "package_id": package_id,
            "error": "No download_url configured for this package",
        })
        return

    packages_dir = map_data_root() / "packages"
    packages_dir.mkdir(parents=True, exist_ok=True)
    archive_name = entry.get("archive_name") or f"{package_id}.tar.gz"
    archive_path = packages_dir / archive_name
    staging = map_data_root() / "runtime" / f"staging-{package_id}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        _write_download_status(package_id, {
            "state": "downloading",
            "package_id": package_id,
            "bytes_received": 0,
            "total_bytes": entry.get("size_bytes"),
            "percent": 0,
        })

        req = urllib.request.Request(url, headers={"User-Agent": "SYLTHARAE-MapPack/1.0"})
        with urllib.request.urlopen(req, timeout=120) as response:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            chunk_size = 1024 * 256
            with archive_path.open("wb") as out:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    out.write(chunk)
                    received += len(chunk)
                    percent = int(received * 100 / total) if total else None
                    _write_download_status(package_id, {
                        "state": "downloading",
                        "package_id": package_id,
                        "bytes_received": received,
                        "total_bytes": total or entry.get("size_bytes"),
                        "percent": percent,
                    })

        _write_download_status(package_id, {
            "state": "extracting",
            "package_id": package_id,
            "percent": 100,
        })
        _safe_extract(archive_path, staging)
        _finalize_install(package_id, staging, entry)
        _write_download_status(package_id, {
            "state": "ready",
            "package_id": package_id,
            "percent": 100,
        })
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.error("Map package download failed for %s: %s", package_id, exc, exc_info=True)
        _write_download_status(package_id, {
            "state": "error",
            "package_id": package_id,
            "error": str(exc),
        })
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        _active_downloads.pop(package_id, None)


def start_package_download(package_id: str) -> Dict[str, Any]:
    entry = package_by_id(package_id)
    if entry is None:
        raise ValueError(f"Unknown map package {package_id!r}")
    if entry.get("builtin"):
        raise ValueError("Built-in layers do not require download")
    if package_id in _active_downloads and _active_downloads[package_id].is_alive():
        return read_download_status(package_id)

    thread = threading.Thread(
        target=_download_worker,
        args=(package_id, entry),
        name=f"map-download-{package_id}",
        daemon=True,
    )
    _active_downloads[package_id] = thread
    thread.start()
    return {"state": "started", "package_id": package_id}


def remove_installed_package(package_id: str) -> None:
    if not _PACKAGE_ID.match(package_id):
        raise ValueError("Invalid package id")
    install_dir = map_data_root() / "installed" / package_id
    if install_dir.exists():
        shutil.rmtree(install_dir)
    status = download_status_path(package_id)
    if status.is_file():
        status.unlink()


def installed_pmtiles_path(package_id: str) -> Optional[Path]:
    manifest = read_installed_manifest(package_id)
    if not manifest:
        return None
    rel = manifest.get("pmtiles") or manifest.get("pmtiles_file")
    if not rel:
        return None
    path = (map_data_root() / "installed" / package_id / str(rel)).resolve()
    root = (map_data_root() / "installed" / package_id).resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        return None
    return path


def raster_tile_path(package_id: str, z: int, x: int, y: int) -> Optional[Path]:
    manifest = read_installed_manifest(package_id)
    if not manifest:
        return None
    template = manifest.get("raster_template") or "tiles/{z}/{x}/{y}.png"
    rel = template.format(z=z, x=x, y=y)
    path = (map_data_root() / "installed" / package_id / rel).resolve()
    root = (map_data_root() / "installed" / package_id).resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        return None
    return path
