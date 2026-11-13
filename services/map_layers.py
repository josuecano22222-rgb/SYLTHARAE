"""Map layer catalog: online basemaps, offline packages, and portable storage stats."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from services import map_packages, map_tile_cache

# Tile source templates (validated in catalog only — not executed server-side as SQL).
_ESRI_IMAGERY = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
_ESRI_REFERENCE = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}"
)
_OSM_STANDARD = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_OPENTOPO = "https://tile.opentopomap.org/{z}/{x}/{y}.png"
_CARTO_DARK = "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"


def _online_layer_defs() -> List[Dict[str, Any]]:
    """Built-in online layers (also mirrored in map_data/catalog.json online_layers)."""
    return [
        {
            "id": "standard-osm",
            "layer_kind": "standard",
            "map_type": "street",
            "requires_network": True,
            "builtin": True,
            "sources": [
                {
                    "id": "base",
                    "type": "raster",
                    "tiles": [_OSM_STANDARD],
                    "tile_size": 256,
                    "maxzoom": 19,
                    "attribution": "© OpenStreetMap contributors",
                }
            ],
        },
        {
            "id": "satellite-esri",
            "layer_kind": "satellite",
            "map_type": "imagery",
            "requires_network": True,
            "builtin": True,
            "sources": [
                {
                    "id": "imagery",
                    "type": "raster",
                    "tiles": [_ESRI_IMAGERY],
                    "tile_size": 256,
                    "maxzoom": 19,
                    "attribution": "Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
                }
            ],
        },
        {
            "id": "hybrid-esri",
            "layer_kind": "hybrid",
            "map_type": "hybrid",
            "requires_network": True,
            "builtin": True,
            "sources": [
                {
                    "id": "imagery",
                    "type": "raster",
                    "tiles": [_ESRI_IMAGERY],
                    "tile_size": 256,
                    "maxzoom": 19,
                    "attribution": "Imagery © Esri",
                },
                {
                    "id": "reference",
                    "type": "raster",
                    "tiles": [_ESRI_REFERENCE],
                    "tile_size": 256,
                    "maxzoom": 19,
                    "opacity": 0.85,
                    "attribution": "Labels © Esri",
                },
            ],
        },
        {
            "id": "terrain-opentopo",
            "layer_kind": "terrain",
            "map_type": "topographic",
            "requires_network": True,
            "builtin": True,
            "sources": [
                {
                    "id": "terrain",
                    "type": "raster",
                    "tiles": [_OPENTOPO],
                    "tile_size": 256,
                    "maxzoom": 17,
                    "attribution": "© OpenStreetMap contributors, SRTM | Style: © OpenTopoMap (CC-BY-SA)",
                }
            ],
        },
        {
            "id": "dark-carto",
            "layer_kind": "dark",
            "map_type": "dark",
            "requires_network": True,
            "builtin": True,
            "sources": [
                {
                    "id": "dark",
                    "type": "raster",
                    "tiles": [_CARTO_DARK],
                    "tile_size": 256,
                    "maxzoom": 19,
                    "attribution": "© OpenStreetMap contributors © CARTO",
                }
            ],
        },
    ]


def _merge_catalog_online(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    catalog_online = catalog.get("online_layers")
    if isinstance(catalog_online, list) and catalog_online:
        return [layer for layer in catalog_online if isinstance(layer, dict)]
    return _online_layer_defs()


def _offline_layer_from_package(pkg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if pkg.get("builtin"):
        return None
    pid = pkg.get("id")
    if not pid:
        return None
    if not pkg.get("installed"):
        return {
            "id": f"offline-{pid}",
            "package_id": pid,
            "layer_kind": pkg.get("layer_kind") or "offline",
            "map_type": pkg.get("map_type") or "offline_raster",
            "requires_network": False,
            "builtin": False,
            "installed": False,
            "downloadable": bool((pkg.get("download_url") or "").strip()),
            "size_bytes": pkg.get("size_bytes"),
            "sources": [],
        }
    manifest = pkg.get("local_manifest") or {}
    layer_kind = manifest.get("layer_kind") or pkg.get("layer_kind") or "offline"
    sources: List[Dict[str, Any]] = []
    if manifest.get("pmtiles") or manifest.get("pmtiles_file"):
        sources.append({
            "id": "pmtiles",
            "type": "pmtiles",
            "package_id": pid,
            "source_layer": manifest.get("source_layer") or "earth",
            "attribution": manifest.get("attribution") or pkg.get("attribution") or "",
        })
    else:
        sources.append({
            "id": "raster",
            "type": "offline_raster",
            "package_id": pid,
            "attribution": manifest.get("attribution") or pkg.get("attribution") or "",
            "maxzoom": manifest.get("max_zoom") or 18,
        })
    return {
        "id": f"offline-{pid}",
        "package_id": pid,
        "layer_kind": layer_kind,
        "map_type": manifest.get("map_type") or pkg.get("map_type") or "offline_raster",
        "requires_network": False,
        "builtin": False,
        "installed": True,
        "downloadable": False,
        "bounds": manifest.get("bounds"),
        "center": manifest.get("center"),
        "sources": sources,
        "attribution": manifest.get("attribution") or pkg.get("attribution") or "",
    }


def list_map_layers(*, translate_name) -> List[Dict[str, Any]]:
    """All selectable layers (online + offline packages), with localized names."""
    catalog = map_packages.load_catalog()
    layers: List[Dict[str, Any]] = []

    name_keys = {
        "standard-osm": "mapLayerStandard",
        "satellite-esri": "mapLayerSatellite",
        "hybrid-esri": "mapLayerHybrid",
        "terrain-opentopo": "mapLayerTerrain",
        "dark-carto": "mapLayerDark",
    }
    default_names = {
        "standard-osm": "Standard map",
        "satellite-esri": "Aerial imagery",
        "hybrid-esri": "Hybrid (imagery + labels)",
        "terrain-opentopo": "Topographic terrain",
        "dark-carto": "Dark map",
    }

    for layer in _merge_catalog_online(catalog):
        lid = layer.get("id")
        if not lid:
            continue
        key = layer.get("name_key") or name_keys.get(str(lid), f"mapLayer_{lid}")
        label = translate_name(key, layer.get("name") or default_names.get(str(lid), str(lid)))
        cache_stats = map_tile_cache.layer_cache_summary(str(lid))
        layers.append({
            **layer,
            "name": label,
            "name_key": key,
            "installed": True,
            "downloadable": False,
            "auto_cache_eligible": True,
            "has_tile_cache": cache_stats["tiles"] > 0,
            "cached_tiles": cache_stats["tiles"],
            "cached_bytes": cache_stats["bytes"],
        })

    for pkg in map_packages.list_packages():
        offline = _offline_layer_from_package(pkg)
        if offline is None:
            continue
        pid = offline["package_id"]
        key = f"mapPackage_{pid}"
        label = translate_name(key, pkg.get("name") or pid)
        offline["name"] = label
        offline["name_key"] = key
        offline["description"] = pkg.get("description") or ""
        layers.append(offline)

    return layers


def storage_summary() -> Dict[str, Any]:
    """Sizes and paths for the portable ``map_data`` bundle."""
    root = map_packages.map_data_root()
    packages_dir = root / "packages"
    installed_dir = root / "installed"

    def dir_bytes(path) -> int:
        total = 0
        if not path.is_dir():
            return 0
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    pass
        return total

    pkg_bytes = dir_bytes(packages_dir)
    inst_bytes = dir_bytes(installed_dir)
    cache_info = map_tile_cache.cache_storage_summary()
    cache_bytes = cache_info.get("cache_bytes") or 0
    return {
        "root": str(root),
        "packages_bytes": pkg_bytes,
        "installed_bytes": inst_bytes,
        "cache_bytes": cache_bytes,
        "cache_tiles": cache_info.get("cache_tiles") or 0,
        "total_bytes": pkg_bytes + inst_bytes + cache_bytes,
        "auto_cache_hint": "Tiles viewed online are saved as compressed WebP images in map_data/registry.db.",
        "portable_hint": "Copy the map_data folder (catalog, cache, packages, installed) to another device running the same application version.",
    }
