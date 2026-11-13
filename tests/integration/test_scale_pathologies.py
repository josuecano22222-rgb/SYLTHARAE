"""Scale pathologies found by auditing the pipeline against multi-million-file
workloads, each pinned by a test.

These are not stylistic concerns: every one of them grows with the corpus and
would dominate a large run long before the pipeline ran out of real work.

1. extraction folder names must be O(1) and stable, not a linear search for a
   free numeric suffix (quadratic across archives that share a basename);
2. an extraction folder must be emptied before extracting into it, or members
   left by an earlier attempt are ingested as children of the current archive;
3. nested-tree enumeration must stream (no full metadata tree, no double
   hashing);
4. per-container accounting must be bounded, not one entry per container;
5. the end-of-run summary must report the accounting, not the size of the
   deliberately bounded results window.
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from core.path_utils import get_extraction_name_file, reset_extraction_dir  # noqa: E402
from pipeline.progress_ledger import (  # noqa: E402
    OUTCOME_COMPLETED,
    ProgressLedger,
)


# ---------------------------------------------------------------------------
# 1. Extraction naming is O(1) and stable
# ---------------------------------------------------------------------------
class TestExtractionNaming:
    def test_same_source_always_maps_to_the_same_folder(self, tmp_path):
        source = tmp_path / "data.zip"
        source.write_bytes(b"PK\x03\x04")
        first = get_extraction_name_file(str(source), ".zip", base_path=tmp_path)
        Path(first).mkdir(parents=True, exist_ok=True)
        second = get_extraction_name_file(str(source), ".zip", base_path=tmp_path)
        assert first == second, "extraction name must be stable for a source file"

    def test_many_same_named_archives_do_not_accumulate_suffixes(self, tmp_path):
        """The old implementation minted name_1, name_2, ... and, worse, did it
        with a linear search: the (n+1)-th folder of a given name cost n stats.
        """
        base = tmp_path / "extracted"
        base.mkdir()
        names = []
        for index in range(50):
            source = tmp_path / f"dir{index}" / "report.zip"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"PK\x03\x04")
            name = get_extraction_name_file(str(source), ".zip", base_path=base)
            Path(name).mkdir(parents=True, exist_ok=True)
            names.append(name)
        assert len(set(names)) == 50, "same-named archives must not collide"
        # No numeric suffix chain, which is what made the search quadratic.
        assert not any(n.rstrip("0123456789_").endswith("report__") and n[-2] == "_"
                       for n in names)
        assert all(len(Path(n).name.split("__")[-1]) == 12 for n in names), names[:3]

    def test_embedded_extraction_without_extension_still_works(self, tmp_path):
        source = tmp_path / "deed.docx"
        source.write_bytes(b"PK\x03\x04")
        name = get_extraction_name_file(str(source), "", base_path=tmp_path)
        assert name.endswith("deed__") is False and "deed__" in name


# ---------------------------------------------------------------------------
# 2. Stale members cannot be inherited
# ---------------------------------------------------------------------------
class TestExtractionFolderReset:
    def test_reset_empties_an_existing_folder(self, tmp_path):
        target = tmp_path / "target"
        (target / "sub").mkdir(parents=True)
        (target / "stale.txt").write_text("old member")
        (target / "sub" / "stale2.txt").write_text("old member")
        reset_extraction_dir(str(target))
        assert list(target.iterdir()) == []

    def test_reset_creates_the_folder_when_missing(self, tmp_path):
        target = tmp_path / "fresh"
        reset_extraction_dir(str(target))
        assert target.is_dir()

    def test_re_extracting_an_archive_does_not_leave_old_members(self, tmp_path):
        """Re-extraction must publish the current member set exactly."""
        source = tmp_path / "bundle.zip"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("member.txt", "current")
        extract_to = get_extraction_name_file(str(source), ".zip", base_path=tmp_path)
        reset_extraction_dir(extract_to)
        with zipfile.ZipFile(source) as archive:
            archive.extractall(extract_to)
        # A second archive that no longer contains the member must not leave it.
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("replacement.txt", "new")
        reset_extraction_dir(extract_to)
        with zipfile.ZipFile(source) as archive:
            archive.extractall(extract_to)
        members = sorted(p.name for p in Path(extract_to).iterdir())
        assert members == ["replacement.txt"], members


# ---------------------------------------------------------------------------
# 3. Nested enumeration streams
# ---------------------------------------------------------------------------
class TestNestedEnumerationStreams:
    def test_router_enumerates_extracted_trees_without_a_metadata_tree(self, tmp_path):
        from reader_file.services.file_router_service import FileRouterService

        root = tmp_path / "extracted"
        for index in range(300):
            (root / f"d{index % 10}").mkdir(parents=True, exist_ok=True)
            (root / f"d{index % 10}" / f"member_{index}.txt").write_text("body")
        service = FileRouterService()
        files, dirs = service._collect_extraction_files(str(root))
        assert len(files) == 300
        assert dirs >= 10
        # Hash is the deferred sentinel: members are not hashed twice.
        from core.file_utils import HASH_DEFERRED_SENTINEL

        assert all(entry["hash"] == HASH_DEFERRED_SENTINEL for entry in files[:5])


# ---------------------------------------------------------------------------
# 4. Per-container accounting is bounded
# ---------------------------------------------------------------------------
class TestBoundedContainerAccounting:
    def test_container_map_does_not_grow_without_bound(self):
        ledger = ProgressLedger(name="bounded-containers")
        ledger._children_detail_limit = 100
        for index in range(1000):
            ledger.record(OUTCOME_COMPLETED, 1, parent=f"container_{index}.zip")
        assert len(ledger.children_by_parent) <= 100

        snapshot = ledger.snapshot()
        # Exactness is preserved: detail plus overflow is the true nested total.
        total = sum(snapshot["children_by_parent"].values())
        total += snapshot["children_by_parent_overflow"]
        assert total == 1000


# ---------------------------------------------------------------------------
# 5. The summary reports the accounting, not the window
# ---------------------------------------------------------------------------
class TestSummaryUsesAccounting:
    def test_processed_count_comes_from_the_ledger_not_the_result_window(self):
        import inspect

        from pipeline import integrated_reader as module

        source = inspect.getsource(module.IntegratedFileReader.process_folder)
        assert "len([r for r in results if r])" not in source, (
            "the summary must not derive processed/failed counts from the "
            "bounded results window"
        )
        assert "final_stats.get('completed'" in source
