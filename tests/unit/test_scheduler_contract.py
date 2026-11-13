"""The scheduler's own contract: the methods it calls must exist.

``services/scheduling/scheduler.py`` documents its whole job as "call
``JobManager.enqueue`` at the right times". It did - into a method that class
did not define, so every fire raised ``AttributeError`` inside APScheduler, and
no scheduled job has ever run in this tree.

This test reads the scheduler's source and resolves every call it makes on its
two collaborators against those classes. It is deliberately an existing-names
check, not a behavioural one: it catches the class of defect that no behavioural
test caught, because no test ever let a schedule fire.

``self._store`` is not checked to the same standard yet: the module
``services/scheduling/store.py`` is a set of functions, and the runner expects
an object with ``list_enabled``/``get``/``get_user``/``disable``. That gap is
recorded in ``audit_report/r22_workbook/REPORT.md`` as DEFECT-OPEN; the check
here is pinned to the collaborator that does exist.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SCHEDULER = ROOT / "services" / "scheduling" / "scheduler.py"


def _calls_on(source: Path, attribute: str):
    """Every ``self.<attribute>(...)`` in the file, as (method name, line)."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute)
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "self"
                and func.value.attr == attribute):
            found.append((func.attr, node.lineno))
    return found


def test_the_job_manager_calls_in_the_scheduler_all_exist():
    from services.jobs.manager import JobManager

    calls = _calls_on(SCHEDULER, "_job_manager")
    assert calls, "the scheduler no longer calls the job manager - did it move?"
    missing = [(name, line) for name, line in calls
               if not hasattr(JobManager, name)]
    assert not missing, f"the scheduler calls methods JobManager lacks: {missing}"


def test_enqueue_passes_the_schedule_options_to_the_job():
    """The options a schedule carries must reach the job, not be dropped."""
    from services.jobs.manager import JobManager

    recorded = {}

    class Recorder(JobManager):
        def create_job(self, job_type, source, options, created_by=None, starter=None):
            recorded.update({"job_type": job_type, "source": source,
                             "options": options, "created_by": created_by})
            return {"job_id": "X"}

    Recorder(repository=object()).enqueue("taxonomy_sync", created_by="alice",
                                          data_file="domain_data.xlsx", apply=True)
    assert recorded["job_type"] == "taxonomy_sync"
    assert recorded["options"] == {"data_file": "domain_data.xlsx", "apply": True}
    assert recorded["created_by"] == "alice"
    assert recorded["source"].startswith("schedule:")


def test_a_fire_without_a_creator_is_attributed_to_the_system():
    from services.jobs.manager import JobManager

    class Recorder(JobManager):
        def create_job(self, job_type, source, options, created_by=None, starter=None):
            return {"created_by": created_by}

    assert Recorder(repository=object()).enqueue("retention")["created_by"] == "system"


class _Schedule:
    def __init__(self, *, schedule_type="retention", disabled=False,
                 owner_id=1, payload=None):
        self.id = 5
        self.schedule_type = schedule_type
        self.disabled = disabled
        self.owner_id = owner_id
        self.payload = payload or {}


class _Store:
    """The shape ``ScheduleRunner`` expects (get / get_user / disable)."""

    def __init__(self, schedule, owner=None, disabled=False):
        self.schedule = schedule
        self.owner = owner if owner is not None else {
            "username": "alice", "role": "admin", "is_active": True}
        self.disabled = []
        self._disabled_flag = disabled

    def get(self, _schedule_id):
        return None if self._disabled_flag else self.schedule

    def get_user(self, _owner_id):
        return self.owner

    def disable(self, schedule_id, reason):
        self.disabled.append((schedule_id, reason))


class _Manager:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, job_type, **options):
        self.enqueued.append((job_type, options))
        return {"job_id": "J"}


def _runner(store):
    from services.scheduling.scheduler import ScheduleRunner

    manager = _Manager()
    # Not started: the tests call the fire path directly, which is exactly the
    # path APScheduler would take.
    return ScheduleRunner(job_manager=manager, store=store), manager


def test_a_schedule_fires_the_job_its_type_names():
    """retention, the type that exists: the schema allows two, not three."""
    store = _Store(_Schedule(schedule_type="retention"))
    runner, manager = _runner(store)
    runner.fire_now({"id": 5, "schedule_type": "retention", "owner_user_id": 1})

    assert len(manager.enqueued) == 1
    job_type, options = manager.enqueued[0]
    assert job_type == "retention"
    assert options["created_by"] == "alice"
    assert store.disabled == []


def _fire(runner, schedule_type="retention"):
    return runner.fire_now({"id": 5, "schedule_type": schedule_type, "owner_user_id": 1})


def test_a_schedule_whose_owner_lost_the_role_is_disabled_not_run():
    store = _Store(_Schedule(), owner={"username": "bob", "role": "analyst",
                                       "is_active": True})
    runner, manager = _runner(store)
    _fire(runner)

    assert manager.enqueued == [], "an unauthorised schedule still ran"
    assert store.disabled == [(5, "owner_role")]


def test_an_inactive_owner_disables_the_schedule():
    store = _Store(_Schedule(), owner={"username": "bob", "role": "admin",
                                       "is_active": False})
    runner, manager = _runner(store)
    _fire(runner)
    assert manager.enqueued == []
    assert store.disabled == [(5, "owner_inactive")]


def test_a_missing_owner_disables_the_schedule():
    store = _Store(_Schedule(), owner=None)
    store.owner = None
    runner, manager = _runner(store)
    _fire(runner)
    assert manager.enqueued == []
    assert store.disabled == [(5, "owner_missing")]


def test_an_unknown_schedule_type_is_an_error_not_a_job():
    """A row naming a type the vocabulary does not know is never guessed at.

    ``job_schedules.schedule_type`` admits two values (a CHECK constraint in
    m0001), so a third cannot exist in the table today; this pins what happens
    if one ever does, or if the row is read from somewhere unconstrained.
    """
    store = _Store(_Schedule(schedule_type="taxonomy_sync"))
    runner, manager = _runner(store)
    outcome = _fire(runner, schedule_type="taxonomy_sync")
    assert manager.enqueued == [], "a type the vocabulary lacks was fired anyway"
    assert outcome["status"] == "error"
    assert "schedule_type must be one of" in outcome["message"]
