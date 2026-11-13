"""Total-hit counting strategies (exact vs timed vs skipped)."""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

CountResult = Tuple[Optional[int], bool, Optional[str]]


def apply_count_mode(
    cursor: Any,
    count_sql: str,
    params: tuple,
    *,
    mode: str = "exact",
    timeout_ms: int = 250,
) -> CountResult:
    """
    Returns (total, is_approximate, note).

    ``skip`` — no COUNT; total is None.
    ``timed`` — SET LOCAL statement_timeout then exact COUNT; on timeout returns (None, True, 'timeout').
    ``exact`` — standard COUNT.
    """
    mode = (mode or "exact").strip().lower()
    if mode == "skip":
        return None, False, "skipped"

    if mode == "timed" and timeout_ms > 0:
        try:
            cursor.execute(f"SET LOCAL statement_timeout = '{int(timeout_ms)}ms'")
            cursor.execute(count_sql, params)
            row = cursor.fetchone()
            cursor.execute("SET LOCAL statement_timeout = 0")
            total = int(row[0]) if row else 0
            return total, False, None
        except Exception as exc:
            logger.info("Count timed out or failed (%s); returning approximate unknown total", exc)
            try:
                cursor.execute("SET LOCAL statement_timeout = 0")
            except Exception:
                pass
            return None, True, "timeout"

    cursor.execute(count_sql, params)
    row = cursor.fetchone()
    return int(row[0]) if row else 0, False, None
