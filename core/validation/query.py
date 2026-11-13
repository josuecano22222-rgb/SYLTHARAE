"""Read numbers out of the query string without trusting them.

``int(request.args.get("limit", 50))`` raises ``ValueError`` for ``?limit=abc``
and the view answers 500 for something a URL bar allows. A read endpoint does
not need to be strict about a page size - the useful answer is the default (or
the clamped bound), and the sweep (``tools/audit/probe_query_params.py``) is
what found four endpoints that crashed instead.

``query_int`` reads ``request.args`` by default so the call sites stay short::

    limit = query_int("limit", 50, minimum=1, maximum=200)

The helpers never raise: a value that is not a number, is not present, or is
``None`` yields the default.
"""

from __future__ import annotations

from typing import Mapping, Optional

from flask import request

__all__ = ["query_int", "query_text"]


def _args(args: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return request.args if args is None else args


def query_int(name: str, default: int, *, minimum: Optional[int] = None,
              maximum: Optional[int] = None,
              args: Optional[Mapping[str, str]] = None) -> int:
    """The integer in *name*, clamped; *default* when absent or unparsable."""
    raw = _args(args).get(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            value = default
    if minimum is not None and value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def query_text(name: str, default: str = "", *, max_length: int = 512,
               strip: bool = True,
               args: Optional[Mapping[str, str]] = None) -> str:
    """The text in *name*, bounded; NUL bytes are removed, not propagated.

    PostgreSQL cannot store a NUL in a text column and psycopg2 raises while
    the statement is being built, so a ``?q=a\\x00b`` turned a search into a
    500. A search for "ab" is the honest reading of that request.
    """
    raw = _args(args).get(name)
    text = "" if raw is None else str(raw)
    text = text.replace("\x00", "")
    if strip:
        text = text.strip()
    return text[:max_length]
