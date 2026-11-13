"""APScheduler wrapper: one scheduler, driven from the database schedule store.

The JobManager already knows how to run every job we need; the scheduler's
only job is to call ``JobManager.enqueue`` at the right times with the right
arguments. It reads ``schedules`` on startup (and after every CRUD change)
and keeps APScheduler in sync.

Schedule -> JobManager mapping:

* ``scenario_evaluation`` -> ``JobManager.enqueue("scenario_evaluation", ...)``
* ``retention``           -> ``JobManager.enqueue("retention", ...)``

A third type for the category workbook (``taxonomy_sync``) is designed but not
scheduled: ``job_schedules.schedule_type`` carries a CHECK constraint from
``m0001`` that allows exactly the two names above, so adding it needs an
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Any, Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

if TYPE_CHECKING:
    from services.jobs.manager import JobManager
    from services.scheduling.store import ScheduleStore

log = logging.getLogger(__name__)


def enabled_by_config() -> bool:
    """Whether this process should run the scheduler.

    Off unless ``SYLTHARAE_SCHEDULER=1``: ``run_web.py`` sets it for a served
    deployment, while tests and import-only processes must not grow scheduler
    threads by accident.
    """
    return (os.environ.get("SYLTHARAE_SCHEDULER") or "").strip().lower() in (
        "1", "true", "yes", "on")


class ScheduleRunner:
    """Starts/stops the scheduler and keeps it in sync with the DB."""

    _instance: Optional["ScheduleRunner"] = None
    _instance_lock = threading.Lock()

    def __init__(self, job_manager: JobManager, store: ScheduleStore) -> None:
        self._job_manager = job_manager
        self._store = store
        self._scheduler = BackgroundScheduler(daemon=True)
        self._job_ids: dict[int, str] = {}  # schedule_id -> apscheduler job id
        self._leader_conn = None            # holds the advisory lock while set

    @classmethod
    def get_instance(cls) -> "ScheduleRunner":
        """The process-wide runner, built over the real store and job manager.

        ``apps/web/app.py`` and ``POST /api/schedules/<id>/run_now`` both call
        this; there is exactly one scheduler per process (the deployment is
        """
        with cls._instance_lock:
            if cls._instance is None:
                from services.jobs.manager import JobManager
                from services.scheduling.store_adapter import ScheduleStoreAdapter

                cls._instance = cls(JobManager.get_instance(),
                                    ScheduleStoreAdapter())
            return cls._instance

    def fire_now(self, schedule: Dict[str, Any]) -> Dict[str, Any]:
        """Run one schedule immediately, with the same authorization rules.

        Used by ``POST /api/schedules/<id>/run_now``. Returns a plain dict the
        route turns into a response: ``{"status": "queued"|"disabled"|"error"}``.
        """
        from services.scheduling.model import ScheduleError, owner_eligible

        schedule_id = int(schedule["id"])
        schedule_type = schedule["schedule_type"]
        owner = self._store.get_user(schedule["owner_user_id"])
        if not owner:
            self._store.disable(schedule_id, "owner_missing")
            return {"status": "disabled", "reason": "owner_missing",
                    "message": "the schedule's owner no longer exists"}
        try:
            ok, reason = owner_eligible(schedule_type, owner.get("role"),
                                        owner.get("is_active", True))
        except ScheduleError as exc:
            # A row naming a type the vocabulary does not know is a data error,
            # not a reason to guess which job the operator meant.
            return {"status": "error", "message": str(exc)}
        if not ok:
            self._store.disable(schedule_id, reason)
            return {"status": "disabled", "reason": reason,
                    "message": f"this schedule can no longer run: {reason}"}
        try:
            job = self._enqueue(schedule_type, schedule_id, schedule.get("payload"),
                                owner.get("username") or "system")
        except Exception as exc:                       # pragma: no cover - defensive
            log.exception("Schedule %d could not be fired", schedule_id)
            return {"status": "error", "message": f"{exc.__class__.__name__}: {exc}"}
        return {"status": "queued", "job_id": job.get("job_id")}

    def _enqueue(self, schedule_type: str, schedule_id: int, payload,
                 actor: str) -> Dict[str, Any]:
        """Translate the stored payload into the job's options and enqueue it.

        The source names the schedule, so a job row says which schedule fired
        it; the options are the ones the job reads, so a ``policy`` chosen on
        the page reaches ``run_retention``. A payload key the job cannot honour
        is logged with its reason instead of being dropped in silence.
        """
        from services.scheduling.model import job_options

        options, dropped = job_options(schedule_type, payload)
        if dropped:
            log.warning("Schedule %d: payload field(s) not passed to the job: %s",
                        schedule_id,
                        "; ".join(f"{key} ({why})" for key, why in dropped.items()))
        return self._job_manager.enqueue(
            schedule_type, created_by=actor,
            source=f"schedule:{schedule_id}", **options)

    def start(self) -> bool:
        """Become the firing process, or decline because another one is.

        Returns ``False`` when the advisory lock is held elsewhere: two web
        processes over one database must not both fire the same schedule, and
        the second one says so rather than doing it quietly.
        """
        if self._leader_conn is None:
            claim = getattr(self._store, "claim_leadership", None)
            if claim is not None:
                self._leader_conn = claim()
                if self._leader_conn is None:
                    log.warning(
                        "Scheduler not started: another process holds the "
                        "scheduler lock for this database")
                    return False
        schedules = self._store.list_enabled()
        for s in schedules:
            self._schedule_one(s)
        self._scheduler.start()
        log.info("Scheduler started with %d job(s)", len(schedules))
        return True

    def stop(self) -> None:
        """Shut down the scheduler cleanly and give the lock back."""
        try:
            self._scheduler.shutdown(wait=False)
        finally:
            release = getattr(self._store, "release_leadership", None)
            if release is not None:
                release(self._leader_conn)
            self._leader_conn = None
            log.info("Scheduler stopped")

    def refresh(self, schedule_id: int) -> None:
        """Resync one schedule after create/update/delete/enable/disable."""
        self._unschedule(schedule_id)
        s = self._store.get(schedule_id)
        if s and not s.disabled:
            self._schedule_one(s)

    def refresh_all(self) -> None:
        """Full resync (used after bulk changes)."""
        for sid in list(self._job_ids):
            self._unschedule(sid)
        for s in self._store.list_enabled():
            self._schedule_one(s)

    def _schedule_one(self, s) -> None:
        trigger = IntervalTrigger(minutes=s.interval_minutes)
        job_id = f"schedule-{s.id}"
        if s.schedule_type == "scenario_evaluation":
            self._scheduler.add_job(
                self._run_scenario_evaluation, trigger, id=job_id,
                args=[s.id],
                replace_existing=True,
            )
        elif s.schedule_type == "retention":
            self._scheduler.add_job(
                self._run_retention, trigger, id=job_id,
                args=[s.id],
                replace_existing=True,
            )
        else:
            log.warning("Unknown schedule type %r for schedule %d", s.schedule_type, s.id)
            return
        self._job_ids[s.id] = job_id
        log.debug("Scheduled %s (id=%d) every %d minutes", s.schedule_type, s.id, s.interval_minutes)

    def _unschedule(self, schedule_id: int) -> None:
        job_id = self._job_ids.pop(schedule_id, None)
        if job_id:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass

    # --- job entry points -------------------------------------------------

    def _run_scenario_evaluation(self, schedule_id: int) -> None:
        s = self._store.get(schedule_id)
        if not s or s.disabled:
            log.info("Schedule %d disabled or missing; skipping", schedule_id)
            return
        owner = self._store.get_user(s.owner_id)
        if not owner:
            self._store.disable(schedule_id, "owner_missing")
            log.info("Schedule %d disabled: owner missing", schedule_id)
            return
        from services.scheduling.model import owner_eligible
        ok, reason = owner_eligible(s.schedule_type, owner.get("role"), owner.get("is_active", True))
        if not ok:
            self._store.disable(schedule_id, reason)
            log.info("Schedule %d disabled: %s", schedule_id, reason)
            return
        self._enqueue("scenario_evaluation", schedule_id, s.payload,
                      owner.get("username") or "system")

    def _run_retention(self, schedule_id: int) -> None:
        s = self._store.get(schedule_id)
        if not s or s.disabled:
            log.info("Schedule %d disabled or missing; skipping", schedule_id)
            return
        owner = self._store.get_user(s.owner_id)
        if not owner:
            self._store.disable(schedule_id, "owner_missing")
            log.info("Schedule %d disabled: owner missing", schedule_id)
            return
        from services.scheduling.model import owner_eligible
        ok, reason = owner_eligible(s.schedule_type, owner.get("role"), owner.get("is_active", True))
        if not ok:
            self._store.disable(schedule_id, reason)
            log.info("Schedule %d disabled: %s", schedule_id, reason)
            return
        self._enqueue("retention", schedule_id, s.payload,
                      owner.get("username") or "system")

# ``apps/web/app.py`` and ``Api/routes/schedules.py`` import ``Scheduler``. The
# class has always been called ``ScheduleRunner``; the alias means one name for
# one thing rather than an ImportError on every start (which is what the app
# logged, and why no schedule ever fired).
Scheduler = ScheduleRunner
