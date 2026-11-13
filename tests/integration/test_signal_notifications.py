"""Future-date notifications come from stored temporal signals (Phase 1).

The legacy English-only, wall-clock analyzer (core/monitoring/future_events)
was removed; both notification entry points now read content_signals.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date

import pytest

from _seed import connect

from core.detection.temporal_intel import DETECTOR_VERSION

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]
_N = iter(range(10_000))

# 5 Oct 2026 in four languages (one date), 15 Mehr 1405 = 7 Oct 2026 (Jalali),
# 15 Ramadan 1447 ~ Mar 2026 (past), and "tomorrow" (unresolved - no anchor).
TEXT = (
    "The summit will be held on 5 October 2026. Tomorrow we decide.\n"
    "سيعقد المؤتمر في ٥ أكتوبر ٢٠٢٦ حتى ١٥ رمضان ١٤٤٧ هـ.\n"
    "הכנס יתקיים ב-5 באוקטובר 2026.\n"
    "نشست در ۱۵ مهر ۱۴۰۵ برگزار خواهد شد.\n"
    "Sastanak će se održati 5. listopada 2026.\n"
)


@pytest.fixture(scope="module")
def tenant(pg_db):
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5,"
                    " CURRENT_DATE) RETURNING id", (f"sn_side_{_U}",))
        side_id = cur.fetchone()[0]
        cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                    " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id", (f"sn_src_{_U}",))
        source_id = cur.fetchone()[0]
    conn.close()
    return {"source_id": source_id, "side_id": side_id}


def _store(tenant, text):
    from database.services.contents_db_service import ContentDBService

    marker = f"{_U}sn{next(_N)}"
    result = ContentDBService().process_full_document(
        hash_value=hashlib.sha256(marker.encode()).hexdigest(), source_id=tenant["source_id"],
        side_id=tenant["side_id"], file_name=f"{marker}.txt",
        file_path=f"/tmp/sn/{marker}.txt", file_size=100, file_type="txt",
        file_status="Read", file_date=date(2026, 1, 1), content_words=["x", marker],
        raw_text=text, attempts=1)
    conn = connect(pg_db_ref["db"])
    with conn.cursor() as cur:
        cur.execute("SELECT p.id FROM paths p JOIN hash_contexts hc ON hc.id = p.context_id"
                    " WHERE hc.hash_id = %s", (result["hash_id"],))
        path_id = cur.fetchone()[0]
    conn.close()
    return result["hash_id"], path_id


pg_db_ref = {}


@pytest.fixture(autouse=True)
def _remember(pg_db):
    pg_db_ref["db"] = pg_db


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _post(client, url, payload=None):
    return client.post(url, json=payload or {}, headers={"X-CSRFToken": _csrf(client)})


def _alerts(pg_db, file_id):
    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT event_date, metadata FROM alerts WHERE type = 'future_date'"
                    " AND file_id = %s ORDER BY event_date", (file_id,))
        rows = cur.fetchall()
    conn.close()
    return rows


def test_analyze_file_uses_stored_multilingual_signals_and_dedups(pg_db, tenant, client_factory):
    _, path_id = _store(tenant, TEXT)
    analyst = client_factory("analyst")

    resp = _post(analyst, f"/api/notifications/analyze-file/{path_id}?reference_date=2026-09-28")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["reference_date"] == "2026-09-28" and body["signal_status"] == "complete"
    assert [n["event_date"] for n in body["notifications"]] == ["2026-10-05", "2026-10-07"]
    assert all(n["id"] > 0 for n in body["notifications"]), "real ids after flush"

    rows = _alerts(pg_db, path_id)
    assert [r[0] for r in rows] == [date(2026, 10, 5), date(2026, 10, 7)]
    jalali = rows[1][1]
    assert jalali["calendar"] == "jalali" and jalali["language"] == "fa"
    assert jalali["detector_ver"] == DETECTOR_VERSION and jalali["reference_date"] == "2026-09-28"
    assert jalali["days_until"] == 9
    assert (jalali["confidence"], jalali["confidence_basis"]) == ("high", "month_name")
    assert jalali["evidence_sentence"] == "نشست در ۱۵ مهر ۱۴۰۵ برگزار خواهد شد."

    again = _post(analyst, f"/api/notifications/analyze-file/{path_id}?reference_date=2026-09-28")
    assert again.get_json()["notifications_created"] == 0, "dedup by (file, event_date)"

    # With a later clock, only what is still ahead would qualify; nothing new.
    later = _post(analyst, f"/api/notifications/analyze-file/{path_id}?reference_date=2026-10-06")
    assert later.get_json()["notifications_created"] == 0


def test_analyze_file_is_write_access_only_and_validates(pg_db, tenant, client_factory):
    _, path_id = _store(tenant, TEXT)
    viewer = client_factory("viewer")
    assert _post(viewer, f"/api/notifications/analyze-file/{path_id}").status_code == 403
    assert _post(viewer, "/api/notifications/scan").status_code == 403
    analyst = client_factory("analyst")
    assert _post(analyst, f"/api/notifications/analyze-file/{path_id}"
                          "?reference_date=28-09-2026").status_code == 400
    assert _post(analyst, "/api/notifications/analyze-file/999999999").status_code == 404


def test_content_never_analysed_is_analysed_on_demand(pg_db, tenant, client_factory):
    hash_id, path_id = _store(tenant, TEXT)
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:  # as if ingested before m0017
        cur.execute("DELETE FROM content_signals WHERE hash_id = %s", (hash_id,))
        cur.execute("DELETE FROM content_signal_runs WHERE hash_id = %s", (hash_id,))
    conn.close()
    resp = _post(client_factory("analyst"),
                 f"/api/notifications/analyze-file/{path_id}?reference_date=2026-09-28")
    assert resp.get_json()["notifications_created"] == 2
    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT status, trigger FROM content_signal_runs WHERE hash_id = %s",
                    (hash_id,))
        assert cur.fetchone() == ("complete", "redetection")
    conn.close()


def test_scan_reads_stored_signals_and_reports_unanalysed_content(pg_db, tenant, admin_client):
    _, far_path = _store(tenant, "The treaty expires on 1 January 2099.")
    unanalysed_hash, unanalysed_path = _store(tenant, "Signed on 2 February 2098.")
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM content_signal_runs WHERE hash_id = %s", (unanalysed_hash,))
        cur.execute("DELETE FROM content_signals WHERE hash_id = %s", (unanalysed_hash,))
    conn.close()

    body = _post(admin_client, "/api/notifications/scan").get_json()
    assert body["success"] is True, body
    assert body["contents_without_current_signals"] >= 1, "unknown is reported, not zero"
    far = _alerts(pg_db, far_path)
    assert [r[0] for r in far] == [date(2099, 1, 1)]
    assert far[0][1]["evidence_sentence"] == "The treaty expires on 1 January 2099."
    assert far[0][1]["confidence"] == "high" and far[0][1]["method"] == "day_month_year"
    assert _alerts(pg_db, unanalysed_path) == [], "no signal -> no guessed notification"
    first = [n for n in body["notifications_created"] if n.get("date") == "2099-01-01"]
    assert first and first[0]["id"] > 0

    again = _post(admin_client, "/api/notifications/scan").get_json()
    assert not [n for n in again["notifications_created"] if n.get("date") == "2099-01-01"]


def test_the_future_dates_setting_is_read_from_the_real_settings_object():
    # The previous code called a non-existent get_setting(); the exception was
    # swallowed and the default returned, so the toggle never applied. A
    # sentinel default proves the stored value is what comes back.
    from settings import get_settings

    value = get_settings().get("notifications", "future_dates_enabled", "SENTINEL")
    assert isinstance(value, bool)


def test_disabling_future_dates_stops_both_entry_points(pg_db, tenant, client_factory,
                                                        admin_client, monkeypatch):
    import settings as settings_pkg

    real = settings_pkg.get_settings()

    class Disabled:
        def get(self, category=None, key=None, default=None):
            if (category, key) == ("notifications", "future_dates_enabled"):
                return False
            return real.get(category, key, default)

    monkeypatch.setattr(settings_pkg, "get_settings", lambda: Disabled())
    _, path_id = _store(tenant, "Deadline 3 March 2097.")
    body = _post(client_factory("analyst"),
                 f"/api/notifications/analyze-file/{path_id}").get_json()
    assert body["skipped"] == "future_dates_disabled" and body["notifications_created"] == 0
    scan = _post(admin_client, "/api/notifications/scan").get_json()
    assert scan["contents_without_current_signals"] is None
    assert _alerts(pg_db, path_id) == []
