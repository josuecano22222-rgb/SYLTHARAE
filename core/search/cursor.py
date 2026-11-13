"""Keyset (cursor) pagination tokens for stable deep pages."""

from __future__ import annotations

import base64
import json
import datetime as _dt
from typing import Any, Dict, Optional, Tuple  # noqa: F401 — Any used in relevance_cursor payload


def encode_cursor(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token or not str(token).strip():
        return None
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(str(token).strip() + pad)
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (ValueError, json.JSONDecodeError):
        return None


def relevance_cursor(
    score: float,
    path_id: int,
    sort_order: str = "desc",
    *,
    query_signature: Optional[str] = None,
    file_date: Optional[_dt.date] = None,
    file_date_known: bool = False,
) -> str:
    """Encode the position of the last row returned.

    The query orders by ``score DESC, file_date DESC, id ASC``. A position
    therefore needs the date as well as the score and id. Callers pass
    ``file_date_known=True`` to record it (``file_date`` may be ``None`` for a
    row with no date). Without it the token is the original v2 shape, which
    stays valid and is resolved with the legacy (score, id) comparison.
    """
    payload: Dict[str, Any] = {
        "v": 2,
        "sort": "relevance",
        "order": sort_order,
        "score": score,
        "id": path_id,
    }
    if file_date_known:
        payload["v"] = 3
        if isinstance(file_date, _dt.datetime):
            file_date = file_date.date()
        payload["date"] = file_date.isoformat() if file_date else None
    if query_signature:
        payload["sig"] = query_signature
    return encode_cursor(payload)


def parse_relevance_cursor_tiebreak(
    token: Optional[str],
    *,
    expected_signature: Optional[str] = None,
) -> Optional[Tuple[bool, Optional[_dt.date]]]:
    """Return ``(has_date, file_date)`` for a valid relevance cursor.

    ``has_date`` is False for legacy v2 tokens; their date is then unknown.
    Returns ``None`` when the token itself is not a valid relevance cursor.
    """
    if parse_relevance_cursor(token, expected_signature=expected_signature) is None:
        return None
    data = decode_cursor(token) or {}
    if "date" not in data:
        return (False, None)
    raw = data.get("date")
    if raw is None:
        return (True, None)
    try:
        return (True, _dt.date.fromisoformat(str(raw)))
    except ValueError:
        return None


def parse_relevance_cursor(
    token: Optional[str],
    *,
    expected_signature: Optional[str] = None,
) -> Optional[Tuple[float, int, str]]:
    data = decode_cursor(token)
    if not data or data.get("sort") != "relevance":
        return None
    if expected_signature:
        sig = data.get("sig")
        if not sig or sig != expected_signature:
            return None
    try:
        return float(data["score"]), int(data["id"]), str(data.get("order", "desc"))
    except (KeyError, TypeError, ValueError):
        return None
