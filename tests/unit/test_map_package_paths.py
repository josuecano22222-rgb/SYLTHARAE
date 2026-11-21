"""Map package archive and manifest paths stay inside the package.

Two checks in ``services/map_packages.py`` asked "is this path inside that
directory?" with ``str(path).startswith(str(root))``. That accepts a **sibling
whose name begins with the root's name**:

    root      /data/installed/pkg
    accepted  /data/installed/pkg_evil/tiles/0/0/0.png   (startswith -> True)

so an archive member named ``../pkg_evil/x`` or a manifest field pointing there
was treated as part of the package. The manifest is read from the downloaded
archive, and ``installed_pmtiles_path``/``raster_tile_path`` serve whatever it
names over HTTP, so this is a path escape with a network edge to it.

``Path.is_relative_to`` compares components, which is what the checks meant.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from services import map_packages


def test_a_sibling_with_a_shared_name_prefix_is_not_inside(tmp_path):
    root = (tmp_path / "pkg").resolve()
    sibling = (tmp_path / "pkg_evil" / "tiles").resolve()
    sibling.mkdir(parents=True)
    root.mkdir()
    assert str(sibling).startswith(str(root)), "the fixture must exercise the prefix"
    assert map_packages._is_inside(sibling, root) is False
    assert map_packages._is_inside(root / "tiles" / "0.png", root) is True


def test_a_zip_member_cannot_escape_through_a_sibling(tmp_path):
    dest = tmp_path / "pkg"
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../pkg_evil/pwned.txt", "escaped")
    with pytest.raises(ValueError, match="escapes destination"):
        map_packages._safe_extract(archive, dest)
    assert not (tmp_path / "pkg_evil" / "pwned.txt").exists()


def test_a_tar_member_cannot_escape_through_a_sibling(tmp_path):
    dest = tmp_path / "pkg"
    archive = tmp_path / "package.tar.gz"
    payload = b"escaped"
    info = tarfile.TarInfo("../pkg_evil/pwned.txt")
    info.size = len(payload)
    with tarfile.open(archive, "w:gz") as tar:
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(ValueError, match="escapes destination"):
        map_packages._safe_extract(archive, dest)
    assert not (tmp_path / "pkg_evil" / "pwned.txt").exists()


def test_an_ordinary_archive_still_extracts(tmp_path):
    dest = tmp_path / "pkg"
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("tiles/0/0/0.png", "tile")
    map_packages._safe_extract(archive, dest)
    assert (dest / "manifest.json").is_file()
    assert (dest / "tiles" / "0" / "0" / "0.png").is_file()


def test_a_manifest_cannot_name_a_file_outside_its_package(tmp_path, monkeypatch):
    map_root = tmp_path / "map_data"
    (map_root / "installed" / "pkg" ).mkdir(parents=True)
    (map_root / "installed" / "pkg_evil").mkdir(parents=True)
    (map_root / "installed" / "pkg_evil" / "secret.pmtiles").write_bytes(b"x")
    (map_root / "installed" / "pkg" / "manifest.json").write_text(
        '{"pmtiles": "../../pkg_evil/secret.pmtiles"}', encoding="utf-8")

    monkeypatch.setattr(map_packages, "map_data_root", lambda: map_root)
    assert map_packages.installed_pmtiles_path("pkg") is None
