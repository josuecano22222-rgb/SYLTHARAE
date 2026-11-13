"""The discovery contract that every ingestion run depends on.

``core/file_utils`` was rewritten to stream (``iter_tree``) instead of building a
list of every file in the tree, because the list form needed ~1.7 KB of resident
memory per entry (~17 GB for a ten-million-file corpus) before a byte of content
was read. These tests pin the behaviour the rest of the pipeline relies on -
record shape, error isolation, hash deferral, and the handling of entries that
are deliberately not ingested - so the rewrite cannot silently change what a run
"discovers".

The key set asserted here is the historical set: it was extracted from the
list-based implementation that preceded the streaming one, so a field that
disappears breaks this test rather than a downstream reader.
"""

import inspect
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import file_utils  # noqa: E402
from core.file_utils import (  # noqa: E402
    HASH_DEFERRED_SENTINEL,
    iter_tree,
    read_tree,
)

#: Fields every record carries (the contract consumers index into).
REQUIRED_FIELDS = {
    "name", "path", "type", "extension", "size", "size_bytes", "hash",
    "created", "modified", "accessed", "readable", "writable", "executable",
    "processing_time",
}


@pytest.fixture()
def tree(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(b"\x00\x01" * 10)
    return tmp_path


def test_every_record_carries_the_documented_fields(tree):
    for record in read_tree(str(tree)):
        missing = REQUIRED_FIELDS - set(record)
        assert not missing, (record.get("name"), missing)


def test_files_directories_and_nested_files_are_all_reported(tree):
    records = {r["name"]: r for r in read_tree(str(tree))}
    assert records["a.txt"]["type"] == "FILE"
    assert records["sub"]["type"] == "DIRECTORY"
    assert records["b.bin"]["type"] == "FILE"
    assert records["a.txt"]["extension"] == ".txt"
    assert records["a.txt"]["size_bytes"] == 5


def test_iter_tree_streams_rather_than_building_a_list(tree):
    """A list cannot be a generator, and the memory guarantee needs one."""
    stream = iter_tree(str(tree))
    assert inspect.isgenerator(stream)
    assert [r["name"] for r in stream]  # starts producing immediately


def test_hashing_can_be_deferred_without_inventing_a_digest(tree):
    """A deferred hash is a sentinel, never something that looks like a digest."""
    record = next(r for r in iter_tree(str(tree), compute_hashes=False)
                  if r["name"] == "a.txt")
    assert record["hash"] == HASH_DEFERRED_SENTINEL
    assert len(HASH_DEFERRED_SENTINEL) != 64


def test_deferred_hashing_does_not_read_the_file(tree, monkeypatch):
    def _explode(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("discovery read file content although hashes were deferred")

    monkeypatch.setattr(file_utils, "calculate_file_hash", _explode)
    records = list(iter_tree(str(tree), compute_hashes=False))
    assert any(r["name"] == "a.txt" for r in records)


def test_default_hashing_still_hashes_readable_files(tree):
    record = next(r for r in read_tree(str(tree)) if r["name"] == "a.txt")
    assert len(record["hash"]) == 64


def test_missing_root_is_reported_not_raised(tmp_path):
    records = list(iter_tree(str(tmp_path / "nope")))
    assert len(records) == 1
    assert records[0]["type"] == "ERROR"
    assert records[0]["error"]


def test_a_file_passed_as_the_root_is_reported_as_that_file(tmp_path):
    """Single-file ingestion: the list-based walk returned nothing here."""
    target = tmp_path / "one.txt"
    target.write_text("only me")
    records = list(iter_tree(str(target)))
    assert [r["name"] for r in records] == ["one.txt"]
    assert records[0]["type"] == "FILE"


def test_symlinks_are_reported_but_not_followed(tree):
    """Following a link would ingest its target twice and can cycle."""
    link = tree / "link-to-sub"
    try:
        link.symlink_to(tree / "sub", target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - platform cannot
        pytest.skip("symlinks unavailable on this platform")

    records = list(iter_tree(str(tree)))
    types = {r["name"]: r["type"] for r in records}
    assert types.get("link-to-sub") == "SYMLINK"
    # the link is a record, but nothing under the link is enumerated a second time
    assert [r["name"] for r in records].count("b.bin") == 1


def test_inventory_accounts_for_entries_it_does_not_ingest(tree):
    """'Not ingested' must be counted, not silently dropped."""
    from pipeline.integrated_reader import IntegratedFileReader

    link = tree / "loop"
    try:
        link.symlink_to(tree, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover
        pytest.skip("symlinks unavailable on this platform")

    reader = IntegratedFileReader(max_workers=1)
    inventory = reader._inventory_tree(str(tree))
    assert inventory["files"] == 2, inventory          # a.txt + sub/b.bin
    assert inventory["not_ingested"].get("SYMLINK") == 1
    # The cycle is not walked, so the count cannot grow.
    assert inventory["total"] == inventory["files"] + inventory["errors"]


def test_readable_and_unreadable_are_reported_per_record(tree):
    records = {r["name"]: r for r in read_tree(str(tree))}
    assert records["a.txt"]["readable"] is True
    if os.name == "nt":  # pragma: no cover - Windows-only check
        assert records["a.txt"]["writable"] is True
