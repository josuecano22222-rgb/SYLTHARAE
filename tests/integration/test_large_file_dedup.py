"""Integration: identical large files must deduplicate end to end (HASH-01).

Runs the real ``StoragePipeline._store_file_sync`` against a disposable
PostgreSQL database. Before HASH-01, ``Metadata['hash']`` for a file at or
above the inline-hash threshold was ``sha256(path|size|mtime)``; the pipeline
accepted it as the content identity, so two byte-identical large files at
different paths were stored as two distinct pieces of content.

The inline threshold is lowered so the deferred path is exercised without
writing 100 MB fixtures.
"""

import datetime
import os
import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

import core.file_utils as file_utils  # noqa: E402
from core.file_utils import create_standardized_result, get_standardized_metadata  # noqa: E402
from core.hashing import hash_file, is_valid_digest  # noqa: E402


@pytest.fixture
def force_deferred_hashing(monkeypatch):
    """Make small fixtures take the large-file (deferred) hashing path."""
    monkeypatch.setattr(file_utils, "HASH_INLINE_MAX_BYTES", 8)


@pytest.fixture
def conn(pg_db):
    c = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def source_side(conn):
    today = datetime.date.today()
    tag = os.getpid()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sides (name, importance, date_creation)"
            " VALUES (%s, 0.5, %s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name"
            " RETURNING id",
            (f"_hash01_side_{tag}", today),
        )
        side_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO sources (name, job, importance, country, date_creation)"
            " VALUES (%s, 'test', 0.5, 'test', %s) ON CONFLICT (name) DO NOTHING"
            " RETURNING id",
            (f"_hash01_source_{tag}", today),
        )
        row = cur.fetchone()
        source_id = row[0] if row else None
        if source_id is None:
            cur.execute(
                "SELECT id FROM sources WHERE name = %s", (f"_hash01_source_{tag}",)
            )
            source_id = cur.fetchone()[0]
    conn.commit()
    return f"_hash01_source_{tag}", f"_hash01_side_{tag}", source_id, side_id


def store(pipeline, path: Path, source_name, side_name):
    """Push one file through the real storage pipeline."""
    metadata = get_standardized_metadata(str(path))
    file_info = {
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower(),
        "type": "FILE",
        "size": path.stat().st_size,
    }
    result = create_standardized_result(str(path), {"content": path.read_text()}, 0.0)
    # Prove the producer emitted the sentinel, not a digest.
    assert metadata["hash"] == file_utils.HASH_DEFERRED_SENTINEL
    assert not is_valid_digest(metadata["hash"])
    return pipeline._store_file_sync(
        file_info, result, source_name=source_name, side_name=side_name
    )


def test_identical_large_files_share_one_content_identity(
    pg_db, conn, source_side, tmp_path, force_deferred_hashing, monkeypatch
):
    from pipeline.storage_pipeline import StoragePipeline

    source_name, side_name, source_id, side_id = source_side

    first = tmp_path / "alpha" / "report.txt"
    second = tmp_path / "beta" / "renamed-report.txt"
    for path in (first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("identical payload for deduplication check", encoding="utf-8")

    # Sanity: the files are byte-identical and above the (lowered) threshold.
    assert hash_file(str(first)) == hash_file(str(second))

    # Count extraction writes: identical bytes must be extracted once.
    from database.services.contents_db_service import ContentDBService

    extraction_calls = {"create_content": 0}
    _orig_create = ContentDBService.create_content

    def _counting_create(self, *args, **kwargs):
        extraction_calls["create_content"] += 1
        return _orig_create(self, *args, **kwargs)

    monkeypatch.setattr(ContentDBService, "create_content", _counting_create)

    pipeline = StoragePipeline()
    first_id = store(pipeline, first, source_name, side_name)
    second_id = store(pipeline, second, source_name, side_name)
    assert first_id, "first file was not stored"
    assert second_id, "second file was not stored"

    # Identity semantics (One Content, Many Contexts): identical bytes at two
    # different physical locations are two OCCURRENCES of one canonical
    # content. The provenance of each encounter is preserved; the content is
    # extracted once and shared.
    assert second_id != first_id, (
        f"two encounters of the same bytes were collapsed (path {first_id})"
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT h.hash, COUNT(p.id) AS occurrences
            FROM hashs h
            JOIN hash_contexts hc ON hc.hash_id = h.id JOIN paths p ON p.context_id = hc.id
            WHERE hc.source_id = %s AND hc.side_id = %s
            GROUP BY h.hash
            """,
            (source_id, side_id),
        )
        rows = cur.fetchall()

    # Exactly one content identity for the whole source/side, two occurrences.
    assert len(rows) == 1, f"expected one identity, got {rows}"
    stored_hash, occurrences = rows[0]
    assert occurrences == 2, occurrences
    assert stored_hash == hash_file(str(first)), (
        "the stored identity must be the real content hash, "
        "not a path/mtime-derived value"
    )
    assert is_valid_digest(stored_hash)

    # Extraction runs once: the second occurrence reuses the shared content.
    assert extraction_calls["create_content"] == 1, extraction_calls
