"""Seed helpers for integration tests that need a small corpus in the real schema.

Rows are inserted with plain SQL through the same tables ingestion writes
(sources, sides, hashs, hash_contexts, paths, contents_raw, contents), so the
tests exercise the real joins rather than a mock. Every label is unique per
call so tests sharing one session database never collide.
"""

import datetime
import hashlib
import itertools

import psycopg2

_SEQ = itertools.count()


def connect(pg_db):
    return psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                            password=pg_db["password"], dbname=pg_db["database"])


def source(cur, name=None, importance=0.5):
    name = name or f"src_{next(_SEQ)}"
    cur.execute(
        "INSERT INTO sources (name, job, importance, country, date_creation)"
        " VALUES (%s, 't', %s, 't', CURRENT_DATE) ON CONFLICT (name)"
        " DO UPDATE SET name = EXCLUDED.name RETURNING id", (name, importance))
    return cur.fetchone()[0]


def side(cur, name=None, importance=0.5):
    name = name or f"side_{next(_SEQ)}"
    cur.execute(
        "INSERT INTO sides (name, importance, date_creation) VALUES (%s, %s, CURRENT_DATE)"
        " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (name, importance))
    return cur.fetchone()[0]


def document(cur, *, source_id, side_id, text, file_name=None, file_type="txt",
             file_date=None, content_date=None, hash_id=None, file_status="Unread"):
    """One path occurrence. Pass ``hash_id`` to add another context/occurrence
    of existing content. Returns ``(path_id, hash_id, context_id)``."""
    n = next(_SEQ)
    if hash_id is None:
        digest = hashlib.sha256(f"{text}:{n}".encode()).hexdigest()
        cur.execute("INSERT INTO hashs (hash) VALUES (%s) RETURNING id", (digest,))
        hash_id = cur.fetchone()[0]
        cur.execute("INSERT INTO contents_raw (hash_id, chunk_seq, content, char_count)"
                    " VALUES (%s, 0, %s, %s)", (hash_id, text, len(text)))
        cur.execute("INSERT INTO contents (content_data, content_date, hash_id)"
                    " VALUES (NULL, %s, %s)", (content_date, hash_id))
    cur.execute(
        "INSERT INTO hash_contexts (hash_id, source_id, side_id) VALUES (%s, %s, %s)"
        " ON CONFLICT (hash_id, source_id, side_id) DO UPDATE SET hash_id = EXCLUDED.hash_id"
        " RETURNING id", (hash_id, source_id, side_id))
    context_id = cur.fetchone()[0]
    file_date = file_date or datetime.date(2026, 1, 1)
    cur.execute(
        "INSERT INTO paths (file_name, file_path, file_size, file_type, file_status,"
        " file_date, date_creation, context_id, processing_status)"
        " VALUES (%s, %s, %s, %s, %s, %s, CURRENT_DATE, %s, 'processed') RETURNING id",
        (file_name or f"doc_{n}.{file_type}", f"/seed/doc_{n}.{file_type}", len(text),
         file_type, file_status, file_date, context_id))
    return cur.fetchone()[0], hash_id, context_id


def word_counts(cur, hash_id, counts):
    """Word frequencies of one content, as ingestion stores them
    (``words`` + ``words_hashs``). ``counts`` maps word -> count; a count of
    ``None`` stores a row whose count is unknown (word_count NULL)."""
    for word, count in counts.items():
        cur.execute("INSERT INTO words (word) VALUES (%s) ON CONFLICT (word)"
                    " DO UPDATE SET word = EXCLUDED.word RETURNING id", (word,))
        word_id = cur.fetchone()[0]
        cur.execute("INSERT INTO words_hashs (hash_id, word_id, word_count, position_indexer)"
                    " VALUES (%s, %s, %s, ''::bytea)", (hash_id, word_id, count))


class SeedLedger:
    """Ids of the rows one test created, so it can remove exactly those rows.

    The session database is shared by every integration module, so a test that
    leaves rows behind changes what other tests see (a report's first page, a
    scan's window). Record every id that a seed call returns, then call
    ``remove_seeded`` from a fixture finaliser."""

    def __init__(self):
        self.sources = []
        self.sides = []
        self.words = []
        self.categories = []
        self.keywords = []
        self.paths = []
        self.contexts = []
        self.hashes = []


def remove_seeded(pg_db, ledger):
    """Delete what ``ledger`` recorded, children first. Ids that were already
    removed by the test are simply absent and do nothing."""
    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM keywords_hashs WHERE keyword_id = ANY(%s)",
                        (ledger.keywords,))
            cur.execute("DELETE FROM keywords WHERE id = ANY(%s)", (ledger.keywords,))
            cur.execute("DELETE FROM categorys WHERE id = ANY(%s)", (ledger.categories,))
            cur.execute("DELETE FROM words WHERE id = ANY(%s)", (ledger.words,))
            cur.execute("DELETE FROM paths WHERE id = ANY(%s)", (ledger.paths,))
            cur.execute("DELETE FROM hash_contexts WHERE id = ANY(%s)", (ledger.contexts,))
            cur.execute("DELETE FROM contents WHERE hash_id = ANY(%s)", (ledger.hashes,))
            cur.execute("DELETE FROM contents_raw WHERE hash_id = ANY(%s)", (ledger.hashes,))
            cur.execute("DELETE FROM hashs WHERE id = ANY(%s)", (ledger.hashes,))
            cur.execute("DELETE FROM sides WHERE id = ANY(%s)", (ledger.sides,))
            cur.execute("DELETE FROM sources WHERE id = ANY(%s)", (ledger.sources,))
    finally:
        conn.close()
