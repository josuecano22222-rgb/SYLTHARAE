"""The scenario engine's "high and imminent" test binds the reference date to
both placeholders of ``core.monitoring.priority.imminent_sql``.

``imminent_sql`` renders::

    s.date_from <= %s + INTERVAL '7 days' AND s.date_to >= %s

The window it describes is ``[reference_date, reference_date + IMMINENT_DAYS]``:
the first placeholder is the reference date (its end is derived from it) and
the second is that same reference date. The scenario engine bound
``(reference_date, reference_date + 7)``, so the second predicate became
``s.date_to >= reference_date + 7 days`` - a signal dated inside the window
but before its end was not imminent. ``high_and_imminent`` was therefore false
for every content, including content dated the reference date itself, and the
priority ladder in ``core.monitoring.priority.priority_from_summary`` never saw
the flag.

The expectation below is not read off the SQL: it is the answer
``priority_from_signal`` - the Python rule the SQL must agree with - gives for
the same signal.
"""

from __future__ import annotations

import datetime as dt

from core.criteria.compiler import AccessScope
from core.monitoring.priority import IMMINENT_DAYS, priority_from_signal
from services.monitoring.scenario_engine import decision_sql
from services.monitoring.scenario_model import parse_definition

TODAY = dt.date(2026, 10, 1)
INSIDE = TODAY + dt.timedelta(days=4)

DEFINITION = {
    "criteria": {"sources": [1]},
    "conditions": {"soon": {"signals": {"signal_types": ["date_reference"]}}},
    "cases": [{"id": "c_soon", "when": {"all": ["soon"]}, "outcome": "urgent"}],
    "outcomes": {"urgent": {"label": "Urgent", "actions": ["notify"]},
                 "none": {"label": "Nothing", "actions": []}},
    "default_outcome": "none",
    "strategy": "first_match",
}


def _imminent_params():
    """The two parameters bound to the ``imm`` expression, in SQL order."""
    decision = decision_sql(parse_definition(DEFINITION), AccessScope.system(), TODAY)
    before = decision.q.sql[:decision.q.sql.index(" AS imm")]
    # Both placeholders of the rendered expression come before " AS imm".
    first = before.count("%s") - 2
    return decision.q.params[first], decision.q.params[first + 1]


def test_both_placeholders_are_the_reference_date():
    first, second = _imminent_params()
    assert first == TODAY
    assert second == TODAY


def test_a_date_inside_the_window_is_imminent_for_the_sql_and_the_python_rule():
    first, second = _imminent_params()
    # The predicates the engine renders, evaluated on a date inside the window.
    sql_says_imminent = (INSIDE <= first + dt.timedelta(days=IMMINENT_DAYS)
                         and INSIDE >= second)
    python_says_imminent = priority_from_signal(
        {"signal_type": "date_reference", "date_from": INSIDE, "date_to": INSIDE,
         "confidence": "high"}, TODAY)
    assert python_says_imminent.value == "critical"
    assert sql_says_imminent, (first, second)
