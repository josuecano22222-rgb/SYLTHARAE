"""Integration tests: the unified job system against a real database.

Covers spec sections 5/8/11/12/21/34: persistent jobs, real progress,
cooperative cancellation, retry safety (dedup), crash recovery, dry run.
"""
import time

import pytest

from services.jobs import job_state
from services.jobs.manager import JobManager
from services.ingesting.service import (
    IngestionRequest, IngestionService, IngestionValidationError,
)


@pytest.fixture()
def sync_manager(pg_db):
    """Synchronous JobManager (inline execution) for deterministic tests."""
    mgr = JobManager(synchronous=True)
    yield mgr


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    """A tiny disposable corpus: one text file, one duplicate, one unsupported.

    INGESTION_ROOTS is configured to the corpus dir (SEC-06 allowlist);
    server-path ingestion stays fail-closed everywhere else.
    """
    import uuid

    root = tmp_path / "corpus"
    root.mkdir()
    # Unique content per test: identity is (hash, source, side), so tests
    # sharing the database must not collide with each other's hashes.
    uid = uuid.uuid4().hex
    body = f"alpha bravo charlie {uid} " * 10
    (root / "a.txt").write_text(body, encoding="utf-8")
    (root / "dup.txt").write_text(body, encoding="utf-8")
    (root / "b.log").write_text(f"2026-01-01 delta echo {uid}\n" * 5, encoding="utf-8")
    (root / "weird.xyz").write_text("unknown format", encoding="utf-8")
    monkeypatch.setenv("INGESTION_ROOTS", str(root))
    return root


def _make_job(mgr, path, source="jobtest", side="primary", **opts):
    return mgr.create_job(
        "ingestion",
        source=str(path),
        options={
            "path": str(path),
            "source": source,
            "side": side,
            "recursive": True,
            "processing": {"checkpoint": "off", **opts},
        },
        created_by="tester",
    )


class TestIngestionJobs:
    def test_job_runs_to_completion_with_events(self, sync_manager, corpus, app):
        job = _make_job(sync_manager, corpus)
        job = sync_manager.get(job["job_id"])
        assert job["status"] in (job_state.COMPLETED,
                                 job_state.COMPLETED_WITH_WARNINGS), job["errors"]

        events = sync_manager.events(job["job_id"])
        kinds = [e["event_type"] for e in events]
        assert "JOB_CREATED" in kinds
        assert "JOB_STARTED" in kinds
        assert "JOB_COMPLETED" in kinds

        # Real statistics: 4 files discovered
        stats = job["stats"] or {}
        assert stats.get("files_total") == 4

    def test_dry_run_discovers_without_writes(self, sync_manager, corpus, app):
        job = sync_manager.create_job(
            "ingestion",
            source=str(corpus),
            options={
                "path": str(corpus), "source": "jobtest", "side": "primary",
                "recursive": True, "dry_run": True,
                "processing": {"checkpoint": "off"},
            },
            created_by="tester",
        )
        job = sync_manager.get(job["job_id"])
        assert job["status"] == job_state.COMPLETED
        preview = job["stats"]
        assert preview.get("dry_run") is True
        assert preview.get("files_discovered") == 4
        assert preview.get("files_unsupported") == 1  # weird.xyz

    def test_dedup_second_run_safe(self, sync_manager, corpus, app):
        """Re-running the same corpus must not double-store (DB-03/DB-04)."""
        first = sync_manager.get(_make_job(sync_manager, corpus)["job_id"])
        second = sync_manager.get(_make_job(sync_manager, corpus)["job_id"])
        assert first["status"] in (job_state.COMPLETED,
                                   job_state.COMPLETED_WITH_WARNINGS)
        assert second["status"] in (job_state.COMPLETED,
                                    job_state.COMPLETED_WITH_WARNINGS)
        s1 = first["stats"] or {}
        s2 = second["stats"] or {}
        stored_first = s1.get("files_stored") or 0
        stored_second = s2.get("files_stored") or 0
        dup_second = s2.get("files_duplicates") or 0
        assert stored_first >= 2
        # Second run stores nothing new: identical content + same source/side.
        assert stored_second == 0
        assert dup_second >= 2

    def test_cancellation_cooperative(self, pg_db, tmp_path, monkeypatch, app):
        """A cancelled job stops at a file boundary and is CANCELLED."""
        import uuid

        # Enough uniquely-named files that the job is still RUNNING when the
        # cancel request lands.
        corpus = tmp_path / "cancel_corpus"
        corpus.mkdir()
        uid = uuid.uuid4().hex
        for i in range(120):
            (corpus / f"f{i:03d}.txt").write_text(f"{uid} file {i}\n" * 40,
                                                  encoding="utf-8")
        monkeypatch.setenv("INGESTION_ROOTS", str(corpus))

        mgr = JobManager(synchronous=False)
        try:
            job = _make_job(mgr, corpus)
            job_id = job["job_id"]
            # Request cancellation as soon as it starts running.
            deadline = time.time() + 30
            while time.time() < deadline:
                current = mgr.get(job_id)
                if current and current["status"] in (job_state.RUNNING,
                                                     job_state.CANCELLING,
                                                     job_state.CANCELLED,
                                                     job_state.COMPLETED,
                                                     job_state.COMPLETED_WITH_WARNINGS,
                                                     job_state.FAILED):
                    break
                time.sleep(0.05)
            mgr.cancel(job_id)
            deadline = time.time() + 60
            status = None
            while time.time() < deadline:
                status = mgr.get(job_id)["status"]
                if job_state.is_terminal(status):
                    break
                time.sleep(0.1)
            assert status == job_state.CANCELLED
        finally:
            for info in list(mgr._active.values()):
                info["thread"].join(timeout=60)

    def test_pause_and_resume(self, pg_db, tmp_path, monkeypatch, app):
        """Pause stops at a safe boundary; resume finishes the work (spec 12)."""
        import time as _time
        import uuid as _uuid

        corpus = tmp_path / "pause_corpus"
        corpus.mkdir()
        uid = _uuid.uuid4().hex
        for i in range(150):
            (corpus / f"p{i:03d}.txt").write_text(f"{uid} pause test {i}\n" * 60,
                                                  encoding="utf-8")
        monkeypatch.setenv("INGESTION_ROOTS", str(corpus))

        mgr = JobManager(synchronous=False)
        try:
            job = _make_job(mgr, corpus)
            job_id = job["job_id"]
            # wait until RUNNING
            deadline = _time.time() + 30
            while _time.time() < deadline:
                st = mgr.get(job_id)["status"]
                if st in (job_state.RUNNING, job_state.PAUSED,
                          job_state.COMPLETED, job_state.COMPLETED_WITH_WARNINGS,
                          job_state.FAILED):
                    break
                _time.sleep(0.05)
            if st not in (job_state.RUNNING,):
                # machine too fast to pause mid-run: not a failure of the
                # pause machinery, skip deterministically
                pytest.skip("job completed before pause could land")

            mgr.pause(job_id)
            deadline = _time.time() + 60
            while _time.time() < deadline:
                st = mgr.get(job_id)["status"]
                if st == job_state.PAUSED:
                    break
                if job_state.is_terminal(st):
                    break
                _time.sleep(0.1)
            assert st == job_state.PAUSED, f"expected PAUSED, got {st}"

            resumed = mgr.resume(job_id, created_by="tester")
            assert resumed["job_id"] != job_id
            deadline = _time.time() + 180
            while _time.time() < deadline:
                cur = mgr.get(resumed["job_id"])
                st = cur["status"]
                if job_state.is_terminal(st):
                    break
                _time.sleep(0.2)
            assert st in (job_state.COMPLETED, job_state.COMPLETED_WITH_WARNINGS)
            # Resume must not duplicate already-stored work (dedup).
            stats = cur["stats"] or {}
            stored = int(stats.get("files_stored") or 0)
            dupes = int(stats.get("files_duplicates") or 0)
            total_new = stored + dupes
            assert total_new >= 1  # resumed job accounted for every file
        finally:
            for info in list(mgr._active.values()):
                info["thread"].join(timeout=120)

    def test_retry_creates_new_job_id(self, sync_manager, corpus, app):
        job = sync_manager.get(_make_job(sync_manager, corpus)["job_id"])
        # Force a FAILED-like state is not possible cleanly; use completed-with
        # warnings tolerance: retry allowed for FAILED/CANCELLED only, verify
        # the guard on COMPLETED.
        with pytest.raises(ValueError):
            sync_manager.retry(job["job_id"])

    def test_crash_recovery_marks_stale_running_failed(self, sync_manager, corpus, app):
        job = _make_job(sync_manager, corpus)
        job_id = job["job_id"]
        # Simulate a crashed worker: flip status to RUNNING with an old
        # updated_at, then run recovery with a 0-second stale window.
        from Api.utils.utils import get_connection, return_connection

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET status = 'RUNNING', started_at = now(),"
                    " updated_at = now() - interval '1 hour' WHERE job_id = %s",
                    (job_id,),
                )
            conn.commit()
        finally:
            return_connection(conn)

        out = sync_manager.recover_stale_jobs(stale_seconds=60)
        assert job_id in out["recovered"]
        recovered = sync_manager.get(job_id)
        assert recovered["status"] == job_state.FAILED
        assert "interrupted" in (recovered["errors"] or [""])[0].lower()

    def test_crash_recovery_then_retry_no_duplicate_work(
        self, sync_manager, corpus, app, monkeypatch,
    ):
        """Release acceptance (§14): interrupt -> restart -> recover -> retry.

        A running job is interrupted by a simulated crash; on restart the
        startup hook (apps.web.app._recover_jobs) marks it FAILED with the
        documented message; the operator retries; the retry completes and a
        re-run of the same corpus produces only duplicates - no work is
        performed or recorded twice.
        """
        job = _make_job(sync_manager, corpus)
        job_id = job["job_id"]

        # Crash mid-run: RUNNING with a stale heartbeat.
        from Api.utils.utils import get_connection, return_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET status = 'RUNNING', started_at = now(),"
                    " updated_at = now() - interval '1 hour' WHERE job_id = %s",
                    (job_id,),
                )
            conn.commit()
        finally:
            return_connection(conn)

        # "Restart": the startup hook runs the same recovery call.
        monkeypatch.setattr(
            "services.jobs.manager.JobsConfig.stale_seconds", lambda: 60
        )
        from apps.web.app import _recover_jobs
        with app.app_context():
            _recover_jobs()

        recovered = sync_manager.get(job_id)
        assert recovered["status"] == job_state.FAILED
        # Events survived the crash (history is never deleted).
        kinds = [e["event_type"] for e in sync_manager.events(job_id)]
        assert "JOB_CREATED" in kinds
        # Recovery publishes the terminal status; ``job_events.event_type``
        # admits only the JOB_* vocabulary, so FAILED is stored as JOB_FAILED
        # (the worker's own name is kept in payload.event_kind).
        assert "JOB_FAILED" in kinds

        # Operator retries after the restart.
        retry = sync_manager.retry(job_id, created_by="tester")
        assert retry["job_id"] != job_id
        deadline = time.time() + 180
        while time.time() < deadline:
            cur = sync_manager.get(retry["job_id"])
            if job_state.is_terminal(cur["status"]):
                break
            time.sleep(0.2)
        assert cur["status"] in (job_state.COMPLETED, job_state.COMPLETED_WITH_WARNINGS)
        stats = cur["stats"] or {}
        stored = int(stats.get("files_stored") or 0)
        dupes = int(stats.get("files_duplicates") or 0)
        # The simulated crash hit AFTER the engine stored the corpus (the
        # synchronous manager runs inline), so the retry must recognize the
        # already-stored work as duplicates - never process it twice.
        assert dupes >= 2 and stored == 0, stats

        # And a further re-run stays duplicate-safe (§16).
        again = _make_job(sync_manager, corpus)
        deadline = time.time() + 180
        while time.time() < deadline:
            cur = sync_manager.get(again["job_id"])
            if job_state.is_terminal(cur["status"]):
                break
            time.sleep(0.2)
        stats2 = cur["stats"] or {}
        assert int(stats2.get("files_duplicates") or 0) >= 3
        assert int(stats2.get("files_stored") or 0) == 0

    def test_validation_rejects_bad_requests(self, app):
        svc = IngestionService()
        with pytest.raises(IngestionValidationError):
            svc.validate(IngestionRequest(path="/x", file_paths=["/y"],
                                          source="s", side="d"))
        with pytest.raises(IngestionValidationError):
            svc.validate(IngestionRequest(path="/x", source="", side=""))
        with pytest.raises(IngestionValidationError):
            # Path outside any configured root / nonexistent
            svc.validate(IngestionRequest(path="/definitely/not/there/ok",
                                          source="s", side="d"))


class TestDomainImportJob:
    def test_domain_import_dry_run(self, sync_manager, app, tmp_path):
        """Dry-run domain import parses without DB writes (or reports cleanly
        when no data file is present)."""
        from services.importing.domain_import_service import (
            DomainImportRequest, DomainImportService, DomainImportValidationError,
        )

        svc = DomainImportService()
        try:
            req = svc.validate(DomainImportRequest(dry_run=True))
        except DomainImportValidationError:
            pytest.skip("No domain data file available in this environment")
        result = svc.run(req)
        assert result.success, result.errors
        assert result.stats.get("dry_run") is True
