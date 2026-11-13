"""Unit: the job event vocabulary lists what the job system actually emits.

`job_state.EVENT_TYPES` describes the values `job_events.event_type` receives.
It is read by `tools/db/integrity_audit.py` to check a database for values
outside the vocabulary, so a constant that omits a value the manager writes
turns a real write into a reported violation - and, the other way round, would
let a CHECK constraint (or any validator built on it) reject the application's
own events.

That is not hypothetical: before this test the tuple was missing `PROGRESS`
(emitted on every throttle heartbeat), `JOB_PAUSED`, and the terminal status
`FAILED` (emitted by `Manager._finish`, which publishes the job status as the
event). The test derives the emitted set from the call sites instead of trusting
a hand-maintained list.
"""

from __future__ import annotations

import pathlib
import re

from services.jobs import job_state

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANAGER = ROOT / "services" / "jobs" / "manager.py"

#: `self._publish(job_id, "JOB_STARTED", ...)` / `throttle.add("WARNING", ...)`
_CALL_LITERAL = re.compile(
    r'(?:_publish|add_event|throttle\.add)\(\s*[^,)]*?,?\s*"([A-Z][A-Z_]{2,})"')

#: `self._finish(job_id, job_state.FAILED, ...)` - the terminal event carries
#: the status, so the status constant is part of the event vocabulary.
_FINISH_STATE = re.compile(r"_finish\(\s*[^,)]*?,\s*job_state\.([A-Z_]+)")


def _emitted_event_types() -> set:
    emitted = set(_CALL_LITERAL.findall(MANAGER.read_text(encoding="utf-8")))
    for constant in _FINISH_STATE.findall(MANAGER.read_text(encoding="utf-8")):
        emitted.add(getattr(job_state, constant))
    return emitted


def _event_writes_in_services() -> set:
    """Every literal passed to `add_event(...)` outside the manager."""
    literals = set()
    for path in (ROOT / "services").rglob("*.py"):
        if path == MANAGER or "__pycache__" in path.parts:
            continue
        literals.update(_CALL_LITERAL.findall(path.read_text(errors="ignore")))
    return literals


def test_every_event_the_manager_emits_is_in_the_vocabulary():
    emitted = _emitted_event_types()
    assert emitted, "the extraction found no emitted events; the test is broken"
    missing = emitted - set(job_state.EVENT_TYPES)
    assert not missing, (
        f"services/jobs/manager.py emits {sorted(missing)} but "
        "job_state.EVENT_TYPES does not list them")


def test_every_add_event_literal_in_services_is_in_the_vocabulary():
    missing = _event_writes_in_services() - set(job_state.EVENT_TYPES)
    assert not missing, (
        f"add_event() is called with {sorted(missing)}, which "
        "job_state.EVENT_TYPES does not list")


def test_the_values_the_audit_reported_missing_are_present():
    """The three the constant was missing, kept as a named regression."""
    for value in ("PROGRESS", "JOB_PAUSED", job_state.FAILED):
        assert value in job_state.EVENT_TYPES


def test_the_vocabulary_has_no_duplicates():
    assert len(job_state.EVENT_TYPES) == len(set(job_state.EVENT_TYPES))
