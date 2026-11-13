"""Horizon bucket primitives - the one definition of the temporal horizon.

The horizon groups resolved temporal signals by how soon their detected
event date arrives, relative to a reference date R:

``overdue``           to < R *and* the sentence is future-oriented (the text
                      promised something by a day that has passed);
``week``              to >= R and from < R + 7 (includes ranges in progress)
``month``             R + 7 <= from < R + 30
``quarter``           R + 30 <= from < R + 90
``later``             from >= R + 90
``past``              to < R and not future-oriented (the explorer's sixth
                      bucket; not part of the horizon proper).

Bucketing uses the earliest possible day of a range (Hijri dates are stored
as +/-2-day ranges). Undated signals are counted separately (``undated``),
never dropped and never placed in a bucket.

This module lives in ``core/detection`` so that both consumers of the
definition - the Signal Explorer query (``services/detection/signal_query``)
and the registered report datasets (``core/reporting/datasets``) - import
one implementation. ``core`` never imports ``services``.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.detection.temporal_intel import SIGNAL_DATE, SIGNAL_RELATIVE

#: (key, first day offset, end offset exclusive) relative to the reference date.
BUCKETS: Tuple[Tuple[str, Optional[int], Optional[int]], ...] = (
    ("overdue", None, 0),
    ("week", 0, 7),
    ("month", 7, 30),
    ("quarter", 30, 90),
    ("later", 90, None),
    ("past", None, 0),
)
BUCKET_KEYS = tuple(b[0] for b in BUCKETS)
#: What ``horizon`` lists when no bucket is requested: the horizon proper.
DEFAULT_BUCKETS = ("overdue", "week", "month", "quarter", "later")
#: The signal types the horizon shows: resolved dates and relative references.
HORIZON_SIGNAL_TYPES = (SIGNAL_DATE, SIGNAL_RELATIVE)


def bucket_sql() -> str:
    """The horizon bucket of signal ``s`` relative to ``ref.r``, the reference
    date. Queries using it join ``REF_SQL``, which binds that date as a
    parameter (no value is ever written into the SQL text)."""
    return ("CASE WHEN s.date_to < ref.r THEN"
            " (CASE WHEN s.text_orientation = 'future' THEN 'overdue' ELSE 'past' END)"
            " WHEN s.date_from < ref.r + 7 THEN 'week'"
            " WHEN s.date_from < ref.r + 30 THEN 'month'"
            " WHEN s.date_from < ref.r + 90 THEN 'quarter'"
            " ELSE 'later' END")


#: One-row relation carrying the reference date; takes one parameter.
REF_SQL = " CROSS JOIN (SELECT %s::date AS r) ref"


def bucket_ranges(reference_date: datetime.date) -> List[Dict[str, Any]]:
    out = []
    for key, start, end in BUCKETS:
        out.append({
            "key": key,
            "from": (reference_date + datetime.timedelta(days=start)).isoformat()
            if start is not None else None,
            "to": (reference_date + datetime.timedelta(days=end - 1)).isoformat()
            if end is not None else None,
        })
    return out
