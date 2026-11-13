"""
Priority derivation for notifications.

A notification's priority is derived from its evidence, never chosen by hand.
This module contains the single function that maps a signal (temporal or
place) and a reference date to a priority level.
"""

from datetime import date, datetime, timedelta
from core.monitoring.notification_service import NotificationPriority


# Rule version for audit trail - matches the SQL function's expected constant.
PRIORITY_RULE_VERSION = "priority-1"

# A date within this many days is "imminent".
IMMINENT_DAYS = 7


def imminent_sql(date_from_col: str, date_to_col: str) -> str:
    """SQL fragment: true when the signal's date range intersects the
    next IMMINENT_DAYS window from the reference date (passed as a
    parameter in the surrounding query)."""
    return (
        f"({date_from_col} IS NOT NULL AND {date_from_col} <= %s + INTERVAL '{IMMINENT_DAYS} days' "
        f"AND {date_to_col} >= %s)"
    )


def priority_from_signal(signal: dict, reference_date: date) -> NotificationPriority:
    """Derive priority from a temporal signal and a reference date.

    The signal dict contains at least ``signal_type``, ``date_from``,
    ``date_to`` (both ``date`` or ISO strings), ``confidence``
    (``high``/``medium``/``low``/None), and optionally ``text_orientation``.

    Priority rules (core.monitoring.priority):
    1. CRITICAL: confidence='high' AND date is imminent (within IMMINENT_DAYS)
    2. HIGH: confidence='high' OR (confidence='medium' AND date is imminent)
    3. MEDIUM: confidence='medium' OR (confidence='low' AND date is imminent)
    4. LOW: everything else (including no date, no confidence)
    """
    # Parse dates if they're strings
    date_from = signal.get('date_from')
    date_to = signal.get('date_to')
    if isinstance(date_from, str):
        date_from = date.fromisoformat(date_from)
    if isinstance(date_to, str):
        date_to = date.fromisoformat(date_to)

    confidence = signal.get('confidence')

    # Imminent window
    imminent_end = reference_date + timedelta(days=IMMINENT_DAYS)
    is_imminent = (date_from is not None and date_from <= imminent_end
                   and date_to is not None and date_to >= reference_date)

    if confidence == 'high' and is_imminent:
        return NotificationPriority.CRITICAL
    if confidence == 'high':
        return NotificationPriority.HIGH
    if confidence == 'medium' and is_imminent:
        return NotificationPriority.HIGH
    if confidence == 'medium':
        return NotificationPriority.MEDIUM
    if confidence == 'low' and is_imminent:
        return NotificationPriority.MEDIUM
    return NotificationPriority.LOW


def priority_basis(summary: dict, reference_date: date) -> dict:
    """The ``priority_basis`` record stored with an outcome.

    Same keys, same values, same types as the JSONB object
    ``services.monitoring.scenario_engine.decision_sql`` builds while it
    decides - the two must not drift, because the record is how a reader
    reconstructs why a row was labelled the way it was, and the SQL/derivation
    agreement test compares them literally.

    ``earliest_event_date`` is an ISO date string (as JSONB holds it) and
    ``highest_confidence`` is ``None`` when no decisive case carried a
    confidence - the SQL leaves the key null in that situation too.
    """
    earliest = summary.get('earliest_event_date')
    if isinstance(earliest, (date, datetime)):
        earliest = earliest.isoformat()
    return {
        'rule': PRIORITY_RULE_VERSION,
        'high_and_imminent': bool(summary.get('high_and_imminent')),
        'highest_confidence': summary.get('highest_confidence'),
        'unrecorded_confidence': summary.get('unrecorded_confidence', 0),
        'matches': summary.get('matches'),
        'earliest_event_date': earliest,
        'reference_date': reference_date.isoformat(),
        'imminent_days': IMMINENT_DAYS,
    }


def priority_from_summary(summary: dict, reference_date: date) -> tuple[NotificationPriority, dict]:
    """Derive priority from a group summary (used by scenario/rule delivery).

    ``summary`` has keys: ``highest_confidence`` (high/medium/low/None),
    ``high_and_imminent`` (bool), ``earliest_event_date`` (date/None), and the
    counts ``matches`` / ``unrecorded_confidence`` that
    :func:`priority_basis` records.

    Returns ``(priority, basis)`` where basis is that complete
    ``priority_basis`` record - not a prose note - so the caller stores the
    same evidence the rule used.

    The ladder is the signal rule's (see :func:`priority_from_signal`) keyed
    on the strongest confidence among the decisive cases, then the imminent
    cases: high -> HIGH, medium (or low+imminent) -> MEDIUM, else LOW.

    The summary form tops out at ``high``: ``scenario_outcomes.priority``
    accepts ``high``/``medium``/``low`` only, and the row recorded for an
    evaluation is the value the delivery reads back. The *signal* rule
    (:func:`priority_from_signal`, feeding a single alert) may return
    ``critical`` because ``alerts.priority`` accepts it. An imminent,
    high-confidence summary therefore lands on ``high`` - the highest rung the
    outcome vocabulary admits - and the basis record shows
    ``high_and_imminent`` true so a report can see the ceiling was storage's,
    not policy's.
    """
    highest = summary.get('highest_confidence')
    high_imminent = summary.get('high_and_imminent', False)
    basis = priority_basis(summary, reference_date)

    if highest == 'high':
        return NotificationPriority.HIGH, basis
    if highest == 'medium' and high_imminent:
        return NotificationPriority.HIGH, basis
    if highest == 'medium':
        return NotificationPriority.MEDIUM, basis
    if highest == 'low' and high_imminent:
        return NotificationPriority.MEDIUM, basis
    return NotificationPriority.LOW, basis
