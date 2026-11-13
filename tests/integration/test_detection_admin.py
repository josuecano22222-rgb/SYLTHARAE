"""Detection page backend: services/detection/detection_admin.py and the
admin routes in Api/routes/signals.py, against PostgreSQL.

The session database is shared, so counts are checked relative to what each
test adds, and the central check is behavioural: the stale count the page
shows equals the number of contents a real ``stale`` re-detection job then
processes.
"""
from __future__ import annotations

import uuid

import psycopg2.extras
import pytest

from _seed import connect, document, side, source


def _docs(pg_db, n, text="Meeting on 12 March 2031 in the capital.", paths=1):
    conn = connect(pg_db)
    out = []
    with conn, conn.cursor() as cur:
        src, sd = source(cur), side(cur)
        for _ in range(n):
            path_id, hash_id, _ = document(cur, source_id=src, side_id=sd, text=text,
                                           file_name=f"det_{uuid.uuid4().hex[:6]}.txt")
            for _ in range(paths - 1):
                document(cur, source_id=src, side_id=sd, text=text, hash_id=hash_id)
            out.append((path_id, hash_id))
    conn.close()
    return out


def _run_row(pg_db, hash_id, detector, version, status, error=None, signals=0):
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO content_signal_runs (hash_id, detector, detector_ver, status,"
                    " signal_count, trigger, error) VALUES (%s, %s, %s, %s, %s, 'redetection', %s)"
                    " ON CONFLICT (hash_id, detector) DO UPDATE SET detector_ver = EXCLUDED.detector_ver,"
                    " status = EXCLUDED.status, error = EXCLUDED.error, ran_at = NOW()",
                    (hash_id, detector, version, status, signals, error))
    conn.close()


def _temporal(status):
    return next(d for d in status["detectors"] if d["detector"] == "temporal")


def test_stale_count_is_what_the_job_processes(pg_db, app):
    from Api.utils.utils import get_connection
    from services.detection import detection_admin as da
    from services.detection.redetection import run_redetection

    _docs(pg_db, 3)
    conn = connect(pg_db)
    before = da.detection_status(conn)
    t = _temporal(before)
    assert before["exact"] is True and before["snapshot"]
    assert t["never_analysed"] >= 3 and t["stale"] >= t["never_analysed"]
    assert before["analysable_contents"] >= t["stale"]

    result = run_redetection(get_connection, scope="stale", detectors=["temporal"])
    assert result.stats["processed"] == t["stale"], (result.stats, t)

    after = _temporal(da.detection_status(conn))
    conn.close()
    assert after["stale"] == 0 and after["never_analysed"] == 0
    # Never-analysed and older-version content moved into the current version;
    # failed-at-current runs were replaced in place (same bucket total).
    delta_current = sum(after["at_current_version"].values()) - sum(t["at_current_version"].values())
    delta_older = sum(t["at_older_versions"].values()) - sum(after["at_older_versions"].values())
    assert delta_current == t["never_analysed"] + delta_older


@pytest.mark.parametrize("versions", [
    "current",                                   # the real current versions
    {"temporal": "temporal-0.0-test-old", "places": "places-x"},  # an arbitrary version
    {"temporal": None, "places": None},          # version unavailable (no gazetteer)
])
def test_coverage_counts_equal_the_jobs_selection_on_every_run_state(pg_db, app, versions):
    """_coverage_counts computes the job's NOT EXISTS predicate as a LEFT JOIN
    (valid because of PRIMARY KEY (hash_id, detector)). Each count must equal
    what the job's own selection SQL returns, over every run state."""
    from services.detection import detection_admin as da
    from services.detection import detectors as registry
    from services.detection import redetection as rd

    docs = _docs(pg_db, 7)
    conn = connect(pg_db)
    with conn.cursor() as cur:
        current = {n: registry.get(n).version(cur) for n in registry.NAMES}
    conn.rollback()
    states = [None, ("old", "complete"), ("cur", "complete"), ("cur", "failed"),
              ("cur", "truncated"), ("cur", "no_text"), ("old", "failed")]
    for i, ((_, hash_id), state) in enumerate(zip(docs, states)):
        for j, name in enumerate(registry.NAMES):
            st = states[(i + j * 3) % len(states)]     # detectors differ per content
            if st is None or current[name] is None and st[0] == "cur":
                continue
            ver = current[name] if st[0] == "cur" else f"{name}-0.0-coverage-old"
            _run_row(pg_db, hash_id, name, ver, st[1],
                     error="x" if st[1] == "failed" else None)
    vers = current if versions == "current" else versions

    with conn.cursor() as cur:
        def job_selects(v):
            sql, params = rd._select_sql("stale", v)
            cur.execute(sql, (0, *params, 10 ** 9))
            return len(cur.fetchall())

        cur.execute("SELECT count(*) FROM hashs h WHERE " + rd.HAS_TEXT)
        analysable = cur.fetchone()[0]
        expected = {"analysable": analysable, "stale_any": job_selects(vers)}
        for name, v in vers.items():
            expected[f"stale_{name}"] = job_selects({name: v})
            cur.execute("SELECT count(*) FROM hashs h WHERE " + rd.HAS_TEXT
                        + " AND NOT EXISTS (SELECT 1 FROM content_signal_runs r"
                          " WHERE r.hash_id = h.id AND r.detector = %s)", (name,))
            expected[f"never_{name}"] = cur.fetchone()[0]
        got = da._coverage_counts(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor), vers)
    conn.rollback()
    conn.close()
    assert got == expected
    if versions == {"temporal": None, "places": None}:
        assert got["stale_temporal"] == got["stale_places"] == got["stale_any"] == analysable


def test_older_and_failed_runs_are_stale_and_listed(pg_db, app):
    from services.detection import detection_admin as da
    from services.detection import detectors as registry

    (_, old_hash), (_, failed_hash) = _docs(pg_db, 2)
    conn = connect(pg_db)
    with conn.cursor() as cur:
        current = registry.get("temporal").version(cur)
    conn.rollback()
    before = _temporal(da.detection_status(conn))
    _run_row(pg_db, old_hash, "temporal", "temporal-0.0-test-old", "complete", signals=2)
    _run_row(pg_db, failed_hash, "temporal", current, "failed", error="boom: <b>not markup</b>")
    after = _temporal(da.detection_status(conn))
    assert after["never_analysed"] == before["never_analysed"] - 2
    assert after["stale"] == before["stale"]              # still stale: older / failed
    assert after["at_older_versions"]["complete"] == before["at_older_versions"]["complete"] + 1
    assert after["at_current_version"]["failed"] == before["at_current_version"]["failed"] + 1

    def runs(**f):
        return da.list_runs(conn, da.parse_run_filters(dict(f, limit="200")))["items"]

    older = runs(detector="temporal", version="older")
    assert old_hash in [r["hash_id"] for r in older]
    assert all(r["detector_ver"] != current and r["is_current_version"] is False for r in older)
    assert old_hash not in [r["hash_id"] for r in runs(detector="temporal", version="current")]
    failed = runs(status="failed", hash_id=str(failed_hash))
    assert len(failed) == 1 and failed[0]["error"] == "boom: <b>not markup</b>"
    assert failed[0]["signal_count"] == 0 and failed[0]["trigger"] == "redetection"
    exact = runs(version="temporal-0.0-test-old")
    assert [r["hash_id"] for r in exact] == [old_hash]
    conn.close()


def test_run_rows_name_a_file_and_count_the_others(pg_db, app):
    from services.detection import detection_admin as da

    [(path_id, hash_id)] = _docs(pg_db, 1, paths=3)
    _run_row(pg_db, hash_id, "temporal", "temporal-x", "complete")
    conn = connect(pg_db)
    [row] = da.list_runs(conn, da.parse_run_filters({"hash_id": str(hash_id), "detector": "temporal"}))["items"]
    conn.close()
    assert row["path_id"] == path_id and row["path_count"] == 3
    assert row["file_name"].startswith("det_")


def test_run_without_a_file_is_listed_with_none(pg_db, app):
    from services.detection import detection_admin as da

    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO hashs (hash) VALUES (%s) RETURNING id", (uuid.uuid4().hex * 2,))
        orphan = cur.fetchone()[0]
    conn.close()
    _run_row(pg_db, orphan, "temporal", "temporal-x", "no_text")
    conn = connect(pg_db)
    [row] = da.list_runs(conn, da.parse_run_filters({"hash_id": str(orphan)}))["items"]
    conn.close()
    assert row["path_id"] is None and row["file_name"] is None and row["path_count"] == 0


def test_paging_states_more_exactly(pg_db, app):
    from services.detection import detection_admin as da

    docs = _docs(pg_db, 3)
    version = f"temporal-page-{uuid.uuid4().hex[:6]}"
    for _, h in docs:
        _run_row(pg_db, h, "temporal", version, "complete")
    conn = connect(pg_db)
    first = da.list_runs(conn, da.parse_run_filters({"version": version, "limit": "2"}))
    second = da.list_runs(conn, da.parse_run_filters({"version": version, "limit": "2", "offset": "2"}))
    exact = da.list_runs(conn, da.parse_run_filters({"version": version, "limit": "3"}))
    conn.close()
    assert first["has_more"] is True and len(first["items"]) == 2
    assert second["has_more"] is False and len(second["items"]) == 1
    assert exact["has_more"] is False and len(exact["items"]) == 3
    ids = [r["hash_id"] for r in first["items"] + second["items"]]
    assert sorted(ids) == sorted(h for _, h in docs) and len(set(ids)) == 3
    assert first["total"] is None and "not counted" in first["total_reason"]


@pytest.mark.parametrize("args,needle", [
    ({"detector": "ner"}, "detector must be one of"),
    ({"status": "ok"}, "status must be one of"),
    ({"trigger": "cron"}, "trigger must be one of"),
    ({"version": "current"}, "needs a detector"),
    ({"limit": "0"}, "limit"), ({"limit": "201"}, "at most 200"),
    ({"offset": "-1"}, "offset"), ({"offset": "100001"}, "at most 100000"),
    ({"hash_id": "x"}, "hash_id"), ({"sort": "asc"}, "unknown filter"),
])
def test_invalid_run_filters_are_refused(args, needle):
    from services.detection import detection_admin as da

    with pytest.raises(da.DetectionAdminError) as exc:
        da.parse_run_filters(args)
    assert exc.value.status == 400 and needle in exc.value.message


def test_unavailable_current_version_is_409_not_a_guess(pg_db, app, monkeypatch):
    from services.detection import detection_admin as da
    from services.detection import detectors as registry

    monkeypatch.setattr(type(registry.get("places")), "version", lambda self, cur: None)
    conn = connect(pg_db)
    with pytest.raises(da.DetectionAdminError) as exc:
        da.list_runs(conn, da.parse_run_filters({"detector": "places", "version": "current"}))
    status = da.detection_status(conn)
    conn.close()
    assert exc.value.status == 409 and exc.value.code == "VERSION_UNAVAILABLE"
    places = next(d for d in status["detectors"] if d["detector"] == "places")
    assert places["current_version"] is None and places["current_version_available"] is False
    # unknown version: nothing counts as current, every analysable content is stale
    assert places["stale"] == status["analysable_contents"]


# -------------------------------------------------------------------- API


@pytest.fixture()
def sync_jobs(monkeypatch):
    from services.jobs.manager import JobManager

    sync = JobManager(synchronous=True)
    monkeypatch.setattr(JobManager, "get_instance", classmethod(lambda cls: sync))
    return sync


def test_api_status_runs_and_page_for_admin(pg_db, admin_client):
    resp = admin_client.get("/api/signals/detection/status")
    assert resp.status_code == 200
    body = resp.get_json()
    # The three detectors the registry registers (Api/routes/signals.py names
    # them: temporal, places, identifiers).
    assert {d["detector"] for d in body["detectors"]} == {"temporal", "places", "identifiers"}
    runs = admin_client.get("/api/signals/detection/runs?limit=5").get_json()
    assert runs["success"] is True and len(runs["items"]) <= 5
    bad = admin_client.get("/api/signals/detection/runs?status=bogus")
    assert bad.status_code == 400 and "status must be one of" in bad.get_json()["error"]["message"]
    page = admin_client.get("/signals/detection")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'id="detectionPage"' in html and "js/pages/detection-page.js" in html


@pytest.mark.parametrize("role", ["analyst", "viewer"])
def test_api_and_page_refuse_non_admins(client_factory, role):
    c = client_factory(role)
    for url in ("/api/signals/detection/status", "/api/signals/detection/runs", "/signals/detection"):
        assert c.get(url).status_code in (302, 403), url


def test_redetect_is_audited_with_its_request(pg_db, admin_client, admin_credentials, sync_jobs):
    [(_, hash_id)] = _docs(pg_db, 1)
    resp = admin_client.post("/api/signals/redetect",
                             json={"scope": "hash_ids", "hash_ids": [hash_id], "detectors": ["temporal"]})
    assert resp.status_code in (200, 202), resp.get_json()
    job_id = resp.get_json()["job"]["job_id"]
    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT username, detail FROM audit_log WHERE action = 'signals.redetect'"
                    " AND resource = %s", (f"signal_redetection:{job_id}",))
        rows = cur.fetchall()
        cur.execute("SELECT status, trigger, job_id FROM content_signal_runs"
                    " WHERE hash_id = %s AND detector = 'temporal'", (hash_id,))
        run = cur.fetchone()
    conn.close()
    assert len(rows) == 1
    username, detail = rows[0]
    assert username == admin_credentials[0]
    assert detail == {"scope": "hash_ids", "detectors": ["temporal"], "hash_id_count": 1}
    assert run[0] in ("complete", "truncated") and run[1] == "redetection" and run[2] == job_id
