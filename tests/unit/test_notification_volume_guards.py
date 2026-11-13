"""Volume/accuracy guards for the notification read paths.

These tests pin the structural answers to "are all precautions in place for
VERY large notification volumes?":

* No endpoint forces a full in-memory refresh on the request path (the old
  ``/stats`` and paginated routes refreshed on EVERY call, racing each
  other - the source of the "10 loaded" vs 5 rows mismatch - and re-read up
  to 10 000 rows from the DB per poll).
* No read path pulls an unbounded/capped-in-memory row window into Python
  to compute counts or pages (``LIMIT 10000`` + Python slicing); counts are
  SQL aggregates, pages are SQL LIMIT/OFFSET.
* ``per_page`` is clamped (the frontend asks for 1000; the server must not
  accept unbounded values).
* The scan endpoint performs at most a constant number of existence queries
  (no per-hash / per-file N+1 probes).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

NOTIFICATIONS = PROJECT_ROOT / "Api" / "routes" / "notifications.py"
NOTIFICATIONS_PAGE = PROJECT_ROOT / "Api" / "routes" / "notifications_page.py"
SERVICE = PROJECT_ROOT / "core" / "monitoring" / "notification_service.py"
MIGRATIONS = PROJECT_ROOT / "database" / "migrations"


def test_paginated_route_never_forces_a_refresh():
    source = NOTIFICATIONS_PAGE.read_text(encoding="utf-8")
    assert "refresh_notifications()" not in source, (
        "the paginated route must not force an in-memory refresh per request"
    )


def test_stats_route_uses_sql_aggregates_not_in_memory_counts():
    source = NOTIFICATIONS.read_text(encoding="utf-8")
    # /stats body: the SQL aggregates only, limited to what the caller may see
    stats_fn = source.split("def get_notification_stats()", 1)[1].split("@app.route", 1)[0]
    assert "get_stats(for_user_id=_viewer_id())" in stats_fn
    assert "refresh_notifications()" not in stats_fn
    assert "get_notifications(limit=" not in stats_fn


def test_no_read_path_loads_a_10000_row_window_into_python():
    for path in (NOTIFICATIONS, NOTIFICATIONS_PAGE, SERVICE):
        source = path.read_text(encoding="utf-8")
        assert "LIMIT 10000" not in source, f"{path.name} still caps a query at LIMIT 10000"
        # The legacy single-notification lookup used get_notifications(limit=10000)
        assert "limit=10000" not in source, f"{path.name} still scans a 10 000-item list"


def test_per_page_is_clamped_but_allows_the_frontend_page_size():
    source = NOTIFICATIONS_PAGE.read_text(encoding="utf-8")
    match = re.search(r"per_page = max\(1, min\(request\.args\.get\('per_page', 20, type=int\), (\d+)\)\)", source)
    assert match, "per_page clamp missing from paginated route"
    clamp = int(match.group(1))
    assert clamp == 1000, (
        f"per_page clamp is {clamp}; the frontend requests 1000 and counts "
        "are computed from exact summary SQL, so the clamp must admit the "
        "full frontend page size"
    )


def test_paginated_route_counts_with_sql_aggregates():
    source = NOTIFICATIONS_PAGE.read_text(encoding="utf-8")
    assert "COUNT(*) FILTER" in source, "summary counts must be computed in SQL"
    assert "LIMIT %s OFFSET %s" in source, "pagination must be SQL-side"


def test_paginated_source_filter_uses_the_content_context_join():
    """Source/side resolve through paths -> hash_contexts (m0011), once."""
    source = NOTIFICATIONS_PAGE.read_text(encoding="utf-8")
    assert "LEFT JOIN hash_contexts hc ON hc.id = p.context_id" in source
    assert "get_file(" not in source, "per-notification file lookups (N+1) are back"


def test_scan_existence_checks_are_set_based_not_n_plus_one():
    source = NOTIFICATIONS.read_text(encoding="utf-8")
    scan_body = source.split("def scan_for_notifications()", 1)[1]
    # Old per-hash probe (ran once per duplicate group):
    assert "AND metadata->>'hash' = %s" not in scan_body, (
        "scan still probes the alerts table once per duplicate hash group"
    )
    # Old per-file future-date probe (ran up to 5 000 times per scan):
    assert "AND event_date = %s" not in scan_body, (
        "scan still probes the alerts table once per candidate file"
    )
    assert "file_id = ANY(%s)" in scan_body, (
        "scan must preload existing (file_id, event_date) pairs in one query"
    )
    assert "metadata->>'hash'" in scan_body  # preload keeps working


def test_scan_candidate_query_returns_one_row_per_path():
    source = NOTIFICATIONS.read_text(encoding="utf-8")
    # DISTINCT + c.id fanned out over content chunks.
    assert "c.id as content_id" not in source
    # Future dates come from stored signals (Phase 1): exactly one - the
    # earliest - future signal per path via LATERAL ... LIMIT 1; contents
    # chunks are not joined at all.
    scan_body = source.split("def scan_for_notifications()", 1)[1]
    future = scan_body.split("# 2. Find files with future dates", 1)[1]
    query = future.split("future_rows = execute_query(", 1)[1].split('fetch="all"', 1)[0]
    assert "JOIN LATERAL (" in query and "LIMIT 1\n                    ) s ON TRUE" in query
    assert "JOIN contents" not in query and "FROM contents" not in query


def test_scan_response_is_built_after_flush():
    source = NOTIFICATIONS.read_text(encoding="utf-8")
    scan_body = source.split("def scan_for_notifications()", 1)[1]
    flush_at = scan_body.index("notification_service.flush_pending_notifications()")
    build_at = scan_body.index("notifications_created = []")
    respond_at = scan_body.index("'notifications_created': notifications_created")
    assert flush_at < build_at < respond_at, (
        "scan must flush (assigning real ids) BEFORE building the response"
    )


def test_dismissed_notifications_are_not_resurrected_by_scans():
    """Existence checks must ignore dismissed state (sticky dismissal)."""
    source = NOTIFICATIONS.read_text(encoding="utf-8")
    scan_body = source.split("def scan_for_notifications()", 1)[1]
    # Neither preload may filter on dismissed:
    preload_section = scan_body.split("duplicate_results = execute_query", 1)[1]
    preload_section = preload_section.split("# 2. Find files with future dates", 1)[0]
    assert "AND dismissed = FALSE" not in preload_section, (
        "scan existence preloads must include dismissed rows, otherwise a "
        "dismissed notification is recreated on the next scan"
    )


def test_alert_index_migration_has_a_unique_version():
    versions = {}
    for path in MIGRATIONS.glob("m[0-9][0-9][0-9][0-9]_*.py"):
        match = re.search(r'^version = "(\d+)"', path.read_text(encoding="utf-8"), re.M)
        assert match, f"{path.name} has no version"
        assert match.group(1) not in versions, (
            f"{path.name} reuses migration version {match.group(1)} "
            f"(already used by {versions.get(match.group(1))})"
        )
        versions[match.group(1)] = path.name
    # BEFORE this also required "m0014_alerts_notification_indexes.py" among the
    # migration files. The frozen baseline squashed that migration into
    # m0001_baseline.py, so the file cannot exist without breaking the freeze.
    # The alert indexes it used to add are asserted against the real database in
    # tests/integration/test_notification_accuracy.py::test_alert_indexes_exist;
    # what is asserted here is what this test is actually about - that the
    # version numbers are unique and the set of migrations is exactly the
    # frozen one.
    # 0004 is the one additive migration the owner authorised for the category
    # hierarchy (it replaces the two-level trigger body with a cycle guard);
    # m0001-m0003 are untouched. A fifth version would mean the freeze moved
    # again, which is what this assertion is here to catch.
    #
    # 0005 is the *second* such exception, approved on 2026-10-27 for the
    # multilingual term names (docs/plans/CATEGORY_TERM_I18N.md, D1): one new
    # table plus its indexes, nothing altered in m0001-m0004. Adding it here is
    # the deliberate, reviewed change of the freeze gate - the diff is this
    # dict and the comment above it, not an exemption.
    assert versions == {"0001": "m0001_baseline.py",
                        "0002": "m0002_category_parent_id.py",
                        "0003": "m0003_sources_attachments.py",
                        "0004": "m0004_category_hierarchy_depth.py",
                        "0005": "m0005_term_names.py"}, versions
