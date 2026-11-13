"""Integration: concurrent document storage must not lose documents.

Root cause of the reported production failures (2026-09 ingest logs):

``StoragePipeline`` holds ONE ``ContentDBService`` and
``IntegratedFileReader._process_with_threads`` starts one managed thread per
file, so several documents are stored through the same service instance at the
same time.  ``ContentDBService.transaction()`` implemented its transaction by
replacing the service's repositories with connection-bound clones
(``setattr(self, repo_name, transactional_repo)``).  Two threads overwrote each
other's repositories, so statements were executed on another thread's
connection - visible as

    psycopg2.errors.UndefinedTable: relation "tmp_words" does not exist
    psycopg2.errors.InFailedSqlTransaction: current transaction is aborted
    psycopg2.errors.DatabaseError: no COPY in progress
    server sent data ("D" message) without prior row description ("T" message)

followed by ``Hash ID N does not exist (transaction may have been rolled
back)`` and foreign key violations (``fk_contents_raw_path``,
``words_hashs_word_id_fkey``, ...) for the documents that lost the race.

These tests drive concurrent ingestion through the shared service and through
the real reader, then verify the database contents - not merely that no
exception was raised.
"""

from __future__ import annotations

import pathlib
import sys
import threading
import uuid
from datetime import date

import psycopg2
import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]

# Vocabulary shared by every document: concurrent inserts of the *same* new
# words exercise the ON CONFLICT path that the old temp-table/COPY design
# could not survive.
_SHARED_VOCAB = [
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
]


def _pg_connect(cfg):
    return psycopg2.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], dbname=cfg["database"],
    )


@pytest.fixture(scope="module")
def tenant(pg_db):
    """A source + side pair shared by every document in this module."""
    source_name = f"_conc_src_{_UNIQUE}"
    side_name = f"_conc_side_{_UNIQUE}"
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
    return {"source_id": source_id, "side_id": side_id,
            "source_name": source_name, "side_name": side_name}


def _document(index: int, words: int = 120):
    """Deterministic document payload for one worker."""
    marker = f"{_UNIQUE}doc{index}"
    content = []
    for i in range(words):
        content.append(_SHARED_VOCAB[i % len(_SHARED_VOCAB)])
        if i % 7 == 0:
            content.append(f"w{index}_{i}")  # per-document vocabulary too
    return {
        "hash_value": f"hash-{marker}",
        "file_name": f"{marker}.txt",
        "file_path": f"/tmp/concurrent/{marker}.txt",
        "content_words": content,
        "title_words": [f"title{index}", _SHARED_VOCAB[index % len(_SHARED_VOCAB)]],
        "raw_text": f"Raw text for {marker}\n" + " ".join(content[:40]),
    }


def _store_documents(service, tenant, indexes, results, errors, barrier):
    """Worker: store a sequence of documents through the shared service."""
    try:
        barrier.wait(timeout=30)  # maximise overlap between threads
        for index in indexes:
            payload = _document(index)
            results[index] = service.process_full_document(
                hash_value=payload["hash_value"],
                source_id=tenant["source_id"],
                side_id=tenant["side_id"],
                file_name=payload["file_name"],
                file_path=payload["file_path"],
                file_size=1024,
                file_type="txt",
                file_status="Read",
                file_date=date.today(),
                content_words=payload["content_words"],
                title_words=payload["title_words"],
                raw_text=payload["raw_text"],
                attempts=1,
            )
    except Exception as exc:  # noqa: BLE001 - reported by the test
        errors[len(results)] = exc


def test_concurrent_storage_through_shared_service(pg_db, tenant):
    """N threads x M documents through one ContentDBService: all or nothing.

    The production log failed several files of a batch with the
    tmp_words/aborted-transaction cascade; after the fix every document of the
    batch must be stored and completely retrievable.
    """
    from database.services.contents_db_service import ContentDBService

    service = ContentDBService()
    threads_count = 6
    docs_per_thread = 3
    total = threads_count * docs_per_thread

    results, errors = {}, {}
    barrier = threading.Barrier(threads_count)
    threads = []
    for t in range(threads_count):
        indexes = [t * docs_per_thread + d for d in range(docs_per_thread)]
        thread = threading.Thread(
            target=_store_documents,
            args=(service, tenant, indexes, results, errors, barrier),
            name=f"ingest-{t}",
        )
        threads.append(thread)

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)

    assert not any(thread.is_alive() for thread in threads), "ingest deadlocked"
    assert not errors, f"concurrent ingest raised: {errors}"
    assert len(results) == total, f"only {len(results)}/{total} completed"

    path_ids = []
    for index, result in sorted(results.items()):
        assert result["success"], f"document {index} not stored: {result}"
        assert result["path_id"], f"document {index} has no path_id: {result}"
        assert result["hash_id"], f"document {index} has no hash_id: {result}"
        path_ids.append(result["path_id"])
    assert len(set(path_ids)) == total, "documents share a path_id"

    # ---- verify the database really contains every document ---------------
    conn = _pg_connect(pg_db)
    try:
        with conn.cursor() as cur:
            for index, result in sorted(results.items()):
                payload = _document(index)
                path_id = result["path_id"]

                cur.execute(
                    "SELECT p.file_name, hc.hash_id FROM paths p"
                    " JOIN hash_contexts hc ON hc.id = p.context_id WHERE p.id = %s",
                    (path_id,),
                )
                row = cur.fetchone()
                assert row, f"path row {path_id} missing for document {index}"
                assert row[0] == payload["file_name"]

                cur.execute(
                    "SELECT COUNT(*) FROM contents WHERE hash_id = %s",
                    (result["hash_id"],),
                )
                assert cur.fetchone()[0] > 0, (
                    f"document {index} has no content rows (words lost)"
                )

                cur.execute(
                    "SELECT COUNT(*) FROM contents_raw WHERE hash_id = %s",
                    (result["hash_id"],),
                )
                assert cur.fetchone()[0] > 0, (
                    f"document {index} has no raw display text"
                )

                cur.execute(
                    "SELECT COUNT(*) FROM words_hashs WHERE hash_id = %s",
                    (result["hash_id"],),
                )
                words_hashs_rows = cur.fetchone()[0]
                assert words_hashs_rows > 0, (
                    f"document {index} is stored but not searchable "
                    f"(no words_hashs rows)"
                )

                cur.execute(
                    "SELECT COUNT(*) FROM titles_content WHERE hash_id = %s",
                    (result["hash_id"],),
                )
                assert cur.fetchone()[0] > 0, f"document {index} has no title row"

            # Every word of the shared vocabulary must exist exactly once.
            cur.execute(
                "SELECT word, COUNT(*) FROM words WHERE word = ANY(%s) GROUP BY word",
                (_SHARED_VOCAB,),
            )
            counts = dict(cur.fetchall())
            for word in _SHARED_VOCAB:
                assert counts.get(word) == 1, (
                    f"word {word!r} was inserted {counts.get(word)} times "
                    f"(duplicate dictionary rows)"
                )
    finally:
        conn.close()


def test_failed_document_does_not_leave_stale_rows(pg_db, tenant, monkeypatch):
    """A failure mid-transaction rolls the document back completely.

    This is the second reported root cause: errors swallowed inside the
    transaction let the caller continue with ids of rows that had already been
    rolled back, producing ``Hash ID N does not exist`` and foreign key
    violations for the *next* write.  Now the whole document must vanish, and a
    retry must store it cleanly.
    """
    from database.services.contents_db_service import ContentDBService

    payload = _document(999)
    service = ContentDBService()

    original_link = ContentDBService.link_words_to_content

    def failing_link(self, hash_id, content_ids):
        raise RuntimeError("injected failure while linking words")

    monkeypatch.setattr(ContentDBService, "link_words_to_content", failing_link)
    with pytest.raises(RuntimeError, match="injected failure"):
        service.process_full_document(
            hash_value=payload["hash_value"],
            source_id=tenant["source_id"],
            side_id=tenant["side_id"],
            file_name=payload["file_name"],
            file_path=payload["file_path"],
            file_size=1024,
            file_type="txt",
            file_status="Read",
            file_date=date.today(),
            content_words=payload["content_words"],
            attempts=1,
        )
    monkeypatch.setattr(ContentDBService, "link_words_to_content", original_link)

    conn = _pg_connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM paths WHERE file_name = %s",
                (payload["file_name"],),
            )
            assert cur.fetchone()[0] == 0, (
                "a rolled-back document left a paths row behind"
            )
            cur.execute(
                "SELECT COUNT(*) FROM hashs WHERE hash = %s",
                (payload["hash_value"],),
            )
            assert cur.fetchone()[0] == 0, (
                "a rolled-back document left a hashs row behind"
            )
    finally:
        conn.close()

    # Retry with a clean service: the same file must now be stored fully.
    retry_service = ContentDBService()
    result = retry_service.process_full_document(
        hash_value=payload["hash_value"],
        source_id=tenant["source_id"],
        side_id=tenant["side_id"],
        file_name=payload["file_name"],
        file_path=payload["file_path"],
        file_size=1024,
        file_type="txt",
        file_status="Read",
        file_date=date.today(),
        content_words=payload["content_words"],
        attempts=1,
    )
    assert result["success"], f"retry after rollback failed: {result}"


def test_batch_of_files_through_reader_is_fully_stored(pg_db, tenant, tmp_path):
    """End-to-end: a folder of files processed in parallel is fully stored.

    Mirrors the production scenario (a batch where some PDFs/text files failed
    with the tmp_words cascade while others succeeded).
    """
    file_count = 12
    for index in range(file_count):
        payload = _document(1000 + index, words=80)
        (tmp_path / payload["file_name"]).write_text(
            " ".join(payload["content_words"]), encoding="utf-8"
        )

    from pipeline.integrated_reader import IntegratedFileReader

    reader = IntegratedFileReader(
        max_workers=4,
        enable_storage=True,
        storage_source=tenant["source_name"],
        storage_side=tenant["side_name"],
    )
    results = reader.process_folder(str(tmp_path))
    assert results, "reader processed nothing"

    # The reader's own accounting must report a clean batch: no storage
    # failures, every file counted as stored.
    stats = reader.get_storage_statistics()
    assert stats.get("failed", 0) == 0, f"storage failures reported: {stats}"
    assert stats.get("completed", 0) == file_count, (
        f"reader stored {stats.get('completed')}/{file_count} files: {stats}"
    )

    conn = _pg_connect(pg_db)
    try:
        with conn.cursor() as cur:
            for index in range(file_count):
                name = _document(1000 + index, words=80)["file_name"]
                cur.execute(
                    "SELECT p.id,"
                    " (SELECT COUNT(*) FROM words_hashs wp WHERE wp.hash_id = (SELECT hc0.hash_id FROM hash_contexts hc0 WHERE hc0.id = p.context_id)),"
                    " (SELECT COUNT(*) FROM contents c WHERE c.hash_id ="
                    " (SELECT hc1.hash_id FROM hash_contexts hc1 WHERE hc1.id = p.context_id))"
                    " FROM paths p WHERE p.file_name = %s",
                    (name,),
                )
                row = cur.fetchone()
                assert row, f"{name} was not stored"
                assert row[1] > 0, f"{name} stored without word index"
                assert row[2] > 0, f"{name} stored without content"
    finally:
        conn.close()


def test_aborted_transaction_is_reported_not_silently_rolled_back(pg_db, tenant):
    """Category check: a failed statement must surface as an abort.

    The old ``get_cursor`` rolled the connection back whenever it found it in
    an error state and then carried on, so callers kept writing with ids of
    rows that no longer existed.  Now the abort is reported immediately, and
    the transaction owner performs the single rollback.
    """
    from database.exceptions import TransactionAbortedError
    from database.services.contents_db_service import ContentDBService

    service = ContentDBService()
    payload = _document(4242)
    hash_value = payload["hash_value"]

    with pytest.raises(TransactionAbortedError):
        with service.transaction():
            # A valid read (unknown occurrence id -> None) before the abort...
            service.resolve_hash_id(999_999_999)
            # First failure aborts the transaction...
            with pytest.raises(TransactionAbortedError):
                service.words_repo.execute("SELECT 1/0")
            # ...and every later statement says so instead of running on a
            # connection that was silently rolled back.
            service.words_repo.execute("SELECT 1")

    conn = _pg_connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM hashs WHERE hash = %s", (hash_value,))
            assert cur.fetchone()[0] == 0, "aborted transaction left rows committed"
    finally:
        conn.close()


def test_optional_step_failure_does_not_lose_the_document(pg_db, tenant, monkeypatch):
    """Derived data (raw display text) is contained in a savepoint.

    A failure while writing the display cache must not roll back a document
    that is otherwise complete - and the degradation must be visible
    (logged + reported in the result warnings), never silent.
    """
    from database.database.repository.contents_repo import ContentsRepository
    from database.services.contents_db_service import ContentDBService

    def failing_raw_store(self, hash_id, text, chunk_size=1024 * 1024):
        raise RuntimeError("injected raw-text failure")

    monkeypatch.setattr(ContentsRepository, "store_raw_content", failing_raw_store)

    service = ContentDBService()
    payload = _document(555)
    result = service.process_full_document(
        hash_value=payload["hash_value"],
        source_id=tenant["source_id"],
        side_id=tenant["side_id"],
        file_name=payload["file_name"],
        file_path=payload["file_path"],
        file_size=1024,
        file_type="txt",
        file_status="Read",
        file_date=date.today(),
        content_words=payload["content_words"],
        raw_text=payload["raw_text"],
        attempts=1,
    )

    assert result["success"], f"optional step failure lost the document: {result}"
    assert any("raw_text" in warning for warning in result["warnings"]), result["warnings"]

    conn = _pg_connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT (SELECT COUNT(*) FROM contents WHERE hash_id ="
                " (SELECT hc0.hash_id FROM hash_contexts hc0 WHERE hc0.id = p.context_id)),"
                " (SELECT COUNT(*) FROM words_hashs WHERE hash_id ="
                " (SELECT hc0.hash_id FROM hash_contexts hc0 WHERE hc0.id = p.context_id))"
                " FROM paths p WHERE p.file_name = %s",
                (payload["file_name"],),
            )
            row = cur.fetchone()
            assert row, "document missing after contained failure"
            assert row[0] > 0 and row[1] > 0, (
                "core data of the document was lost with the optional step"
            )
    finally:
        conn.close()


def test_reingesting_the_same_file_reports_the_existing_document(pg_db, tenant):
    """Dedup contract of ``process_full_document`` after the rewrite.

    Storing the same hash twice must resolve to the existing document (same
    path_id, no second row), and the duplicate must be reported so the caller
    can count it as "already processed" instead of storing a copy.
    """
    from database.services.contents_db_service import ContentDBService

    service = ContentDBService()
    payload = _document(777)

    def store():
        return service.process_full_document(
            hash_value=payload["hash_value"],
            source_id=tenant["source_id"],
            side_id=tenant["side_id"],
            file_name=payload["file_name"],
            file_path=payload["file_path"],
            file_size=1024,
            file_type="txt",
            file_status="Read",
            file_date=date.today(),
            content_words=payload["content_words"],
            attempts=1,
        )

    first = store()
    assert first["success"] and first["path_id"]

    second = store()
    assert second["success"], second
    assert "Duplicate" in (second["error"] or ""), second
    assert second["path_id"] == first["path_id"], (first, second)

    conn = _pg_connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM paths WHERE file_name = %s",
                (payload["file_name"],),
            )
            assert cur.fetchone()[0] == 1, "re-ingest created a second path row"
            cur.execute(
                "SELECT COUNT(*) FROM hashs WHERE hash = %s",
                (payload["hash_value"],),
            )
            assert cur.fetchone()[0] == 1, "re-ingest created a second hash row"
    finally:
        conn.close()


def test_summary_tells_content_copies_from_already_recorded_files(pg_db, tenant, tmp_path,
                                                                  capsys):
    """Field report (Windows run after step 13): three files with content that
    was already stored were saved as new occurrences, yet the run summary said
    "Duplicate files: 0". Two different facts, both now counted and labelled."""
    import uuid

    from pipeline.integrated_reader import IntegratedFileReader, storage_summary_lines

    unique = uuid.uuid4().hex
    shared = f"shared content {unique} " * 20
    (tmp_path / "a.txt").write_text(shared, encoding="utf-8")
    (tmp_path / "b_copy_of_a.txt").write_text(shared, encoding="utf-8")
    (tmp_path / "c.txt").write_text(f"different content {unique} " * 20, encoding="utf-8")

    def run():
        reader = IntegratedFileReader(max_workers=1, enable_storage=True,
                                      storage_source=tenant["source_name"],
                                      storage_side=tenant["side_name"])
        reader.process_folder(str(tmp_path))
        return reader.get_storage_statistics()

    first = run()
    assert first["failed"] == 0
    assert first["completed"] == 3
    assert first["content_reused"] == 1, first
    assert first["duplicates"] == 0, first
    lines = storage_summary_lines(first)
    assert "   Files stored in database: 3" in lines
    assert "      of which new copies of content already stored: 1" in lines
    assert "   Files already recorded (nothing new written): 0" in lines
    assert "Files stored in database: 3" in capsys.readouterr().out

    second = run()  # the very same files again: every occurrence is recorded
    assert second["duplicates"] == 3, second
    assert second["completed"] == 0 and second["content_reused"] == 0, second
