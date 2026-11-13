"""The schedule model: what may be scheduled and what it must carry.

Pure validation and vocabulary - no database, no threads. The store and the
scheduler both speak this language, and the unit suite pins it.

Two job types are schedulable in step 20, each mapping onto an existing
JobManager job (no second job framework):

* ``scenario_evaluation`` - evaluates every active scenario (same privilege);
* ``retention``           - prunes aged rows per retention policy (administrators only).

Authorization is re-decided at every fire: the owner's current role and
activity are read from the database. A schedule whose owner lost the required
role is disabled with a stated reason instead of running anyway.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 525600  # one year

SCENARIO_EVALUATION = "scenario_evaluation"
RETENTION = "retention"

# A third type for the category workbook's reconciliation is designed but not
# schedulable: ``job_schedules.schedule_type`` carries a CHECK constraint from
# m0001 that admits exactly the two names above. Adding one needs an authorised
# migration; until then the workbook sync runs at startup, from the interface,
# or from the command line (docs/DATA_ENTRY.md).
SCHEDULE_TYPES = (SCENARIO_EVALUATION, RETENTION)

#: The role an owner must hold *at each fire* for the schedule to run.
#: "evaluate every scenario" is the same privilege as the manual all-scenarios trigger.
REQUIRED_ROLE = {SCENARIO_EVALUATION: "admin", RETENTION: "admin"}

DISABLE_REASONS = {
    "paused": "paused by the owner or an administrator",
    "owner_inactive": "the owner is deactivated",
    "owner_role": "the owner no longer holds the required role",
    "owner_missing": "the owner no longer exists",
    "definition_invalid": "the scheduled report/version/parameters no longer validate",
}


class ScheduleError(ValueError):
    """A refused schedule definition (message is safe to show)."""


def validate_interval(minutes: Any) -> int:
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ScheduleError("interval_minutes must be a whole number of minutes")
    if not (MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES):
        raise ScheduleError(
            f"interval_minutes must be between {MIN_INTERVAL_MINUTES} and "
            f"{MAX_INTERVAL_MINUTES}")
    return minutes


def validate_payload(schedule_type: str, payload: Any) -> Dict[str, Any]:
    """Validate ``payload`` for ``schedule_type``; returns the stored shape.

    ``scenario_evaluation`` payload: empty object means "every active scenario"
    (same as manual all-scenarios trigger). Optional {"scenario_id": int, "version": int}
    to evaluate a specific scenario.
    ``retention`` payload: optional {"policy": str, "max_age_days": int} to run
    a specific policy; empty means all enabled policies.
    """
    if schedule_type not in SCHEDULE_TYPES:
        raise ScheduleError(
            f"schedule_type must be one of: {', '.join(SCHEDULE_TYPES)}")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ScheduleError("payload must be an object")

    if schedule_type == SCENARIO_EVALUATION:
        extra = sorted(set(payload) - {"scenario_id", "version"})
        if extra:
            raise ScheduleError(f"unknown payload field(s): {', '.join(extra)}")
        if "scenario_id" in payload:
            scenario_id = payload.get("scenario_id")
            if not isinstance(scenario_id, int):
                raise ScheduleError("payload.scenario_id must be an integer")
            # Note: database existence check happens at runtime, not here
        version = payload.get("version")
        if version is not None and not isinstance(version, int):
            raise ScheduleError("payload.version must be an integer")
        return dict(payload)

    if schedule_type == RETENTION:
        extra = sorted(set(payload) - {"policy", "max_age_days"})
        if extra:
            raise ScheduleError(f"unknown payload field(s): {', '.join(extra)}")
        policy = payload.get("policy")
        if policy is not None and not isinstance(policy, str):
            raise ScheduleError("payload.policy must be a string")
        # Accept any string policy; validity checked at runtime
        max_age_days = payload.get("max_age_days")
        if max_age_days is not None and not isinstance(max_age_days, int):
            raise ScheduleError("payload.max_age_days must be an integer")
        return dict(payload)

    raise ScheduleError(f"schedule_type must be one of: {', '.join(SCHEDULE_TYPES)}")


#: How a stored payload maps onto the options the job actually reads.
#:
#: ``validate_payload`` pins the *stored* vocabulary; ``run_retention`` and
#: ``run_scenario_evaluation`` speak a different one. Nothing translated between
#: them, and the runner used to pass ``schedule_id`` instead - an option no job
#: reads - so a schedule's payload never reached its job. Keys the job cannot
#: honour are returned alongside the translation so the caller can say so out
#: loud rather than drop them in silence.
PAYLOAD_TARGET = {
    "retention": {"policy": "areas"},
    "scenario_evaluation": {"scenario_id": "scenario_ids"},
}


def job_options(schedule_type: str, payload: Any) -> tuple:
    """``(options, dropped)`` - the job's keyword arguments for this payload.

    ``dropped`` maps each stored key that has no counterpart to the reason. An
    unknown retention ``policy`` is dropped rather than handed to the job: the
    job would ask the policy table, which answers with a message the operator
    cannot act on from a log line.
    """
    if schedule_type not in SCHEDULE_TYPES:
        raise ScheduleError(
            f"schedule_type must be one of: {', '.join(SCHEDULE_TYPES)}")
    payload = dict(payload or {})
    targets = PAYLOAD_TARGET.get(schedule_type, {})
    options: Dict[str, Any] = {}
    dropped: Dict[str, str] = {}
    for key, value in sorted(payload.items()):
        if key not in targets:
            dropped[key] = "no counterpart in the job's options"
            continue
        if key == "policy":
            from services.retention.model import POLICIES

            if value not in POLICIES:
                dropped[key] = f"{value!r} is not a retention area"
                continue
            options["areas"] = [value]
        elif key == "scenario_id":
            options["scenario_ids"] = [value]
        else:                                    # a mapping with no rule yet
            dropped[key] = "no translation is defined"
    return options, dropped


def required_role(schedule_type: str) -> str:
    if schedule_type not in SCHEDULE_TYPES:
        raise ScheduleError(
            f"schedule_type must be one of: {', '.join(SCHEDULE_TYPES)}")
    return REQUIRED_ROLE[schedule_type]


def owner_eligible(schedule_type: str, role: Optional[str],
                   is_active: bool) -> Tuple[bool, Optional[str]]:
    """Whether an owner with this role/activity may still run the schedule."""
    if not is_active:
        return False, "owner_inactive"
    needed = required_role(schedule_type)
    if needed == "admin":
        return (True, None) if role == "admin" else (False, "owner_role")
    return (True, None) if role in ("admin", "analyst") else (False, "owner_role")


def describe(schedule_type: str, payload: Dict[str, Any]) -> str:
    """One line for the page: what the schedule runs."""
    if schedule_type == RETENTION:
        return "every enabled retention policy"
    return "every active scenario"