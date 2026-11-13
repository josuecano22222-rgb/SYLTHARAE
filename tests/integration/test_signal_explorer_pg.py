"""Step 10 integration: Horizon and Signal Explorer over durable signals.

PostgreSQL through all migrations, documents stored by the real ingestion
path (signals detected once, at ingestion), read through the HTTP API and
the service. The database is shared with other test modules, so every query
here is restricted to this module's own source through *document criteria* -
which is itself the path under test.

Rewritten for the frozen schema. BEFORE, the three explorer tests assumed a
gazetteer that shipped with external ids and a ``places`` array on every place
signal: the module fixture stored documents without loading any gazetteer (so
place detection matched nothing), filtered with
``place_key="wikidata:Q3579"``, and the detail test indexed
``listing["items"][0]`` - which raised IndexError once the list was empty.

Now:  the fixture applies ``data/gazetteer/places_seed.json`` through
``sync_seed`` before storing (geo_places/geo_place_names are how the frozen
schema holds a gazetteer - there is no geo_gazetteer_loads table and no
external-key column), and every place filter uses the derived key form
``place:<id>`` that the API publishes for that schema. The assertions kept
are the same ones: the detector facet count, one unambiguous Zagreb match, an
ambiguous Tripoli match with two candidates, per-place filtering, totals and
facets agreeing, deterministic pagination, and a detail view carrying run
versions and occurrences. The public-key form is additionally asserted, so a
change of key scheme fails here instead of at a call site.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date

import pytest

from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]
REF = "2026-10-01"

TEXT = ("The review will be held on 5 October 2026.\n"
        "The audit will take place on 20 October 2026.\n"
        "The summit will convene on 15 December 2026.\n"
        "The treaty will expire on 1 June 2027.\n"
        "The deadline will be 1 September 2026.\n"
        "The agreement was signed on 1 March 2020.\n"
        "The meeting is on 03/04/2026.\n"
        "سيعقد الاجتماع في 20 رمضان 1448 هـ.\n"
        "جلسه در ۱۵ مهر ۱۴۰۵ برگزار خواهد شد.\n"
        "Delegates flew in from Zagreb and Tripoli.\n")
OTHER_TEXT = "The inspection will occur on 6 October 2026 in Geneva.\n"

_N = iter(range(10_000))


@pytest.fixture(scope="module")
def world(pg_db):
    from database.services.contents_db_service import ContentDBService

    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        ids = {}
        for key in ("a", "b"):
            cur.execute("INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5,"
                        " CURRENT_DATE) RETURNING id", (f"hz_side_{key}_{_U}",))
            ids[f"side_{key}"] = cur.fetchone()[0]
            cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                        " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id",
                        (f"hz_src_{key}_{_U}",))
            ids[f"source_{key}"] = cur.fetchone()[0]
    conn.close()

    def store(text, key):
        marker = f"{_U}hz{next(_N)}"
        return ContentDBService().process_full_document(
            hash_value=hashlib.sha256(marker.encode()).hexdigest(), source_id=ids[f"source_{key}"],
            side_id=ids[f"side_{key}"], file_name=f"{marker}.txt",
            file_path=f"/tmp/hz/{marker}.txt", file_size=100, file_type="txt",
            file_status="Read", file_date=date(2026, 1, 1), content_words=["hz", marker],
            raw_text=text, attempts=1)["hash_id"]

    # Place detection matches against the database gazetteer, which the frozen
    # schema keeps in geo_places/geo_place_names (there is no geo_gazetteer_loads
    # and no external key column), so the seed must be applied first.
    from services.geo.gazetteer import load_seed_file, sync_seed

    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        sync_seed(cur, load_seed_file(), loaded_by="signal-explorer-test")
    conn.close()

    ids["hash_a"] = store(TEXT, "a")
    ids["hash_b"] = store(OTHER_TEXT, "b")

    # The public place key is derived from the row id ("place:<id>"): geo_places
    # has no external-identifier column, and the id is stable across seed syncs.
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        for label, key in (("Zagreb", "zagreb"), ("Tripoli", "tripoli")):
            cur.execute("SELECT id FROM geo_places WHERE label = %s ORDER BY id", (label,))
            found = [row[0] for row in cur.fetchall()]
            if key == "zagreb":
                ids["zagreb_key"] = f"place:{found[0]}"
            ids[f"tripoli_keys"] = [f"place:{i}" for i in found]
    conn.close()
    return ids


def _get(client, url, **params):
    resp = client.get(url, query_string=params)
    return resp.status_code, resp.get_json()


def _horizon(client, world, **params):
    params.setdefault("reference_date", REF)
    params.setdefault("source_id", world["source_a"])
    return _get(client, "/api/signals/horizon", **params)


# --- Horizon ------------------------------------------------------------------

def test_horizon_buckets_every_dated_reference_against_the_reference_date(viewer_client, world):
    status, body = _horizon(viewer_client, world)
    assert status == 200, body
    counts = {b["key"]: b["signals"] for b in body["buckets"]}
    assert counts == {"overdue": 1, "week": 2, "month": 1, "quarter": 1, "later": 2, "past": 1}
    ranges = {b["key"]: (b["from"], b["to"]) for b in body["buckets"]}
    assert ranges["week"] == ("2026-10-01", "2026-10-07")
    assert ranges["month"] == ("2026-10-08", "2026-10-30")
    assert ranges["overdue"] == (None, "2026-09-30")
    # Listed by default: the horizon proper, not historical dates.
    assert body["listed_buckets"] == ["overdue", "week", "month", "quarter", "later"]
    assert body["total"] == 7 and body["unit"] == "signal"
    surfaces = [(i["bucket"], i["surface"]) for i in body["items"]]
    assert surfaces == [
        ("overdue", "1 September 2026"), ("week", "5 October 2026"), ("week", "۱۵ مهر ۱۴۰۵"),
        ("month", "20 October 2026"), ("quarter", "15 December 2026"),
        ("later", "20 رمضان 1448 هـ"), ("later", "1 June 2027")]


def test_horizon_items_answer_what_where_why_and_how(viewer_client, world):
    _, body = _horizon(viewer_client, world, bucket="week")
    item = body["items"][0]
    assert item["surface"] == "5 October 2026" and item["value"]
    assert item["sentence"] == "The review will be held on 5 October 2026."       # why
    assert item["method"] and item["confidence"] == "high"                         # how
    assert item["detector"] == "temporal" and item["detector_version_current"] is True
    assert item["document"]["hash_id"] == world["hash_a"]                          # where
    assert item["document"]["source_id"] == world["source_a"]
    assert item["document"]["occurrences"] == 1
    assert item["char_end"] - item["char_start"] == len("5 October 2026")


def test_horizon_keeps_undated_and_historical_references_separate(viewer_client, world):
    _, body = _horizon(viewer_client, world)
    assert body["undated"]["ambiguous"] == 1          # 03/04/2026: two readings, no date
    assert body["undated"]["unresolved"] == 0
    _, past = _horizon(viewer_client, world, bucket="past")
    assert [i["surface"] for i in past["items"]] == ["1 March 2020"]


def test_horizon_filters_by_calendar_language_confidence_and_event_window(viewer_client, world):
    _, hijri = _horizon(viewer_client, world, calendar="hijri")
    assert {b["key"]: b["signals"] for b in hijri["buckets"]}["later"] == 1
    assert [i["calendar"] for i in hijri["items"]] == ["hijri"]
    _, fa = _horizon(viewer_client, world, language="fa")
    assert [i["surface"] for i in fa["items"]] == ["۱۵ مهر ۱۴۰۵"]
    _, window = _horizon(viewer_client, world, event_from="2026-10-06", event_to="2026-10-31")
    assert sorted(i["surface"] for i in window["items"]) == ["20 October 2026", "۱۵ مهر ۱۴۰۵"]
    _, low = _horizon(viewer_client, world, confidence="low")
    assert low["total"] == 0 and low["undated"]["ambiguous"] == 1


def test_horizon_document_criteria_isolate_sources(viewer_client, world):
    _, b = _horizon(viewer_client, world, source_id=world["source_b"])
    assert [i["surface"] for i in b["items"]] == ["6 October 2026"]
    _, both = _get(viewer_client, "/api/signals/horizon", reference_date=REF,
                   source_id=[world["source_a"], world["source_b"]], bucket="week")
    assert sorted(i["surface"] for i in both["items"]) == [
        "5 October 2026", "6 October 2026", "۱۵ مهر ۱۴۰۵"]
    _, side = _horizon(viewer_client, world, source_id=None, side_id=world["side_b"])
    assert side["total"] == 1


def test_horizon_is_deterministic_and_fingerprinted(viewer_client, world):
    _, one = _horizon(viewer_client, world)
    _, two = _horizon(viewer_client, world)
    assert one["query_fingerprint"] == two["query_fingerprint"]
    assert one["items"] == two["items"]
    _, other_day = _horizon(viewer_client, world, reference_date="2026-10-10")
    assert other_day["query_fingerprint"] != one["query_fingerprint"]
    # 5 and 7 October are overdue ten days later (future-oriented sentences).
    assert {b["key"]: b["signals"] for b in other_day["buckets"]}["overdue"] == 3
    assert one["criteria"]["sources"] == [world["source_a"]]
    assert any("Criteria fingerprint" in line for line in one["explain"])


def test_horizon_reports_content_it_has_not_measured(viewer_client, world, pg_db):
    from _seed import document

    _, before = _horizon(viewer_client, world)
    assert before["coverage"]["matching_contents"] == 1
    assert before["coverage"]["current"] == 1 and before["coverage"]["not_measured"] == 0
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:   # content stored without detection: never analysed
        document(cur, source_id=world["source_a"], side_id=world["side_a"],
                 text="Due on 9 October 2026.", file_name=f"{_U}_raw.txt")
    conn.close()
    _, after = _horizon(viewer_client, world)
    cov = after["coverage"]
    assert (cov["matching_contents"], cov["current"], cov["never_analysed"],
            cov["not_measured"]) == (2, 1, 1, 1)
    # The unmeasured document contributes nothing to the buckets - and says so.
    assert after["buckets"] == before["buckets"]
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE content_signal_runs SET detector_ver = 'temporal-0.9.0'"
                    " WHERE hash_id = %s AND detector = 'temporal'", (world["hash_a"],))
    conn.close()
    try:
        _, stale = _horizon(viewer_client, world)
        assert stale["coverage"]["stale_version"] == 1
        assert stale["coverage"]["not_measured"] == 2
    finally:
        conn = connect(pg_db)
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE content_signal_runs SET detector_ver = %s"
                        " WHERE hash_id = %s AND detector = 'temporal'",
                        (after["detector_versions"]["temporal"], world["hash_a"]))
        conn.close()


def test_horizon_validation(viewer_client, world):
    for params, fragment in (({"detector": "places"}, "temporal signals only"),
                             ({"signal_type": "orientation"}, "date and relative"),
                             ({"bucket": "someday"}, "bucket"),
                             ({"reference_date": "01/10/2026"}, "reference_date"),
                             ({"confidence": "certain"}, "confidence"),
                             ({"event_from": "2026-12-01", "event_to": "2026-01-01"}, "event_from"),
                             ({"limit": "0"}, "limit"), ({"limit": "201"}, "limit"),
                             ({"source_id": "x"}, "source_id")):
        status, body = _horizon(viewer_client, world, **params)
        assert status == 400, (params, body)
        assert fragment in body["error"]["message"], (params, body)


# --- Explorer -------------------------------------------------------------------

def test_explorer_lists_both_detectors_with_exact_totals_and_facets(viewer_client, world):
    status, body = _get(viewer_client, "/api/signals", source_id=world["source_a"],
                        reference_date=REF)
    assert status == 200, body
    detectors = {f["value"]: f["count"] for f in body["facets"]["detector"]}
    assert detectors["places"] == 2 and detectors["temporal"] >= 10
    assert body["total"] == sum(detectors.values()) and body["contents"] == 1
    assert sum(f["count"] for f in body["facets"]["confidence"]) == body["total"]
    calendars = {f["value"]: f["count"] for f in body["facets"]["calendar"]}
    assert calendars["hijri"] == 1 and calendars["jalali"] == 1
    places = [i for i in body["items"] if i["detector"] == "places"]
    assert {p["places"][0]["label"] for p in places if len(p["places"]) == 1} == {"Zagreb"}
    assert any(len(p["places"]) == 2 for p in places)      # Tripoli stays ambiguous
    # The candidate keys are the frozen schema's derived form, not wikidata ids.
    candidate_keys = [c["place_key"] for item in places for c in item["places"]]
    assert candidate_keys and all(key.startswith("place:") for key in candidate_keys)
    assert set(candidate_keys) >= {world["zagreb_key"], *world["tripoli_keys"]}


def test_explorer_filters_and_paginates_deterministically(viewer_client, world):
    base = {"source_id": world["source_a"], "reference_date": REF}
    _, places = _get(viewer_client, "/api/signals", detector="places", **base)
    assert {i["surface"] for i in places["items"]} == {"Zagreb", "Tripoli"}
    # place_key is "place:<id>" in the frozen schema; both Tripolis are addressed
    # individually and the ambiguous mention carries both candidates.
    _, tripoli = _get(viewer_client, "/api/signals", place_key=world["tripoli_keys"][0], **base)
    assert [i["surface"] for i in tripoli["items"]] == ["Tripoli"]
    _, both = _get(viewer_client, "/api/signals", place_key=world["tripoli_keys"], **base)
    assert both["total"] == tripoli["total"]
    _, zagreb = _get(viewer_client, "/api/signals", place_key=world["zagreb_key"], **base)
    assert [i["surface"] for i in zagreb["items"]] == ["Zagreb"]
    _, ambiguous = _get(viewer_client, "/api/signals", resolution="ambiguous", **base)
    assert {i["surface"] for i in ambiguous["items"]} == {"Tripoli", "03/04/2026"}
    _, evidence = _get(viewer_client, "/api/signals", evidence_text="SUMMIT", **base)
    assert {i["surface"] for i in evidence["items"]} == {"15 December 2026", "will"}
    _, wildcard = _get(viewer_client, "/api/signals", evidence_text="%", **base)
    assert wildcard["total"] == 0                      # '%' is literal, not "everything"
    _, full = _get(viewer_client, "/api/signals", sort="document", limit=200, **base)
    pages = []
    for offset in range(0, full["total"], 4):
        _, page = _get(viewer_client, "/api/signals", sort="document", limit=4, offset=offset,
                       **base)
        assert page["total"] == full["total"]
        pages.extend(i["signal_id"] for i in page["items"])
    assert pages == [i["signal_id"] for i in full["items"]]
    assert len(set(pages)) == full["total"]


def test_explorer_accepts_a_saved_search_only_when_the_caller_may_read_it(
        viewer_client, admin_client, world, pg_db):
    from core.criteria.model import from_dict

    criteria = from_dict({"sources": [world["source_b"]]})
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        ids = {}
        for username in ("testviewer", "testadmin"):
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            owner = cur.fetchone()[0]
            cur.execute("INSERT INTO saved_searches (owner_user_id, name, query, filters,"
                        " criteria, criteria_fingerprint, criteria_schema_version)"
                        " VALUES (%s, %s, '', '{}', %s, %s, 1) RETURNING id",
                        (owner, f"hz_{username}_{_U}", json.dumps(criteria.to_dict()),
                         criteria.fingerprint()))
            ids[username] = cur.fetchone()[0]
    conn.close()
    status, own = _get(viewer_client, "/api/signals", saved_search_id=ids["testviewer"],
                       reference_date=REF)
    assert status == 200, own
    assert own["saved_search_id"] == ids["testviewer"]
    assert own["criteria_fingerprint"] == criteria.fingerprint()
    assert own["contents"] == 1 and {i["hash_id"] for i in own["items"]} == {world["hash_b"]}
    status, body = _get(viewer_client, "/api/signals", saved_search_id=ids["testadmin"])
    assert status == 404 and body["error"]["code"] == "NOT_FOUND"
    status, body = _get(viewer_client, "/api/signals", saved_search_id=ids["testviewer"],
                        source_id=world["source_a"])
    assert status == 400 and "cannot be combined" in body["error"]["message"]


def test_explorer_validation(viewer_client, world):
    for params in ({"sort": "random"}, {"detector": "ner"}, {"language": "de"},
                   {"method": "DROP TABLE"}, {"place_key": "Tripoli"}, {"hash_id": "-1"},
                   {"keyword_logic": "XOR", "keyword_id": "1"}, {"evidence_text": "x" * 201}):
        status, body = _get(viewer_client, "/api/signals", **params)
        assert status == 400, (params, body)


# --- detail -----------------------------------------------------------------------

def test_signal_detail_carries_run_versions_and_occurrences(viewer_client, world):
    _, listing = _get(viewer_client, "/api/signals", source_id=world["source_a"],
                      detector="places", place_key=world["tripoli_keys"][0])
    signal_id = listing["items"][0]["signal_id"]
    status, item = _get(viewer_client, f"/api/signals/{signal_id}", reference_date=REF)
    assert status == 200, item
    assert item["signal_id"] == signal_id and item["surface"] == "Tripoli"
    assert item["resolution"] == "ambiguous" and len(item["places"]) == 2
    assert item["run"]["status"] == "complete" and item["run"]["current_version"] is True
    assert item["occurrences"]["total"] == 1 and item["occurrences"]["truncated"] is False
    assert item["occurrences"]["items"][0]["file_path"].startswith("/tmp/hz/")
    status, body = _get(viewer_client, "/api/signals/999999999")
    assert status == 404 and body["error"]["code"] == "NOT_FOUND"


# --- authorization, scope, failure ---------------------------------------------------

def test_signal_reads_require_authentication(client):
    for url in ("/api/signals", "/api/signals/horizon", "/api/signals/1"):
        assert client.get(url).status_code == 401, url
    assert client.get("/signals").status_code in (302, 401)


def test_access_scope_is_applied_in_sql_before_retrieval(pg_db, world):
    """The service honours a restricted scope for every read (the schema has
    no per-source ACL yet, so routes pass an unrestricted scope; the SQL path
    a future ACL will use is exercised here directly)."""
    from core.criteria.compiler import AccessScope
    from core.criteria.model import Criteria
    from services.detection import signal_query

    ref = date(2026, 10, 1)
    only_b = AccessScope(user_id=1, role="viewer", allowed_source_ids=(world["source_b"],))
    nothing = AccessScope(user_id=1, role="viewer", allowed_source_ids=())
    a_only = signal_query.parse_filter(_Args(hash_id=[str(world["hash_a"])]))
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            body = signal_query.explore(cur, a_only, Criteria(), only_b, ref)
            assert body["total"] == 0 and body["facets"]["detector"] == []
            hz = signal_query.horizon(cur, a_only, Criteria(), only_b, ref)
            assert hz["total"] == 0 and hz["coverage"]["matching_contents"] == 1  # only b
            assert signal_query.explore(cur, signal_query.SignalFilter(), Criteria(),
                                        nothing, ref)["total"] == 0
            cur.execute("SELECT id FROM content_signals WHERE hash_id = %s LIMIT 1",
                        (world["hash_a"],))
            signal_id = cur.fetchone()[0]
            with pytest.raises(LookupError):
                signal_query.signal_detail(cur, signal_id, only_b, ref)
            visible = signal_query.explore(cur, a_only, Criteria(),
                                           AccessScope.unrestricted(), ref)
            assert visible["total"] > 0
    finally:
        conn.rollback()
        conn.close()


def test_a_statement_timeout_is_reported_not_swallowed(viewer_client, monkeypatch):
    from services.detection import signal_query

    def slow_explore(cur, *args, **kwargs):
        cur.execute("SET LOCAL statement_timeout = 10")
        cur.execute("SELECT pg_sleep(1)")
        raise AssertionError("unreachable")

    monkeypatch.setattr(signal_query, "explore", slow_explore)
    status, body = _get(viewer_client, "/api/signals")
    assert status == 503 and body["error"]["code"] == "QUERY_TIMEOUT"


def test_signals_page_renders_for_a_viewer_with_its_contract_data(viewer_client):
    resp = viewer_client.get("/signals")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="signals-page-data"' in html
    assert 'data-page-type="signals"' in html
    assert "js/pages/signals-page.js" not in html
    data = html.split('id="signals-page-data">', 1)[1].split("</script>", 1)[0]
    parsed = json.loads(data)
    assert parsed["buckets"][:5] == ["overdue", "week", "month", "quarter", "later"]
    assert parsed["max_page_size"] == 200
    assert parsed["options_status"] == "complete"
    assert 'id="optionsNotice"' not in html


def _page_data(html):
    return json.loads(html.split('id="signals-page-data">', 1)[1].split("</script>", 1)[0])


def test_capped_filter_lists_say_they_are_capped(viewer_client, monkeypatch):
    """The menus hold at most FILTER_OPTION_LIMIT entries; one more row is read
    so a cut list is known to be cut, and the page says so."""
    import database.queries as queries
    from Api.routes import signals as routes

    monkeypatch.setattr(routes, "FILTER_OPTION_LIMIT", 2)
    asked = []

    def fake_sources(search=None, limit=50):
        asked.append(limit)
        return [{"id": i, "name": f"s{i}"} for i in range(1, 4)][:limit]

    monkeypatch.setattr(queries, "list_sources", fake_sources)
    monkeypatch.setattr(queries, "list_sides", lambda search=None, limit=50: [])
    html = viewer_client.get("/signals").get_data(as_text=True)
    data = _page_data(html)
    assert asked == [3]
    assert data["options_status"] == "truncated" and len(data["sources"]) == 2
    assert 'id="optionsNotice"' in html and "Only the first 2 sources" in html


def test_unloadable_filter_lists_are_reported_not_left_empty(viewer_client, monkeypatch):
    import database.queries as queries

    def broken(search=None, limit=50):
        raise RuntimeError("sources table unavailable")

    monkeypatch.setattr(queries, "list_sources", broken)
    resp = viewer_client.get("/signals")
    assert resp.status_code == 200          # the rest of the page still works
    html = resp.get_data(as_text=True)
    assert _page_data(html)["options_status"] == "unavailable"
    assert "could not be loaded" in html


class _Args(dict):
    """Minimal MultiDict stand-in for calling the parser directly."""

    def getlist(self, name):
        value = self.get(name)
        return list(value) if isinstance(value, list) else ([] if value is None else [value])


def test_every_api_response_is_read_from_one_snapshot(viewer_client, world):
    """Totals, facets, buckets, coverage and the page come from one
    REPEATABLE READ snapshot, and the response says so."""
    from services.detection import signal_query

    for url in ("/api/signals", "/api/signals/horizon"):
        status, body = _get(viewer_client, url, reference_date="2026-10-01")
        assert status == 200 and body["read_consistency"] == signal_query.SNAPSHOT, url
    _, explorer = _get(viewer_client, "/api/signals", hash_id=world["hash_a"])
    status, detail = _get(viewer_client, f"/api/signals/{explorer['items'][0]['signal_id']}")
    assert status == 200 and detail["read_consistency"] == signal_query.SNAPSHOT


def test_the_snapshot_excludes_rows_committed_during_the_read(pg_db, world):
    """What REPEATABLE READ buys: a signal committed by another connection
    between two statements of one response is not half-seen (count says N,
    page shows N+1). A caller-opened transaction is reported as such."""
    from services.detection import signal_query

    reader, writer = connect(pg_db), connect(pg_db)
    try:
        with reader.cursor() as cur:
            assert signal_query._begin_read(cur) == signal_query.SNAPSHOT
            cur.execute("SELECT COUNT(*) FROM content_signals")
            before = cur.fetchone()[0]
            with writer.cursor() as w:
                w.execute(
                    "INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type,"
                    " value, surface, char_start, char_end, resolution, evidence, dedup_key)"
                    " VALUES (%s, 'temporal', 'temporal-test', 'date_reference', 'x', 'x',"
                    " 900000, 900001, 'unresolved', '{}', %s)",
                    (world["hash_a"], uuid.uuid4().hex + uuid.uuid4().hex))
            writer.commit()
            cur.execute("SELECT COUNT(*) FROM content_signals")
            assert cur.fetchone()[0] == before
            assert signal_query._begin_read(cur) == signal_query.CALLER_TRANSACTION
        reader.rollback()
        with reader.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM content_signals")
            assert cur.fetchone()[0] == before + 1
    finally:
        reader.rollback()
        with writer.cursor() as w:
            w.execute("DELETE FROM content_signals WHERE detector_ver = 'temporal-test'")
        writer.commit()
        reader.close()
        writer.close()


def test_the_read_is_read_only_in_postgresql(pg_db):
    import psycopg2

    from services.detection import signal_query

    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            signal_query._begin_read(cur)
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                cur.execute("DELETE FROM content_signals WHERE id = -1")
    finally:
        conn.rollback()
        conn.close()
