"""PostgreSQL-backed saved searches (replaces ``data/saved_searches.json``).

The public dictionaries returned here keep the exact keys the JSON store
returned (``id``, ``name``, ``query``, ``filters``, ``user_id``,
``created_at``, ``last_used``) so the page, the "Run" link and every API
consumer keep working, plus the new canonical fields (``criteria``,
``criteria_fingerprint``, ``unowned``).

Authorisation lives here, not in the templates: ``can_read``/``can_write``
decide per row, and every route calls them. A saved search belongs to its
owner; an *unowned* legacy search (recorded before API-04 fixed the user id)
is visible to administrators only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import psycopg2.extras

from core.criteria import (
    CRITERIA_SCHEMA_VERSION,
    CriteriaError,
    from_legacy_search,
    legacy_unmapped_keys,
)

logger = logging.getLogger(__name__)

LEGACY_SOURCE_JSON = "saved_searches.json"

_COLUMNS = (
    "id, owner_user_id, name, query, filters, criteria, criteria_fingerprint, "
    "criteria_schema_version, created_at, updated_at, "
    "last_used_at, legacy_source, legacy_id, import_notes"
)


class SavedSearchError(ValueError):
    """A saved search that cannot be accepted, with the reason."""


def _default_connection():
    from Api.utils import get_connection

    return get_connection()


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _row_to_public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "query": row["query"],
        "filters": row["filters"] or {},
        "user_id": row["owner_user_id"],
        "owner_user_id": row["owner_user_id"],
        "unowned": row["owner_user_id"] is None,
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
        "last_used": _iso(row["last_used_at"]),
        "criteria": row["criteria"],
        "criteria_fingerprint": (row["criteria_fingerprint"] or "").strip(),
        "criteria_schema_version": row["criteria_schema_version"],
        "legacy_id": row["legacy_id"],
        "import_notes": row["import_notes"],
    }


def _derive(query: str, filters: Dict[str, Any]):
    """Canonical criteria for a legacy definition; refuses what it cannot map."""
    if not isinstance(filters, dict):
        raise SavedSearchError("filters must be an object")
    try:
        criteria = from_legacy_search(query, filters)
    except CriteriaError as exc:
        raise SavedSearchError(str(exc)) from None
    return criteria


def can_read(row: Optional[Dict[str, Any]], user_id: Optional[int], is_admin: bool) -> bool:
    if row is None or user_id is None:
        return False
    owner = row.get("owner_user_id")
    if owner is None:
        return bool(is_admin)
    return owner == user_id


def can_write(row: Optional[Dict[str, Any]], user_id: Optional[int], is_admin: bool) -> bool:
    # Same rule: nobody edits another user's private search; unowned legacy
    # searches are an administrator's to fix or remove.
    return can_read(row, user_id, is_admin)


class SavedSearchRepository:
    def __init__(self, connection_factory: Callable = _default_connection):
        self._connect = connection_factory

    # ------------------------------------------------------------------
    def create(self, owner_user_id: Optional[int], name: str, query: str,
               filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise SavedSearchError("Name is required")
        if len(name) > 255:
            raise SavedSearchError("Name is at most 255 characters")
        filters = filters or {}
        criteria = _derive(query or "", filters)
        notes = legacy_unmapped_keys(filters)
        with self._connect() as conn, conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"INSERT INTO saved_searches (owner_user_id, name, query, filters, criteria,"  # nosec B608 # _COLUMNS is a module constant; values are bound parameters
                f" criteria_fingerprint, criteria_schema_version, import_notes)"
                f" VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_COLUMNS}",
                (owner_user_id, name, query or "", psycopg2.extras.Json(filters),
                 psycopg2.extras.Json(criteria.to_dict()), criteria.fingerprint(),
                 CRITERIA_SCHEMA_VERSION,
                 psycopg2.extras.Json({"unmapped_filter_keys": notes}) if notes else None),
            )
            return _row_to_public(cur.fetchone())

    def get(self, search_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn, conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM saved_searches WHERE id = %s", (search_id,))  # nosec B608 # _COLUMNS is a module constant; values are bound parameters
            row = cur.fetchone()
            return _row_to_public(row) if row else None

    def list_visible(self, user_id: Optional[int], is_admin: bool) -> List[Dict[str, Any]]:
        """The caller's own searches; administrators also see unowned legacy ones."""
        if user_id is None:
            return []
        with self._connect() as conn, conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if is_admin:
                cur.execute(
                    f"SELECT {_COLUMNS} FROM saved_searches"  # nosec B608 # _COLUMNS is a module constant; values are bound parameters
                    " WHERE owner_user_id = %s OR owner_user_id IS NULL"
                    " ORDER BY created_at ASC, id ASC", (user_id,))
            else:
                cur.execute(
                    f"SELECT {_COLUMNS} FROM saved_searches WHERE owner_user_id = %s"  # nosec B608 # _COLUMNS is a module constant; values are bound parameters
                    " ORDER BY created_at ASC, id ASC", (user_id,))
            return [_row_to_public(r) for r in cur.fetchall()]

    def update(self, search_id: int, *, name: Optional[str] = None,
               query: Optional[str] = None,
               filters: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        current = self.get(search_id)
        if current is None:
            return None
        new_name = current["name"] if name is None else (name or "").strip()
        if not new_name:
            raise SavedSearchError("Name is required")
        if len(new_name) > 255:
            raise SavedSearchError("Name is at most 255 characters")
        new_query = current["query"] if query is None else query
        new_filters = current["filters"] if filters is None else filters
        criteria = _derive(new_query or "", new_filters)
        notes = legacy_unmapped_keys(new_filters)
        with self._connect() as conn, conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"UPDATE saved_searches SET name = %s, query = %s, filters = %s,"  # nosec B608 # _COLUMNS is a module constant; values are bound parameters
                f" criteria = %s, criteria_fingerprint = %s, criteria_schema_version = %s,"
                f" import_notes = %s, updated_at = NOW()"
                f" WHERE id = %s RETURNING {_COLUMNS}",
                (new_name, new_query or "", psycopg2.extras.Json(new_filters),
                 psycopg2.extras.Json(criteria.to_dict()), criteria.fingerprint(),
                 CRITERIA_SCHEMA_VERSION,
                 psycopg2.extras.Json({"unmapped_filter_keys": notes}) if notes else None,
                 search_id),
            )
            row = cur.fetchone()
            return _row_to_public(row) if row else None

    def delete(self, search_id: int) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM saved_searches WHERE id = %s", (search_id,))
            return cur.rowcount > 0

    def mark_used(self, search_id: int) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE saved_searches SET last_used_at = NOW() WHERE id = %s",
                        (search_id,))

    def assign_owner(self, search_id: int, owner_user_id: int) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE saved_searches SET owner_user_id = %s, updated_at = NOW()"
                        " WHERE id = %s", (owner_user_id, search_id))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Legacy JSON import
    # ------------------------------------------------------------------
    def import_legacy_json(self, path: Path) -> Dict[str, Any]:
        """Import ``data/saved_searches.json`` once, idempotently, losslessly.

        * Keyed by the file's SHA-256 in ``saved_search_imports``: the same
          content is never imported twice, so a search deleted after import
          cannot be resurrected by re-reading the old file.
        * Each entry keeps its legacy id (``UNIQUE (legacy_source, legacy_id)``),
          so even a *changed* file cannot duplicate an already-imported entry.
        * An entry that cannot be converted is recorded verbatim in the
          ledger's ``failures`` - never dropped silently.
        * The JSON file is left in place as a backup; it is no longer read or
          written by the application.
        """
        path = Path(path)
        summary: Dict[str, Any] = {
            "source_file": str(path), "status": "absent", "entries_found": 0,
            "entries_imported": 0, "entries_already_present": 0, "failures": [],
        }
        if not path.exists():
            return summary
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        summary["file_sha256"] = digest

        try:
            entries = json.loads(raw.decode("utf-8")) if raw.strip() else []
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            entries = None
            summary["failures"].append({"entry": None,
                                        "raw": raw.decode("utf-8", "replace"),
                                        "reason": f"file is not valid JSON: {exc}"})
        if entries is not None and not isinstance(entries, list):
            summary["failures"].append({"entry": entries,
                                        "reason": "file does not contain a JSON list"})
            entries = None

        with self._connect() as conn, conn.cursor() as cur:
            # Serialise concurrent importers (two workers starting at once).
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('saved_search_import'))")
            cur.execute("SELECT 1 FROM saved_search_imports WHERE file_sha256 = %s", (digest,))
            if cur.fetchone():
                summary["status"] = "already_imported"
                return summary

            cur.execute("SELECT id FROM users")
            known_users = {r[0] for r in cur.fetchall()}

            for entry in entries or []:
                summary["entries_found"] += 1
                try:
                    if not isinstance(entry, dict):
                        raise SavedSearchError("entry is not an object")
                    legacy_id = entry.get("id")
                    if isinstance(legacy_id, bool) or not isinstance(legacy_id, int):
                        raise SavedSearchError(f"entry has no integer id: {legacy_id!r}")
                    name = str(entry.get("name") or "").strip()
                    if not name:
                        raise SavedSearchError("entry has no name")
                    query = entry.get("query") or ""
                    if not isinstance(query, str):
                        raise SavedSearchError("query is not text")
                    filters = entry.get("filters") or {}
                    criteria = _derive(query, filters)
                    notes: Dict[str, Any] = {}
                    unmapped = legacy_unmapped_keys(filters)
                    if unmapped:
                        notes["unmapped_filter_keys"] = unmapped
                    owner = entry.get("user_id")
                    owner_id: Optional[int] = None
                    if owner not in (None, ""):
                        try:
                            candidate = int(owner)
                        except (TypeError, ValueError):
                            candidate = None
                        if candidate in known_users:
                            owner_id = candidate
                        else:
                            notes["legacy_owner_unresolved"] = owner
                    extra = sorted(set(entry) - {"id", "name", "query", "filters",
                                                 "user_id", "created_at", "last_used"})
                    if extra:
                        notes["legacy_extra_fields"] = {k: entry[k] for k in extra}
                    created_at = entry.get("created_at")
                    last_used = entry.get("last_used")
                    cur.execute("SAVEPOINT legacy_entry")
                    try:
                        cur.execute(
                            "INSERT INTO saved_searches (owner_user_id, name, query, filters,"
                            " criteria, criteria_fingerprint, criteria_schema_version,"
                            " created_at, last_used_at, legacy_source, legacy_id, import_notes)"
                            " VALUES (%s, %s, %s, %s, %s, %s, %s,"
                            " COALESCE(%s::timestamptz, NOW()), %s::timestamptz, %s, %s, %s)"
                            " ON CONFLICT (legacy_source, legacy_id) DO NOTHING",
                            (owner_id, name[:255], query, psycopg2.extras.Json(filters),
                             psycopg2.extras.Json(criteria.to_dict()), criteria.fingerprint(),
                             CRITERIA_SCHEMA_VERSION, created_at or None, last_used or None,
                             LEGACY_SOURCE_JSON, legacy_id,
                             psycopg2.extras.Json(notes) if notes else None),
                        )
                        inserted = cur.rowcount
                        cur.execute("RELEASE SAVEPOINT legacy_entry")
                    except psycopg2.Error as db_exc:
                        cur.execute("ROLLBACK TO SAVEPOINT legacy_entry")
                        raise SavedSearchError(
                            f"database rejected entry: {db_exc.__class__.__name__}: "
                            f"{str(db_exc).strip().splitlines()[0]}") from None
                    if inserted:
                        summary["entries_imported"] += 1
                    else:
                        summary["entries_already_present"] += 1
                except SavedSearchError as exc:
                    summary["failures"].append({"entry": entry, "reason": str(exc)})

            cur.execute(
                "INSERT INTO saved_search_imports (source_file, file_sha256, entries_found,"
                " entries_imported, entries_already_present, failures)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (str(path), digest, summary["entries_found"], summary["entries_imported"],
                 summary["entries_already_present"],
                 psycopg2.extras.Json(summary["failures"])),
            )
        summary["status"] = "imported"
        if summary["failures"]:
            logger.error("Saved-search import from %s: %d entr(y/ies) could not be "
                         "imported; recorded in saved_search_imports.failures",
                         path, len(summary["failures"]))
        logger.info("Saved-search import from %s: found=%d imported=%d already=%d",
                    path, summary["entries_found"], summary["entries_imported"],
                    summary["entries_already_present"])
        return summary


_import_lock = threading.Lock()
_import_done = False


def ensure_legacy_imported(repo: SavedSearchRepository, path: Path) -> Optional[Dict[str, Any]]:
    """Run the legacy import once per process (cheap no-op afterwards)."""
    global _import_done
    if _import_done:
        return None
    with _import_lock:
        if _import_done:
            return None
        summary = repo.import_legacy_json(path)
        _import_done = True
        return summary


def _reset_import_guard_for_tests() -> None:
    global _import_done
    _import_done = False
