"""The single writer of path revision history (migration 0029).

Every recorded transition of a path goes through :func:`record_path_revision`
- one implementation, append-only, no rewrite. The Change report
(``change@1``) and any future consumer read the same log; an operation that
changes path metadata without calling this function is a defect: the change
would be invisible to the report, which is the "unrecordable change" the
table exists to prevent.

Discipline:

* ``kind`` is validated here, not only by the CHECK - a caller passing a
  future kind must fail loudly at the call site, not silently insert;
* values are JSON objects of the columns that changed (at least one),
  bound as ``%s::jsonb`` from ``json.dumps`` - never string-interpolated;
* the actor is recorded when the operation has one (``current_user``);
  a system operation passes ``None`` and the report says so - it never
  invents a person.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from Api.utils import execute_query

KINDS = ("modified", "removed")


def record_path_revision(path_id: int, kind: str, *,
                         old_values: Dict[str, Any],
                         new_values: Dict[str, Any],
                         actor_id: Optional[int] = None) -> int:
    """Append one revision event for ``path_id``; returns the event id."""
    if kind not in KINDS:
        raise ValueError(f"unknown path revision kind: {kind!r}")
    if not isinstance(old_values, dict) or not isinstance(new_values, dict):
        raise ValueError("revision values must be JSON objects")
    row = execute_query(
        "INSERT INTO path_revisions (path_id, changed_by, change_kind,"
        " old_values, new_values)"
        " VALUES (%s, %s, %s, %s::jsonb, %s::jsonb) RETURNING id",
        (int(path_id), actor_id, kind,
         json.dumps(old_values), json.dumps(new_values)),
        fetch="one",
    )
    return int(row[0])
