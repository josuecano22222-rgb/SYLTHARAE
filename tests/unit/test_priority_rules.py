"""The two priority rules in core/monitoring/priority.py, without a database.

``priority_from_signal`` labels one alert and may reach ``critical``;
``priority_from_summary`` labels an evaluation outcome, whose column admits
``high``/``medium``/``low`` only, so it stops at ``high``. Both return a
``NotificationPriority`` that compares equal to the text stored in the
database, and the summary rule returns the same ``priority_basis`` record the
deciding SQL writes.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.monitoring.notification_service import NotificationPriority
from core.monitoring.priority import (
    IMMINENT_DAYS,
    PRIORITY_RULE_VERSION,
    priority_basis,
    priority_from_signal,
    priority_from_summary,
)

TODAY = dt.date(2026, 10, 1)
IN_A_WEEK = (TODAY + dt.timedelta(days=3)).isoformat()


def summary(**overrides) -> dict:
    base = {"highest_confidence": "high", "high_and_imminent": False,
            "earliest_event_date": TODAY + dt.timedelta(days=3),
            "matches": 2, "unrecorded_confidence": 0}
    base.update(overrides)
    return base


# --- the values are the stored vocabulary -----------------------------------

def test_priority_values_compare_equal_to_the_stored_text():
    # alerts.priority and scenario_outcomes.priority are VARCHAR; a value read
    # back is a plain str. The derived enum must equal it, otherwise every
    # read-side comparison silently fails on the type.
    for level in NotificationPriority:
        assert level == level.value
        assert level.value == level


# --- the signal rule --------------------------------------------------------

@pytest.mark.parametrize("confidence,date_from,expected", [
    ("high", TODAY.isoformat(), NotificationPriority.CRITICAL),
    ("high", "2027-01-01", NotificationPriority.HIGH),
    ("medium", TODAY.isoformat(), NotificationPriority.HIGH),
    ("medium", "2027-01-01", NotificationPriority.MEDIUM),
    ("low", TODAY.isoformat(), NotificationPriority.MEDIUM),
    ("low", "2027-01-01", NotificationPriority.LOW),
    (None, TODAY.isoformat(), NotificationPriority.LOW),
])
def test_signal_rule_ladder(confidence, date_from, expected):
    signal = {"confidence": confidence, "date_from": date_from,
              "date_to": date_from, "signal_type": "date_reference"}
    assert priority_from_signal(signal, TODAY) is expected


def test_imminent_window_is_the_documented_one():
    signal = {"confidence": "high", "signal_type": "date_reference",
              "date_from": (TODAY + dt.timedelta(days=IMMINENT_DAYS)).isoformat(),
              "date_to": (TODAY + dt.timedelta(days=IMMINENT_DAYS)).isoformat()}
    assert priority_from_signal(signal, TODAY) is NotificationPriority.CRITICAL
    later = dict(signal, date_from=(TODAY + dt.timedelta(days=IMMINENT_DAYS + 1)).isoformat(),
                 date_to=(TODAY + dt.timedelta(days=IMMINENT_DAYS + 1)).isoformat())
    assert priority_from_signal(later, TODAY) is NotificationPriority.HIGH


# --- the summary rule -------------------------------------------------------

def test_summary_rule_is_capped_at_high():
    # An imminent, high-confidence group is the strongest case the summary
    # rule can see. alerts.priority would take 'critical' here; the outcome
    # column does not, so the ceiling of this rule is 'high'.
    priority, basis = priority_from_summary(
        summary(highest_confidence="high", high_and_imminent=True), TODAY)
    assert priority is NotificationPriority.HIGH
    assert priority == "high"
    assert basis["high_and_imminent"] is True


@pytest.mark.parametrize("highest,imminent,expected", [
    ("high", False, NotificationPriority.HIGH),
    ("medium", True, NotificationPriority.HIGH),
    ("medium", False, NotificationPriority.MEDIUM),
    ("low", True, NotificationPriority.MEDIUM),
    ("low", False, NotificationPriority.LOW),
    (None, False, NotificationPriority.LOW),
])
def test_summary_rule_ladder(highest, imminent, expected):
    priority, _ = priority_from_summary(
        summary(highest_confidence=highest, high_and_imminent=imminent), TODAY)
    assert priority is expected


def test_summary_basis_is_the_record_the_sql_writes():
    s = summary(highest_confidence="high", high_and_imminent=True, matches=3,
                unrecorded_confidence=1,
                earliest_event_date=dt.date(2026, 10, 4))
    _, basis = priority_from_summary(s, TODAY)
    assert basis == {
        "rule": PRIORITY_RULE_VERSION,
        "high_and_imminent": True,
        "highest_confidence": "high",
        "unrecorded_confidence": 1,
        "matches": 3,
        "earliest_event_date": "2026-10-04",
        "reference_date": TODAY.isoformat(),
        "imminent_days": IMMINENT_DAYS,
    }
    assert basis == priority_basis(s, TODAY)


def test_basis_keeps_an_absent_date_and_confidence_null():
    _, basis = priority_from_summary(
        summary(highest_confidence=None, earliest_event_date=None, matches=0), TODAY)
    assert basis["earliest_event_date"] is None
    assert basis["highest_confidence"] is None
    assert basis["matches"] == 0
