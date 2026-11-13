"""Map package catalog and path helpers (no database)."""

from __future__ import annotations

from services import map_packages


def test_catalog_loads_offline_package_entries():
    packages = map_packages.list_packages()
    ids = {p.get("id") for p in packages}
    assert "offline-standard-demo" in ids


def test_map_layers_include_standard_online():
    from services import map_layers

    layers = map_layers.list_map_layers(translate_name=lambda _k, d: d)
    ids = {layer["id"] for layer in layers}
    assert "standard-osm" in ids
    assert "satellite-esri" in ids


def test_map_registry_syncs_sqlite():
    from services import map_registry

    map_registry.sync_from_catalog()
    summary = map_registry.registry_summary()
    assert summary["layer_rows"] >= 5
    assert (map_registry.registry_path()).is_file()


def test_map_bundle_roundtrip(tmp_path, monkeypatch):
    from services import map_bundle, map_packages

    monkeypatch.setattr(map_packages, "map_data_root", lambda: tmp_path)
    (tmp_path / "catalog.json").write_text(
        '{"version": 2, "online_layers": [], "offline_packages": []}',
        encoding="utf-8",
    )
    buffer = map_bundle.create_portable_bundle()
    result = map_bundle.import_portable_bundle(buffer)
    assert result["files_extracted"] >= 1
    assert (tmp_path / "catalog.json").is_file()


def test_tile_cache_store_and_read(tmp_path, monkeypatch):
    import io

    from PIL import Image

    from services import map_packages, map_tile_cache

    monkeypatch.setattr(map_packages, "map_data_root", lambda: tmp_path)
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(10, 20, 30)).save(buf, format="PNG")
    png = buf.getvalue()
    map_tile_cache.store_cached_tile("standard-osm", "base", 3, 1, 2, png)
    out = map_tile_cache.read_cached_tile("standard-osm", "base", 3, 1, 2)
    assert out is not None and out.startswith(b"\x89PNG")
    summary = map_tile_cache.layer_cache_summary("standard-osm")
    assert summary["tiles"] == 1
    assert summary["bytes"] < summary["raw_bytes"]


def test_storage_summary_includes_cache(tmp_path, monkeypatch):
    from services import map_layers, map_packages, map_tile_cache

    monkeypatch.setattr(map_packages, "map_data_root", lambda: tmp_path)
    (tmp_path / "catalog.json").write_text('{"version": 2, "online_layers": [], "packages": []}', encoding="utf-8")
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2, 2)).save(buf, format="PNG")
    map_tile_cache.store_cached_tile("standard-osm", "base", 0, 0, 0, buf.getvalue())
    summary = map_layers.storage_summary()
    assert summary["cache_tiles"] == 1
    assert summary["total_bytes"] >= 1


def test_geolocation_export_spec_registered():
    from Api.routes.exports import EXPORT_SPECS

    assert "geolocation" in EXPORT_SPECS


def test_the_suite_never_writes_the_tracked_map_registry():
    """A test run must not dirty a tracked binary in the checkout.

    ``map_data/registry.db`` is 15 MB and tracked: the tile cache lives in it.
    ``test_map_registry_syncs_sqlite`` above opens and syncs it, which is why
    every full run left the file modified in ``git status`` and made "did this
    run change anything?" unanswerable. ``MAP_DATA_DIR`` moves the whole tree
    (and ``tests/conftest.py`` points it at an ignored temporary directory).
    """
    from services import map_registry

    from core.app_paths import get_project_root

    path = map_registry.registry_path().resolve()
    tracked = (get_project_root() / "map_data").resolve()
    assert not path.is_relative_to(tracked), (
        f"the registry is inside the tracked map tree: {path}")


def test_the_map_data_root_can_be_moved(monkeypatch, tmp_path):
    monkeypatch.setenv(map_packages.MAP_DATA_DIR_ENV, str(tmp_path / "map"))
    root = map_packages.map_data_root()
    assert root == (tmp_path / "map").resolve()
    assert (root / "packages").is_dir()
