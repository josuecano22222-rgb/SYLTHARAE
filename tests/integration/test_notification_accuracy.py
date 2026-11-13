"""Integration: notification accuracy end-to-end (real app + real PostgreSQL).

Reproduces the evidence from the user's log and asserts the fixed behaviour:

* 5 notifications flushed -> the table holds 5 rows, ``/stats`` reports the
  table's numbers, ``/api/notifications/paginated`` reports a
  ``summary.total`` of 5, and a concurrent refresh storm cannot inflate the
  in-memory view to 10.
* ``POST /api/notifications/scan`` returns REAL database ids (the response
  used to be built before the flush and carried temporary negative ids),
  creates the duplicate notification exactly once, and - after the user
  dismisses it - does NOT resurrect it on the next scan (the old existence
  check filtered on ``dismissed = FALSE``).
* Source filtering resolves through ``paths -> hash_contexts`` (m0011) in a
  single SQL join.
* ``per_page`` is clamped server-side at 1000 (the frontend's page size).
"""

from __future__ import annotations

import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]
_ACC_TITLE = f"Accuracy Probe {_UNIQUE}"
_DUP_HASH = f"acc-dup-hash-{_UNIQUE}"
_DUP_A = f"acc-dup-a-{_UNIQUE}.txt"
_DUP_B = f"acc-dup-b-{_UNIQUE}.txt"


def _connect(pg_db):
    return psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )


def _scalar(conn, query, params=None):
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return row[0] if row else None


@pytest.fixture(scope="module")
def duplicate_pair(pg_db):
    """Two paths sharing one content context - the scan's duplicate input."""
    path_ids = []
    conn = _connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation)"
                " VALUES (%s, 1.0, CURRENT_DATE) RETURNING id",
                (f"acc-side-{_UNIQUE}",),
            )
            side_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES (%s, 'test', 1.0, 'NL', CURRENT_DATE) RETURNING id",
                (f"acc-source-{_UNIQUE}",),
            )
            source_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                (_DUP_HASH,),
            )
            hash_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                " VALUES (%s, %s, %s) RETURNING id",
                (hash_id, source_id, side_id),
            )
            context_id = cur.fetchone()[0]
            for name in (_DUP_A, _DUP_B):
                cur.execute(
                    """
                    INSERT INTO paths (file_name, file_path, file_size, file_type,
                                       file_status, file_date, date_creation, context_id)
                    VALUES (%s, %s, 10, 'txt', 'Read', CURRENT_DATE, CURRENT_DATE, %s)
                    RETURNING id
                    """,
                    (name, f"/acc/{name}", context_id),
                )
                path_ids.append(cur.fetchone()[0])
        conn.commit()
        yield {"path_ids": path_ids, "source_id": source_id, "side_id": side_id}
    finally:
        # Remove seeded rows; alerts referencing them cascade (FK ON DELETE).
        with conn.cursor() as cur:
            cur.execute("DELETE FROM paths WHERE id = ANY(%s)", (path_ids,))
        conn.commit()
        conn.close()


@pytest.fixture(scope="module", autouse=True)
def cleanup_alerts(pg_db):
    yield
    conn = _connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM alerts
                WHERE title LIKE %s
                   OR title LIKE %s
                   OR TRIM(COALESCE(metadata->>'hash', '')) = %s
                """,
                (f"%{_UNIQUE}%", f"%{_DUP_A}%", _DUP_HASH),
            )
        conn.commit()
    finally:
        conn.close()


def test_flush_stats_and_pages_agree_with_the_database(admin_client, pg_db):
    """The '10 loaded vs 5 rows' scenario: everything must equal the table."""
    from core.monitoring.notification_service import (
        Notification,
        NotificationPriority,
        NotificationType,
        get_notification_service,
    )

    service = get_notification_service()
    for i in range(5):
        service._save_notification(
            Notification(
                id=None,
                type=NotificationType.INFO,
                priority=NotificationPriority.LOW,
                title=f"{_ACC_TITLE} #{i}",
                message=f"accuracy probe {i}",
                file_id=None,
                event_date=None,
                metadata={"probe": _UNIQUE},
                created_at=datetime.now(),
            )
        )

    written = service.flush_pending_notifications()
    assert written == 5, "all five queued notifications must persist"
    assert service.get_pending_count() == 0

    conn = _connect(pg_db)
    try:
        db_total = _scalar(conn, "SELECT COUNT(*) FROM alerts WHERE dismissed = FALSE")
        db_unread = _scalar(
            conn,
            "SELECT COUNT(*) FROM alerts WHERE dismissed = FALSE AND NOT read",
        )
        db_probe = _scalar(
            conn,
            "SELECT COUNT(*) FROM alerts WHERE title LIKE %s",
            (f"{_ACC_TITLE}%",),
        )
    finally:
        conn.close()
    assert db_probe == 5, "flush must write exactly the five queued rows"

    # Service stats: exact equality with the table (no in-memory inflation).
    stats = service.get_stats()
    assert stats["total"] == db_total
    assert stats["unread"] == db_unread

    # API stats endpoint: same numbers.
    resp = admin_client.get("/api/notifications/stats")
    assert resp.status_code == 200
    api_stats = resp.get_json()["stats"]
    assert api_stats["total"] == db_total
    assert api_stats["unread"] == db_unread

    # Paginated endpoint: exact summary for a search filter.
    resp = admin_client.get(
        "/api/notifications/paginated",
        query_string={"search": _ACC_TITLE, "per_page": 1000, "show_read": "true"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["summary"]["total"] == 5
    assert body["summary"]["unread"] == 5
    assert body["pagination"]["total"] == 5
    assert len(body["notifications"]) == 5
    # Titles pass through the shared formatter untouched (INFO stored text).
    titles = {n["title"] for n in body["notifications"]}
    assert titles == {f"{_ACC_TITLE} #{i}" for i in range(5)}

    # Paging is SQL-side and stable: two pages of 3 + 2, no overlap.
    page_ids = []
    for page in (1, 2):
        resp = admin_client.get(
            "/api/notifications/paginated",
            query_string={"search": _ACC_TITLE, "per_page": 3, "page": page,
                          "read_status": "all"},
        )
        body = resp.get_json()
        assert body["pagination"]["total"] == 5
        page_ids.extend(n["id"] for n in body["notifications"])
    assert len(page_ids) == 5 and len(set(page_ids)) == 5

    # Single-notification lookup goes straight to the row.
    resp = admin_client.get(f"/api/notifications/{page_ids[0]}")
    assert resp.status_code == 200
    assert resp.get_json()["notification"]["id"] == page_ids[0]


def test_concurrent_refreshes_cannot_inflate_the_in_memory_view(admin_client, pg_db):
    """Ten concurrent refreshes of a N-row table still yield N rows, not 2N."""
    from core.monitoring.notification_service import get_notification_service

    service = get_notification_service()
    conn = _connect(pg_db)
    try:
        db_total = _scalar(conn, "SELECT COUNT(*) FROM alerts WHERE dismissed = FALSE")
    finally:
        conn.close()
    assert db_total >= 5

    threads = [
        threading.Thread(target=service.refresh_notifications)
        for _ in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads), "refresh threads deadlocked"

    assert len(service._notifications) == min(db_total, 1000) + service.get_pending_count(), (
        "concurrent refreshes must swap atomically - the old clear()+append "
        "race doubled rows in memory (the '10 loaded' vs 5 rows evidence)"
    )


def test_scan_returns_real_ids_and_is_sticky_after_dismiss(admin_client, pg_db, duplicate_pair):
    assert len(duplicate_pair["path_ids"]) == 2

    # --- first scan creates the duplicate notification with a REAL id ---
    resp = admin_client.post("/api/notifications/scan")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True

    created = [
        c for c in body.get("notifications_created", [])
        if c["type"] == "duplicate" and _UNIQUE in c.get("title", "")
    ]
    assert len(created) == 1, f"expected exactly one new duplicate notification, got {created}"
    notif_id = created[0]["id"]
    assert notif_id > 0, (
        f"scan response carried a temporary id ({notif_id}): the response "
        "must be built AFTER the flush"
    )

    # The row exists with the accurate duplicate_count.
    conn = _connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT type, metadata->>'duplicate_count', dismissed FROM alerts WHERE id = %s",
                (notif_id,),
            )
            row = cur.fetchone()
        assert row is not None, "scanned notification must exist in the database"
        assert row[0] == "similar_files"
        assert row[1] == "2"
        assert row[2] is False

        db_total = _scalar(conn, "SELECT COUNT(*) FROM alerts WHERE dismissed = FALSE")
    finally:
        conn.close()

    # Stats agree with the table immediately after the scan.
    resp = admin_client.get("/api/notifications/stats")
    assert resp.get_json()["stats"]["total"] == db_total

    # The displayed message is the duplicate message with the real count.
    resp = admin_client.get(f"/api/notifications/{notif_id}")
    shown = resp.get_json()["notification"]
    assert "Found 2 duplicate file(s)" in shown["message"]
    assert "similar" not in shown["message"].lower()

    # Source/side filtering runs in ONE SQL join (the old implementation
    # issued a get_file + hashs query PER notification - an N+1 pattern).
    # The scan notification has no source_id in metadata, so it must be
    # found through paths -> hash_contexts.
    resp = admin_client.get(
        "/api/notifications/paginated",
        query_string={"source_id": duplicate_pair["source_id"], "per_page": 1000,
                      "show_read": "true"},
    )
    body = resp.get_json()
    matching_ids = {n["id"] for n in body["notifications"]}
    assert notif_id in matching_ids, (
        "source filter must resolve source_id through paths->hash_contexts "
        "when metadata does not carry one"
    )

    resp = admin_client.get(
        "/api/notifications/paginated",
        query_string={"source_id": 987654321, "per_page": 1000, "show_read": "true"},
    )
    body = resp.get_json()
    matching_ids = {n["id"] for n in body["notifications"]}
    assert notif_id not in matching_ids, (
        "strict filtering must exclude notifications whose source cannot be "
        "resolved"
    )

    # --- second scan: no duplicate created for the same hash ---
    resp = admin_client.post("/api/notifications/scan")
    body2 = resp.get_json()
    recreated = [
        c for c in body2.get("notifications_created", [])
        if c["type"] == "duplicate" and _UNIQUE in c.get("title", "")
    ]
    assert recreated == [], "scan must not create a second notification for the same hash"

    # --- dismiss via API ---
    resp = admin_client.post(f"/api/notifications/{notif_id}/dismiss")
    assert resp.status_code == 200, resp.get_data(as_text=True)

    # Dismissed notification disappears from the page (search by the
    # duplicate's primary file name).
    resp = admin_client.get(
        "/api/notifications/paginated",
        query_string={"search": _DUP_A, "per_page": 1000, "show_read": "true"},
    )
    body = resp.get_json()
    assert body["summary"]["total"] == 0, "dismissed notification must not be listed"

    # --- third scan: dismissed notification is NOT resurrected ---
    resp = admin_client.post("/api/notifications/scan")
    body3 = resp.get_json()
    resurrected = [
        c for c in body3.get("notifications_created", [])
        if c["type"] == "duplicate" and _UNIQUE in c.get("title", "")
    ]
    assert resurrected == [], (
        "a dismissed duplicate must stay dismissed: the old existence check "
        "filtered on dismissed = FALSE and re-created it on every scan"
    )

    # And the row itself is still marked dismissed (not re-inserted).
    conn = _connect(pg_db)
    try:
        count = _scalar(
            conn,
            "SELECT COUNT(*) FROM alerts WHERE TRIM(COALESCE(metadata->>'hash', '')) = %s",
            (_DUP_HASH,),
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT dismissed FROM alerts WHERE TRIM(COALESCE(metadata->>'hash', '')) = %s",
                (_DUP_HASH,),
            )
            state = cur.fetchone()
    finally:
        conn.close()
    assert count == 1, "exactly one notification row for the hash may exist"
    assert state is not None and state[0] is True, "the surviving row must be the dismissed one"


def test_per_page_is_clamped_to_the_frontend_page_size(admin_client):
    resp = admin_client.get(
        "/api/notifications/paginated",
        query_string={"per_page": 999999},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["pagination"]["per_page"] == 1000


def test_alert_indexes_exist(pg_db):
    """The notification read paths are indexed - on the frozen index names.

    BEFORE this asserted ``idx_alerts_dismissed_created``, an index that the
    frozen baseline does not declare: m0001_baseline.py creates
    ``idx_alerts_recipient_dismissed_created`` (recipient_user_id, dismissed,
    created_at DESC) instead, which covers the paginated per-recipient query
    the route actually runs. The other three names are unchanged and still
    asserted. The replacement is stricter where it matters: the column list of
    each index is checked, not just its name, so an index cannot keep its name
    while losing the columns the query needs.
    """
    expected = {
        # name -> leading columns the index must cover
        "idx_alerts_metadata_hash": ["metadata"],
        "idx_alerts_recipient_dismissed_created": ["recipient_user_id", "dismissed", "created_at"],
        "idx_alerts_type_dismissed_created": ["type", "dismissed", "created_at"],
        "idx_alerts_type_event_date": ["type", "event_date"],
    }
    conn = _connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'alerts'"
            )
            defs = {name: definition for name, definition in cur.fetchall()}
    finally:
        conn.close()

    missing = {name for name in expected if name not in defs}
    assert not missing, f"missing alert indexes: {sorted(missing)}"
    for name, columns in expected.items():
        definition = defs[name]
        position = -1
        for column in columns:
            found = definition.find(column, position + 1)
            assert found > position, (
                f"{name} does not cover {column!r} in order: {definition}")
            position = found
