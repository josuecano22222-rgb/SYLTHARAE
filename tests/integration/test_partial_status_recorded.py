"""A stored document that lost a derived step must say so in the database.

Recorded defect: a 2.1 GB PST was reported as

    [STORAGE] ⚠️  'ssss.pst' stored with degraded data: raw_text step failed ...

while its row still said ``processed``.  "The file is reported as successful
despite incomplete extracted content" is a forensic-accuracy problem: an
operator querying the database cannot tell that the display text is missing.

These tests pin the contract:

* the failing step is named in ``status_detail``;
* ``processing_status`` becomes ``partially_processed`` - not ``processed`` (a lie) and not
  ``failed`` (the evidence *is* stored and must stay findable);
* the document itself remains stored and searchable;
* a rejected status write cannot take the document down with it.
"""

from __future__ import annotations

import pathlib
import sys
import uuid
from datetime import date

import psycopg2
import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]


def _pg_connect(cfg):
    return psycopg2.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], dbname=cfg["database"],
    )


@pytest.fixture(scope="module")
def tenant(pg_db):
    source_name = f"_partial_src_{_UNIQUE}"
    side_name = f"_partial_side_{_UNIQUE}"
    conn = _pg_connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation)"
                " VALUES (%s, 0.5, %s)"
                " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name"
                " RETURNING id",
                (side_name, date.today()),
            )
            side_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES (%s, 'test', 0.5, 'test', %s)"
                " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name"
                " RETURNING id",
                (source_name, date.today()),
            )
            source_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return {"source_id": source_id, "side_id": side_id}


def _store(service, tenant, index, raw_text="display text"):
    marker = f"{_UNIQUE}partial{index}"
    return service.process_full_document(
        hash_value=f"hash-{marker}",
        source_id=tenant["source_id"],
        side_id=tenant["side_id"],
        file_name=f"{marker}.txt",
        file_path=f"/tmp/partial/{marker}.txt",
        file_size=1024,
        file_type="txt",
        file_status="Read",
        file_date=date.today(),
        content_words=["alpha", "beta", "gamma"],
        title_words=[f"title{index}"],
        raw_text=raw_text,
        attempts=1,
    )


def test_partial_status_is_allowed_by_the_schema(pg_db):
    """'partially_processed' is part of m0007's status vocabulary."""
    conn = _pg_connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_get_constraintdef(c.oid)
                  FROM pg_constraint c
                  JOIN pg_class t ON t.oid = c.conrelid
                 WHERE t.relname = 'paths'
                   AND c.conname = 'paths_processing_status_check'
                """
            )
            definition = cur.fetchone()[0]
    finally:
        conn.close()
    assert "'partially_processed'" in definition, definition


def test_failed_derived_step_marks_the_row_partial(pg_db, tenant, monkeypatch):
    from database.database.repository.contents_repo import ContentsRepository
    from database.services.contents_db_service import ContentDBService

    service = ContentDBService()
    stored = _store(service, tenant, 1)
    assert stored.get("path_id"), stored

    def failing_store(self, path_id, text, chunk_size=1024 * 1024):
        raise RuntimeError("injected raw-text failure")

    monkeypatch.setattr(ContentsRepository, "store_raw_content", failing_store)
    degraded = _store(service, tenant, 2)
    monkeypatch.undo()

    conn = _pg_connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT processing_status, status_detail FROM paths WHERE id = %s",
                (degraded["path_id"],),
            )
            status, detail = cur.fetchone()
            cur.execute(
                "SELECT count(*) FROM words_hashs WHERE hash_id = %s",
                (degraded["hash_id"],),
            )
            word_links = cur.fetchone()[0]
    finally:
        conn.close()

    assert degraded.get("path_id"), "the document must still be stored"
    assert degraded.get("warnings"), "the degraded step must be reported"
    assert status == "partially_processed", (
        f"a document missing its display text reported '{status}': degraded "
        f"storage must not look like a clean success"
    )
    assert "raw_text" in (detail or ""), f"the failing step must be named: {detail!r}"
    assert word_links > 0, "the document must stay searchable despite the partial store"


def test_clean_document_is_not_marked_partial(pg_db, tenant):
    from database.services.contents_db_service import ContentDBService

    service = ContentDBService()
    stored = _store(service, tenant, 3)

    conn = _pg_connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT processing_status FROM paths WHERE id = %s",
                (stored["path_id"],),
            )
            status = cur.fetchone()[0]
    finally:
        conn.close()
    assert status != "partially_processed", "a complete store must not be marked partial"
