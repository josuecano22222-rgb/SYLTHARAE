"""Database test: archive/child title lineage (PARENT-02) - rewritten for the
frozen schema.

BEFORE (this file, on the pre-freeze schema)
--------------------------------------------
The tests asserted a self-referencing parent column:

    def test_parent_linkage_is_persisted(...)
        parent_id = insert_title(conn, [1, 2, 3], archive_hash_id, "Main")
        child_id  = insert_title(conn, [4, 5], child_hash_id, "Branch", parent_id)
        ...
        assert row[2] == parent_id                 # title_content_id

    def test_linkage_is_retrievable_from_the_child(...)
        SELECT ... FROM titles_content c
          JOIN titles_content parent ON parent.id = c.title_content_id
        assert row[1] == "container.zip"

    def test_foreign_key_still_enforced(...)
        with pytest.raises(...):
            insert_title(conn, [9], child_hash_id, "Branch", parent=999_999)

and ``read_title`` selected ``title_content_id``.

AFTER (frozen schema, database/migrations - which may not change)
------------------------------------------------------------------
``CREATE TABLE titles_content`` in m0001_baseline.py is

    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title_data BYTEA,
    title_status VARCHAR(10) NOT NULL CHECK (...) DEFAULT 'Main',
    hash_id INTEGER NOT NULL REFERENCES hashs(id)

- four columns, no parent. There is nothing lineage can be stored in, so the
column-based assertions cannot be kept by any honest means; keeping them would
mean changing the schema, which the freeze forbids. The production code was
already aligned: ``TitlesContentRepository.insert_titles_content`` documents
that the parameter is *gone rather than silently ignored*, and
``ContentsDBService.create_title_content`` no longer accepts one.

What this file asserts instead - every behaviour the frozen schema supports,
with the original defect (a supplied parent being **silently discarded**) still
covered, now in the other direction:

1. the table has exactly the frozen columns and no ``title_content_id``
   (drift guard - this fails if anyone adds the column without a decision);
2. a title is stored and read back with its status, hash and payload;
3. supplying a parent is a loud ``TypeError`` and writes nothing (a silent drop
   is the defect; a refusal is not);
4. omitting the parent still works and yields the ``Main`` default;
5. ``Branch`` is not storable at all - the frozen CHECK admits only ``'Main'`` -
   so the status that used to mark a child is checked as rejected;
6. the remaining foreign key (``hash_id -> hashs``) is still enforced.

Nothing here is looser than before: every old assertion either survives as an
equivalent on the frozen schema or is replaced by a stricter one (a silent
drop became a refusal, which is checked for the absence of a written row).
"""

import datetime
import hashlib
import itertools
import os
import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_SEQ = itertools.count()

#: The frozen column set of ``titles_content`` (m0001_baseline.py).
FROZEN_COLUMNS = {"id", "title_data", "title_status", "hash_id"}


def unique_hash(label: str) -> str:
    """A distinct 64-char digest per invocation.

    hashs is UNIQUE (hash, source_id, side_id), so reusing a fixed value makes
    the second test in a session collide with the first.
    """
    return hashlib.sha256(f"{label}:{os.getpid()}:{next(_SEQ)}".encode()).hexdigest()


@pytest.fixture
def conn(pg_db):
    c = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    yield c
    c.close()


@pytest.fixture
def two_paths(conn):
    """Two paths rows to stand in for an archive and its extracted child."""
    today = datetime.date.today()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5, %s)"
            " RETURNING id",
            ("test-side-" + unique_hash("side")[:8], today),
        )
        side_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO sources (name, job, importance, country, date_creation)"
            " VALUES (%s, %s, 1.0, %s, %s) RETURNING id",
            ("test-source-" + unique_hash("source")[:8], "test", "NL", today),
        )
        source_id = cur.fetchone()[0]

        pairs = []
        for name in ("container.zip", "child.pdf"):
            digest = unique_hash(name)
            cur.execute(
                "INSERT INTO hashs (hash) VALUES (%s) RETURNING id", (digest,),
            )
            hash_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                " VALUES (%s, %s, %s) RETURNING id",
                (hash_id, source_id, side_id),
            )
            context_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO paths (file_name, file_path, file_size, file_type,"
                " file_date, date_creation, context_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (name, "/tmp/" + name, 1024, "Zip" if name.endswith("zip") else "PDF",
                 today, today, context_id),
            )
            pairs.append((cur.fetchone()[0], hash_id))
    conn.commit()
    return pairs


def _insert(conn, word_ids, hash_id, title_status="Main"):
    """Insert a title the way production code does - without a parent."""
    from database.database.repository.titles_content_repo import TitlesContentRepository

    # BaseRepository(db, connection=None): passing the connection puts the repo
    # in transaction context so it uses our connection rather than opening one.
    # Titles belong to canonical content (hash_id) since migration m0011.
    repo = TitlesContentRepository(None, connection=conn)
    return repo.insert_titles_content(word_ids, hash_id, title_status)


def read_title(conn, title_id):
    """The frozen row shape: no title_content_id to read."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title_status, hash_id, title_data"
            " FROM titles_content WHERE id = %s",
            (title_id,),
        )
        return cur.fetchone()


def test_titles_content_has_exactly_the_frozen_columns(conn):
    """The lineage column cannot exist in the frozen schema - assert it.

    If a parent column ever appears here it must arrive with a migration
    decision, not by accident; this is the guard for that.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = 'titles_content'"
        )
        columns = {row[0] for row in cur.fetchall()}
    assert columns == FROZEN_COLUMNS, columns
    assert "title_content_id" not in columns


def test_a_title_is_stored_with_its_status_hash_and_payload(conn, two_paths):
    """The insert path still writes a complete row."""
    (archive_path_id, archive_hash_id), _ = two_paths
    title_id = _insert(conn, [1, 2, 3], archive_hash_id, "Main")
    conn.commit()

    row = read_title(conn, title_id)
    assert row is not None
    assert row[1] == "Main"
    assert row[2] == archive_hash_id
    assert row[3], "title_data is empty: the word ids were not packed"


def test_only_the_main_title_status_is_accepted(conn, two_paths):
    """``Branch`` is not storable: the frozen CHECK admits exactly one status.

    m0001_baseline.py sets ``_TITLE_STATUSES = "'Main'"``, so
    ``title_status VARCHAR(10) ... CHECK (title_status IN ('Main'))``. Before
    the freeze the test wrote ``'Branch'`` and read it back; that is impossible
    here, and it is the same reason lineage has nowhere to live. Assert the
    constraint rather than dropping the concept silently.
    """
    from database.exceptions import QueryError

    (archive_path_id, archive_hash_id), (child_path_id, child_hash_id) = two_paths
    title_id = _insert(conn, [1], archive_hash_id, "Main")
    conn.commit()
    assert read_title(conn, title_id)[1] == "Main"

    for rejected in ("Branch", "NotAStatus"):
        with pytest.raises((psycopg2.Error, QueryError)):
            _insert(conn, [2], child_hash_id, rejected)
        conn.rollback()

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM titles_content WHERE hash_id = %s",
                    (child_hash_id,))
        assert cur.fetchone()[0] == 0


def test_supplying_a_parent_is_refused_loudly_and_writes_nothing(conn, two_paths):
    """The original defect was a *silent* drop; a refusal is the fix.

    Before, ``insert_titles_content(ids, hash, status, parent)`` accepted the
    parent and threw it away. The parameter is gone; a caller that still passes
    it gets a TypeError, and no row is written for that hash.
    """
    (archive_path_id, archive_hash_id), (child_path_id, child_hash_id) = two_paths
    from database.database.repository.titles_content_repo import TitlesContentRepository

    repo = TitlesContentRepository(None, connection=conn)
    with pytest.raises(TypeError):
        repo.insert_titles_content([4, 5], child_hash_id, "Branch", 12345)
    conn.rollback()

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM titles_content WHERE hash_id = %s",
                    (child_hash_id,))
        assert cur.fetchone()[0] == 0, "a refused call still wrote a title row"


def test_omitting_a_parent_still_works(conn, two_paths):
    """Backwards compatibility: existing callers pass no parent."""
    (_archive_path_id, archive_hash_id), _ = two_paths
    title_id = _insert(conn, [7, 8], archive_hash_id)
    conn.commit()

    row = read_title(conn, title_id)
    assert row[1] == "Main"


def test_the_hash_foreign_key_is_still_enforced(conn, two_paths):
    """The remaining foreign key (hash_id -> hashs) must still be enforced."""
    from database.exceptions import QueryError

    _archive, (_child_path_id, _child_hash_id) = two_paths
    # The repository wraps driver errors, so accept either the raw psycopg2
    # error or the project's QueryError - what matters is that it propagates.
    with pytest.raises((psycopg2.Error, QueryError)):
        _insert(conn, [9], 999_999, "Branch")
    conn.rollback()


