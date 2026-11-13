"""Unified job manager (spec sections 5, 11, 12, 14, 34).

* Persistent jobs in the `jobs` table (migration 0006, additive).
* Worker threads with explicit concurrency limits (configurable).
* Real progress from worker state - never fake percentages.
* Cooperative cancellation and pause at safe file boundaries.
* Crash recovery: RUNNING jobs not updated within a stale window are
  marked FAILED (identifiable) on startup; resumable jobs keep checkpoints
  so already-stored files are not duplicated (checkpoint + dedup).
* Structured events for the live UI (SSE + polling).
"""
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from services.jobs import job_state
from services.jobs.repository import JobRepository

logger = logging.getLogger(__name__)


class JobsConfig:
    """Job-system configuration (defaults < settings file < env, read-time)."""

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, "") or default)
        except (TypeError, ValueError):
            return default

    @classmethod
    def max_concurrent_jobs(cls) -> int:
        return max(1, cls._env_int("JOBS_MAX_CONCURRENT", 2))

    @classmethod
    def stale_seconds(cls) -> int:
        return max(60, cls._env_int("JOBS_STALE_SECONDS", 6 * 3600))

    @classmethod
    def progress_min_interval(cls) -> float:
        return max(0.2, cls._env_float("JOBS_PROGRESS_INTERVAL", 1.0))

    @classmethod
    def event_sample_threshold(cls) -> int:
        """Per-job FILE_* event cap; above this, per-file events are sampled."""
        return cls._env_int("JOBS_EVENT_SAMPLE_THRESHOLD", 500)

    @classmethod
    def _env_float(cls, name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, "") or default)
        except (TypeError, ValueError):
            return default


class EventThrottle:
    """Aggregates per-file events for large jobs; keeps errors/duplicates."""

    def __init__(self, sample_threshold: int):
        self.sample_threshold = max(1, int(sample_threshold))
        self.counts: Dict[str, int] = {}
        self.samples: Dict[str, int] = {}
        self.pending: List[Dict[str, Any]] = []

    def add(self, event_type: str, payload: Dict[str, Any]) -> None:
        n = self.counts.get(event_type, 0) + 1
        self.counts[event_type] = n
        always = event_type in ("ERROR", "WARNING", "JOB_CREATED", "JOB_STARTED",
                                "JOB_COMPLETED", "JOB_CANCELLED", "PHASE_STARTED",
                                "PHASE_COMPLETED", "DUPLICATE_DETECTED")
        if always or n <= self.sample_threshold:
            self.pending.append({"event_type": event_type, "payload": payload})
        elif n == self.sample_threshold + 1:
            self.pending.append({
                "event_type": "WARNING",
                "payload": {
                    "message": (
                        f"Per-file events for {event_type} exceeded "
                        f"{self.sample_threshold}; further events are sampled"
                    ),
                },
            })

    def flush(self, repo: JobRepository, job_id: str) -> None:
        if self.pending:
            try:
                repo.add_events(job_id, self.pending)
            except Exception:
                logger.exception("Failed to persist job events")
            self.pending = []


def _summary_of(result: Any) -> Optional[Dict[str, Any]]:
    """The stored result of a job: its own summary, or the mapping it returned."""
    if hasattr(result, "to_dict"):
        return result.to_dict().get("result_summary")
    if isinstance(result, dict):
        return result
    return None


class JobManager:
    """Creates, executes and controls long-running operations."""

    _instance_lock = threading.Lock()
    _instance: Optional["JobManager"] = None

    #: Jobs that change stored content or signals, and the scenario-evaluation
    #: trigger each one records.
    _SCENARIO_TRIGGERS = {"ingestion": "ingestion", "batch_import": "ingestion",
                          "signal_redetection": "redetection"}

    @classmethod
    def get_instance(cls) -> "JobManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = JobManager()
            return cls._instance

    def __init__(self, repository: Optional[JobRepository] = None,
                 synchronous: bool = False):
        self.repo = repository or JobRepository()
        self.synchronous = synchronous  # tests/CLI: execute inline
        self._active: Dict[str, Dict[str, Any]] = {}
        self._active_lock = threading.Lock()
        self._subscribers: List[Any] = []  # queue.Queue per SSE client
        self._sub_lock = threading.Lock()
        self._lock_held = threading.Lock()

    # ------------------------------------------------------------------
    # pub/sub for live events (SSE; polling fallback reads the DB)
    # ------------------------------------------------------------------
    def subscribe(self, q) -> None:
        with self._sub_lock:
            self._subscribers.append(q)

    def unsubscribe(self, q) -> None:
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _publish(self, job_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait({"job_id": job_id, "event_type": event_type,
                              "payload": payload, "ts": time.time()})
            except Exception:
                continue

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------
    def create_job(self, job_type: str, source: str, options: Dict[str, Any],
                   created_by: Optional[str] = None,
                   starter: Optional[Callable[[Dict[str, Any]], Any]] = None) -> Dict[str, Any]:
        """Persist a job and (unless synchronous) start a worker thread.

        ``starter(job_record)`` executes the operation; the built-in starters
        cover the known job types (see ``_run_job``).
        """
        job_id = uuid.uuid4().hex[:12].upper()
        record = self.repo.create({
            "job_id": job_id,
            "job_type": job_type,
            "status": job_state.QUEUED,
            "progress": 0,
            "source": (source or "")[:512],
            "options": options or {},
            "created_by": created_by,
        })
        self.repo.add_event(job_id, "JOB_CREATED", {"job_type": job_type})
        self._publish(job_id, "JOB_CREATED", {"status": record["status"]})

        if self.synchronous:
            self._execute(record, starter)
        else:
            t = threading.Thread(
                target=self._worker, args=(record, starter),
                name=f"job-{job_id}", daemon=True,
            )
            with self._active_lock:
                self._active[job_id] = {"thread": t, "cancel": threading.Event()}
            t.start()
        return self.repo.get(job_id)

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------
    def enqueue(self, job_type: str, created_by: Optional[str] = None,
                source: Optional[str] = None, **options: Any) -> Dict[str, Any]:
        """Create a job from a schedule.

        ``services/scheduling/scheduler.py`` documents its whole job as
        "call ``JobManager.enqueue`` at the right times" - and did so, into a
        method that did not exist, so every scheduled fire raised
        ``AttributeError`` inside APScheduler and no scheduled job ever ran.
        This is that entry point: a thin, explicit wrapper over ``create_job``
        so a schedule's options arrive as the job's options.
        """
        return self.create_job(
            job_type,
            source=source or f"schedule:{job_type}",
            options=dict(options),
            created_by=created_by or "system",
        )

    def _worker(self, record: Dict[str, Any], starter) -> None:
        job_id = record["job_id"]
        try:
            self._wait_for_slot(job_id)
            if self.repo.get(job_id) is None:
                return
            current = self.repo.get(job_id)
            if current["status"] == job_state.CANCELLED:
                return
            self._execute(current, starter)
        except Exception:
            logger.exception("Job worker crashed: %s", job_id)
            try:
                self._finish(job_id, job_state.FAILED,
                             errors=["Job worker failed (see server logs)"])
            except Exception:
                pass
        finally:
            with self._active_lock:
                self._active.pop(job_id, None)

    def _wait_for_slot(self, job_id: str) -> None:
        """Block this worker thread until a concurrency slot is free.

        Bug fix: this used to also track ``self._active`` (populated at
        *thread creation* time, before the thread has done anything) and
        treat "my own thread is alive" as "a slot is occupied". Since every
        job's thread is inserted into ``self._active`` immediately in
        ``create_job`` -- before ``_wait_for_slot`` even runs -- submitting
        more than ``max_concurrent_jobs`` jobs within a short window made
        every thread past the limit see all of its *equally-still-waiting*
        siblings as "running" and sleep forever waiting for a slot that can
        never free (none of them ever reach ``_execute`` to finish and
        release it). The only correct source of truth for "how many jobs
        are actually running" is the persisted job status in the database,
        set by ``_execute`` -- so gate purely on that.
        """
        max_jobs = JobsConfig.max_concurrent_jobs()
        while True:
            current = self.repo.get(job_id)
            if current is None:
                return
            if current["status"] == job_state.CANCELLED:
                return
            active = self.repo.count_by_status().get(job_state.RUNNING, 0)
            if active < max_jobs:
                return
            time.sleep(0.5)

    def _execute(self, record: Dict[str, Any], starter) -> None:
        job_id = record["job_id"]
        now = datetime.now(timezone.utc).isoformat()
        self.repo.update_fields(job_id, {
            "status": job_state.RUNNING, "started_at": now,
            "current_phase": "Starting",
        })
        self.repo.add_event(job_id, "JOB_STARTED", {})
        self._publish(job_id, "JOB_STARTED", {})

        throttle = EventThrottle(JobsConfig.event_sample_threshold())
        last_flush = {"t": time.time()}

        # PROGRESS: emission policy.
        #
        # The engine now calls back once per *file* (including nested children),
        # from several worker threads at once. Writing every one of those to the
        # database would swamp it, but the previous code had no throttle at all -
        # ``JobsConfig.progress_min_interval()`` was defined, documented and
        # never used. The rule below is bounded *and* real-time:
        #
        #   * any change in the displayed integer percent  -> emit (at most ~100
        #     emissions per job, and every one of them is a visible movement of
        #     the bar),
        #   * any phase change                             -> emit,
        #   * otherwise                                    -> emit at most once
        #     per JOBS_PROGRESS_INTERVAL as a heartbeat.
        #
        # The most recent snapshot is always kept so the final flush publishes
        # the true end state rather than a stale intermediate one.
        min_interval = JobsConfig.progress_min_interval()
        emit_state = {"t": 0.0, "percent": -1, "phase": None, "last": None}

        def _persist(fields: Dict[str, Any]) -> None:
            try:
                self.repo.update_fields(job_id, fields)
                self._publish(job_id, "PROGRESS", fields)
            except Exception:
                logger.debug("progress persist failed", exc_info=True)

        def _fields_for(snapshot: Dict[str, Any]) -> Dict[str, Any]:
            percent = int(snapshot.get("percent") or 0)
            return {
                "progress": min(100, max(0, percent)),
                "current_phase": snapshot.get("current_phase") or "Processing",
                "current_item": (snapshot.get("current_file") or "")[:512],
                "stats": snapshot,
            }

        def progress_cb(snapshot: Dict[str, Any]) -> None:
            percent = int(snapshot.get("percent") or 0)
            phase = snapshot.get("current_phase")
            now = time.time()
            emit_state["last"] = snapshot

            moved = percent != emit_state["percent"]
            phase_changed = phase != emit_state["phase"]
            heartbeat = (now - emit_state["t"]) >= min_interval
            if not (moved or phase_changed or heartbeat):
                return

            emit_state["t"] = now
            emit_state["percent"] = percent
            emit_state["phase"] = phase
            _persist(_fields_for(snapshot))

        def flush_progress(final_percent: Optional[int] = None,
                           final_stats: Optional[Dict[str, Any]] = None) -> None:
            """Publish the last known state (used at every job boundary).

            ``final_stats`` carries the engine's closing accounting (storage
            counters such as ``files_stored``/``files_duplicates`` and the
            byte totals).  The periodic progress callbacks only publish the
            reader's live snapshot, so without merging these here the job -
            and therefore the Jobs page and ``/api/jobs/<id>`` - reported
            ``duplicates: null`` even though the engine had detected the
            duplicates.  Live keys win where both exist (they describe what
            actually happened), and the final numbers fill in the rest.
            """
            snapshot = emit_state["last"]
            if snapshot is None and not final_stats:
                return
            merged: Dict[str, Any] = dict(snapshot or {})
            for key, value in (final_stats or {}).items():
                if value is None:
                    continue
                merged.setdefault(key, value)
            fields = _fields_for(merged)
            if final_percent is not None:
                fields["progress"] = min(100, max(0, int(final_percent)))
            _persist(fields)

        try:
            if starter is not None:
                result = starter(self._job_context(record, progress_cb, throttle))
            else:
                result = self._run_builtin(record, progress_cb, throttle)
                if result is not None and not getattr(result, "cancelled", False):
                    trigger = self._SCENARIO_TRIGGERS.get(record.get("job_type"))
                    if trigger:
                        self._enqueue_scenario_evaluation(record, result, trigger)
            throttle.flush(self.repo, job_id)

            finished = datetime.now(timezone.utc).isoformat()
            if result is None:
                self._finish(job_id, job_state.FAILED,
                             errors=["Job produced no result"])
                return
            cancelled = bool(getattr(result, "cancelled", False))
            paused = bool(getattr(result, "paused", False))
            errors = list(getattr(result, "errors", []) or [])
            warnings = list(getattr(result, "warnings", []) or [])

            if paused:
                self.repo.update_fields(job_id, {
                    "status": job_state.PAUSED, "completed_at": finished,
                    "stats": getattr(result, "stats", {}),
                    "warnings": warnings,
                })
                # Keep the real percent: a paused job resumes from where it
                # stopped, and showing 0% would misrepresent the stored work.
                flush_progress(final_stats=getattr(result, "stats", {}) or {})
                self._publish(job_id, "JOB_PAUSED", {})
                return
            if cancelled:
                status = job_state.CANCELLED
                self.repo.add_event(job_id, "JOB_CANCELLED", {})
                self._publish(job_id, "JOB_CANCELLED", {})
            else:
                status = job_state.final_state_for(bool(errors), bool(warnings))
                self.repo.add_event(job_id, "JOB_COMPLETED", {"status": status})
                self._publish(job_id, "JOB_COMPLETED", {"status": status})
            # PROGRESS: a job that finished normally has settled every
            # discovered unit, so 100% is correct. A cancelled or failed job
            # previously had its progress *reset to 0*, throwing away the real
            # work already done - the final percent is now taken from the
            # engine's own accounting.
            final_stats = getattr(result, "stats", {}) or {}
            if status in (job_state.COMPLETED, job_state.COMPLETED_WITH_WARNINGS):
                final_percent = 100
            else:
                final_percent = None
                for key in ("percent", "files_percent"):
                    if final_stats.get(key) is not None:
                        try:
                            final_percent = int(final_stats[key])
                            break
                        except (TypeError, ValueError):
                            continue
                if final_percent is None:
                    done = final_stats.get("files_processed") or final_stats.get("files_done")
                    total = final_stats.get("files_total") or final_stats.get("files_discovered")
                    if done is not None and total:
                        final_percent = int((int(done) / int(total)) * 100)

            self.repo.update_fields(job_id, {
                "status": status, "completed_at": finished,
                "stats": final_stats,
                "errors": errors, "warnings": warnings,
                # A job that returns a plain dict (retention does) had its result
                # thrown away: ``dict(result).get("result_summary")`` is always
                # None, so the per-area counts an operator checks after a prune
                # were stored nowhere. Keep the dict as the summary.
                "result_summary": _summary_of(result),
            })
            flush_progress(
                final_percent if final_percent is not None else 100,
                final_stats=final_stats,
            )
        except Exception as exc:
            from core.errors import ClientSafeError, client_safe_message

            flush_progress()
            if isinstance(exc, ClientSafeError):
                # A rejection (invalid request, missing file), not a fault:
                # the user needs the reason, not a correlation id.
                logger.warning("Job %s rejected: %s", job_id, exc)
                message = str(exc)
            else:
                logger.exception("Job %s failed", job_id)
                message = client_safe_message(exc, subsystem="services.jobs")
            self._finish(job_id, job_state.FAILED, errors=[message])

    def _job_context(self, record, progress_cb, throttle) -> Dict[str, Any]:
        """Context handed to custom starters (import services)."""
        return {
            "job": record,
            "progress": progress_cb,
            "emit": lambda et, payload=None: (
                throttle.add(et, payload or {}),
                self._publish(record["job_id"], et, payload or {}),
            ),
        }

    def _finish(self, job_id: str, status: str, errors: List[str]) -> None:
        finished = datetime.now(timezone.utc).isoformat()
        merged = list(errors or [])
        try:
            existing = self.repo.get(job_id) or {}
            for line in existing.get("errors") or []:
                if line and line not in merged:
                    merged.append(line)
        except Exception:
            pass
        self.repo.update_fields(job_id, {
            "status": status, "completed_at": finished, "errors": merged,
        })
        self._publish(job_id, status, {})

    # ------------------------------------------------------------------
    # Built-in starters for known job types
    # ------------------------------------------------------------------
    def _run_builtin(self, record, progress_cb, throttle):
        from services.ingesting.options import IngestionOptions

        options = record.get("options") or {}
        job_type = record["job_type"]
        if job_type == "ingestion":
            from services.ingesting.service import IngestionRequest, IngestionService

            job_id = record["job_id"]

            request = IngestionRequest(
                path=options.get("path"),
                file_paths=options.get("file_paths") or [],
                source=options.get("source", ""),
                side=options.get("side", ""),
                recursive=bool(options.get("recursive", True)),
                options=IngestionOptions(**(options.get("processing") or {})),
                dry_run=bool(options.get("dry_run", False)),
                created_by=record.get("created_by"),
            )

            def control_flag(column):
                def _check():
                    try:
                        current = self.repo.get(job_id)
                        return bool(current and current.get(column))
                    except Exception:
                        return False
                return _check

            mgr = self

            def engine_cb(reader):
                # Direct in-process signal path for instant cancel/pause.
                mgr.register_engine(job_id, reader)

            from services.jobs.issue_registry import JobIssueRegistry

            issue_registry = JobIssueRegistry()

            def wrapped_progress(snapshot):
                snapshot = dict(snapshot)
                if not snapshot.get("current_phase"):
                    snapshot["current_phase"] = "Processing files"
                throttle.add("FILE_COMPLETED", {
                    "files_done": snapshot.get("files_done"),
                    "percent": snapshot.get("percent"),
                })
                progress_cb(snapshot)
                if issue_registry.dirty:
                    errors, warnings = issue_registry.snapshot()
                    try:
                        mgr.repo.update_fields(job_id, {
                            "errors": errors[:500],
                            "warnings": warnings[:500],
                        })
                        for line in errors[-3:]:
                            throttle.add("ERROR", {"message": line})
                        for line in warnings[-3:]:
                            throttle.add("WARNING", {"message": line})
                    except Exception:
                        logger.debug("issue registry persist failed", exc_info=True)
                    issue_registry.clear_dirty()

            try:
                return IngestionService().run(
                    request, progress_cb=wrapped_progress,
                    cancel_cb=control_flag("cancellation_requested"),
                    pause_cb=control_flag("pause_requested"),
                    engine_cb=engine_cb,
                    issue_registry=issue_registry,
                )
            finally:
                mgr.unregister_engine(job_id)
        if job_type == "domain_import":
            from services.importing.domain_import_service import (
                DomainImportRequest, DomainImportService,
            )

            request = DomainImportRequest(
                data_file=options.get("data_file"),
                dry_run=bool(options.get("dry_run", False)),
                created_by=record.get("created_by"),
            )
            return DomainImportService().run(request, progress_cb=progress_cb)
        if job_type == "taxonomy_sync":
            # The workbook is authoritative; this job is the mechanism that
            # makes that true without a human running a command. It writes the
            # plan it computed in one transaction and returns the snapshot that
            # can undo it (services/importing/taxonomy_sync_service.py).
            from services.importing.taxonomy_sync_service import (
                TaxonomySyncRequest, TaxonomySyncService,
            )

            request = TaxonomySyncRequest(
                data_file=options.get("data_file"),
                apply=bool(options.get("apply", False)),
                auto_renames=bool(options.get("auto_renames", True)),
                prune_categories=bool(options.get("prune_categories", False)),
                prune_words=bool(options.get("prune_words", False)),
                created_by=record.get("created_by"),
            )
            return TaxonomySyncService().run(request, progress_cb=progress_cb)
        if job_type == "backup_import":
            from services.importing.backup_import_service import (
                BackupImportRequest, BackupImportService,
            )

            request = BackupImportRequest(
                backup_path=options.get("backup_path"),
                backup_name=options.get("backup_name"),
                dry_run=bool(options.get("dry_run", False)),
                created_by=record.get("created_by"),
            )
            return BackupImportService().run(request, progress_cb=progress_cb)
        if job_type == "batch_import":
            return self._run_batch_import(record, progress_cb, throttle)
        if job_type == "signal_redetection":
            # Apply the current detector version to stored content
            # (services/detection/redetection.py). One transaction per
            # content; failures are recorded per content, not swallowed.
            from Api.utils.utils import get_connection
            from services.detection.redetection import run_redetection

            job_id = record["job_id"]

            def cancelled():
                try:
                    current = self.repo.get(job_id)
                    return bool(current and current.get("cancellation_requested"))
                except Exception:
                    # Same policy as ingestion's control flags: an unreadable
                    # flag means "keep going" - but it is never silent.
                    logger.warning("job %s: cancellation flag unreadable", job_id,
                                   exc_info=True)
                    return False

            return run_redetection(
                get_connection, scope=options.get("scope", "stale"),
                hash_ids=options.get("hash_ids"), job_id=job_id,
                progress_cb=progress_cb, cancel_cb=cancelled,
                detectors=options.get("detectors"),
            )
        
        if job_type in ("scenario_evaluation", "scenario_dry_run"):
            # Scenarios (services/monitoring/scenario_engine.py). A dry-run
            # writes only its own record; an evaluation records outcomes and
            # notifications, each scenario in its own transaction.
            from Api.utils.utils import get_connection
            from services.monitoring.scenario_engine import (run_scenario_dry_run,
                                                             run_scenario_evaluation)

            job_id = record["job_id"]
            if job_type == "scenario_dry_run":
                return run_scenario_dry_run(
                    get_connection, scenario_id=options["scenario_id"],
                    requested_by=options.get("requested_by"), job_id=job_id,
                    progress_cb=progress_cb)

            def scenarios_cancelled():
                current = self.repo.get(job_id)
                return bool(current and current.get("cancellation_requested"))

            return run_scenario_evaluation(
                get_connection, scenario_ids=options.get("scenario_ids"),
                trigger=options.get("trigger", "all_scenarios"), job_id=job_id,
                progress_cb=progress_cb, cancel_cb=scenarios_cancelled)
        if job_type == "retention":
            # Retention (services/retention/service.py): prune the areas whose
            # policy enables them (settings retention.<area>_days, 0 = keep
            # forever), in batched transactions, one audit row per area.
            from Api.utils.utils import get_connection
            from services.retention.service import run_retention

            job_id = record["job_id"]

            def retention_cancelled():
                current = self.repo.get(job_id)
                return bool(current and current.get("cancellation_requested"))

            return run_retention(get_connection, areas=options.get("areas"),
                                 actor=options.get("actor", "system"),
                                 job_id=job_id, progress_cb=progress_cb,
                                 cancel_cb=retention_cancelled)
        raise ValueError(f"Unknown job type: {job_type}")

    def _enqueue_scenario_evaluation(self, record, result, trigger) -> None:
        """Enqueue a scenario evaluation job after a content-changing job completes."""
        try:
            from Api.utils.utils import get_connection
            from services.monitoring.scenario_engine import active_scenario_ids

            with get_connection() as conn:
                if not active_scenario_ids(conn):
                    return
            self.create_job("scenario_evaluation",
                            source=f"scenarios:{trigger}:{record['job_id']}",
                            options={"trigger": trigger}, created_by="system")
        except Exception:
            logger.exception("job %s: scenario evaluation could not be enqueued",
                             record.get("job_id"))
            warnings = getattr(result, "warnings", None)
            if isinstance(warnings, list):
                warnings.append("Scenarios were not evaluated after this job"
                                " (see server logs)")

    def _run_batch_import(self, record, progress_cb, throttle):
        from services.ingesting.service import IngestionRequest, IngestionService
        from services.ingesting.options import IngestionOptions
        from services.importing.backup_import_service import BatchImportService
        from services.jobs.issue_registry import JobIssueRegistry

        options = record.get("options") or {}
        job_id = record["job_id"]
        validated = BatchImportService().validate(
            options.get("file_paths") or [],
            # JOB-02: API clients may send numeric source/side (JSON ints);
            # downstream validation calls .strip(), so normalize to str here.
            str(options.get("source", "") or ""), str(options.get("side", "") or ""),
        )
        request = IngestionRequest(
            file_paths=validated["validated_paths"],
            source=validated["source"], side=validated["side"],
            options=IngestionOptions(**(options.get("processing") or {})),
        )

        issue_registry = JobIssueRegistry()
        mgr = self

        def wrapped(snapshot):
            snapshot = dict(snapshot)
            # Only default the phase when the engine has not named one, so the
            # real "Expanding archive: ..." activity stays visible.
            if not snapshot.get("current_phase"):
                snapshot["current_phase"] = "Batch import"
            throttle.add("FILE_COMPLETED", {
                "files_done": snapshot.get("files_done"),
                "percent": snapshot.get("percent"),
            })
            progress_cb(snapshot)
            if issue_registry.dirty:
                errors, warnings = issue_registry.snapshot()
                try:
                    mgr.repo.update_fields(job_id, {
                        "errors": errors[:500],
                        "warnings": warnings[:500],
                    })
                    for line in errors[-3:]:
                        throttle.add("ERROR", {"message": line})
                    for line in warnings[-3:]:
                        throttle.add("WARNING", {"message": line})
                except Exception:
                    logger.debug("issue registry persist failed", exc_info=True)
                issue_registry.clear_dirty()

        return IngestionService().run(
            request, progress_cb=wrapped, issue_registry=issue_registry
        )

    # ------------------------------------------------------------------
    # Control operations (spec sections 11/12/21)
    # ------------------------------------------------------------------
    def cancel(self, job_id: str) -> Dict[str, Any]:
        job = self._require(job_id)
        if job_state.is_terminal(job["status"]):
            return job
        if job["status"] == job_state.QUEUED:
            self.repo.update_fields(job_id, {
                "status": job_state.CANCELLED,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "cancellation_requested": True,
            })
            self.repo.add_event(job_id, "JOB_CANCELLED", {"queued": True})
            self._publish(job_id, "JOB_CANCELLED", {"queued": True})
            return self.repo.get(job_id)
        # ``jobs.status`` has a closed vocabulary (ck_jobs_status) that does
        # not include CANCELLING, so a cancellation *request* is a flag: the
        # job stays RUNNING while its worker stops at the next boundary and
        # then becomes CANCELLED. The API already exposes the flag, so the UI
        # can show "cancelling" without a status the schema cannot store.
        self.repo.update_fields(job_id, {"cancellation_requested": True})
        self.repo.add_event(job_id, "WARNING", {"message": "Cancellation requested"})
        with self._active_lock:
            info = self._active.get(job_id)
        if info:
            info["cancel"].set()
        # Ask the in-process engine (if this process owns the job) to stop.
        self._signal_engine(job_id, "cancel")
        # Cancellation lands within one file boundary; finalize if the worker
        # already exited (single-process deployments).
        deadline = time.time() + 30
        while time.time() < deadline:
            job = self.repo.get(job_id)
            if job and job_state.is_terminal(job["status"]):
                return job
            time.sleep(0.2)
        return self.repo.get(job_id)

    def pause(self, job_id: str, wait_seconds: float = 0.0) -> Dict[str, Any]:
        """Request a pause and return the job.

        The engine stops at its next checkpoint, which can take a long time
        while a large file is in progress. The request therefore returns at once
        (``wait_seconds=0``) and the job reaches PAUSED on its own; callers watch
        the status. A caller that needs to wait can pass a bound. The old
        behaviour blocked the HTTP worker for up to 30 s per click.
        """
        job = self._require(job_id)
        if job["status"] != job_state.RUNNING:
            raise ValueError("Only RUNNING jobs can be paused")
        self.repo.update_fields(job_id, {"pause_requested": True})
        self._signal_engine(job_id, "pause")
        deadline = time.time() + max(0.0, float(wait_seconds))
        while wait_seconds and time.time() < deadline:
            job = self.repo.get(job_id)
            if job and job["status"] in (job_state.PAUSED, job_state.CANCELLED,
                                         job_state.FAILED):
                return job
            time.sleep(0.2)
        return self.repo.get(job_id)

    def resume(self, job_id: str, created_by: Optional[str] = None) -> Dict[str, Any]:
        job = self._require(job_id)
        if job["status"] != job_state.PAUSED:
            raise ValueError("Only PAUSED jobs can be resumed")
        options = dict(job.get("options") or {})
        # A fresh execution with the same checkpoint policy continues where
        # the pause stopped (checkpoint skips processed files; dedup guards).
        return self.create_job(
            job["job_type"], job.get("source") or "", options,
            created_by=created_by or job.get("created_by"),
        )

    def retry(self, job_id: str, created_by: Optional[str] = None) -> Dict[str, Any]:
        """Retry a failed/cancelled job as a new job with identical options.

        Safe by design: deduplication prevents duplicate database records,
        and checkpointed ingestions skip already-processed files.
        """
        job = self._require(job_id)
        if job["status"] not in (job_state.FAILED, job_state.CANCELLED,
                                 job_state.COMPLETED_WITH_WARNINGS):
            raise ValueError("Only FAILED, CANCELLED or COMPLETED_WITH_WARNINGS jobs can be retried")
        return self.create_job(
            job["job_type"], job.get("source") or "",
            dict(job.get("options") or {}),
            created_by=created_by or job.get("created_by"),
        )

    def _signal_engine(self, job_id: str, kind: str) -> None:
        """Best-effort direct signal to an engine running in this process."""
        engine = getattr(self, "_engines", {}).get(job_id)
        if engine is None:
            return
        try:
            if kind == "cancel":
                engine.request_cancel()
            else:
                engine.request_pause()
        except Exception:
            logger.debug("engine signal failed", exc_info=True)

    def register_engine(self, job_id: str, engine) -> None:
        if not hasattr(self, "_engines"):
            self._engines = {}
        self._engines[job_id] = engine

    def unregister_engine(self, job_id: str) -> None:
        if hasattr(self, "_engines"):
            self._engines.pop(job_id, None)

    # ------------------------------------------------------------------
    # Crash recovery (spec section 34)
    # ------------------------------------------------------------------
    def recover_stale_jobs(self, stale_seconds: Optional[int] = None) -> Dict[str, Any]:
        """Mark interrupted jobs FAILED (identifiable); nothing is deleted.

        Already-stored files stay stored (each file commits its own
        transaction); checkpointed ingestions resume without duplicating
        work; deduplication protects everything else.
        """
        # ``stale_seconds=0`` is a real request ("reclaim everything"), not an
        # absent argument: the startup hook uses it, and ``0 or default`` would
        # have silently waited for the six-hour window instead.
        window = JobsConfig.stale_seconds() if stale_seconds is None else int(stale_seconds)
        stale = self.repo.stale_running(window)
        recovered = []
        for job in stale:
            message = (
                "Application restarted while the job was running; "
                "the job was interrupted. Retry to continue (processed "
                "files are not duplicated)."
            )
            self._finish(job["job_id"], job_state.FAILED, errors=[message])
            # Persist a terminal event so the audit trail reflects the
            # recovery (the happy path persists JOB_COMPLETED the same way).
            try:
                self.repo.add_event(job["job_id"], "FAILED", {
                    "reason": "crash_recovery", "errors": [message],
                })
            except Exception:
                logger.exception("Failed to persist crash-recovery event for %s",
                                 job["job_id"])
            recovered.append(job["job_id"])
        if recovered:
            logger.warning("Recovered %d stale jobs: %s", len(recovered), recovered)
        return {"recovered": recovered, "count": len(recovered)}

    # ------------------------------------------------------------------
    def _require(self, job_id: str) -> Dict[str, Any]:
        job = self.repo.get(job_id)
        if job is None:
            raise KeyError(f"Job not found: {job_id}")
        return job

    def get(self, job_id: str):
        return self.repo.get(job_id)

    def list(self, **kwargs):
        return self.repo.list_jobs(**kwargs)

    def events(self, job_id: str, limit: int = 500, after_id: int = 0):
        self._require(job_id)
        return self.repo.get_events(job_id, limit=limit, after_id=after_id)

    def delete(self, job_id: str) -> bool:
        job = self._require(job_id)
        if not job_state.is_terminal(job["status"]):
            raise ValueError("Only completed/failed/cancelled jobs can be deleted")
        with self._active_lock:
            self._active.pop(job_id, None)
        return self.repo.delete(job_id)
