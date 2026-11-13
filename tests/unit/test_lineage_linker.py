"""Unit tests for StoragePipeline._link_extracted_children (task 2, PARENT-01).

Uses a stub repository so the recursion and the hierarchy string construction
are tested without a database.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.storage_pipeline import StoragePipeline  # noqa: E402


class StubRepo:
    def __init__(self, names=None, fail_ids=()):
        self.names = names or {}
        self.fail_ids = set(fail_ids)
        self.updates = []

    def get_lineage(self, path_id):
        return (self.names.get(path_id, f"file{path_id}"), self.names.get(path_id))

    def update_lineage(self, path_id, parent_path_id, hierarchy_path):
        if path_id in self.fail_ids:
            raise RuntimeError("db exploded")
        self.updates.append((path_id, parent_path_id, hierarchy_path))


def pipeline_with(repo):
    pipe = StoragePipeline.__new__(StoragePipeline)
    pipe.db_service = type("S", (), {"paths_repo": repo})() if repo is not None else None
    return pipe


class TestNoOpCases:
    @pytest.mark.parametrize("content", [None, {}, {"extracted_files": "nope"},
                                         {"extracted_files": []}])
    def test_no_children_writes_nothing(self, content):
        repo = StubRepo()
        assert pipeline_with(repo)._link_extracted_children(content, 1) == 0
        assert repo.updates == []

    def test_no_parent_id_writes_nothing(self):
        repo = StubRepo()
        content = {"extracted_files": [{"database_path_id": 5}]}
        assert pipeline_with(repo)._link_extracted_children(content, None) == 0
        assert repo.updates == []

    def test_no_db_service_does_not_raise(self):
        content = {"extracted_files": [{"database_path_id": 5}]}
        assert pipeline_with(None)._link_extracted_children(content, 1) == 0

    def test_child_without_a_path_id_is_skipped(self):
        repo = StubRepo()
        content = {"extracted_files": [{"Metadata": {"name": "orphan.pdf"}}]}
        assert pipeline_with(repo)._link_extracted_children(content, 1) == 0
        assert repo.updates == []


class TestLinking:
    """Names are registered for every id the linker touches.

    The chain is built from the child row's OWN stored file_name, not from the
    archive member's name - see test_hierarchy_uses_the_rows_own_name. So the
    stub has to know each row's name, exactly as production does.
    """

    def test_single_child_gets_parent_and_hierarchy(self):
        repo = StubRepo(names={1: "outer.zip", 2: "child.pdf"})
        content = {"extracted_files": [
            {"database_path_id": 2, "Metadata": {"name": "child.pdf"}},
        ]}
        pipe = pipeline_with(repo)
        assert pipe._link_extracted_children(content, 1) == 1
        assert repo.updates == [(2, 1, "outer.zip::child.pdf")]

    def test_hierarchy_falls_back_to_file_name(self):
        """A top-level container has no stored hierarchy yet."""
        repo = StubRepo(names={2: "child.pdf"})  # parent 1 unregistered
        content = {"extracted_files": [
            {"database_path_id": 2, "Metadata": {"name": "child.pdf"}},
        ]}
        pipeline_with(repo)._link_extracted_children(content, 1)
        assert repo.updates[0][2] == "file1::child.pdf"

    def test_name_is_derived_from_path_when_absent(self):
        """No stored name on the row, so the member metadata is the fallback."""
        repo = StubRepo(names={1: "a.zip", 2: ""})  # row 2 has no file_name
        content = {"extracted_files": [
            {"database_path_id": 2, "Metadata": {"path": "/tmp/x/deep.pdf"}},
        ]}
        pipeline_with(repo)._link_extracted_children(content, 1)
        assert repo.updates[0][2] == "a.zip::deep.pdf"

    def test_hierarchy_uses_the_rows_own_name(self):
        """The row's stored name wins over the archive member's name.

        Identical bytes under two member names collapse onto one paths row.
        Building the chain from the member name wrote the LAST member's name
        into that row, so file_name and hierarchy_path contradicted each other:
        observed as file_name='duplicate_a.txt' with
        hierarchy_path='dup.zip::duplicate_b.txt'. Deriving the chain from the
        stored name makes the two agree by construction and makes repeated
        links idempotent.
        """
        repo = StubRepo(names={1: "outer.zip", 2: "duplicate_a.txt"})
        content = {"extracted_files": [
            {"database_path_id": 2, "Metadata": {"name": "duplicate_a.txt"}},
            {"database_path_id": 2, "Metadata": {"name": "duplicate_b.txt"}},
        ]}
        pipeline_with(repo)._link_extracted_children(content, 1)
        assert repo.updates == [
            (2, 1, "outer.zip::duplicate_a.txt"),
            (2, 1, "outer.zip::duplicate_a.txt"),
        ], repo.updates
        assert all(u[2].endswith("::duplicate_a.txt") for u in repo.updates)

    def test_recursion_builds_the_full_chain(self):
        repo = StubRepo(names={1: "outer.zip", 2: "inner.zip", 3: "scan.png"})
        content = {"extracted_files": [{
            "database_path_id": 2,
            "Metadata": {"name": "inner.zip"},
            "Content": {"extracted_files": [{
                "database_path_id": 3,
                "Metadata": {"name": "scan.png"},
            }]},
        }]}
        pipe = pipeline_with(repo)
        assert pipe._link_extracted_children(content, 1) == 2
        assert repo.updates == [
            (2, 1, "outer.zip::inner.zip"),
            (3, 2, "outer.zip::inner.zip::scan.png"),
        ]

    def test_separator_is_a_named_constant(self):
        assert StoragePipeline.HIERARCHY_SEPARATOR == "::"


class TestFailureHandling:
    def test_one_bad_child_does_not_abort_the_rest(self):
        """A failed link must not fail the ingest - the child is still indexed."""
        repo = StubRepo(names={1: "a.zip", 3: "good.pdf"}, fail_ids={2})
        content = {"extracted_files": [
            {"database_path_id": 2, "Metadata": {"name": "bad.pdf"}},
            {"database_path_id": 3, "Metadata": {"name": "good.pdf"}},
        ]}
        pipe = pipeline_with(repo)
        assert pipe._link_extracted_children(content, 1) == 1
        assert repo.updates == [(3, 1, "a.zip::good.pdf")]

    def test_get_lineage_failure_does_not_raise(self):
        class Broken(StubRepo):
            def get_lineage(self, path_id):
                raise RuntimeError("no connection")

        repo = Broken()
        content = {"extracted_files": [
            {"database_path_id": 2, "Metadata": {"name": "c.pdf"}},
        ]}
        pipeline_with(repo)._link_extracted_children(content, 1)
        assert repo.updates == [(2, 1, "c.pdf")]
