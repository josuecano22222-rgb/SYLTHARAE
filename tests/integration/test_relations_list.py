"""Integration: the "Relations" list shows the relations the count promises.

Verified defect. ``GET /api/archives/hashs`` (the Relations section) reported
``total_estimated`` from a COUNT over the duplicate-content set, but built its
page by walking ``hashs`` in id order through the generic cursor paginator and
dropping every non-duplicate AFTER the fetch:

    fetch_limit = max(limit * 3, 50)
    result = paginator.get_page(cursor=cursor, limit=fetch_limit, ...)
    for row in result['data']:
        if file_count > 1 or hash_variants > 0:
            hashs.append(row)

On any archive whose first rows are ordinary (non-duplicate) hashes - the
normal case: duplicates are created when the *same* content is imported again,
so they sit at higher ids - the page came back empty while the count was
non-zero. The sidebar said "12 items"; the section opened on "No items found
in this section". When a page did find relations, ``next_cursor`` was taken
from the filtered list, so paging skipped or repeated relations.

The fix moves the duplicate filter into the SQL that pages AND counts
(``Api.utils.utils.relation_duplicates_sql``) and pages with a keyset cursor
that carries its own sort key. This test seeds the exact shape that broke -
60 ordinary hashes first, the relations behind them - and walks the section.

Proven here end to end against a real database:

1. The first page is NOT empty: every relation is listed, none of the ordinary
   hashes leak in.
2. ``total_estimated`` equals the sidebar count (``archive_statistics``), so
   the number the user sees and the list they open agree.
3. Paging with next_cursor visits every relation exactly once - no gap, no
   repeat - for the default ordering (file count, high to low), for name
   ordering and for id ordering.
4. Search narrows the list and its count together.

A "relation" is one canonical content row (``hashs`` is unique per hash
since migration m0011) that ties more than one artifact together: several
contexts hold the content (the same content in different source/side
collections - previously each of those was its own ``hashs`` row, now they are
``hash_contexts`` rows on one content) and/or several occurrences (stored
paths) exist across those contexts.

The database is shared with the rest of the suite, and other modules seed
duplicate content of their own. Every seeded hash value therefore carries this
module's unique tag (``hashs.hash`` is CHAR(64), so the tag fits in the value)
and every listing assertion scopes the section with ``search=<tag>``: the test
then measures its own rows only, in any suite order, while still exercising the
same SQL, cursor and count the user's page uses.
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

#: Ordinary, non-duplicate hashes seeded BEFORE the relations. 60 of them
#: exceed the old ``max(limit * 3, 50)`` fetch window, which is what made the
#: first page come back empty.
_ORDINARY = 60


def tagged_hash(tag: str, label: str) -> str:
    """A distinct 64-char digest that CONTAINS ``tag``.

    ``hashs.hash`` is CHAR(64), so the tag has to fit inside the value - and
    putting it there is what makes ``search=<tag>`` select exactly this
    module's rows.
    """
    digest = hashlib.sha256(
        f"{label}:{os.getpid()}:{next(_SEQ)}".encode()).hexdigest()
    return f"{tag}-{label}-{digest}"[:64]


@pytest.fixture(scope="module")
def archive(pg_db):
    """An archive whose relations sit behind 60 ordinary hashes.

    Returns ``hash row id -> expected file_count`` for every relation, so the
    assertions do not have to re-derive who is supposed to be listed.
    """
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    today = datetime.date.today()
    tag = f"_rel_{os.getpid()}_{next(_SEQ)}"
    try:
        with conn.cursor() as cur:
            def add_side(name):
                cur.execute(
                    "INSERT INTO sides (name, importance, date_creation)"
                    " VALUES (%s, 0.5, %s) ON CONFLICT (name)"
                    " DO UPDATE SET name = EXCLUDED.name RETURNING id",
                    (name, today),
                )
                return cur.fetchone()[0]

            side_id = add_side(f"{tag}_side")
            side2_id = add_side(f"{tag}_side2")
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES (%s, 't', 0.5, 't', %s) ON CONFLICT (name)"
                " DO UPDATE SET name = EXCLUDED.name RETURNING id",
                (f"{tag}_src", today),
            )
            source_id = cur.fetchone()[0]

            def add_hash(value, side, source, copies=1):
                """One canonical content row + one context + ``copies`` paths.

                Identity-model seeding: ``hashs`` is UNIQUE per hash, so the
                same content in another source/side is a second
                ``hash_contexts`` row on the SAME content row (the
                ``ON CONFLICT`` keeps the canonical row single); each context
                holds its own occurrences (``paths``).
                """
                cur.execute(
                    "INSERT INTO hashs (hash) VALUES (%s)"
                    " ON CONFLICT (hash) DO NOTHING RETURNING id",
                    (value,),
                )
                row = cur.fetchone()
                hash_id = row[0] if row else None
                if hash_id is None:
                    cur.execute("SELECT id FROM hashs WHERE hash = %s", (value,))
                    hash_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                    " VALUES (%s, %s, %s)"
                    " ON CONFLICT (hash_id, source_id, side_id) DO NOTHING"
                    " RETURNING id",
                    (hash_id, source, side),
                )
                context_id = cur.fetchone()[0]
                for copy in range(copies):
                    cur.execute(
                        "INSERT INTO paths (file_name, file_path, file_size,"
                        " file_type, file_status, file_date, date_creation,"
                        " context_id) VALUES (%s, %s, 10, 'FILE', 'Read',"
                        " %s, %s, %s) RETURNING id",
                        (f"{tag}_{hash_id}_{copy}.txt",
                         f"/tmp/{tag}_{hash_id}_{copy}.txt",
                         today, today, context_id),
                    )
                return hash_id

            # Ordinary, single-occurrence content first: they occupy the low
            # ids and are NOT relations.
            for i in range(_ORDINARY):
                add_hash(tagged_hash(tag, f"ordinary-{i}"), side_id, source_id)

            # Relations behind them: canonical content with more than one
            # context or more than one occurrence.
            expected = {
                add_hash(tagged_hash(tag, "relation-three-copies"),
                         side_id, source_id, copies=3): 3,
                add_hash(tagged_hash(tag, "relation-two-copies"),
                         side_id, source_id, copies=2): 2,
                add_hash(tagged_hash(tag, "relation-other-two"),
                         side_id, source_id, copies=2): 2,
            }

            # Same content imported from another side: ONE canonical content
            # row, TWO contexts (one path each) -> two occurrences. The old
            # model stored this as two hash rows; the identity model stores it
            # as one content row the section must still report.
            cross_side = tagged_hash(tag, "relation-cross-side")
            first_cross = add_hash(cross_side, side_id, source_id)
            second_cross = add_hash(cross_side, side2_id, source_id)
            assert first_cross == second_cross, (
                "hashs is UNIQUE per hash: both contexts share one content row"
            )
            expected[first_cross] = 2
    finally:
        conn.commit()
        conn.close()

    return {"expected": expected, "tag": tag}


def _fetch_page(admin_client, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    resp = admin_client.get(f"/api/archives/hashs?{query}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True
    return body


def _walk(admin_client, limit, **params):
    """Every row the section lists, following next_cursor to the end."""
    rows, cursors, guard, cursor = [], [], 0, None
    while True:
        body = _fetch_page(admin_client, limit=limit, cursor=cursor, **params)
        rows.extend(body["data"])
        if not body["has_next"]:
            assert body["next_cursor"] in (None, ""), body["next_cursor"]
            return rows, body["total_estimated"]
        assert body["next_cursor"], "has_next without a cursor: paging would stall"
        assert body["next_cursor"] not in cursors, "cursor repeated: infinite paging"
        cursors.append(body["next_cursor"])
        cursor = body["next_cursor"]
        guard += 1
        assert guard < 50, "paging did not terminate"


def _listed_ids(rows):
    return {row["id"] for row in rows}


@pytest.mark.parametrize("sort_dir", ["asc", "desc"])
def test_relations_page_is_not_empty_when_count_is_not_zero(
        admin_client, archive, sort_dir):
    """The reported bug: a non-zero count opened onto an empty list.

    Both directions matter. The old code fetched a window of ``hashs`` in id
    order and filtered afterwards, so the relations only ever appeared when
    the window happened to contain them: ascending (the ids the relations were
    given last) came back empty while the count was correct, and descending
    only looked right by accident of which rows the window covered.
    """
    expected = archive["expected"]
    # limit=10 with 60 ordinary hashes in front: the old fetch window
    # (max(limit * 3, 50) rows in id order) could not reach the relations.
    body = _fetch_page(admin_client, limit=10, sort_by="file_count",
                       sort_dir=sort_dir, search=archive["tag"])

    assert body["total_estimated"] == len(expected), body
    assert body["data"], (
        "Relations reported a non-zero total but listed nothing - the empty "
        "list the bug produced"
    )
    assert len(body["data"]) == len(expected)
    assert _listed_ids(body["data"]) == set(expected)
    for row in body["data"]:
        assert row["file_count"] == expected[row["id"]], row


def test_relations_count_matches_sidebar_count(admin_client, archive):
    """The sidebar count and the list total describe the same set."""
    from Api.utils import get_archive_statistics
    from Api.utils.utils import count_relation_duplicates

    expected = archive["expected"]
    body = _fetch_page(admin_client, limit=10, search=archive["tag"])

    # The page's total and the count behind the sidebar are one query
    # (relation_duplicates_sql): for the same predicate they agree, and the
    # unfiltered sidebar number is that same count.
    assert body["total_estimated"] == len(expected), body
    assert count_relation_duplicates(archive["tag"]) == len(expected)
    assert get_archive_statistics()["total_hashes"] == count_relation_duplicates()


def test_relations_paging_visits_every_relation_exactly_once(admin_client, archive):
    """Keyset paging over the FILTERED set: no gaps, no repeats."""
    expected = archive["expected"]

    rows, total = _walk(admin_client, 2, sort_by="file_count", sort_dir="desc",
                        search=archive["tag"])
    assert total == len(expected)
    assert len(rows) == len(expected), rows
    assert len(_listed_ids(rows)) == len(expected), "a relation was listed twice"
    assert _listed_ids(rows) == set(expected)

    # Ordering is server-side: file count descending.
    counts = [row["file_count"] for row in rows]
    assert counts == sorted(counts, reverse=True), counts


def test_relations_paging_by_name(admin_client, archive):
    """Name ordering pages just as cleanly (its cursor is a different shape)."""
    expected = archive["expected"]
    rows, total = _walk(admin_client, 1, sort_by="name", sort_dir="asc",
                        search=archive["tag"])

    assert total == len(expected)
    assert _listed_ids(rows) == set(expected)
    names = [row["name"] for row in rows]
    assert names == sorted(names), names


def test_relations_paging_by_id_descending(admin_client, archive):
    """Id ordering (legacy integer cursors) still walks the whole set."""
    expected = archive["expected"]
    rows, total = _walk(admin_client, 3, sort_by="id", sort_dir="desc",
                        search=archive["tag"])

    assert total == len(expected)
    assert _listed_ids(rows) == set(expected)
    ids = [row["id"] for row in rows]
    assert ids == sorted(ids, reverse=True), ids


def test_relations_search_narrows_list_and_count(admin_client, archive):
    """Search filters the page and the total through the same predicate."""
    expected = archive["expected"]
    with psycopg2.connect(
        host=os.environ.get("DB_HOST"), port=int(os.environ.get("DB_PORT", 5432)),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        dbname=os.environ.get("DB_NAME"),
    ) as probe:
        with probe.cursor() as cur:
            cur.execute("SELECT hash FROM hashs WHERE id = %s",
                        (max(expected),))
            target = cur.fetchone()[0].strip()

    body = _fetch_page(admin_client, limit=10, search=target)
    listed = [row["id"] for row in body["data"]]
    assert body["total_estimated"] == len(listed), (body["total_estimated"], listed)
    # Under the identity model both cross-side contexts share ONE canonical
    # content row (max(expected)), so the value's search narrows to exactly
    # that relation.
    assert listed == [max(expected)], listed

    # A substring that no relation hash contains lists nothing - and says so.
    body = _fetch_page(admin_client, limit=10, search="ffffffffffffffffffff")
    assert body["data"] == []
    assert body["total_estimated"] == 0


def test_ordinary_hashes_are_not_relations(admin_client, archive):
    """Single-path hashes stay out of the list, however the section is sorted."""
    expected = archive["expected"]
    for sort_by, sort_dir in (("file_count", "desc"), ("name", "asc"),
                              ("id", "asc")):
        rows, total = _walk(admin_client, 50, sort_by=sort_by, sort_dir=sort_dir,
                            search=archive["tag"])
        assert total == len(expected), (sort_by, sort_dir)
        assert _listed_ids(rows) == set(expected), (sort_by, sort_dir)
        assert all(row["file_count"] >= 1 for row in rows)
