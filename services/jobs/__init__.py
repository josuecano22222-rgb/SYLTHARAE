"""Persistent job system: manager, state machine, repository, events."""
from services.jobs import job_state
from services.jobs.job_state import (
    ALL_STATES,
    CANCELLED,
    CANCELLING,
    COMPLETED,
    COMPLETED_WITH_WARNINGS,
    FAILED,
    PAUSED,
    QUEUED,
    RUNNING,
    is_terminal,
)

__all__ = ["JobManager", "JobsConfig", "job_state", "QUEUED",
           "RUNNING", "PAUSED", "CANCELLING", "CANCELLED",
           "COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED",
           "is_terminal", "ALL_STATES"]
