"""Integration: tokens too large for PostgreSQL's btree index must not
kill the ingest.

Root cause (observed with a real PST ingest):

    psycopg2.errors.ProgramLimitExceeded: index row size 3544 exceeds
    btree version 4 maximum 2704 for index "words_word_key"

``words.word`` is ``TEXT UNIQUE`` -> btree index.  E-mail bodies embed
multi-kilobyte unbroken base64 / ``data:`` URI runs; the tokenizer
emits such a run as ONE giant "word"; a single oversized token aborted
the whole ``INSERT INTO words SELECT ... FROM tmp_words`` export query,
rolled back the entire document transaction and lost the complete file
(11,441 e-mail messages in the reported case).

Expected behaviour after the fix:
* the document stores successfully (normal words indexed + searchable);
* no token above the safety limit ever reaches the words dictionary;
* the verbatim blob is still preserved in the raw display store, so
  viewing and in-document search lose nothing.
"""

import os
import random
import sys
import uuid
from datetime import date
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]
_MARKER = f"PLOVERNEST{_UNIQUE}"

# ~4.7 KB of unbroken "word" characters: a single tokenizer token that
# is INCOMPRESSIBLE (pglz cannot shrink random data, exactly like the
# real payload that killed the PST ingest), so the index row stays far
# beyond the 2704-byte btree maximum.  The base64url alphabet (A-Z a-z
# 0-9 - _) matters twice over: every character survives the tokenizer's
# word pattern as one token, and the entropy defeats pglz compression.
# (Classic base64 contains '+' and '/', which the tokenizer treats as
# separators; repetitive fillers get compressed below the limit by
# PostgreSQL and the insert then succeeds.)
_B64URL_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


def _incompressible_token(total_chars: int) -> str:
    rng = random.Random(0xF11EA5A1)
    return "".join(rng.choice(_B64URL_ALPHABET) for _ in range(total_chars))


_BLOB = _incompressible_token(4700)
assert len(_BLOB.encode("utf-8")) > 2704


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("oversize_corpus")
    (root / f"blob-{_UNIQUE}.txt").write_text(
        f"lead word {_MARKER} before payload\n"
        f"{_BLOB}\n"
        "trail word after payload\n",
        encoding="utf-8",
    )

    # E-mail variant - the shape of the reported PST failure: a message
    # whose body embeds one multi-kilobyte unbroken token (inline base64
    # data URI / tracking payload).
    (root / f"mail-{_UNIQUE}.eml").write_text(
        f"From: Sender <sender-{_UNIQUE}@example.org>\n"
        f"To: analyst <analyst-{_UNIQUE}@example.org>\n"
        f"Subject: {_MARKER} inline payload\n"
        "Date: Mon, 14 Sep 2026 10:00:00 +0000\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        f"Body before {_MARKER} payload follows.\n"
        f"data:image/png;base64,{_BLOB}\n"
        "Body after payload.\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture(scope="module")
def ingested(pg_db, corpus):
    tag = os.getpid()
    source_name, side_name = (
        f"_ovrsz01_src_{tag}_{_UNIQUE}",
        f"_ovrsz01_side_{tag}",
    )

    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    today = date.today()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5, %s)"
            " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name",
            (side_name, today),
        )
        cur.execute(
            "INSERT INTO sources (name, job, importance, country, date_creation)"
            " VALUES (%s, 'test', 0.5, 'test', %s) ON CONFLICT (name) DO NOTHING",
            (source_name, today),
        )
    conn.commit()
    conn.close()

    from pipeline.integrated_reader import IntegratedFileReader

    reader = IntegratedFileReader(
        max_workers=2, enable_storage=True,
        storage_source=source_name, storage_side=side_name,
    )
    results = reader.process_folder(str(corpus))
    assert results, "nothing was processed"

    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            path_ids = {}
            statuses = {}
            for suffix, pattern in (
                ("txt", f"blob-{_UNIQUE}.txt"),
                ("eml", f"mail-{_UNIQUE}.eml"),
            ):
                cur.execute(
                    "SELECT id, file_status FROM paths WHERE file_name LIKE %s"
                    " ORDER BY id DESC LIMIT 1",
                    (pattern,),
                )
                row = cur.fetchone()
                # Before the fix these rows never existed: the oversized
                # token aborted the whole document transaction
                # (ProgramLimitExceeded).
                assert row, f"document was not stored - oversized token killed the ingest ({suffix})"
                path_ids[suffix] = row[0]
                statuses[suffix] = row[1]
    finally:
        conn.close()

    return {
        "path_id": path_ids["txt"],
        "eml_path_id": path_ids["eml"],
        "file_status": statuses["txt"],
        "eml_file_status": statuses["eml"],
    }


# ---------------------------------------------------------------------------
# 1. The ingest itself survives the oversized token
# ---------------------------------------------------------------------------

def test_document_stored_as_read(ingested):
    assert ingested["file_status"] == "Read", (
        f"file_status = {ingested['file_status']!r}"
    )


def test_no_oversized_token_reaches_the_dictionary(pg_db, ingested):
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            # Hard invariant: PostgreSQL could not index anything above
            # ~2704 bytes anyway; our cap keeps the dictionary clean.
            cur.execute("SELECT COUNT(*) FROM words WHERE octet_length(word) > 2000")
            assert cur.fetchone()[0] == 0, "oversized word reached the words table"

            # The blob itself must not be stored (tokenizer lowercases).
            cur.execute("SELECT id FROM words WHERE word = %s", (_BLOB.lower(),))
            assert cur.fetchone() is None, "giant blob stored as a word"

            # Nor any truncation/prefix of it.
            cur.execute(
                "SELECT id FROM words WHERE word LIKE %s",
                (_BLOB[:60].lower() + "%",),
            )
            assert cur.fetchone() is None, "blob prefix stored as a word"
    finally:
        conn.close()


def test_normal_words_of_the_same_document_are_stored(pg_db, ingested):
    """The blob must not take the rest of the document down with it."""
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM words WHERE word = %s", (_MARKER.lower(),))
            word_row = cur.fetchone()
            assert word_row, "marker word missing from dictionary"

            cur.execute(
                "SELECT wp.word_count FROM words_hashs wp"
                " JOIN words w ON w.id = wp.word_id"
                " WHERE wp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s) AND w.word = %s",
                (ingested["path_id"], _MARKER.lower()),
            )
            linked = cur.fetchone()
            assert linked, "marker word not linked to the stored path"
    finally:
        conn.close()


def test_search_finds_the_document_again(pg_db, ingested):
    from Api.services.search_service import SearchService

    results, _ = SearchService.full_text_search(query=_MARKER, limit=20)
    hit = next((r for r in results if r["id"] == ingested["path_id"]), None)
    assert hit, "stored document not found by search"


def test_raw_display_text_preserves_the_blob_verbatim(ingested):
    """Index safety must not cost display fidelity."""
    from Api.utils.utils import load_text_content

    text = load_text_content(ingested["path_id"])
    assert text, "no display content stored"
    assert _MARKER in text
    assert _BLOB in text, "verbatim blob missing from raw display text"


def test_email_with_inline_payload_stores_and_searches(pg_db, ingested):
    """The reported PST shape: an e-mail body embedding one multi-KB
    unbroken token must store, index and search like any other message."""
    assert ingested["eml_file_status"] == "Read"

    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT wp.word_count FROM words_hashs wp"
                " JOIN words w ON w.id = wp.word_id"
                " WHERE wp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s) AND w.word = %s",
                (ingested["eml_path_id"], _MARKER.lower()),
            )
            assert cur.fetchone(), "marker word not linked to the e-mail path"
    finally:
        conn.close()

    from Api.services.search_service import SearchService

    results, _ = SearchService.full_text_search(query=_MARKER, limit=20)
    hit = next(
        (r for r in results if r["id"] == ingested["eml_path_id"]), None
    )
    assert hit, "e-mail with oversized inline payload not searchable"

    from Api.utils.utils import load_text_content

    text = load_text_content(ingested["eml_path_id"])
    assert _BLOB in text, "inline payload missing from e-mail display text"
