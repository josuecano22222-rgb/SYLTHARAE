"""Shared utilities for monitoring scenarios.

This module provides the common error type and helper functions used by
scenario storage. The rule-specific storage functions have been removed.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras


class RuleError(Exception):
    """A request refused for a stated reason. ``status`` is the HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _not_found(what: str = "scenario") -> RuleError:
    # Absent and not permitted are the same answer.
    return RuleError("NOT_FOUND", f"{what} not found", 404)


def owner_eligibility(cur, user_id: int) -> Tuple[bool, Optional[str], Optional[str]]:
    """``(eligible, reason, role)`` for a scenario owner, read now.

    Scenarios follow the same eligibility as rules: active analyst or administrator.
    """
    cur.execute("SELECT role, is_active FROM users WHERE id = %s", (user_id,))
    row = _as_dict(cur, cur.fetchone())
    if row is None:
        return False, "owner_inactive", None
    role, active = row["role"], row["is_active"]
    if not active:
        return False, "owner_inactive", role
    if role not in ("analyst", "admin"):
        return False, "owner_role", role
    return True, None, role


def _as_dict(cur, row) -> Optional[Dict[str, Any]]:
    """A row as a dict, from a plain or a dict cursor."""
    if row is None or isinstance(row, dict):
        return row
    return {d[0]: v for d, v in zip(cur.description, row)}


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None