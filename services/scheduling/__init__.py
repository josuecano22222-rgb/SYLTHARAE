"""Scheduling (step 20): interval-based schedules over existing JobManager jobs.

* :mod:`services.scheduling.model` - the vocabulary and pure validation;
* :mod:`services.scheduling.store` - CRUD and the atomic due-claim;
* :mod:`services.scheduling.scheduler` - the tick loop that fires due
  schedules through the JobManager (never a second job framework).

Every fire re-reads the owner's current role and activity; an owner who
lost the required role, or a report payload that no longer validates,
disables the schedule with a stated reason instead of running anyway.
Claiming advances ``next_run_at`` before enqueueing (at-most-once), so
downtime yields one catch-up fire, never a burst.
"""

from services.scheduling.model import (  # noqa: F401
    MIN_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES, SCHEDULE_TYPES,
    ScheduleError, validate_interval, validate_payload, required_role,
    owner_eligible, describe, DISABLE_REASONS,
)
