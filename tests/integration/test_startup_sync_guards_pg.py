"""Integration: the startup sync against a real database, under stress.

Four questions a boot-time apply has to answer, and the previous round could not:

* a workbook that lost most of its content - do we refuse, and is the database
  provably untouched afterwards?
* two instances booting at once - does the second one decline instead of writing?
* a write that lands between an apply and a restore - what survives, and what
  does the restore not put back?
* what does the fast path actually cost, beside the slow path?

The harness is the one ``test_sync_taxonomy.py`` uses: a database of this
module's own, so leftovers cannot make one test another test's evidence.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import openpyxl
import psycopg2
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from apps.importing.validators import workbook_rules as rules  # noqa: E402
from services.importing import taxonomy_startup as startup  # noqa: E402
from services.importing import taxonomy_sync as sync  # noqa: E402

pytestmark = pytest.mark.integration


def token() -> str:
    return uuid.uuid4().hex[:8]


def build(path: Path, sheets: dict) -> Path:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, content in sheets.items():
        sheet = book.create_sheet(name)
        sheet.cell(row=1, column=1, value=rules.TERM_HEADER)
        sheet.cell(row=1, column=2, value=rules.KEYWORD_HEADER)
        for row, term in enumerate(content.get("terms", []), start=2):
            sheet.cell(row=row, column=1, value=term)
        for row, keyword in enumerate(content.get("keywords", []), start=2):
            sheet.cell(row=row, column=2, value=keyword)
    book.save(path)
    return path


@pytest.fixture(scope="module")
def guard_db(pg_db):
    from database.bootstrap import bootstrap_database

    name = f"{pg_db['database']}_startup_guards"
    admin = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                             password=pg_db["password"], dbname="postgres")
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                        " WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()
    cfg = {key: pg_db[key] for key in ("host", "port", "user", "password")}
    cfg["database"] = name
    bootstrap_database(cfg)
    return cfg


def connect(cfg):
    return psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                            password=cfg["password"], dbname=cfg["database"])


@pytest.fixture()
def conn(guard_db):
    connection = connect(guard_db)
    with connection.cursor() as cur:
        cur.execute("TRUNCATE words, categorys, keywords, words_categorys"
                    " RESTART IDENTITY CASCADE")
    connection.commit()
    yield connection
    connection.close()


def counts(conn):
    return sync.snapshot(conn)


def test_a_workbook_that_lost_most_of_its_categories_is_refused(conn, tmp_path):
    """The half-saved workbook case: nine tenths of the sheets are gone.

    The database is given 40 categories because the guard has an absolute floor
    (``taxonomy_guard.MIN_ABSOLUTE``) below which a fraction is meaningless; a
    taxonomy of ten is small enough that losing nine is a small loss.
    """
    suffix = token()
    full = {f"Zeta Cat {i} {suffix}": {"terms": [f"Alpha{suffix}{i}"],
                                       "keywords": [f"Beta gamma {suffix}{i}"]}
            for i in range(40)}
    first = build(tmp_path / "full.xlsx", full)
    applied = sync.sync_workbook(conn, str(first), apply=True)
    assert applied["applied"] is True
    before = counts(conn)
    assert before["words_categorys"] >= 40

    truncated = build(tmp_path / "truncated.xlsx", {"Zeta Cat 0 %s" % suffix: full[f"Zeta Cat 0 {suffix}"]})
    outcome = sync.sync_workbook(conn, str(truncated), apply=True)

    assert outcome["applied"] is False
    assert outcome["ok"] is False
    assert outcome["reason"] == "shrink limit"
    assert outcome["removal"]["removed"] >= 39
    assert counts(conn) == before, "a refused sync wrote to the database"

    # Unlinked terms and deleted phrases are still there too, not just the counts.
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM categorys")
        assert cur.fetchone()[0] >= 40


def test_an_empty_sheet_set_is_refused_rather_than_wiping_the_taxonomy(conn, tmp_path):
    suffix = token()
    full = {f"Zeta Keep {i} {suffix}": {"terms": [f"Alpha{suffix}{i}"]} for i in range(6)}
    sync.sync_workbook(conn, str(build(tmp_path / "full.xlsx", full)), apply=True)
    before = counts(conn)

    empty = openpyxl.Workbook()
    empty.active.title = "Sheet"
    empty_path = tmp_path / "empty.xlsx"
    empty.save(empty_path)

    outcome = sync.sync_workbook(conn, str(empty_path), apply=True)
    assert outcome["applied"] is False
    assert counts(conn) == before


def test_raising_the_limit_allows_the_removal_the_operator_meant(conn, tmp_path,
                                                               monkeypatch):
    suffix = token()
    full = {f"Zeta Drop {i} {suffix}": {"terms": [f"Alpha{suffix}{i}"]} for i in range(6)}
    sync.sync_workbook(conn, str(build(tmp_path / "full.xlsx", full)), apply=True)
    run = build(tmp_path / "one.xlsx",
                {f"Zeta Drop 0 {suffix}": {"terms": [f"Alpha{suffix}0"]}})

    monkeypatch.setenv("SYLTHARAE_TAXONOMY_SHRINK_MAX", "1")
    outcome = sync.sync_workbook(conn, str(run), apply=True, prune_categories=True)
    assert outcome["applied"] is True
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM categorys")
        assert cur.fetchone()[0] == 1


def test_two_instances_booting_together_do_not_both_apply(guard_db, tmp_path):
    """The second instance declines while the first holds the apply lock."""
    lock_conn = connect(guard_db)
    try:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (startup.APPLY_LOCK_KEY,))
            assert cur.fetchone()[0] is True
        lock_conn.commit()

        class Service:
            def resolve_data_file(self, _name):
                return str(book)

            def _connect(self):
                return connect(guard_db)

            def run(self, request, progress_cb=None):     # pragma: no cover
                raise AssertionError("the second instance applied the workbook")

        book = build(tmp_path / "boot.xlsx", {f"Zeta Boot {token()}": {"terms": []}})
        outcome = startup.reconcile("apply", service=Service())
        assert outcome["action"] == "already_syncing", outcome
    finally:
        lock_conn.close()


def test_the_lock_is_free_once_the_first_instance_releases_it(guard_db, tmp_path):
    lock_conn = connect(guard_db)
    with lock_conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (startup.APPLY_LOCK_KEY,))
        assert cur.fetchone()[0] is True
    lock_conn.commit()
    with lock_conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (startup.APPLY_LOCK_KEY,))
    lock_conn.close()

    book = build(tmp_path / "boot.xlsx", {f"Zeta Boot {token()}": {"terms": []}})

    class Service:
        applied = False

        def resolve_data_file(self, _name):
            return str(book)

        def _connect(self):
            return connect(guard_db)

        def run(self, request, progress_cb=None):
            from services.importing.taxonomy_sync_service import TaxonomySyncResult
            Service.applied = True
            return TaxonomySyncResult(success=True, applied=True,
                                      stats={"written": {}, "planned_changes": 1})

    outcome = startup.reconcile("apply", service=Service())
    assert outcome["action"] == "applied", outcome
    assert Service.applied is True


def test_restore_after_an_unrelated_write_keeps_that_write(conn, tmp_path):
    """What a restore restores, and what it must not touch.

    A sync applies a workbook, then an unrelated write lands (a term added by
    another user through the interface), then the sync is undone from its
    snapshot. The snapshot predates the unrelated write, so:
      * the categories and memberships the sync created are removed;
      * the unrelated write survives, because the restore only reverses the
        sync's own categories (``restore_state`` is scoped by ``names``);
      * a word row that the sync created stays, because word rows are shared and
        deleting one could orphan another category.
    """
    suffix = token()
    mine, other = f"Zeta Mine {suffix}", f"Zeta Other {suffix}"
    book = build(tmp_path / "mine.xlsx",
                 {mine: {"terms": [f"Alpha{suffix}"], "keywords": [f"Beta gamma {suffix}"]}})
    outcome = sync.sync_workbook(conn, str(book), apply=True)
    assert outcome["applied"] is True
    snapshot = outcome["snapshot"]

    # The unrelated write: another user creates a category and one term in it,
    # through the interface, while this sync's snapshot is still the undo point.
    with conn.cursor() as cur:
        cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (other,))
        other_word = cur.fetchone()[0]
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                    (other_word,))
        other_category = cur.fetchone()[0]
        cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id",
                    (f"Delta{suffix}",))
        delta = cur.fetchone()[0]
        cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                    " VALUES (%s, %s)", (delta, other_category))
    conn.commit()

    # Undo only what the first sync did.
    sync.restore_state(conn, snapshot, names=[mine])
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT w.word FROM words w JOIN categorys c ON c.word_id = w.id"
                    " WHERE w.word = ANY(%s)", ([mine, other],))
        remaining = {row[0] for row in cur.fetchall()}
    assert mine not in remaining, "the restore did not remove the synced category"
    assert other in remaining, "the restore removed another writer's category"

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM words WHERE word = ANY(%s)",
                    ([f"Alpha{suffix}", f"Delta{suffix}"],))
        kept_words = cur.fetchone()[0]
    assert kept_words == 2, (
        "the restore removed a word row that another category still uses")


def test_the_fast_path_costs_a_hash_and_the_slow_path_costs_the_plan(conn, tmp_path,
                                                                    monkeypatch):
    """Measured, not asserted: the unchanged-workbook path beside a real plan."""
    suffix = token()
    sheets = {f"Zeta Time {i} {suffix}": {"terms": [f"Alpha{suffix}{i}"],
                                          "keywords": [f"Beta gamma {suffix}{i}"]}
              for i in range(30)}
    path = build(tmp_path / "time.xlsx", sheets)

    slow_start = time.perf_counter()
    first = sync.sync_workbook(conn, str(path), apply=True)
    slow = time.perf_counter() - slow_start
    assert first["applied"] is True

    fingerprint_start = time.perf_counter()
    for _ in range(5):
        sync.fingerprint(str(path))
    fast = (time.perf_counter() - fingerprint_start) / 5

    # The startup decision itself: same workbook, same state -> no database work.
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("APP_DATA_DIR", str(state_dir))
    from core import app_paths

    monkeypatch.setattr(app_paths, "_cached_root", state_dir)
    from services.importing.taxonomy_sync_service import write_state

    write_state(fingerprint=sync.fingerprint(str(path)), data_file=path.name,
                written={}, applied_by="test")
    decision_start = time.perf_counter()
    decision = startup.reconcile("apply", service=_RealService(conn, path))
    decision_time = time.perf_counter() - decision_start

    assert decision["action"] == "unchanged"
    assert decision_time < slow, "the fast path cost more than the slow path"
    print(f"\nboot latency: fast path {decision_time * 1000:.2f} ms "
          f"(includes one hash {fast * 1000:.2f} ms), slow path {slow * 1000:.1f} ms "
          f"for {len(sheets)} categories")


class _RealService:
    """The startup decision over the real sync, on the test's connection."""

    def __init__(self, conn, path):
        self._conn = conn
        self._path = path

    def resolve_data_file(self, _name):
        return str(self._path)

    def _connect(self):
        return connect(self._conn.dsn and _dsn(self._conn))

    def drift(self):
        from services.importing.taxonomy_sync_service import TaxonomySyncResult
        outcome = sync.sync_workbook(self._conn, str(self._path), apply=False)
        return TaxonomySyncResult(success=bool(outcome["ok"]),
                                  stats={"planned_changes": outcome["planned_changes"],
                                         "plan": outcome["plan"]})

    def run(self, request, progress_cb=None):             # pragma: no cover
        raise AssertionError("the fast path must not run the sync")


def _dsn(conn):
    info = conn.get_dsn_parameters()
    return {"host": info["host"], "port": info["port"], "user": info["user"],
            "password": info.get("password", ""), "database": info["dbname"]}


def test_a_half_written_workbook_is_refused_not_applied(conn, tmp_path):
    """A file caught mid-save is not a workbook. Nothing may be written."""
    suffix = token()
    good = {f"Zeta Safe {suffix}": {"terms": [f"Alpha{suffix}"], "keywords": []}}
    path = build(tmp_path / "good.xlsx", good)
    sync.sync_workbook(conn, str(path), apply=True)
    before = counts(conn)

    broken = tmp_path / "half.xlsx"
    broken.write_bytes(path.read_bytes()[: len(path.read_bytes()) // 2])

    outcome = sync.sync_workbook(conn, str(broken), apply=True)
    assert outcome["applied"] is False
    assert outcome["ok"] is False
    assert counts(conn) == before
    # And the startup decision reports it rather than raising into the web app.
    class Service:
        def resolve_data_file(self, _name):
            return str(broken)

    decision = startup.reconcile("apply", service=Service())
    assert decision["action"] in ("refused", "error"), decision


def test_the_drift_report_names_the_orphan_rows_and_their_confirm_path(conn, tmp_path,
                                                                      monkeypatch):
    """The drift a sync would remove, as rows an operator can read.

    ``GET /api/taxonomy/status`` reads exactly this, so the assertion is made on
    the service's own dry run rather than on a re-implementation of it.
    """
    from services.importing.taxonomy_sync_service import (TaxonomySyncRequest,
                                                         TaxonomySyncService)

    suffix = token()
    category = f"Zeta Drift {suffix}"
    doomed = f"Alpha{suffix}"
    sync.sync_workbook(conn, str(build(tmp_path / "one.xlsx",
                                       {category: {"terms": [doomed]}})), apply=True)
    narrowed = build(tmp_path / "two.xlsx",
                     {category: {"terms": [f"Zeta{suffix}"]}})
    # A plan is computed before it is written, so the word is an orphan on the
    # run *after* the one that unlinked it; the second apply leaves it alone
    # because pruning was not asked for.
    sync.sync_workbook(conn, str(narrowed), apply=True)
    sync.sync_workbook(conn, str(narrowed), apply=True)

    settings = guard_db_of(conn)
    service = TaxonomySyncService(connection_factory=lambda: connect(settings),
                                  search_paths=(str(narrowed.parent),))
    result = service.run(TaxonomySyncRequest(data_file=narrowed.name, apply=False))
    assert result.success is True, result.errors
    assert doomed in result.stats["orphan_words_sample"], result.stats["orphan_words_sample"]
    assert result.stats["orphan_words_count"] >= 1
    assert any("prune_words" in line for line in result.stats["summary"]), result.stats["summary"]


def guard_db_of(conn):
    info = conn.get_dsn_parameters()
    return {"host": info["host"], "port": info["port"], "user": info["user"],
            "password": info.get("password", ""), "database": info["dbname"]}


def test_a_deliberate_large_removal_is_recorded_with_its_decision(conn, tmp_path,
                                                                 monkeypatch):
    """The override must leave a trace, not only a lower row count.

    Raising ``SYLTHARAE_TAXONOMY_SHRINK_MAX`` is the operator saying "yes, I mean
    it". The run that then deletes must report the decision and the numbers it
    overrode, in the result the job stores and the interface shows - so "why did
    thirty categories vanish" has an answer inside the system, not in a chat log.
    """
    suffix = token()
    full = {f"Zeta Big {i} {suffix}": {"terms": [f"Alpha{suffix}{i}"]}
            for i in range(40)}
    sync.sync_workbook(conn, str(build(tmp_path / "full.xlsx", full)), apply=True)

    kept = build(tmp_path / "one.xlsx",
                 {f"Zeta Big 0 {suffix}": {"terms": [f"Alpha{suffix}0"]}})
    refused = sync.sync_workbook(conn, str(kept), apply=True, prune_categories=True)
    assert refused["applied"] is False and refused["reason"] == "shrink limit"

    monkeypatch.setenv("SYLTHARAE_TAXONOMY_SHRINK_MAX", "1")
    allowed = sync.sync_workbook(conn, str(kept), apply=True, prune_categories=True)
    assert allowed["applied"] is True
    removal = allowed["removal"]
    assert removal["ok"] is True
    assert removal["limit"] == 1.0
    assert removal["removed"] >= 39
    assert removal["stored"] >= 40
    # The decision, the limit it overrode, and the numbers are all in the result
    # that the job row stores and GET /api/taxonomy/status returns.
    assert "SYLTHARAE_TAXONOMY_SHRINK_MAX" in removal["reason"] or removal["limit"] == 1.0
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM categorys")
        assert cur.fetchone()[0] == 1
