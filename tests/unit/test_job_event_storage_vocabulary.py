"""Every job event the code writes is storable in ``job_events``.

``job_events.event_type`` has a closed vocabulary (a CHECK in the baseline
migration), while the job system speaks a richer one - per-file, per-phase and
warning/error events. The two are reconciled in exactly one place,
``services.jobs.repository.stored_event``, and this module pins that:
translation may rename an event, but it may never produce a value the column
rejects, and it must not lose the original name.

The allowed set is read from the frozen baseline migration rather than typed
here, so the test follows the schema instead of asserting a copy of it.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.unit


def _allowed_event_types() -> set[str]:
    """The vocabulary the baseline migration's CHECK admits."""
    source = (ROOT / "database" / "migrations" / "m0001_baseline.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        if getattr(target, "id", None) == "_JOB_EVENT_TYPES" and value is not None:
            literal = _flatten(value)
            return {part.strip().strip("'\"")
                    for part in str(literal).split("',") if part.strip()}
    raise AssertionError("_JOB_EVENT_TYPES not found in the baseline migration")


def _flatten(node) -> str:
    """The literal value of a joined string expression (``a + b + c``)."""
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _flatten(node.left) + _flatten(node.right)
    raise AssertionError(f"cannot read {ast.dump(node)[:60]}")


def _emitted_kinds() -> set[str]:
    """Every event name the job system can write (its own declared set)."""
    from services.jobs import job_state

    return set(job_state.EVENT_TYPES)


@pytest.fixture(scope="module")
def allowed():
    values = _allowed_event_types()
    assert len(values) >= 5, values
    return values


def test_the_translation_never_produces_a_rejected_value(allowed):
    from services.jobs.repository import stored_event

    rejected = {}
    for kind in _emitted_kinds():
        stored, _payload = stored_event(kind, {"message": "x"})
        if stored not in allowed:
            rejected[kind] = stored
    assert not rejected, (
        "these emitted events would be stored as a value the CHECK rejects: "
        f"{rejected}")


def test_the_lifecycle_events_are_stored_under_their_own_name(allowed):
    from services.jobs.repository import stored_event

    for kind in ("JOB_CREATED", "JOB_STARTED", "JOB_COMPLETED", "JOB_CANCELLED",
                 "JOB_FAILED", "JOB_PAUSED", "PROGRESS"):
        if kind not in _emitted_kinds() or kind not in allowed:
            continue
        stored, _ = stored_event(kind, {})
        assert stored == kind, f"{kind} was renamed to {stored}"


def test_a_translated_event_keeps_the_original_kind():
    from services.jobs.repository import stored_event

    stored, payload = stored_event("WARNING", {"message": "Cancellation requested"})
    assert stored != "WARNING"
    assert payload["event_kind"] == "WARNING"
    assert payload["message"] == "Cancellation requested"

    stored, payload = stored_event("FAILED", {"reason": "crash recovery"})
    assert stored == "JOB_FAILED"
    assert payload["event_kind"] == "FAILED"


def test_an_unknown_event_is_stored_rather_than_dropped(allowed):
    from services.jobs.repository import stored_event

    stored, payload = stored_event("SOMETHING_NEW", {"a": 1})
    assert stored in allowed
    assert payload["event_kind"] == "SOMETHING_NEW"


def test_the_stored_vocabulary_matches_the_migration(allowed):
    from services.jobs.repository import STORED_EVENT_TYPES

    assert set(STORED_EVENT_TYPES) == set(allowed)
