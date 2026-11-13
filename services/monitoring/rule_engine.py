"""Shared utilities for monitoring rule/scenario evaluation.

This module provides the common evaluation helpers used by both rule and
scenario engines. The rule-specific evaluation logic has been removed;
only the shared utilities remain.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from core.criteria.access import AccessScope


#: Maximum individual notifications per evaluation (overflow goes to summary).
MAX_ALERTS_PER_EVALUATION = 50

#: Confidence ranking for SQL: high=3, medium=2, low=1, unknown=0.
_CONF_RANK_SQL = ("CASE s.confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2"
                  " WHEN 'low' THEN 1 ELSE 0 END")

#: Confidence levels ordered by severity.
CONFIDENCE_LEVELS = ("low", "medium", "high")


def _conditions(definition, compiled, reference_date: datetime.date):
    """Build WHERE conditions and parameters for a rule/scenario definition's signals.

    Returns (conds, params) where conds is a list of SQL fragments and params
    is the corresponding list of parameter values.
    """
    from services.detection import signal_query

    conds, params = signal_query._where(definition.signals, compiled)
    if definition.min_confidence:
        floor = CONFIDENCE_LEVELS.index(definition.min_confidence)
        conds.append("s.confidence = ANY(%s)")
        params.append(list(CONFIDENCE_LEVELS[floor:]))
    if definition.event_window_days:
        lo, hi = definition.event_window_days
        conds.append("s.date_to >= %s AND s.date_from <= %s")
        params.extend([reference_date + datetime.timedelta(days=lo),
                       reference_date + datetime.timedelta(days=hi)])
    return conds, params


def _scope_json(scope: AccessScope) -> Dict[str, Any]:
    """Serialize an AccessScope for storage with an evaluation record."""
    return {"user_id": scope.user_id, "role": scope.role,
            "allowed_source_ids": (list(scope.allowed_source_ids)
                                   if scope.allowed_source_ids is not None else None),
            "suppressed_source_count": scope.suppressed_source_count}