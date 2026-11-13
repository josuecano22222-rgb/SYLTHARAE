"""Job state machine (unified operations job system).

States and the allowed transitions between them. The state machine is pure
data so it can be unit-tested without a database.
"""
from typing import Optional

# Terminal states
QUEUED = "QUEUED"
RUNNING = "RUNNING"
PAUSED = "PAUSED"
#: A *phase*, not a stored status: ``jobs.status`` (ck_jobs_status) admits
#: QUEUED/RUNNING/COMPLETED/COMPLETED_WITH_WARNINGS/FAILED/CANCELLED/PAUSED
#: only. A cancellation request sets ``jobs.cancellation_requested`` while the
#: job is still RUNNING, and the worker moves it to CANCELLED at the next safe
#: boundary. The constant stays because the transitions below - and the UI's
#: "cancelling" presentation - are expressed in terms of it.
CANCELLING = "CANCELLING"
CANCELLED = "CANCELLED"
COMPLETED = "COMPLETED"
COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
FAILED = "FAILED"

ALL_STATES = (
    QUEUED, RUNNING, PAUSED, CANCELLING, CANCELLED,
    COMPLETED, COMPLETED_WITH_WARNINGS, FAILED,
)

TERMINAL_STATES = {CANCELLED, COMPLETED, COMPLETED_WITH_WARNINGS, FAILED}

# Allowed transitions: from -> set of to
TRANSITIONS = {
    QUEUED: {RUNNING, CANCELLING, CANCELLED, FAILED},
    RUNNING: {PAUSED, CANCELLING, CANCELLED, COMPLETED,
              COMPLETED_WITH_WARNINGS, FAILED},
    PAUSED: {RUNNING, CANCELLING, CANCELLED},
    CANCELLING: {CANCELLED, FAILED},
}

# Event types emitted to job_events / SSE (spec section 10).
#
# This is the vocabulary `job_events.event_type` actually receives, so it must
# list every string the job system can write:
#   * the phase/file/data events below, emitted by the starters and the
#     throttle list in `services/jobs/manager.py`;
#   * the lifecycle events (`JOB_*`), emitted by the manager;
#   * `PROGRESS` - the throttle-related heartbeat, emitted per
#     `JOBS_PROGRESS_INTERVAL`;
#   * `JOB_PAUSED`;
#   * the terminal event, which carries the job **status** rather than a
#     separate name (see `Manager._finish` -> `_publish(job_id, status)`), so
#     `FAILED` is part of this vocabulary too.
# `tests/unit/test_job_event_vocabulary.py` derives the emitted set from those
# call sites and fails if a new string is published without being listed here;
# before that test existed this tuple was missing PROGRESS, JOB_PAUSED and
# FAILED - three values the manager writes - so it could not have been used to
# validate the column.
EVENT_TYPES = (
    "JOB_CREATED", "JOB_STARTED", "PHASE_STARTED", "FILE_DISCOVERED",
    "FILE_PROCESSING", "FILE_COMPLETED", "FILE_SKIPPED", "FILE_FAILED",
    "ARCHIVE_EXTRACTED", "TEXT_EXTRACTED", "OCR_COMPLETED",
    "DUPLICATE_DETECTED", "DATABASE_WRITE", "WARNING", "ERROR",
    "PHASE_COMPLETED", "JOB_COMPLETED", "JOB_CANCELLED",
    "JOB_PAUSED", "PROGRESS", FAILED,
)


def can_transition(current: str, new: str) -> bool:
    return new in TRANSITIONS.get(current, set())


def transition(current: str, new: str) -> str:
    """Return ``new`` if the transition is legal, else raise ValueError."""
    if not can_transition(current, new):
        raise ValueError(f"Illegal job state transition: {current} -> {new}")
    return new


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def final_state_for(has_errors: bool, has_warnings: bool) -> str:
    """Choose COMPLETED / COMPLETED_WITH_WARNINGS / FAILED by outcome."""
    if has_errors:
        return FAILED
    if has_warnings:
        return COMPLETED_WITH_WARNINGS
    return COMPLETED


def describe_status_row(row: dict, now=None) -> Optional[str]:
    """Human description helper for API serialization (unused by engine)."""
    if row is None:
        return None
    return str(row.get("status", ""))
