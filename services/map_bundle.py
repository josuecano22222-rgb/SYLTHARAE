"""Portable compressed map bundle (.sylmap.tar.gz) for map_data portability."""

from __future__ import annotations

import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services import map_packages, map_registry

BUNDLE_SUFFIX = ".sylmap.tar.gz"
MANIFEST_NAME = "bundle_manifest.json"


def _bundle_manifest() -> Dict[str, Any]:
    return {
        "format": "syltharae-map-bundle",
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "catalog_version": map_packages.load_catalog().get("version"),
        "registry": map_registry.registry_summary(),
    }


def create_portable_bundle() -> io.BytesIO:
    """Tar.gz of catalog, registry.db, packages/*.tar.gz, and installed/ tree."""
    map_registry.sync_from_catalog()
    root = map_packages.map_data_root()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        manifest = _bundle_manifest()
        data = json.dumps(manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name=MANIFEST_NAME)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

        def add_path(path: Path, arcname: str) -> None:
            if not path.exists():
                return
            tar.add(path, arcname=arcname, recursive=path.is_dir())

        add_path(root / "catalog.json", "map_data/catalog.json")
        add_path(root / "registry.db", "map_data/registry.db")
        add_path(root / "packages", "map_data/packages")
        add_path(root / "installed", "map_data/installed")
    buffer.seek(0)
    return buffer


def _safe_arc_path(arcname: str) -> Optional[Path]:
    """Return relative path under map_data if arcname is safe, else None."""
    normalized = arcname.replace("\\", "/").lstrip("/")
    if not normalized.startswith("map_data/"):
        return None
    rel = normalized[len("map_data/") :]
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return None
    return Path(rel)


def import_portable_bundle(stream) -> Dict[str, Any]:
    """Extract a .sylmap.tar.gz produced by create_portable_bundle into map_data/."""
    root = map_packages.map_data_root()
    root.mkdir(parents=True, exist_ok=True)
    extracted: List[str] = []
    manifest: Optional[Dict[str, Any]] = None

    with tarfile.open(fileobj=stream, mode="r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            if member.isdir():
                continue
            base_name = member.name.replace("\\", "/").lstrip("/")
            if base_name == MANIFEST_NAME or base_name.endswith(f"/{MANIFEST_NAME}"):
                raw = tar.extractfile(member)
                if raw:
                    manifest = json.loads(raw.read().decode("utf-8"))
                continue
            rel = _safe_arc_path(member.name)
            if rel is None:
                raise ValueError(f"Unsafe path in bundle: {member.name}")
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = tar.extractfile(member)
            if raw is None:
                continue
            data = raw.read()
            target.write_bytes(data)
            extracted.append(str(rel).replace("\\", "/"))

    if manifest is None:
        raise ValueError("Bundle is missing bundle_manifest.json")

    if manifest.get("format") != "syltharae-map-bundle":
        raise ValueError("Unrecognized map bundle format")

    map_registry.sync_from_catalog()
    return {
        "files_extracted": len(extracted),
        "paths": extracted[:50],
        "manifest": manifest,
        "registry": map_registry.registry_summary(),
    }


def list_bundle_contents() -> List[str]:
    root = map_packages.map_data_root()
    paths: List[str] = ["catalog.json", "registry.db (includes compressed tile_cache BLOBs)"]
    for folder in ("packages", "installed"):
        base = root / folder
        if not base.is_dir():
            continue
        for child in sorted(base.rglob("*")):
            if child.is_file():
                paths.append(str(child.relative_to(root)).replace("\\", "/"))
    return paths
