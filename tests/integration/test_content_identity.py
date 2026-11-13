"""Integration: content identity - the central regression test of m0011.

The architecture under test (Master Directive, data-identity sections):

    CANONICAL CONTENT     HASH                     (hashs, UNIQUE(hash))
    CONTEXT               HASH + SOURCE + SIDE     (hash_contexts,
                                                    UNIQUE(hash_id, source_id,
                                                    side_id))
    OCCURRENCE            CONTEXT + physical row   (paths, context_id)

"Duplicate" means different things at each layer, and the system must keep
the layers apart:

* same HASH + same SOURCE + same SIDE + same physical occurrence
  -> duplicate OCCURRENCE (no new path row);
* same HASH + same SOURCE + same SIDE + different physical occurrence
  -> legitimate second OCCURRENCE (same context, new path row);
* same HASH + different SOURCE or SIDE
  -> same canonical CONTENT + a new CONTEXT (never a second content row).

The directive's reference dataset is stored verbatim here and asserted
against the real database, not against service return values:

    HASH A
      |-- Source 1 / Side 1  -> occurrence A
      |-- Source 1 / Side 2  -> occurrence B
      |-- Source 2 / Side 1  -> occurrence C
      +-- Source 2 / Side 2  -> occurrence D

    canonical content count = 1
    context count           = 4
    occurrence count        = 4

Adding "HASH B + Source 1 + Side 1" must yield canonical content count = 2.

Also pinned here: extraction reuse (a second arrival of the same HASH runs no
content-derived processing at all), the delete lifecycle (occurrence ->
context -> canonical content, each level removed only when it has no
remaining dependants), delete + re-ingest (no stale identity blocks the
re-ingest), search (one content, many contexts, never four documents) and
analysis (content-level titles shared, path-level analyst categories kept
distinct per occurrence).
"""

import datetime
import os
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

from database.services.contents_db_service import ContentDBService
from database.services.dedup_service import DeduplicationService

_TAG = f"id_{os.getpid()}_{uuid.uuid4().hex[:8]}"

WORDS_A = [
    f"idword_a1", "identity", "canonical", "content", "probe",
    "context", "occurrence", "provenance", "shared", "lifecycle",
]
TITLE_A = [f"idtitle_{_TAG}", "identity", "canonical"]


def _hash(value: str) -> str:
    """A distinct, FULL 64-char content hash that carries the module tag.

    ``hashs.hash`` is CHAR(64); a shorter value is stored right-padded with
    spaces, which breaks naive string comparisons against the DB value.
    ``{value}`` also stays a unique substring, so the hash remains
    searchable by label (e.g. the module tag plus the label).
    """
    return f"{_TAG}-{value}-{uuid.uuid4().hex}-{uuid.uuid4().hex}"[:64]


@pytest.fixture()
def conn(pg_db):
    c = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    yield c
    c.close()


@pytest.fixture()
def dedup(conn):
    return DeduplicationService(lambda: conn)


@pytest.fixture()
def service():
    return ContentDBService()


@pytest.fixture()
def sources_sides(conn):
    """Source 1 / Side 1 and Source 2 / Side 2 of the reference dataset.

    Names are unique per fixture instantiation (the test database is shared
    across the suite and names are UNIQUE columns).
    """
    today = datetime.date.today()
    inst = uuid.uuid4().hex[:8]
    with conn.cursor() as cur:
        ids = {}
        names = {}
        for i in (1, 2):
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation)"
                " VALUES (%s, 0.5, %s) RETURNING id",
                (f"{_TAG}_{inst}_side{i}", today),
            )
            ids[f"side{i}"] = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO sources (name, job, importance, country,"
                " date_creation) VALUES (%s, 't', 0.5, 'NL', %s)"
                " RETURNING id",
                (f"{_TAG}_{inst}_source{i}", today),
            )
            ids[f"source{i}"] = cur.fetchone()[0]
            names[f"side{i}"] = f"{_TAG}_{inst}_side{i}"
            names[f"source{i}"] = f"{_TAG}_{inst}_source{i}"
    conn.commit()
    return {"ids": ids, "names": names}


def ingest(service, ids, hash_value, source_key, side_key, name, path):
    """Store one occurrence through the single authoritative ingestion path."""
    return service.process_full_document(
        hash_value=hash_value,
        source_id=ids[source_key],
        side_id=ids[side_key],
        file_name=name,
        file_path=path,
        file_size=512,
        file_type="txt",
        file_status="Read",
        file_date=datetime.date.today(),
        content_words=list(WORDS_A),
        title_words=list(TITLE_A),
        raw_text="canonical content probe text",
        attempts=1,
    )


@pytest.fixture()
def dataset(service, sources_sides, conn):
    """The directive's reference dataset: HASH A in all four contexts."""
    ids = sources_sides["ids"]
    hash_a = _hash("A")
    occurrences = {}
    for source_key, side_key in (
        ("source1", "side1"),
        ("source1", "side2"),
        ("source2", "side1"),
        ("source2", "side2"),
    ):
        label = f"{source_key}/{side_key}"
        result = ingest(
            service, ids, hash_a, source_key, side_key,
            f"{label}.txt", f"/identity/{label}.txt",
        )
        assert result["success"], result
        occurrences[label] = result
    conn.commit()
    return {
        "hash_a": hash_a,
        "occurrences": occurrences,
        "ids": ids,
        "names": sources_sides["names"],
    }


# ---------------------------------------------------------------------------
# 1. The identity rules are enforced by the database, not the application
# ---------------------------------------------------------------------------

def test_db_uniqueness_enforces_content_and_context_identity(conn):
    """Raw duplicate INSERTs must be rejected: no application-level checks."""
    hash_a = _hash("uq-content")
    with conn.cursor() as cur:
        cur.execute("INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                    (hash_a,))
        hash_id = cur.fetchone()[0]

        # A second canonical content row for the same HASH is impossible.
        # (The violation aborts the transaction; roll back to clear it and
        # restore the canonical row, which the rollback removed.)
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute("INSERT INTO hashs (hash) VALUES (%s)", (hash_a,))
        conn.rollback()
        cur.execute("INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                    (hash_a,))
        hash_id = cur.fetchone()[0]

        # One context per (content, source, side).
        cur.execute(
            "INSERT INTO sources (name, job, importance, country,"
            " date_creation) VALUES (%s, 't', 0.5, 'NL', CURRENT_DATE)"
            " RETURNING id", (f"{_TAG}_uq_src",),
        )
        source_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO sides (name, importance, date_creation)"
            " VALUES (%s, 0.5, CURRENT_DATE) RETURNING id",
            (f"{_TAG}_uq_side",),
        )
        side_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
            " VALUES (%s, %s, %s) RETURNING id",
            (hash_id, source_id, side_id),
        )
        # The duplicate context row is rejected immediately.
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                " VALUES (%s, %s, %s)",
                (hash_id, source_id, side_id),
            )
    conn.rollback()


# ---------------------------------------------------------------------------
# 2. The reference dataset: one content, four contexts, four occurrences
# ---------------------------------------------------------------------------

def test_dataset_counts_content_context_occurrence(dataset, dedup, conn):
    hash_a = dataset["hash_a"]
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM hashs WHERE hash = %s", (hash_a,))
        content_ids = [r[0] for r in cur.fetchall()]
        assert len(content_ids) == 1, (
            f"expected ONE canonical content row, found {len(content_ids)}"
        )
        hash_id = content_ids[0]

        cur.execute(
            "SELECT source_id, side_id FROM hash_contexts WHERE hash_id = %s",
            (hash_id,),
        )
        contexts = {(r[0], r[1]) for r in cur.fetchall()}
        expected_pairs = {
            (dataset["ids"][k], dataset["ids"][s])
            for k, s in (("source1", "side1"), ("source1", "side2"),
                         ("source2", "side1"), ("source2", "side2"))
        }
        assert len(contexts) == 4, "expected FOUR contexts"
        assert contexts == expected_pairs, contexts

        cur.execute(
            "SELECT COUNT(*) FROM paths p"
            " JOIN hash_contexts hc ON p.context_id = hc.id"
            " WHERE hc.hash_id = %s",
            (hash_id,),
        )
        assert cur.fetchone()[0] == 4, "expected FOUR occurrences"

    # The identity service reports the same three layers independently.
    layer_contexts = dedup.content_contexts(hash_a)
    assert len(layer_contexts) == 4
    assert {
        (c["source_id"], c["side_id"]) for c in layer_contexts
    } == expected_pairs
    assert all(c["occurrence_count"] == 1 for c in layer_contexts)

    layer_occurrences = dedup.content_occurrences(hash_a)
    assert len(layer_occurrences) == 4
    assert {o["path_id"] for o in layer_occurrences} == {
        r["path_id"] for r in dataset["occurrences"].values()
    }

    # path_identity resolves one occurrence to all three layers at once.
    identity = dedup.path_identity(
        dataset["occurrences"]["source2/side2"]["path_id"])
    assert identity is not None
    assert identity["hash"] == hash_a
    assert identity["hash_id"] == hash_id
    assert identity["source_id"] == dataset["ids"]["source2"]
    assert identity["side_id"] == dataset["ids"]["side2"]
    assert identity["context_id"] in {c["context_id"] for c in layer_contexts}


def test_second_content_increases_canonical_count(dataset, service,
                                                  sources_sides, conn):
    """HASH B + Source 1 + Side 1: canonical content count becomes 2."""
    ids = dataset["ids"]
    hash_b = _hash("B")
    result = ingest(service, ids, hash_b, "source1", "side1",
                    "b.txt", "/identity/b.txt")
    assert result["success"], result
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM hashs WHERE hash = ANY(%s)",
            ([dataset["hash_a"], hash_b],),
        )
        assert cur.fetchone()[0] == 2, (
            "one canonical row per distinct hash - two hashes, two rows"
        )
        cur.execute("SELECT id FROM hashs WHERE hash = %s", (hash_b,))
        hash_id_b = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM hash_contexts WHERE hash_id = %s",
            (hash_id_b,),
        )
        assert cur.fetchone()[0] == 1
        # HASH A is untouched by HASH B's arrival.
        cur.execute(
            "SELECT COUNT(*) FROM hash_contexts WHERE hash_id = %s",
            (dataset["occurrences"]["source1/side1"]["hash_id"],),
        )
        assert cur.fetchone()[0] == 4


# ---------------------------------------------------------------------------
# 3. The occurrence rules, stated explicitly
# ---------------------------------------------------------------------------

def test_same_everything_is_a_duplicate_occurrence(service, sources_sides,
                                                    conn):
    """Same HASH + SOURCE + SIDE + physical occurrence -> no new row."""
    ids = sources_sides["ids"]
    h = _hash("dup-occ")
    first = ingest(service, ids, h, "source1", "side1",
                   "d.txt", "/identity/dup/d.txt")
    assert first["success"] and first["duplicate"] is False
    second = ingest(service, ids, h, "source1", "side1",
                    "d.txt", "/identity/dup/d.txt")
    assert second["success"]
    assert second["duplicate"] is True
    assert second["path_id"] == first["path_id"], (
        "the same physical occurrence must resolve to the same path row"
    )
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM paths p"
            " JOIN hash_contexts hc ON p.context_id = hc.id"
            " WHERE hc.hash_id = (SELECT id FROM hashs WHERE hash = %s)",
            (h,),
        )
        assert cur.fetchone()[0] == 1


def test_same_context_different_location_is_second_occurrence(
        service, sources_sides, conn):
    """Different physical occurrence in the same context is NOT collapsed."""
    ids = sources_sides["ids"]
    h = _hash("second-occ")
    first = ingest(service, ids, h, "source1", "side1",
                   "x.txt", "/identity/second/x.txt")
    second = ingest(service, ids, h, "source1", "side1",
                    "y.txt", "/identity/second/y.txt")
    assert first["success"] and second["success"]
    assert second["duplicate"] is False, (
        "a different physical occurrence of the same content is legitimate"
    )
    assert second["content_reused"] is True
    assert second["path_id"] != first["path_id"]
    assert second["hash_id"] == first["hash_id"]
    assert second["context_id"] == first["context_id"]
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT COUNT(*) FROM hashs WHERE hash = %s),"
            " (SELECT COUNT(*) FROM hash_contexts"
            "   WHERE hash_id = (SELECT id FROM hashs WHERE hash = %s)),"
            " (SELECT COUNT(*) FROM paths p"
            "   JOIN hash_contexts hc ON p.context_id = hc.id"
            "   WHERE hc.hash_id = (SELECT id FROM hashs WHERE hash = %s))",
            (h, h, h),
        )
        contents, contexts, occurrences = cur.fetchone()
    assert (contents, contexts, occurrences) == (1, 1, 2)


def test_different_source_or_side_is_new_context_not_new_content(
        service, sources_sides, conn):
    ids = sources_sides["ids"]
    h = _hash("new-context")
    r1 = ingest(service, ids, h, "source1", "side1",
                "n1.txt", "/identity/newctx/n1.txt")
    r2 = ingest(service, ids, h, "source2", "side1",
                "n2.txt", "/identity/newctx/n2.txt")
    r3 = ingest(service, ids, h, "source1", "side2",
                "n3.txt", "/identity/newctx/n3.txt")
    for r in (r1, r2, r3):
        assert r["success"]
        assert r["duplicate"] is False
    assert r2["content_reused"] is True
    assert r3["content_reused"] is True
    assert {r["hash_id"] for r in (r1, r2, r3)} == {r1["hash_id"]}, (
        "the canonical content is one row, whatever the context"
    )
    assert len({r["context_id"] for r in (r1, r2, r3)}) == 3
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM hashs WHERE hash = %s", (h,))
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT COUNT(*) FROM hash_contexts"
            " WHERE hash_id = (SELECT id FROM hashs WHERE hash = %s)", (h,))
        assert cur.fetchone()[0] == 3


# ---------------------------------------------------------------------------
# 4. Extraction is reused, proven by instrumentation, not row counts alone
# ---------------------------------------------------------------------------

def test_second_arrival_runs_no_content_derived_processing(
        service, sources_sides, conn):
    """When HASH A arrives a second time, canonical processing is skipped.

    The service layer is instrumented directly: content extraction, raw text,
    the word index and the keyword index must each run exactly once for the
    whole lifetime of the content - and the row counts must confirm it.
    """
    ids = sources_sides["ids"]
    h = _hash("reuse")
    calls = {
        "create_content": 0,
        "store_raw_content": 0,
        "link_words_to_content": 0,
        "process_keywords_for_content": 0,
    }

    def counting(name, original):
        def wrapper(*args, **kwargs):
            calls[name] += 1
            return original(*args, **kwargs)
        return wrapper

    service.create_content = counting(
        "create_content", service.create_content)
    service.contents_repo.store_raw_content = counting(
        "store_raw_content", service.contents_repo.store_raw_content)
    service.link_words_to_content = counting(
        "link_words_to_content", service.link_words_to_content)
    service.process_keywords_for_content = counting(
        "process_keywords_for_content",
        service.process_keywords_for_content)

    first = ingest(service, ids, h, "source1", "side1",
                   "r1.txt", "/identity/reuse/r1.txt")
    assert first["success"] and first["content_reused"] is False
    # Every canonical extraction step ran for the first arrival.
    assert all(counts >= 1 for counts in calls.values()), calls

    contents_before = None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT COUNT(*) FROM contents"
            "        WHERE hash_id = (SELECT id FROM hashs WHERE hash = %s)),"
            " (SELECT COUNT(*) FROM contents_raw"
            "   WHERE hash_id = (SELECT id FROM hashs WHERE hash = %s)),"
            " (SELECT COUNT(*) FROM words_hashs"
            "   WHERE hash_id = (SELECT id FROM hashs WHERE hash = %s))",
            (h, h, h),
        )
        contents_before = cur.fetchone()

    second = ingest(service, ids, h, "source2", "side2",
                    "r2.txt", "/identity/reuse/r2.txt")
    assert second["success"]
    assert second["content_reused"] is True, (
        "the second arrival must be flagged as reusing the canonical content"
    )
    # ...and it must not have touched any content-derived store.
    assert calls == {
        "create_content": 1,
        "store_raw_content": 1,
        "link_words_to_content": 1,
        "process_keywords_for_content": 1,
    }, calls
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT COUNT(*) FROM contents"
            "        WHERE hash_id = (SELECT id FROM hashs WHERE hash = %s)),"
            " (SELECT COUNT(*) FROM contents_raw"
            "   WHERE hash_id = (SELECT id FROM hashs WHERE hash = %s)),"
            " (SELECT COUNT(*) FROM words_hashs"
            "   WHERE hash_id = (SELECT id FROM hashs WHERE hash = %s))",
            (h, h, h),
        )
        assert cur.fetchone() == contents_before, (
            "reused content must not add a single extraction row"
        )


# ---------------------------------------------------------------------------
# 5. Delete lifecycle: occurrence -> context -> canonical content
# ---------------------------------------------------------------------------

def test_delete_cascades_only_when_no_dependants_remain(dataset, dedup,
                                                        conn):
    hash_a = dataset["hash_a"]
    ids = dataset["ids"]
    occ_a = dataset["occurrences"]["source1/side1"]
    occ_b = dataset["occurrences"]["source2/side2"]

    def counts():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT (SELECT COUNT(*) FROM paths p"
                "        JOIN hash_contexts hc ON p.context_id = hc.id"
                "        WHERE hc.hash_id = %s),"
                " (SELECT COUNT(*) FROM hash_contexts WHERE hash_id = %s),"
                " (SELECT COUNT(*) FROM hashs WHERE hash = %s)",
                (occ_a["hash_id"], occ_a["hash_id"], hash_a),
            )
            return cur.fetchone()

    # Delete occurrence A: its context holds nothing else, so the context
    # goes too - but the content still has three live contexts.
    lifecycle = dedup.delete_path(occ_a["path_id"])
    assert lifecycle == {
        "path_deleted": True,
        "context_deleted": True,
        "hash_deleted": False,
    }
    assert counts() == (3, 3, 1), "shared content must survive"
    assert dedup.find_canonical_hash(hash_a) is not None, (
        "the content must stay resolvable while any context is alive"
    )

    # Delete every remaining occurrence: the content dies with its last
    # context, and only then.
    for label in ("source1/side2", "source2/side1"):
        result = dedup.delete_path(dataset["occurrences"][label]["path_id"])
        assert result["context_deleted"] is True
        assert result["hash_deleted"] is False
    assert counts() == (1, 1, 1)

    final = dedup.delete_path(occ_b["path_id"])
    assert final == {
        "path_deleted": True,
        "context_deleted": True,
        "hash_deleted": True,
    }
    assert counts() == (0, 0, 0), "no orphaned context or content rows"


def test_shared_content_survives_each_delete(dataset, dedup, conn):
    """Deleting one of four contexts never touches the other three."""
    hash_a = dataset["hash_a"]
    occ_a = dataset["occurrences"]["source1/side1"]

    result = dedup.delete_path(occ_a["path_id"])
    assert result["hash_deleted"] is False

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM paths p"
            " JOIN hash_contexts hc ON p.context_id = hc.id"
            " WHERE hc.hash_id = (SELECT id FROM hashs WHERE hash = %s)",
            (hash_a,),
        )
        assert cur.fetchone()[0] == 3
        # Every surviving occurrence still resolves to the same content.
        for label, occ in dataset["occurrences"].items():
            if label == "source1/side1":
                continue
            identity = dedup.path_identity(occ["path_id"])
            assert identity is not None and identity["hash"] == hash_a, (
                f"{label} lost its canonical content"
            )


def test_delete_and_reingest_has_no_stale_identity(service, dedup,
                                                    sources_sides, conn):
    """ingest -> delete -> re-ingest: nothing stale blocks the re-ingest."""
    ids = sources_sides["ids"]
    h = _hash("reingest")
    first = ingest(service, ids, h, "source1", "side1",
                   "re.txt", "/identity/reingest/re.txt")
    assert first["success"]
    path_id = first["path_id"]

    lifecycle = dedup.delete_path(path_id)
    assert lifecycle["path_deleted"] is True
    assert lifecycle["context_deleted"] is True
    assert lifecycle["hash_deleted"] is True

    # Re-ingest the identical file through the normal path.
    again = ingest(service, ids, h, "source1", "side1",
                   "re.txt", "/identity/reingest/re.txt")
    assert again["success"], f"re-ingest was blocked: {again}"
    assert again["duplicate"] is False, "the deleted record is gone"
    assert again["path_id"] > 0 and again["path_id"] != path_id
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT file_name FROM paths WHERE id = %s",
                    (again["path_id"],))
        assert cur.fetchone()[0] == "re.txt"
        cur.execute(
            "SELECT COUNT(*) FROM hash_contexts"
            " WHERE hash_id = (SELECT id FROM hashs WHERE hash = %s)", (h,))
        assert cur.fetchone()[0] == 1, "exactly one context after re-ingest"
        # The canonical extraction still exists (reused, not rebuilt stale).
        assert dedup.extraction_exists(again["hash_id"]) is True


# ---------------------------------------------------------------------------
# 6. Search: one content, many contexts - never four documents
# ---------------------------------------------------------------------------

def test_search_finds_all_occurrences_but_one_relation(dataset, conn):
    hash_a = dataset["hash_a"]
    from Api.utils import count_relation_duplicates

    # A word of the content finds all four occurrences, each with the
    # correct context metadata.
    from Api.utils import search_files_by_word
    results, total = search_files_by_word(WORDS_A[0], per_page=50)
    mine = [r for r in results if r["id"] in {
        o["path_id"] for o in dataset["occurrences"].values()
    }]
    assert len(mine) == 4, (
        f"search must surface every occurrence of the shared content, "
        f"found {len(mine)} of 4"
    )
    assert total >= 4

    names = dataset["names"]
    pairs = {(r["source_name"], r["side_name"]) for r in mine}
    expected_pairs = {
        (names["source1"], names["side1"]),
        (names["source1"], names["side2"]),
        (names["source2"], names["side1"]),
        (names["source2"], names["side2"]),
    }
    assert pairs == expected_pairs, (
        "each occurrence must carry its own context, not a merged one"
    )

    # The Relations section reports ONE canonical relation for the four
    # occurrences - not four independent documents. The search key is the
    # trailing UUID hex of THIS hash (wildcard-free, 128 bits unique), so it
    # matches only this canonical content even though other tests in the
    # shared database store their own relations under the same module tag.
    assert count_relation_duplicates(hash_a[-32:]) == 1, (
        "one canonical content with several contexts is one relation"
    )


# ---------------------------------------------------------------------------
# 7. Analysis: content-level titles shared, path-level categories distinct
# ---------------------------------------------------------------------------

def test_analysis_layers_survive_canonical_deduplication(dataset, conn):
    from Api.services.analyst_categories import AnalystCategoryService

    hash_a = dataset["hash_a"]
    occ_a = dataset["occurrences"]["source1/side1"]
    occ_b = dataset["occurrences"]["source2/side2"]

    # Titles are content-level: ONE Main title row for the content, and every
    # occurrence resolves to it through its own context.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM titles_content"
            " WHERE hash_id = (SELECT id FROM hashs WHERE hash = %s)"
            " AND title_status = 'Main'",
            (hash_a,),
        )
        title_count = cur.fetchone()[0]
    assert title_count == 1, (
        "the canonical document is one piece of content - one Main title"
    )

    for occ in (occ_a, occ_b):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM titles_content"
                " WHERE hash_id = (SELECT c2.hash_id FROM paths p2"
                "   JOIN hash_contexts c2 ON c2.id = p2.context_id"
                "   WHERE p2.id = %s) AND title_status = 'Main'",
                (occ["path_id"],),
            )
            assert cur.fetchone() is not None, (
                f"occurrence {occ['path_id']} cannot reach the title"
            )

    # Analyst categorization is occurrence-level: two occurrences of the same
    # content keep two DISTINCT classifications.
    res_a, err_a = AnalystCategoryService.assign(
        [occ_a["path_id"]], category_name=f"{_TAG}-analysis-A",
        create_category=True)
    assert err_a is None, err_a
    res_b, err_b = AnalystCategoryService.assign(
        [occ_b["path_id"]], category_name=f"{_TAG}-analysis-B",
        create_category=True)
    assert err_b is None, err_b

    by_file = AnalystCategoryService.categories_for_files(
        [occ_a["path_id"], occ_b["path_id"]])
    assert {c["name"] for c in by_file[occ_a["path_id"]]} == \
        {f"{_TAG}-analysis-A"}
    assert {c["name"] for c in by_file[occ_b["path_id"]]} == \
        {f"{_TAG}-analysis-B"}
    conn.commit()
