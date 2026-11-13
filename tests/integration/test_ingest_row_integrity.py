"""Two ingest writes that did not match the frozen schema or each other.

* ``TitleQueries.insert_title`` inserted ``title_content_id``, a column the
  baseline ``titles_content`` table does not have (id, title_data,
  title_status, hash_id). Every title insert therefore raised UndefinedColumn:
  the document was stored but its title was not, and the failure surfaced as a
  partial document rather than as "this statement is wrong".
* ``FileQueries.mark_partial`` replaced ``paths.status_detail`` with the
  derived step's message, discarding the reason the reader had already
  recorded (a container naming the members it could not decode, a page-capped
  PDF). The documented behaviour is now accumulation, bounded to 500
  characters, with a repeated message not stored twice.
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


def connect(cfg):
    return psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                            password=cfg["password"], dbname=cfg["database"])


@pytest.fixture(scope="module")
def tenant(pg_db):
    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("INSERT INTO sides (name, importance, date_creation)"
                        " VALUES (%s, 0.5, %s) RETURNING id",
                        (f"_ingest_side_{_UNIQUE}", date.today()))
            side_id = cur.fetchone()[0]
            cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                        " VALUES (%s, 'test', 0.5, 'test', %s) RETURNING id",
                        (f"_ingest_src_{_UNIQUE}", date.today()))
            source_id = cur.fetchone()[0]
            cur.execute("INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                        (f"_ingest_hash_{_UNIQUE}",))
            hash_id = cur.fetchone()[0]
            cur.execute("INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                        " VALUES (%s, %s, %s) RETURNING id", (hash_id, source_id, side_id))
            context_id = cur.fetchone()[0]
            cur.execute("INSERT INTO paths (file_name, file_path, file_size, file_type,"
                        " file_date, date_creation, context_id)"
                        " VALUES (%s, %s, 0, 'txt', %s, %s, %s) RETURNING id",
                        (f"_ingest_{_UNIQUE}.txt", f"/tmp/_ingest_{_UNIQUE}.txt",
                         date.today(), date.today(), context_id))
            path_id = cur.fetchone()[0]
    finally:
        conn.close()
    return {"hash_id": hash_id, "path_id": path_id, "context_id": context_id}


def test_a_title_is_stored_without_a_column_the_schema_lacks(pg_db, tenant):
    """The insert must name only columns ``titles_content`` has."""
    from database.database.queries.title_queries import TitleQueries
    from database.database.repository.titles_content_repo import TitlesContentRepository

    sql = TitleQueries.insert_title()
    assert "title_content_id" not in sql, sql

    from database.services.contents_db_service import ContentDBService

    service = ContentDBService()
    repo = TitlesContentRepository(service.db)
    title_id = repo.insert_titles_content([1, 2, 3], tenant["hash_id"], "Main")
    assert title_id, title_id

    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT title_status, hash_id FROM titles_content WHERE id = %s",
                        (title_id,))
            status, hash_id = cur.fetchone()
    finally:
        conn.close()
    assert (status, hash_id) == ("Main", tenant["hash_id"])


def test_a_partial_row_keeps_the_reason_it_already_had(pg_db, tenant):
    from database.database.repository.paths_repo import PathsRepository
    from database.services.contents_db_service import ContentDBService

    repo = PathsRepository(ContentDBService().db)
    assert repo.mark_partial(tenant["path_id"],
                             "container could not decode member 3.msg") is True
    repo.mark_partial(tenant["path_id"], "raw_text step failed")
    repo.mark_partial(tenant["path_id"], "raw_text step failed")

    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT status_detail FROM paths WHERE id = %s", (tenant["path_id"],))
            detail = cur.fetchone()[0]
    finally:
        conn.close()

    assert "container could not decode member 3.msg" in detail, detail
    assert "raw_text step failed" in detail, detail
    assert detail.count("raw_text step failed") == 1, detail
    assert len(detail) <= 500, len(detail)
