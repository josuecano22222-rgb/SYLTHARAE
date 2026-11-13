"""Phase 1 integration: signals are produced at ingestion and by re-detection.

Executed against PostgreSQL through the real ``process_full_document`` path
and the real JobManager - not a synthetic demo.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date

import psycopg2
import pytest

from _seed import connect

from core.detection.temporal_intel import DETECTOR_VERSION

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]

MULTILINGUAL = (
    "The summit will be held on 5 October 2026. Tomorrow we decide.\n"
    "سيعقد المؤتمر في ٥ أكتوبر ٢٠٢٦ وسوف يستمر حتى ١٥ رمضان ١٤٤٧ هـ.\n"
    "הכנס יתקיים ב-5 באוקטובר 2026.\n"
    "نشست در ۱۵ مهر ۱۴۰۵ برگزار خواهد شد.\n"
    "Sastanak će se održati 5. listopada 2026.\n"
)


@pytest.fixture(scope="module", autouse=True)
def loaded_gazetteer(pg_db):
    """The place detector needs a gazetteer, and it is not this module's data.

    ``data/gazetteer/places_seed.json`` is reference data a deployment loads
    once. The suite empties the database between modules, so a module that reads
    places has to say so: before this fixture the tests passed only because
    ``test_places_pg`` happened to run earlier in the alphabetical order, and a
    reversed order failed with "no gazetteer is loaded".
    """
    from services.geo import gazetteer

    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            gazetteer.sync_seed(cur, loaded_by="test_suite_signal_ingestion")
        conn.commit()
    finally:
        conn.close()
    gazetteer._invalidate_cache()
    return True


@pytest.fixture(scope="module")
def tenant(pg_db):
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5,"
                    " CURRENT_DATE) RETURNING id", (f"sig_side_{_U}",))
        side_id = cur.fetchone()[0]
        cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                    " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id", (f"sig_src_{_U}",))
        source_id = cur.fetchone()[0]
    conn.close()
    return {"source_id": source_id, "side_id": side_id}


_N = iter(range(10_000))


def _store(tenant, raw_text=MULTILINGUAL, hash_value=None, source_id=None):
    from database.services.contents_db_service import ContentDBService

    n = next(_N)
    marker = f"{_U}sig{n}"
    return ContentDBService().process_full_document(
        hash_value=hash_value or hashlib.sha256(marker.encode()).hexdigest(),
        source_id=source_id or tenant["source_id"], side_id=tenant["side_id"],
        file_name=f"{marker}.txt", file_path=f"/tmp/sig/{marker}.txt", file_size=100,
        file_type="txt", file_status="Read", file_date=date(2026, 1, 1),
        content_words=["summit", "october", marker], raw_text=raw_text, attempts=1)


def _rows(pg_db, sql, params):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def test_ingestion_stores_multilingual_signals_with_offsets_into_stored_text(pg_db, tenant):
    result = _store(tenant)
    assert result["success"] and not result["warnings"], result
    hash_id = result["hash_id"]
    assert result["signals"]["status"] == "complete"

    stored = "".join(r[0] for r in _rows(
        pg_db, "SELECT content FROM contents_raw WHERE hash_id = %s ORDER BY chunk_seq",
        (hash_id,)))
    signals = _rows(pg_db, "SELECT signal_type, value, surface, char_start, char_end, language,"
                           " resolution, date_from, date_to, detector_ver, anchor_date"
                           " FROM content_signals WHERE hash_id = %s ORDER BY char_start",
                    (hash_id,))
    assert signals
    for sig in signals:
        assert stored[sig[3]:sig[4]] == sig[2], f"offsets do not address stored text: {sig}"
        assert sig[9] == DETECTOR_VERSION
    values = {s[1] for s in signals}
    assert "gregorian:2026-10-05" in values
    assert "hijri:1447-09-15" in values
    assert "jalali:1405-07-15" in values
    assert {s[5] for s in signals if s[1] == "gregorian:2026-10-05"} >= {"en", "ar", "he", "hr"}

    # No authored document date exists, so "Tomorrow" is kept but NOT resolved.
    tomorrow = [s for s in signals if s[1] == "rel:+1d"]
    assert tomorrow and tomorrow[0][6] == "unresolved"
    assert tomorrow[0][7] is None and tomorrow[0][10] is None

    stored = _rows(pg_db, "SELECT char_start, char_end, method, confidence, confidence_basis,"
                          " evidence_sentence, sentence_start, sentence_end"
                          " FROM content_signals WHERE hash_id = %s", (hash_id,))
    raw = "".join(r[0] for r in _rows(pg_db, "SELECT content FROM contents_raw WHERE hash_id = %s"
                                          " ORDER BY chunk_seq", (hash_id,)))
    for cs, ce, method, level, basis, sentence, ss, se in stored:
        assert method and level in ("high", "medium", "low") and basis
        assert raw[ss:se] == sentence and ss <= cs < ce <= se, "sentence quotes stored text"

    run = _rows(pg_db, "SELECT status, signal_count, trigger, detector_ver, error"
                       " FROM content_signal_runs WHERE hash_id = %s AND detector = 'temporal'", (hash_id,))
    assert run == [("complete", len(signals), "ingestion", DETECTOR_VERSION, None)]


def test_same_content_in_a_new_context_is_not_detected_twice(pg_db, tenant):
    first = _store(tenant)
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                    " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id",
                    (f"sig_src3_{_U}",))
        source_id = cur.fetchone()[0]
    conn.close()
    count_before = _rows(pg_db, "SELECT count(*) FROM content_signals WHERE hash_id = %s",
                         (first["hash_id"],))[0][0]
    # Re-store the *same bytes* (same hash) under another source.
    from database.services.contents_db_service import ContentDBService
    hash_value = _rows(pg_db, "SELECT hash FROM hashs WHERE id = %s", (first["hash_id"],))[0][0]
    again = ContentDBService().process_full_document(
        hash_value=hash_value, source_id=source_id, side_id=tenant["side_id"],
        file_name="copy.txt", file_path=f"/tmp/sig/copy_{_U}.txt", file_size=100,
        file_type="txt", file_status="Read", file_date=date(2026, 1, 2),
        content_words=["summit"], raw_text=MULTILINGUAL, attempts=1)
    assert again["hash_id"] == first["hash_id"] and again.get("content_reused")
    count_after = _rows(pg_db, "SELECT count(*) FROM content_signals WHERE hash_id = %s",
                        (first["hash_id"],))[0][0]
    assert count_after == count_before


def test_empty_text_is_recorded_as_no_text_not_as_nothing_found(pg_db, tenant):
    result = _store(tenant, raw_text="   ")
    run = _rows(pg_db, "SELECT status, signal_count FROM content_signal_runs WHERE hash_id = %s AND detector = 'temporal'",
                (result["hash_id"],))
    assert run == [("no_text", 0)]


def test_a_database_error_in_detection_is_contained_and_recorded(pg_db, tenant, monkeypatch):
    """A real PostgreSQL error (not a Python exception) inside step 10."""
    from services.detection import signal_store

    def broken(cur, hash_id, result, **kwargs):
        cur.execute("SELECT 1 / 0")  # division_by_zero: aborts to the savepoint

    monkeypatch.setattr(signal_store, "store_detection", broken)
    result = _store(tenant)
    monkeypatch.undo()
    assert result["success"], "a derived step must not take the document down"
    assert any("signals step failed" in w for w in result["warnings"])
    hash_id = result["hash_id"]
    status, detail = _rows(pg_db, "SELECT processing_status, status_detail FROM paths"
                                  " WHERE id = %s", (result["path_id"],))[0]
    assert status == "partially_processed" and "signals" in detail
    run = _rows(pg_db, "SELECT status, error FROM content_signal_runs WHERE hash_id = %s AND detector = 'temporal'",
                (hash_id,))
    assert run and run[0][0] == "failed" and "DivisionByZero" in run[0][1]
    assert _rows(pg_db, "SELECT count(*) FROM words_hashs WHERE hash_id = %s", (hash_id,))[0][0] > 0


def test_a_database_error_in_the_raw_text_step_is_contained(pg_db, tenant, monkeypatch):
    """Pins the fix to TransactionScope.savepoint: a real driver error inside a
    'contained' optional step used to escape as TransactionAbortedError and
    roll back the whole document, contrary to the documented contract."""
    from database.database.repository.contents_repo import ContentsRepository

    def failing_store(self, hash_id, text, chunk_size=1024 * 1024):
        with self.get_cursor(commit=False) as cur:
            cur.execute("SELECT 1 / 0")

    monkeypatch.setattr(ContentsRepository, "store_raw_content", failing_store)
    result = _store(tenant)
    monkeypatch.undo()
    assert result["success"] and result.get("path_id"), result
    status, detail = _rows(pg_db, "SELECT processing_status, status_detail FROM paths"
                                  " WHERE id = %s", (result["path_id"],))[0]
    assert status == "partially_processed" and "raw_text" in detail
    # Without stored text, signals are a recorded failure - not "none found".
    run = _rows(pg_db, "SELECT status, error FROM content_signal_runs WHERE hash_id = %s AND detector = 'temporal'",
                (result["hash_id"],))
    assert run[0][0] == "failed" and "display text was not stored" in run[0][1]


# --------------------------------------------------------------------------
# Re-detection through the existing JobManager
# --------------------------------------------------------------------------


def test_redetection_job_applies_the_current_version_and_is_idempotent(pg_db, tenant):
    from services.jobs.manager import JobManager

    stored = _store(tenant)
    hash_id = stored["hash_id"]
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        # Simulate content analysed by an older detector version.
        cur.execute("UPDATE content_signal_runs SET detector_ver = 'temporal-0.9.0'"
                    " WHERE hash_id = %s", (hash_id,))
        cur.execute("UPDATE content_signals SET detector_ver = 'temporal-0.9.0'"
                    " WHERE hash_id = %s", (hash_id,))
    conn.close()
    before = _rows(pg_db, "SELECT count(*) FROM content_signals WHERE hash_id = %s", (hash_id,))[0][0]

    manager = JobManager(synchronous=True)
    job = manager.create_job("signal_redetection", source="test",
                             options={"scope": "hash_ids", "hash_ids": [hash_id]})
    assert job["status"] == "COMPLETED", job
    assert job["stats"]["processed"] == 1 and job["stats"]["complete"] == 1
    versions = _rows(pg_db, "SELECT DISTINCT detector_ver FROM content_signals WHERE hash_id = %s",
                     (hash_id,))
    assert versions == [(DETECTOR_VERSION,)], "old-version signals must be replaced, not kept"
    after = _rows(pg_db, "SELECT count(*) FROM content_signals WHERE hash_id = %s", (hash_id,))[0][0]
    assert after == before
    run = _rows(pg_db, "SELECT trigger, job_id FROM content_signal_runs WHERE hash_id = %s AND detector = 'temporal'",
                (hash_id,))[0]
    assert run == ("redetection", job["job_id"])

    # A second run over 'stale' content does not touch it again.
    from Api.utils.utils import get_connection
    from services.detection.redetection import run_redetection
    again = run_redetection(get_connection, scope="stale")
    stale_ids = _rows(pg_db, "SELECT count(*) FROM content_signal_runs WHERE hash_id = %s"
                             " AND detector = 'temporal'"
                             " AND detector_ver <> %s", (hash_id, DETECTOR_VERSION))[0][0]
    assert stale_ids == 0
    second = run_redetection(get_connection, scope="stale")
    assert second.stats["processed"] == 0, "stale selection must converge"


def test_redetection_records_missing_content_as_a_failure(pg_db):
    from Api.utils.utils import get_connection
    from services.detection.redetection import run_redetection

    result = run_redetection(get_connection, scope="hash_ids", hash_ids=[2_000_000_000])
    assert result.stats["failed"] == 1 and result.warnings
    with pytest.raises(ValueError):
        run_redetection(get_connection, scope="hash_ids", hash_ids=[])


# --------------------------------------------------------------------------
# m0017 constraints
# --------------------------------------------------------------------------


def _insert_signal(cur, hash_id, **over):
    values = dict(hash_id=hash_id, detector="temporal", detector_ver="v", signal_type="date_reference",
                  value="x", surface="x", char_start=0, char_end=1, resolution="absolute",
                  date_from=date(2026, 1, 1), date_to=date(2026, 1, 1), anchor_date=None,
                  dedup_key=uuid.uuid4().hex + uuid.uuid4().hex)
    values.update(over)
    cur.execute(
        "INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type, value, surface,"
        " char_start, char_end, resolution, date_from, date_to, anchor_date, evidence, dedup_key)"
        " VALUES (%(hash_id)s, %(detector)s, %(detector_ver)s, %(signal_type)s, %(value)s,"
        " %(surface)s, %(char_start)s, %(char_end)s, %(resolution)s, %(date_from)s, %(date_to)s,"
        " %(anchor_date)s, '{}', %(dedup_key)s)", values)


def _constraint_definitions(conn, table):
    """Name -> normalised CHECK/FOREIGN KEY definition for ``table``.

    The frozen baseline writes its own constraint names and no application
    code reads them, so these tests assert the rule (the definition), not the
    name a migration happened to choose.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = %s::regclass AND contype IN ('c', 'f')", (table,))
        return {name: " ".join(definition.split()) for name, definition in cur.fetchall()}


def _assert_rejected_by_rule(conn, table, rule, insert):
    definitions = _constraint_definitions(conn, table)
    with conn.cursor() as cur:
        with pytest.raises(psycopg2.Error) as info:
            insert(cur)
    violated = info.value.diag.constraint_name
    matching = {name for name, definition in definitions.items() if rule in definition}
    assert matching, f"no constraint on {table} enforces {rule!r}"
    assert violated in matching, (
        f"the row was rejected by {violated!r}, whose definition is"
        f" {definitions.get(violated)!r}; the rule asked for {rule!r}")


@pytest.mark.parametrize("over, rule", [
    ({"resolution": "ambiguous"}, "= (date_from IS NOT NULL)"),
    ({"date_from": None, "date_to": None}, "= (date_from IS NOT NULL)"),
    ({"date_to": None}, "(date_from IS NULL) = (date_to IS NULL)"),
    ({"date_from": date(2026, 2, 1)}, "date_from <= date_to"),
    ({"resolution": "document_relative"}, "anchor_date IS NOT NULL"),
    ({"char_end": 0}, "char_end > char_start"),
    ({"dedup_key": "Z" * 64}, "dedup_key ~ '^[0-9a-f]{64}$'"),
    ({"signal_type": "Bad Type"}, "'date_reference'::character varying"),
])
def test_signal_constraints(pg_db, tenant, over, rule):
    hash_id = _store(tenant)["hash_id"]
    conn = connect(pg_db)
    try:
        _assert_rejected_by_rule(conn, "content_signals", rule,
                                 lambda cur: _insert_signal(cur, hash_id, **over))
    finally:
        conn.rollback()
        conn.close()


def test_dedup_key_is_unique_and_on_conflict_skips(pg_db, tenant):
    hash_id = _store(tenant)["hash_id"]
    key = "a" * 63 + "b"
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            _insert_signal(cur, hash_id, dedup_key=key)
            with pytest.raises(psycopg2.errors.UniqueViolation):
                _insert_signal(cur, hash_id, dedup_key=key)
        conn.rollback()
        with conn.cursor() as cur:
            _insert_signal(cur, hash_id, dedup_key=key)
            cur.execute("INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type,"
                        " value, surface, char_start, char_end, resolution, evidence, dedup_key)"
                        " VALUES (%s, 'temporal', 'v', 'orientation', 'x', 'x', 0, 1, 'unresolved',"
                        " '{}', %s) ON CONFLICT (dedup_key) DO NOTHING", (hash_id, key))
            assert cur.rowcount == 0
    finally:
        conn.rollback()
        conn.close()


def test_failed_run_requires_error_and_deleting_content_cascades(pg_db, tenant):
    hash_id = _store(tenant)["hash_id"]
    conn = connect(pg_db)
    try:
        _assert_rejected_by_rule(
            conn, "content_signal_runs", "= (error IS NOT NULL)",
            lambda cur: cur.execute("UPDATE content_signal_runs SET status = 'failed',"
                                    " error = NULL WHERE hash_id = %s", (hash_id,)))
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM content_signals WHERE hash_id = %s", (hash_id,))
            assert cur.fetchone()[0] > 0
            # Content deletion cascades to its signals and runs.
            cur.execute("DELETE FROM content_signals WHERE hash_id = %s", (hash_id,))
            cur.execute("SELECT count(*) FROM content_signal_runs WHERE hash_id = %s AND detector = 'temporal'", (hash_id,))
            assert cur.fetchone()[0] == 1
    finally:
        conn.rollback()
        conn.close()


# --------------------------------------------------------------------------
# API: read (authenticated, scope-checked) and re-detect (admin, JobManager)
# --------------------------------------------------------------------------


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def test_signals_api_returns_evidence_and_per_request_clock(pg_db, tenant, client_factory, client):
    hash_id = _store(tenant)["hash_id"]
    viewer = client_factory("viewer")

    before = viewer.get(f"/api/content/{hash_id}/signals?reference_date=2026-09-28")
    assert before.status_code == 200, before.get_data(as_text=True)
    body = before.get_json()
    assert body["status"] == "complete" and body["run"]["current_version"] is True
    assert body["reference_date"] == "2026-09-28" and body["reference_date_source"] == "request"
    summit = [s for s in body["signals"] if s["value"] == "gregorian:2026-10-05"]
    assert len(summit) == 4 and {s["clock_orientation"] for s in summit} == {"future"}
    assert {s["language"] for s in summit} == {"en", "ar", "he", "hr"}
    assert all(s["evidence"]["pattern"] for s in body["signals"])

    after = viewer.get(f"/api/content/{hash_id}/signals?reference_date=2027-01-01").get_json()
    assert {s["clock_orientation"] for s in after["signals"]
            if s["value"] == "gregorian:2026-10-05"} == {"past"}
    tomorrow = [s for s in after["signals"] if s["value"] == "rel:+1d"][0]
    assert tomorrow["clock_orientation"] is None, "unknown is not a guess"

    default = viewer.get(f"/api/content/{hash_id}/signals").get_json()
    assert default["reference_date_source"] == "server_utc_today"

    assert viewer.get(f"/api/content/{hash_id}/signals?reference_date=28/09/2026").status_code == 400
    assert viewer.get("/api/content/999999999/signals").status_code == 404
    assert client.get(f"/api/content/{hash_id}/signals").status_code in (401, 302)


def test_signals_for_enforces_the_access_scope_before_reading(pg_db, tenant):
    from core.criteria.compiler import AccessScope
    from services.detection.signal_store import signals_for

    hash_id = _store(tenant)["hash_id"]
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            ok = signals_for(cur, hash_id, date(2026, 1, 1),
                             scope=AccessScope(allowed_source_ids=(tenant["source_id"],)))
            assert ok["signals"]
            for denied in (AccessScope(allowed_source_ids=(tenant["source_id"] + 10_000,)),
                           AccessScope(allowed_source_ids=())):
                with pytest.raises(LookupError):
                    signals_for(cur, hash_id, date(2026, 1, 1), scope=denied)
            with pytest.raises(TypeError):
                signals_for(cur, hash_id, date(2026, 1, 1), scope=None)
    finally:
        conn.close()


def test_redetect_api_is_admin_only_validated_and_uses_the_job_manager(
        pg_db, tenant, client_factory, admin_client, monkeypatch):
    from services.jobs.manager import JobManager

    sync = JobManager(synchronous=True)
    monkeypatch.setattr(JobManager, "get_instance", classmethod(lambda cls: sync))
    hash_id = _store(tenant)["hash_id"]

    def post(c, payload):
        return c.post("/api/signals/redetect", json=payload,
                      headers={"X-CSRFToken": _csrf(c)})

    for role in ("viewer", "analyst"):
        assert post(client_factory(role), {"scope": "stale"}).status_code == 403

    for bad in ({"scope": "everything"}, {"scope": "hash_ids"}, {"scope": "hash_ids", "hash_ids": [0]},
                {"scope": "hash_ids", "hash_ids": ["1"]}, {"scope": "hash_ids", "hash_ids": [True]},
                {"scope": "stale", "hash_ids": [1]}):
        resp = post(admin_client, bad)
        assert resp.status_code == 400, (bad, resp.get_data(as_text=True))

    resp = post(admin_client, {"scope": "hash_ids", "hash_ids": [hash_id, hash_id]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    job = resp.get_json()["job"]
    assert job["job_type"] == "signal_redetection" and job["status"] == "COMPLETED"
    run = _rows(pg_db, "SELECT trigger, job_id FROM content_signal_runs WHERE hash_id = %s AND detector = 'temporal'",
                (hash_id,))[0]
    assert run == ("redetection", job["job_id"])
