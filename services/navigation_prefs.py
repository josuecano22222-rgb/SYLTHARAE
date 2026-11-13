"""Per-user sidebar navigation preferences.

Reads and writes ``user_navigation_prefs`` (m0033) and is applied by
``core.interfaces.navigation.build_navigation``. The rules:

* only interfaces the registry declares may appear in a preference row -
  an unknown id is refused before anything is written;
* ``hidden`` removes an entry the registry would otherwise show for this
  user; it can never make a hidden-by-role entry visible;
* ``position`` (1-based, unique per user when set) places an entry inside
  its domain; ``NULL`` keeps the declared order after every positioned
  entry of that domain;
* a preference row for an interface that later leaves the registry is
  ignored by the builder and reported here as stale so the settings page
  can offer to clean it.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from core.interfaces.registry import get_all_interfaces, get_interface


class NavigationPrefsError(ValueError):
    """The requested preference contradicts the registry or the table."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def get_prefs(conn, user_id: int) -> Dict[str, Dict[str, object]]:
    """``{interface_id: {"hidden": bool, "position": int|None}}`` for one
    user. Rows for interfaces the registry no longer declares are returned
    under their id as well (the builder ignores them); the caller decides
    whether to prune them."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT interface_id, hidden, position FROM user_navigation_prefs"
            " WHERE user_id = %s ORDER BY interface_id", (user_id,))
        rows = cur.fetchall()
    return {r[0]: {"hidden": bool(r[1]), "position": r[2]} for r in rows}


def set_prefs(conn, user_id: int,
              entries: List[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    """Replace this user's preferences with ``entries`` (a full desired
    state, so the settings page stays the single authority). Each entry is
    ``{"interface_id", "hidden", "position"}``. Positions must be unique
    whole numbers >= 1 when given. Returns the stored state."""
    if not isinstance(entries, list):
        raise NavigationPrefsError("VALIDATION_FAILED",
                                   "entries must be a list")
    cleaned: List[Tuple[str, bool, Optional[int]]] = []
    seen_positions = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise NavigationPrefsError("VALIDATION_FAILED",
                                       "every entry must be an object")
        unknown = sorted(set(entry) - {"interface_id", "hidden", "position"})
        if unknown:
            raise NavigationPrefsError(
                "VALIDATION_FAILED", f"unknown field(s): {', '.join(unknown)}")
        interface_id = entry.get("interface_id")
        if not isinstance(interface_id, str) or not get_interface(interface_id):
            raise NavigationPrefsError(
                "VALIDATION_FAILED",
                f"unknown interface {interface_id!r}: the sidebar is built "
                "from the declared interfaces")
        hidden = entry.get("hidden", False)
        if not isinstance(hidden, bool):
            raise NavigationPrefsError("VALIDATION_FAILED",
                                       f"{interface_id}: hidden must be true or false")
        position = entry.get("position", None)
        if position is not None:
            if isinstance(position, bool) or not isinstance(position, int) or position < 1:
                raise NavigationPrefsError(
                    "VALIDATION_FAILED",
                    f"{interface_id}: position must be a whole number >= 1")
            if position in seen_positions:
                raise NavigationPrefsError(
                    "VALIDATION_FAILED",
                    f"position {position} is given twice")
            seen_positions.add(position)
        cleaned.append((interface_id, hidden, position))

    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_navigation_prefs WHERE user_id = %s",
                    (user_id,))
        for interface_id, hidden, position in cleaned:
            cur.execute(
                "INSERT INTO user_navigation_prefs"
                " (user_id, interface_id, hidden, position, updated_at)"
                " VALUES (%s, %s, %s, %s, NOW())",
                (user_id, interface_id, hidden, position))
    return get_prefs(conn, user_id)


def reset_prefs(conn, user_id: int) -> None:
    """Back to the declared navigation: drop every row of this user."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_navigation_prefs WHERE user_id = %s",
                    (user_id,))


def prune_stale(conn, user_id: int) -> int:
    """Drop rows whose interface left the registry. Returns the count."""
    declared = {i.id for i in get_all_interfaces()}
    with conn.cursor() as cur:
        cur.execute("SELECT interface_id FROM user_navigation_prefs"
                    " WHERE user_id = %s", (user_id,))
        stale = [r[0] for r in cur.fetchall()
                 if declared is not None and r[0] not in declared]
        for interface_id in stale:
            cur.execute("DELETE FROM user_navigation_prefs"
                        " WHERE user_id = %s AND interface_id = %s",
                        (user_id, interface_id))
    return len(stale)
