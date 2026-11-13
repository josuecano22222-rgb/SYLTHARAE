"""Safe SQL identifier construction (SEC-03).

The only approved way to interpolate table or column identifiers into SQL.
Every identifier passes an explicit allowlist check *and* psycopg2's
``sql.Identifier`` composition, so an allowlist miss fails closed.
"""

from __future__ import annotations

import re
from typing import Dict, FrozenSet

from psycopg2 import sql

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class IdentifierError(ValueError):
    """Raised when an identifier is not on the approved allowlist."""


def validate_identifier(name: str, allowlist: FrozenSet[str] | Dict[str, str], field: str = "identifier") -> str:
    """Validate *name* against *allowlist* and return the **approved** value.

    ``allowlist`` may map client-facing names to real column names (e.g.
    ``"file_count" -> "tc.id"``), or be a set of literal identifiers.
    Anything else raises :class:`IdentifierError` - callers must convert this
    to HTTP 400, never silently substitute a default for *write* paths.
    """
    if isinstance(allowlist, dict):
        if name in allowlist:
            approved = allowlist[name]
        else:
            raise IdentifierError(f"Invalid {field}: {name!r}")
    else:
        if name in allowlist:
            approved = name
        else:
            raise IdentifierError(f"Invalid {field}: {name!r}")
    # Defense in depth: approved values must still look like identifiers.
    for part in approved.split("."):
        if not _IDENTIFIER_RE.match(part):
            raise IdentifierError(f"Invalid {field}: {name!r}")
    return approved


def qualified_identifier(name: str) -> sql.SQL:
    """Compose a validated ``alias.column`` or ``column`` into safe SQL."""
    parts = name.split(".")
    for part in parts:
        if not _IDENTIFIER_RE.match(part):
            raise IdentifierError(f"Invalid identifier: {name!r}")
    return sql.SQL(".").join([sql.Identifier(p) for p in parts])


def sort_direction(value: str, default: str = "DESC") -> str:
    """Allowlist for sort direction."""
    v = (value or default).upper()
    if v not in ("ASC", "DESC"):
        raise IdentifierError(f"Invalid sort direction: {value!r}")
    return v
