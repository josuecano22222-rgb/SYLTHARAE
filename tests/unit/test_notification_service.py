"""Behavioural tests for NotificationService accuracy guarantees.

Covers the defects behind the "10 loaded vs 5 rows" evidence:

* ``refresh_notifications`` used ``clear()`` + append with no lock, so two
  concurrent refreshes (stats poll vs scan) interleaved into duplicated
  in-memory rows.  The swap must be atomic.
* ``flush_pending_notifications`` popped a batch and then swallowed per-row
  failures with ``continue`` (the comment promised a retry that never
  happened).  The flush must be a single multi-row statement per batch and
  re-queue the whole batch on failure.
* ``/stats`` forced a full refresh and counted a list capped at
  LIMIT 1000.  Stats must be SQL aggregates and must never load the
  in-memory list.

``Api.utils`` is replaced with a stub module so the service can be exercised
without Flask/psycopg2/PostgreSQL.
"""

from __future__ import annotations

import sys
import threading
import types
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.monitoring.notification_service import (  # noqa: E402
    Notification,
    NotificationPriority,
    NotificationService,
    NotificationType,
)

ALERT_ROW_COLUMNS = 13


@pytest.fixture
def api_utils(monkeypatch):
    """Stub module for ``Api.utils`` capturing every execute_query call."""
    module = types.ModuleType("Api.utils")
    calls = []
    handler = {"fn": None}

    def execute_query(query, params=None, fetch="all", use_cache=False):
        calls.append({"query": query, "params": params, "fetch": fetch})
        fn = handler["fn"]
        if fn is not None:
            return fn(query, params, fetch)
        return [] if fetch == "all" else None

    module.execute_query = execute_query
    module.calls = calls
    module.handler = handler
    monkeypatch.setitem(sys.modules, "Api.utils", module)
    return module


def make_notification(**overrides) -> Notification:
    defaults = dict(
        id=None,
        type=NotificationType.SIMILAR_FILES,
        priority=NotificationPriority.MEDIUM,
        title="Duplicate Files Detected: a.docx",
        message="Found 2 duplicate file(s) with the same hash. Files: a.docx, b.docx",
        file_id=1,
        event_date=None,
        # The file is referenced by file_id; the display name lives in the
        # metadata of rows written before the reader joined ``paths``.
        metadata={"hash": "h", "duplicate_count": 2, "file_names": ["a.docx", "b.docx"],
                  "file_name": "a.docx", "file_path": "/x/a.docx"},
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        read=False,
        dismissed=False,
    )
    defaults.update(overrides)
    return Notification(**defaults)


def make_service(api_utils) -> NotificationService:
    # Construction runs _load_notifications against the stub (empty result).
    return NotificationService()


def test_refresh_swaps_atomically_under_concurrency(api_utils, monkeypatch):
    """Two concurrent refreshes must not double-load the same rows."""
    service = make_service(api_utils)

    barrier = threading.Barrier(2, timeout=10)

    def fake_fetch(limit: int = 1000):
        # Force both threads to interleave inside the load.
        barrier.wait()
        return [make_notification(id=i + 1) for i in range(5)]

    monkeypatch.setattr(service, "_fetch_notifications", fake_fetch)

    threads = [
        threading.Thread(target=service.refresh_notifications)
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert all(not t.is_alive() for t in threads), "refresh threads deadlocked"
    ids = [n.id for n in service._notifications]
    assert len(ids) == 5, f"expected 5 rows after concurrent refreshes, got {len(ids)}"
    assert len(set(ids)) == 5, "duplicate in-memory rows detected"


def test_refresh_preserves_unflushed_pending_notifications(api_utils, monkeypatch):
    service = make_service(api_utils)
    for _ in range(3):
        service._save_notification(make_notification())

    monkeypatch.setattr(
        service,
        "_fetch_notifications",
        lambda limit=1000: [make_notification(id=i + 100) for i in range(5)],
    )
    service.refresh_notifications()

    # 5 persisted + 3 still queued = 8, none lost, none duplicated.
    assert len(service._notifications) == 8
    assert service.get_pending_count() == 3
    negative = [n.id for n in service._notifications if n.id is not None and n.id < 0]
    assert len(negative) == 3


def test_flush_uses_one_multi_row_insert_and_maps_real_ids(api_utils):
    service = make_service(api_utils)

    def insert_handler(query, params, fetch):
        assert "INSERT INTO alerts" in query
        assert params is not None
        assert len(params) % 13 == 0
        count = len(params) // 13
        # Echo created_at exactly as sent (TIMESTAMP round-trip).
        return [(1000 + i, params[13 * i + 7]) for i in range(count)]

    api_utils.handler["fn"] = insert_handler

    created = [service._save_notification(make_notification()) for _ in range(5)]
    original_ids = [n.id for n in created]
    assert all(i is not None and i < 0 for i in original_ids)

    written = service.flush_pending_notifications()

    assert written == 5
    inserts = [c for c in api_utils.calls if "INSERT INTO alerts" in c["query"]]
    assert len(inserts) == 1, "flush must use a single multi-row statement, not per-row inserts"
    assert len(inserts[0]["params"]) == 5 * 13
    assert service.get_pending_count() == 0
    assert [n.id for n in created] == [1000, 1001, 1002, 1003, 1004]
    # The in-memory list sees the same objects with real ids.
    ids = {n.id for n in service._notifications}
    assert {1000, 1001, 1002, 1003, 1004} <= ids


def test_flush_maps_ids_even_when_created_at_collides(api_utils):
    """Identical created_at values must not mis-attribute returned ids."""
    service = make_service(api_utils)

    def insert_handler(query, params, fetch):
        count = len(params) // 13
        return [(500 + i, params[13 * i + 7]) for i in range(count)]

    api_utils.handler["fn"] = insert_handler

    same_moment = datetime(2026, 1, 1, 12, 0, 0)
    created = [
        service._save_notification(make_notification(created_at=same_moment))
        for _ in range(3)
    ]

    written = service.flush_pending_notifications()

    assert written == 3
    assert len({n.id for n in created}) == 3
    assert len({n.created_at for n in created}) == 3, "created_at keys must be unique per batch"


def test_flush_failure_requeues_the_whole_batch(api_utils):
    service = make_service(api_utils)

    def boom(query, params, fetch):
        raise RuntimeError("database unavailable")

    api_utils.handler["fn"] = boom
    created = [service._save_notification(make_notification()) for _ in range(4)]

    written = service.flush_pending_notifications()

    assert written == 0
    # Nothing silently dropped: all four are still queued for retry.
    assert service.get_pending_count() == 4
    assert all(n.id is not None and n.id < 0 for n in created)

    # Retry succeeds and writes everything.
    api_utils.handler["fn"] = lambda q, p, f: [
        (900 + i, p[13 * i + 7]) for i in range(len(p) // 13)
    ]
    written = service.flush_pending_notifications()
    assert written == 4
    assert service.get_pending_count() == 0


def test_get_stats_uses_sql_aggregates_and_overlays_pending(api_utils, monkeypatch):
    service = make_service(api_utils)

    def stats_handler(query, params, fetch):
        flat = " ".join(query.split())
        if "COUNT(*) FILTER" in flat:
            return (5, 2)
        if "GROUP BY type" in flat:
            return [("similar_files", 4), ("future_date", 1)]
        if "GROUP BY priority" in flat:
            return [("medium", 3), ("high", 2)]
        if "event_date BETWEEN" in flat:
            return (1,)
        raise AssertionError(f"unexpected stats query: {flat}")

    api_utils.handler["fn"] = stats_handler

    # Stats must never fall back to loading the (bounded) in-memory list.
    monkeypatch.setattr(
        service,
        "_fetch_notifications",
        lambda limit=1000: pytest.fail("get_stats must not load the in-memory list"),
    )
    monkeypatch.setattr(
        service,
        "get_notifications",
        lambda **kwargs: pytest.fail("get_stats must not scan the in-memory list"),
    )

    # One pending duplicate (unread) + one pending future date within 30 days.
    service._save_notification(make_notification(read=False))
    service._save_notification(
        make_notification(
            type=NotificationType.FUTURE_DATE,
            priority=NotificationPriority.HIGH,
            title="Future Date Detected: x",
            message="Future date found",
            event_date=date.today() + timedelta(days=3),
            metadata={"days_until": 3},
            read=False,
        )
    )

    stats = service.get_stats()

    assert stats["total"] == 7          # 5 persisted + 2 pending
    assert stats["unread"] == 2 + 2     # 2 persisted unread + 2 pending unread
    assert stats["by_type"]["similar_files"] == 5
    assert stats["by_type"]["future_date"] == 2
    assert stats["upcoming_events"] == 2  # 1 persisted in range + 1 pending in range
    assert stats["by_priority"]["high"] == 2 + 1
    assert stats["by_priority"]["medium"] == 3 + 1


def test_get_stats_without_pending_matches_db_exactly(api_utils):
    service = make_service(api_utils)
    api_utils.handler["fn"] = lambda q, p, f: (
        (7, 3) if "COUNT(*) FILTER" in " ".join(q.split()) else
        [("info", 7)] if "GROUP BY type" in " ".join(q.split()) else
        [("low", 7)] if "GROUP BY priority" in " ".join(q.split()) else
        [(0,)]
    )
    stats = service.get_stats()
    assert stats == {
        "total": 7,
        "unread": 3,
        "by_type": {"info": 7},
        "by_priority": {"low": 7},
        "upcoming_events": 0,
    }


def test_get_upcoming_events_merges_pending_and_sorts(api_utils):
    service = make_service(api_utils)

    def upcoming_row(event_offset, row_id):
        # ALERT_COLUMNS order: alerts has no file_name/file_path columns (the
        # display values live in metadata), and the reader is positional.
        return (
            row_id, "future_date", "high", "Future Date Detected", "m",
            1, date.today() + timedelta(days=event_offset),
            {"file_name": "f.docx", "file_path": "/f.docx"},
            datetime(2026, 1, 1), False, False, None, None, None,
        )

    api_utils.handler["fn"] = lambda q, p, f: [upcoming_row(10, 31), upcoming_row(5, 32)]

    # Pending (unflushed) future date sooner than either persisted row.
    service._save_notification(
        make_notification(
            type=NotificationType.FUTURE_DATE,
            title="Future Date Detected: soon",
            event_date=date.today() + timedelta(days=2),
            metadata={"days_until": 2},
        )
    )

    upcoming = service.get_upcoming_events(days_ahead=30)

    assert [n.event_date for n in upcoming] == [
        date.today() + timedelta(days=2),
        date.today() + timedelta(days=5),
        date.today() + timedelta(days=10),
    ]


def test_mark_as_read_accepts_pending_temp_ids(api_utils):
    service = make_service(api_utils)

    def update_handler(query, params, fetch):
        # Temp ids do not exist in the database yet.
        return None

    api_utils.handler["fn"] = update_handler

    pending = service._save_notification(make_notification())
    assert service.mark_as_read(pending.id) is True
    assert pending.read is True

    # Unknown id: no database row and no in-memory object -> False (404).
    assert service.mark_as_read(-999999) is False


def test_mark_as_read_reports_database_hits_without_memory_entries(api_utils):
    service = make_service(api_utils)

    def update_handler(query, params, fetch):
        if "UPDATE alerts" in query and params and params[0] == 42:
            return (42,)
        return None

    api_utils.handler["fn"] = update_handler

    assert service.mark_as_read(42) is True
    assert service.mark_as_read(777) is False


def test_dismiss_reports_database_hits_without_memory_entries(api_utils):
    service = make_service(api_utils)

    def update_handler(query, params, fetch):
        if "UPDATE alerts" in query and params and params[0] == 42:
            return (42,)
        return None

    api_utils.handler["fn"] = update_handler

    assert service.dismiss_notification(42) is True
    assert service.dismiss_notification(777) is False


def test_a_database_error_while_marking_propagates_instead_of_reading_as_not_found(api_utils):
    # Previously swallowed and reported as False -> the route answered 404
    # "not found" for a notification that exists. The route maps it to 500.
    service = make_service(api_utils)

    def failing(query, params, fetch):
        if "UPDATE alerts" in query:
            raise RuntimeError("connection lost")
        return None

    api_utils.handler["fn"] = failing
    with pytest.raises(RuntimeError, match="connection lost"):
        service.mark_as_read(42)
    with pytest.raises(RuntimeError, match="connection lost"):
        service.dismiss_notification(42)


def test_upcoming_event_counts_use_the_utc_date(api_utils, monkeypatch):
    # Event dates are stored from UTC-based signals; the server's local date
    # could be a day off. get_stats must compare against utc_today().
    import core.monitoring.notification_service as ns

    monkeypatch.setattr(ns, "utc_today", lambda: date(2031, 1, 2))
    seen = []

    def recorder(query, params, fetch):
        if "event_date" in query:
            seen.append(params)
        return None

    api_utils.handler["fn"] = recorder
    make_service(api_utils).get_stats(for_user_id=5)
    assert seen and any(date(2031, 1, 2) in (p or ()) for p in seen)


def test_utc_today_is_the_utc_date():
    from datetime import timezone

    import core.monitoring.notification_service as ns

    before = datetime.now(timezone.utc).date()
    assert ns.utc_today() in (before, before + timedelta(days=1))
