"""Regressions for the large-container / large-file defects.

Every test here pins a failure that was observed in a real run:

* a 2.1 GB PST whose 19 566 attachments were declared "never reached a worker"
  and skipped while the nested reader was still working through them, after the
  run reported completion;
* the timeout formula that killed that container at 1 322 s (and "recommended"
  125 555 s for the same file, and years for a 5 TB file);
* the resource monitor that treated CPU saturation as overload, shrank the
  worker pool from 8 to 2, and pushed extracted sub-trees into sequential
  processing;
* the CLI reporting ``len(results)`` - a bounded window - as "Total Files";
* PostgreSQL rejecting extracted text that contains NUL, which silently dropped
  the display text of binary-container content.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.integrated_reader import (  # noqa: E402
    MAX_FILE_TIMEOUT_S,
    MIN_ASSUMED_BYTES_PER_S,
    file_timeout_seconds,
    recommended_timeout_seconds,
)
from pipeline.progress_ledger import (  # noqa: E402
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    ProgressLedger,
)

GB = 1024 ** 3


# ---------------------------------------------------------------------------
# Timeout policy
# ---------------------------------------------------------------------------
class TestFileTimeoutPolicy:
    def test_large_container_gets_a_budget_it_can_finish_in(self):
        """The 2.1 GB PST was killed at 1 322 s; reading it alone takes longer."""
        budget = file_timeout_seconds({'size_bytes': int(2.1 * GB)}, 1200)
        assert budget > 1800, (
            "a 2.1 GB container must get more than the 1 322 s that killed the "
            "real PST run"
        )

    def test_budget_scales_with_the_measured_rate(self):
        slow = file_timeout_seconds({'size_bytes': int(2 * GB)}, 1200, 10 * 1024 * 1024)
        fast = file_timeout_seconds({'size_bytes': int(2 * GB)}, 1200, 200 * 1024 * 1024)
        assert slow > fast, "a slower run must budget more time for the same bytes"
        assert fast > 1200

    def test_budget_is_capped_so_one_file_cannot_run_forever(self):
        assert file_timeout_seconds({'size_bytes': 5 * 1024 * GB}, 1200) == MAX_FILE_TIMEOUT_S
        assert file_timeout_seconds(
            {'size_bytes': 5 * 1024 * GB}, 1200, max_timeout=3600) == 3600

    def test_ceiling_is_configurable_upward(self):
        assert file_timeout_seconds({'size_bytes': 5 * 1024 * GB}, 1200,
                                    max_timeout=7 * 24 * 3600) == 7 * 24 * 3600

    def test_small_files_keep_the_base_timeout(self):
        assert file_timeout_seconds({'size_bytes': 4096}, 1200) == 1200
        assert file_timeout_seconds({}, 1200) == 1200

    def test_no_size_is_ever_given_less_than_the_base(self):
        for size in (0, 1, 10 ** 6, 10 ** 9, 10 ** 12, 10 ** 15):
            assert file_timeout_seconds({'size_bytes': size}, 900) >= 900

    def test_recommendation_reflects_the_measured_rate_not_a_constant(self):
        """The old rule (60 s/MB + 10 min) recommended 34 h for this file."""
        recommended = recommended_timeout_seconds(
            {'size_bytes': int(2.1 * GB)}, 1200, 60 * 1024 * 1024)
        assert recommended < 3600
        assert recommended > 1200

    def test_recommendation_falls_back_to_the_floor_rate(self):
        recommended = recommended_timeout_seconds({'size_bytes': int(2.1 * GB)}, 1200)
        assert recommended == int(1200 + int(2.1 * GB) / MIN_ASSUMED_BYTES_PER_S)


# ---------------------------------------------------------------------------
# Container work tracking
# ---------------------------------------------------------------------------
class TestContainerWorkTracking:
    def test_container_publishes_children_and_they_settle(self):
        ledger = ProgressLedger()
        ledger.add_discovered(1, key='container', initial=True)  # the container file
        ledger.add_discovered(19_566, key='/extracted/pst', parent='/x/ssss.pst')

        assert ledger.container_outstanding('/x/ssss.pst') == 19_566
        assert ledger.snapshot()['containers_in_flight'] == 1

        for _ in range(19_566):
            unit = ledger.begin(path='/extracted/pst/attachment.bin')
            ledger.settle(unit, OUTCOME_COMPLETED, container='/x/ssss.pst')

        assert ledger.container_outstanding('/x/ssss.pst') == 0
        assert ledger.snapshot()['containers_in_flight'] == 0
        assert ledger.snapshot()['containers_completed'] == 1

    def test_container_with_outstanding_work_is_visible_to_the_run(self):
        ledger = ProgressLedger()
        ledger.add_discovered(1, key='c', initial=True)
        ledger.add_discovered(5, key='e', parent='container-A')
        assert ledger.live_containers() == {'container-A': 5}
        assert ledger.outstanding_container_work() == 5

    def test_settling_without_the_container_argument_still_counts_the_unit(self):
        """Existing callers keep working; only container attribution is lost."""
        ledger = ProgressLedger()
        unit = ledger.begin(path='f')
        assert ledger.settle(unit, OUTCOME_COMPLETED) is True
        assert ledger.snapshot()['files_completed'] == 1

    def test_settle_is_idempotent_per_unit(self):
        ledger = ProgressLedger()
        ledger.add_discovered(2, key='e', parent='c')
        unit = ledger.begin(path='f')
        assert ledger.settle(unit, OUTCOME_COMPLETED, container='c') is True
        assert ledger.settle(unit, OUTCOME_COMPLETED, container='c') is False
        assert ledger.container_outstanding('c') == 1, (
            "the second settle must not decrement the container again"
        )

    def test_abandon_with_container_clears_outstanding(self):
        ledger = ProgressLedger()
        ledger.add_discovered(1, key='c', initial=True)
        ledger.add_discovered(4, key='e', parent='c')
        ledger.abandon(4, OUTCOME_FAILED, container='c')
        assert ledger.container_outstanding('c') == 0
        snapshot = ledger.snapshot()
        assert snapshot['files_failed'] == 4
        # The container file's own unit is still open - correct: this ledger
        # only settled the 4 children.
        assert snapshot['files_pending'] == 1

    def test_container_map_is_bounded_but_never_wrong(self):
        ledger = ProgressLedger()
        limit = ledger._container_detail_limit
        for i in range(limit + 50):
            ledger.add_discovered(1, key=f'e{i}', parent=f'c{i}')
        # Only the bound is kept; the map never grows without limit.
        assert len(ledger.live_containers()) <= limit
        # And a dropped entry reads as "not in flight" - what a stale
        # container means - rather than as a large number.
        assert ledger.container_outstanding('c0') == 0


# ---------------------------------------------------------------------------
# The end-of-run sweep must not write off live work
# ---------------------------------------------------------------------------
class TestEndOfRunSweep:
    def _reader(self, ledger):
        from pipeline.integrated_reader import IntegratedFileReader

        reader = IntegratedFileReader(
            max_workers=2, enable_monitoring=False, enable_storage=False,
            progress_ledger=ledger, container_path=None,
        )
        return reader

    def test_wait_returns_when_the_container_finishes(self):
        ledger = ProgressLedger()
        ledger.add_discovered(1, key='c', initial=True)
        ledger.add_discovered(200, key='e', parent='container-A')
        reader = self._reader(ledger)

        def worker():
            for _ in range(200):
                unit = ledger.begin(path='/out/member.txt')
                ledger.settle(unit, OUTCOME_COMPLETED, container='container-A')
                time.sleep(0.002)

        thread = threading.Thread(target=worker)
        thread.start()
        outstanding = reader._wait_for_live_containers()
        thread.join()
        assert outstanding == 0, "the sweep waited, so nothing was left behind"
        assert ledger.container_outstanding('container-A') == 0

    def test_wait_reports_work_that_makes_no_progress(self):
        ledger = ProgressLedger()
        ledger.add_discovered(1, key='c', initial=True)
        ledger.add_discovered(7, key='e', parent='stuck-container')
        reader = self._reader(ledger)
        reader.CONTAINER_STALL_GRACE_S = 0.3  # keep the test fast

        started = time.monotonic()
        outstanding = reader._wait_for_live_containers()
        elapsed = time.monotonic() - started

        assert outstanding == 7
        assert 0.2 <= elapsed < 10, "a stalled container must not hang the run"

    def test_unprocessed_work_is_failed_not_silently_skipped(self):
        """No cancel + no worker = lost coverage, and it must be reported."""
        ledger = ProgressLedger()
        ledger.add_discovered(1, key='c', initial=True)
        ledger.add_discovered(19_013, key='never-run')

        # Simulate the sweep's decision for work nobody touched.
        snapshot = ledger.snapshot()
        outstanding = snapshot['files_pending']
        # 1 container unit + 19 013 children, none of them settled yet.
        assert outstanding == 19_014
        ledger.abandon(outstanding, OUTCOME_FAILED)
        snapshot = ledger.snapshot()
        assert snapshot['files_failed'] == 19_014
        assert snapshot['files_skipped'] == 0, (
            "work that never ran is a failure, not a skip - 'skipped' hid a real "
            "loss of coverage in the real run"
        )
        assert snapshot['files_pending'] == 0
        assert snapshot['complete'] is True

    def test_cancelled_work_is_skipped(self):
        ledger = ProgressLedger()
        ledger.add_discovered(1, key='c', initial=True)
        ledger.add_discovered(50, key='never-run')
        ledger.abandon(50, OUTCOME_SKIPPED)
        assert ledger.snapshot()['files_skipped'] == 50
        assert ledger.snapshot()['files_failed'] == 0


# ---------------------------------------------------------------------------
# NUL sanitisation for the raw display text
# ---------------------------------------------------------------------------
class TestRawTextSanitisation:
    def test_nul_is_replaced_and_counted(self):
        from database.database.repository.contents_repo import _sanitise_pg_text

        text = "before\x00after\x00"
        sanitised, count = _sanitise_pg_text(text)
        assert count == 2
        assert "\x00" not in sanitised
        assert sanitised == "before\ufffdafter\ufffd", (
            "position must be preserved, not closed up"
        )
        assert len(sanitised) == len(text)

    def test_text_without_nul_is_returned_unchanged(self):
        from database.database.repository.contents_repo import _sanitise_pg_text

        text = "no nul here \u05e9\u05dc\u05d5\u05dd \U0001f600"
        sanitised, count = _sanitise_pg_text(text)
        assert sanitised is text and count == 0

    def test_sanitisation_is_deterministic(self):
        from database.database.repository.contents_repo import _sanitise_pg_text

        text = "a\x00b"
        assert _sanitise_pg_text(text) == _sanitise_pg_text(text)

    def test_the_result_is_storable_by_postgres(self):
        """The sanitised string survives a round trip through a TEXT column."""
        pytest.importorskip("psycopg2")
        import pgserver
        import tempfile

        from database.database.repository.contents_repo import _sanitise_pg_text

        sanitised, _ = _sanitise_pg_text("x\x00y")
        pgdata = tempfile.mkdtemp(prefix='nul_pg_')
        pgserver.initdb(["-U", "postgres", "-A", "trust", "-E", "UTF8"], pgdata=pgdata)
        server = pgserver.get_server(pgdata)

        import psycopg2

        # Platform-correct address: pgserver advertises a unix-socket directory
        # on POSIX and a TCP host on Windows. Parsing the URI here and assuming
        # the socket shape made this test read the *database name* as a host and
        # fail with "Network is down" on Windows; the shared helper resolves it
        # the same way the rest of the suite does.
        from conftest import pg_connection_settings

        settings = pg_connection_settings(server, "postgres")
        conn = psycopg2.connect(host=settings["host"], port=settings["port"],
                                user=settings["user"], password=settings["password"],
                                dbname="postgres")
        try:
            with conn, conn.cursor() as cur:
                cur.execute("CREATE TABLE t (v text)")
                cur.execute("INSERT INTO t (v) VALUES (%s)", (sanitised,))
                cur.execute("SELECT v FROM t")
                assert cur.fetchone()[0] == "x\ufffdy"
                # The unsanitised value is exactly what PostgreSQL refuses.
                with pytest.raises(Exception):
                    cur.execute("INSERT INTO t (v) VALUES (%s)", ("x\x00y",))
        finally:
            conn.close()
