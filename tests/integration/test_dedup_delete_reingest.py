"""Integration tests: hashing, deduplication, delete/re-ingest (DB-03..05, Gate 4).

Acceptance workflow from the audit (DB-05):

    ingest file -> verify record -> delete record -> ingest identical file
    -> verify new valid record exists
"""

import sys
import datetime
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

from core.hashing import hash_file
from database.services.dedup_service import DeduplicationService


@pytest.fixture()
def conn(pg_db):
    c = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    yield c
    c.rollback()
    c.close()


@pytest.fixture()
def dedup(conn):
    return DeduplicationService(lambda: conn)


@pytest.fixture()
def source_side_ids(conn):
    today = datetime.date.today()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sides (name, importance, date_creation)"
            " VALUES (%s, %s, %s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name"
            " RETURNING id",
            (f"_test_side_{os.getpid()}", 0.5, today),
        )
        side_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO sources (name, job, importance, country, date_creation)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (name) DO NOTHING RETURNING id",
            (f"_test_source_{os.getpid()}", "test", 0.5, "test", today),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "SELECT id FROM sources WHERE name = %s", (f"_test_source_{os.getpid()}",)
            )
            row = cur.fetchone()
        source_id = row[0]
        conn.commit()
    return source_id, side_id


import os  # noqa: E402


def make_file(tmp_path, content: bytes = b"unique content for dedup tests"):
    f = tmp_path / "sample.txt"
    f.write_bytes(content)
    return f


def path_row(f: Path):
    return {
        "file_name": f.name,
        "file_path": str(f),
        "file_size": f.stat().st_size,
        "file_type": "txt",
        "file_date": datetime.date.today(),
        "date_creation": datetime.date.today(),
    }


class TestDedupSemantics:
    def test_register_and_find(self, conn, dedup, source_side_ids, tmp_path):
        source_id, side_id = source_side_ids
        f = make_file(tmp_path)
        content_hash = hash_file(f)

        first = dedup.register_content(content_hash, source_id, side_id, path_row(f))
        assert first["duplicate"] is False
        assert first["path_id"] > 0

        # Same content again IS a duplicate with the same path id.
        is_dup, existing = dedup.check_duplicate(content_hash, source_id, side_id)
        assert is_dup is True
        assert existing == first["path_id"]

        # Same content under a different side is NOT a duplicate.
        is_dup2, _ = dedup.check_duplicate(content_hash, source_id, side_id + 1000000)
        assert is_dup2 is False


class TestDeleteReingest:
    def test_full_acceptance_workflow(self, conn, dedup, source_side_ids, tmp_path):
        """The audit's failing workflow: ingest -> delete -> re-ingest."""
        source_id, side_id = source_side_ids
        f = make_file(tmp_path, b"reingest acceptance workflow content")
        content_hash = hash_file(f)

        # 1. ingest
        reg = dedup.register_content(content_hash, source_id, side_id, path_row(f))
        path_id = reg["path_id"]
        assert path_id > 0

        # verify record
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM paths WHERE id = %s", (path_id,))
            assert cur.fetchone()[0] == 1

        # 2. delete (content lifecycle: occurrence -> context -> content)
        result = dedup.delete_path(path_id)
        assert result == {
            "path_deleted": True,
            "context_deleted": True,
            "hash_deleted": True,
        }

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM paths WHERE id = %s", (path_id,))
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT COUNT(*) FROM hash_contexts"
                " WHERE hash_id = (SELECT id FROM hashs WHERE hash = %s)"
                " AND source_id = %s AND side_id = %s",
                (content_hash, source_id, side_id),
            )
            assert cur.fetchone()[0] == 0, "orphaned context row must be cleaned on delete"
            cur.execute("SELECT COUNT(*) FROM hashs WHERE hash = %s", (content_hash,))
            assert cur.fetchone()[0] == 0, "orphaned hash row must be cleaned on delete"

        # 3. re-ingest the identical file - must succeed (DB-05 acceptance)
        reg2 = dedup.register_content(content_hash, source_id, side_id, path_row(f))
        assert reg2["duplicate"] is False
        assert reg2["path_id"] > 0
        assert reg2["path_id"] != path_id  # a NEW valid record

        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_name, file_size FROM paths WHERE id = %s", (reg2["path_id"],)
            )
            name, size = cur.fetchone()
        assert name == f.name
        assert size == f.stat().st_size

    def test_hash_survives_while_other_paths_reference_it(
        self, conn, dedup, source_side_ids, tmp_path
    ):
        source_id, side_id = source_side_ids
        f1 = tmp_path / "one.txt"
        f2 = tmp_path / "two.txt"
        f1.write_bytes(b"shared content bytes")
        f2.write_bytes(b"shared content bytes")
        content_hash = hash_file(f1)

        r1 = dedup.register_content(content_hash, source_id, side_id, path_row(f1))
        # Same content, different file path in the same context: a second
        # legitimate occurrence. Its identity is the shared context.
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO paths (file_name, file_path, file_size, file_type,"
                " file_date, date_creation, context_id)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (f2.name, str(f2), f2.stat().st_size, "txt",
                 datetime.date.today(), datetime.date.today(), r1["context_id"]),
            )
            second_path_id = cur.fetchone()[0]
            conn.commit()

        # Delete one occurrence: shared content and context must survive.
        lifecycle = dedup.delete_path(r1["path_id"])
        assert lifecycle["path_deleted"] is True
        assert lifecycle["context_deleted"] is False
        assert lifecycle["hash_deleted"] is False
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM hashs WHERE id = %s", (r1["hash_id"],))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT COUNT(*) FROM hash_contexts WHERE id = %s", (r1["context_id"],))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT COUNT(*) FROM paths WHERE id = %s", (second_path_id,))
            assert cur.fetchone()[0] == 1

    def test_orphan_cleanup_tool(self, conn, dedup, source_side_ids, tmp_path):
        source_id, side_id = source_side_ids
        f = make_file(tmp_path, b"orphan cleanup test")
        content_hash = hash_file(f)
        reg = dedup.register_content(content_hash, source_id, side_id, path_row(f))

        # Simulate the legacy defect: force-delete the path without cleanup.
        with conn.cursor() as cur:
            cur.execute("DELETE FROM paths WHERE id = %s", (reg["path_id"],))
            conn.commit()

        # Layered identity: the empty CONTEXT is orphaned now, but the
        # CANONICAL CONTENT is not - it still has that context. The repair
        # cascades context-first, and only then does the content become an
        # orphan. (The old model had no context layer, so the hash orphaned
        # immediately; the assertion below pins the new order.)
        orphan_contexts = dedup.cleanup_orphaned_contexts(dry_run=True)
        assert orphan_contexts >= 1
        assert dedup.cleanup_orphaned_hashes(dry_run=True) == 0, (
            "the content is not an orphan while its (empty) context exists"
        )
        removed_contexts = dedup.cleanup_orphaned_contexts(dry_run=False)
        assert removed_contexts >= 1
        # Now the content has no context left: it is an orphan and removable.
        orphans = dedup.cleanup_orphaned_hashes(dry_run=True)
        assert orphans >= 1
        removed = dedup.cleanup_orphaned_hashes(dry_run=False)
        assert removed >= 1
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM hashs WHERE id = %s", (reg["hash_id"],))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT COUNT(*) FROM hash_contexts WHERE id = %s", (reg["context_id"],))
            assert cur.fetchone()[0] == 0
