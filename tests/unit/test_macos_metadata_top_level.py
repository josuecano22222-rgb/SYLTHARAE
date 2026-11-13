"""macOS AppleDouble metadata in the top-level intake tree is not ingested.

An unzipped Mac export keeps ``__MACOSX/._*`` resource forks beside the real
files. They were read as images and failed with "cannot identify image file".
Archive members were already filtered; the top-level walk was not.
"""

from __future__ import annotations

import os

import pytest

from pipeline.integrated_reader import IntegratedFileReader, _macos_metadata_reason


@pytest.fixture()
def intake(tmp_path):
    root = tmp_path / "intake"
    (root / "__MACOSX" / "logos").mkdir(parents=True)
    (root / "logos").mkdir()
    (root / "logos" / "ASF_logo.png").write_bytes(b"\x89PNG-real")
    (root / "__MACOSX" / "logos" / "._ASF_logo.png").write_bytes(b"\x00\x05\x16\x07")
    (root / "._loose.jpg").write_bytes(b"\x00\x05\x16\x07")
    return str(root)


def test_the_reason_is_named_for_both_metadata_forms(intake):
    root = intake
    assert _macos_metadata_reason({"path": os.path.join(root, "__MACOSX", "logos", "x.png")}, root) == "macosx_metadata"
    assert _macos_metadata_reason({"path": os.path.join(root, "._loose.jpg")}, root) == "appledouble_resource_fork"
    assert _macos_metadata_reason({"path": os.path.join(root, "logos", "ASF_logo.png")}, root) is None


def test_inventory_counts_metadata_as_not_ingested(intake):
    reader = IntegratedFileReader(max_workers=1)
    inventory = reader._inventory_tree(intake)
    assert inventory["files"] == 1
    assert inventory["not_ingested"] == {"macosx_metadata": 1, "appledouble_resource_fork": 1}


def test_the_phase_batches_never_contain_metadata(intake):
    reader = IntegratedFileReader(max_workers=1)
    names = []
    for phase in ("image",):
        for batch in reader._iter_phase_batches(intake, phase, 50):
            names.extend(os.path.basename(item["path"]) for item in batch)
    assert names == ["ASF_logo.png"]
