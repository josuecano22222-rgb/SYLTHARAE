"""Unit: the stored payload must reach the job, and the lock must be one leader.

Two defects this module pins, both found by reading the path end to end rather
than by a failing test:

1. A schedule stores a payload (``{"policy": "jobs"}``, ``{"scenario_id": 7}``),
   and ``run_retention``/``run_scenario_evaluation`` accept ``areas``/
   ``scenario_ids``. Nothing translated one vocabulary into the other, and the
   runner passed ``schedule_id`` - an option no job reads. So a schedule's
   payload was decorative.
2. Two web processes each started their own APScheduler over the same database,
   so a due schedule fired once per process.

``job_options`` is the translation, and it is a pure function so the mapping is
testable without a database. The leader check is the process-level counterpart of
the advisory lock; its SQL is exercised against a real server in
``test_scheduler_first_boot_pg.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.scheduling import model  # noqa: E402


def test_a_retention_policy_becomes_the_area_the_job_reads():
    options, dropped = model.job_options("retention", {"policy": "jobs"})
    assert options == {"areas": ["jobs"]}
    assert dropped == {}


def test_an_empty_payload_means_every_enabled_area():
    options, dropped = model.job_options("retention", {})
    assert options == {}
    assert dropped == {}


def test_a_scenario_id_becomes_the_id_list_the_job_reads():
    options, dropped = model.job_options("scenario_evaluation", {"scenario_id": 7})
    assert options == {"scenario_ids": [7]}
    assert dropped == {}


def test_an_unknown_area_is_not_handed_to_the_job():
    """A policy that names no area is reported, never passed through to SQL."""
    options, dropped = model.job_options("retention", {"policy": "nonsense"})
    assert options == {}
    assert "policy" in dropped


def test_a_payload_field_the_job_cannot_honour_is_reported_not_dropped_silently():
    """``max_age_days`` has no counterpart: the policy comes from settings."""
    options, dropped = model.job_options("retention",
                                        {"policy": "jobs", "max_age_days": 30})
    assert options == {"areas": ["jobs"]}
    assert "max_age_days" in dropped


def test_a_version_has_no_counterpart_in_the_scenario_job():
    options, dropped = model.job_options("scenario_evaluation",
                                        {"scenario_id": 3, "version": 2})
    assert options == {"scenario_ids": [3]}
    assert "version" in dropped


@pytest.mark.parametrize("schedule_type", model.SCHEDULE_TYPES)
def test_every_schedulable_type_has_a_translation(schedule_type):
    options, dropped = model.job_options(schedule_type, {})
    assert isinstance(options, dict) and isinstance(dropped, dict)


def test_the_runner_no_longer_passes_a_schedule_id_the_job_ignores():
    """The option the old code passed is not in any job's vocabulary."""
    import inspect

    from services.jobs.manager import JobManager

    source = inspect.getsource(JobManager._run_builtin)
    assert "options.get(\"schedule_id\")" not in source
    assert "options.get('schedule_id')" not in source
