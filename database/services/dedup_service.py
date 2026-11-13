"""Identity authority (One Content, Many Contexts) - the ONLY place where
content identity, contextual identity and occurrence identity are defined.

Three levels, exactly as enforced by migration ``m0011_content_identity``:

    CONTENT IDENTITY      ``hashs.hash``                    UNIQUE (hash)
    CONTEXT IDENTITY      (hash, source, side)              UNIQUE on hash_contexts
    OCCURRENCE            a ``paths`` row inside a context  (provenance)

Every duplicate decision in SYLTHARAE must go through this service:

* **canonical duplicate** - the bytes were seen before (any context).  The
  canonical row and its content-derived extraction (text, words, keywords,
  titles) are REUSED, never re-created per context.
* **context duplicate** - the content was already represented in this
  (source, side).  The context row is reused; the occurrence may still be new.
* **occurrence duplicate** - this exact physical encounter is already
  recorded.  Its location key is ``hierarchy_path`` for in-container members
  (``archive.zip::member.txt``) and ``file_path`` for everything else.  Only
  an occurrence duplicate suppresses a new ``paths`` row.

Different folders, filenames, containers, sources, sides or import runs are
legitimate distinct occurrences of one content: they are preserved, never
discarded, and never duplicate the canonical content.

Exact repeats are one record. Ingestion writes no second occurrence for the
same source, same side, same file name, same content and same details (size,
type, date), wherever the file came from. ``remove_same_context_duplicates``
(the files library's "remove exact repeats" action) clears such repeats that
already exist, keeping the earliest. Same content under another name, source
or side is a different record and is never touched; the content and its
contexts always remain.

Concurrency: registration is a chain of ``ON CONFLICT DO NOTHING`` upserts
against the unique constraints above inside one transaction, so two workers
ingesting identical content concurrently converge on one canonical row and
one context row (DB-04, architecture directive Section 15).

Delete lifecycle (DB-05): ``delete_path`` removes an occurrence and, in the
same transaction, any context left with zero occurrences and any canonical
content left with zero contexts (its derived stores cascade).  Re-ingestion
after deletion always works: no identity check ever blocks on rows that have
no live occurrence.

Transaction ownership: every method accepts ``commit`` (default ``True``).
Callers running inside their own transaction - ``process_full_document`` is
the canonical example - pass ``commit=False`` and own commit/rollback.  The
connection returned by the factory is never closed here: factories may hand
out pooled or caller-owned connections.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Identity SQL - defined once, used by every method below.  These statements
# ARE the identity model; no other module may redefine them.
# ---------------------------------------------------------------------------

_SQL = {
    "insert_canonical": """
        INSERT INTO hashs (hash)
        VALUES (%s)
        ON CONFLICT (hash) DO NOTHING
        RETURNING id
    """,
    "get_canonical_id": """
        SELECT id FROM hashs WHERE hash = %s
    """,
    "insert_context": """
        INSERT INTO hash_contexts (hash_id, source_id, side_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (hash_id, source_id, side_id) DO NOTHING
        RETURNING id
    """,
    "get_context_id": """
        SELECT id
        FROM hash_contexts
        WHERE hash_id = %s AND source_id = %s AND side_id = %s
    """,
    "get_context_id_by_value": """
        SELECT c.id
        FROM hash_contexts c
        JOIN hashs h ON h.id = c.hash_id
        WHERE h.hash = %s AND c.source_id = %s AND c.side_id = %s
    """,
    "insert_path": """
        INSERT INTO paths (
            file_name, file_path, file_size, file_type, file_status, file_date,
            date_creation, context_id, coordinates, extraction_provenance,
            processing_status, status_detail, attempts, parent_path_id,
            hierarchy_path, status_updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s::jsonb,
            %s, %s, %s, %s,
            %s, NOW()
        )
        RETURNING id
    """,
    "find_occurrence": """
        SELECT p.id
        FROM paths p
        JOIN hash_contexts c ON c.id = p.context_id
        JOIN hashs h ON h.id = c.hash_id
        WHERE h.hash = %s
          AND c.source_id = %s
          AND c.side_id = %s
          AND (%s IS NULL OR COALESCE(p.hierarchy_path, p.file_path) = %s)
        ORDER BY p.id
        LIMIT 1
    """,
    "find_live_path_any": """
        SELECT p.id
        FROM paths p
        JOIN hash_contexts c ON c.id = p.context_id
        JOIN hashs h ON h.id = c.hash_id
        WHERE h.hash = %s
          AND c.source_id = %s
          AND c.side_id = %s
        ORDER BY p.id
        LIMIT 1
    """,
    "hash_live_for_source": """
        SELECT 1
        FROM paths p
        JOIN hash_contexts c ON c.id = p.context_id
        JOIN hashs h ON h.id = c.hash_id
        WHERE h.hash = %s AND c.source_id = %s
        LIMIT 1
    """,
    "hash_live_for_source_side": """
        SELECT 1
        FROM paths p
        JOIN hash_contexts c ON c.id = p.context_id
        JOIN hashs h ON h.id = c.hash_id
        WHERE h.hash = %s AND c.source_id = %s AND c.side_id = %s
        LIMIT 1
    """,
    "hash_live_anywhere": """
        SELECT 1
        FROM paths p
        JOIN hash_contexts c ON c.id = p.context_id
        JOIN hashs h ON h.id = c.hash_id
        WHERE h.hash = %s
        LIMIT 1
    """,
    "extraction_exists": """
        SELECT 1 FROM contents WHERE hash_id = %s LIMIT 1
    """,
    "raw_extraction_exists": """
        SELECT 1 FROM contents_raw WHERE hash_id = %s LIMIT 1
    """,
    "content_contexts": """
        SELECT c.id, c.source_id, c.side_id, s.name, si.name, c.date_creation,
               (SELECT COUNT(*) FROM paths p WHERE p.context_id = c.id)
        FROM hash_contexts c
        JOIN hashs h ON h.id = c.hash_id
        LEFT JOIN sources s ON s.id = c.source_id
        LEFT JOIN sides si ON si.id = c.side_id
        WHERE h.hash = %s
        ORDER BY c.id
    """,
    "content_occurrences": """
        SELECT p.id, p.file_name, p.file_path, p.hierarchy_path,
               p.parent_path_id, p.file_size, p.file_type, p.file_status,
               p.processing_status, p.date_creation,
               c.id, c.source_id, c.side_id, s.name, si.name
        FROM paths p
        JOIN hash_contexts c ON c.id = p.context_id
        JOIN hashs h ON h.id = c.hash_id
        LEFT JOIN sources s ON s.id = c.source_id
        LEFT JOIN sides si ON si.id = c.side_id
        WHERE h.hash = %s
        ORDER BY p.id
    """,
    "path_identity": """
        SELECT h.hash, h.id, c.id, c.source_id, c.side_id
        FROM paths p
        JOIN hash_contexts c ON c.id = p.context_id
        JOIN hashs h ON h.id = c.hash_id
        WHERE p.id = %s
    """,
    "same_context_groups": """
        SELECT c.id, h.hash, c.source_id, c.side_id,
               COALESCE(s.name, ''), COALESCE(si.name, ''),
               array_agg(p.id ORDER BY p.id),
               array_agg(p.file_name ORDER BY p.id)
        FROM paths p
        JOIN hash_contexts c ON c.id = p.context_id
        JOIN hashs h ON h.id = c.hash_id
        LEFT JOIN sources s ON s.id = c.source_id
        LEFT JOIN sides si ON si.id = c.side_id
        GROUP BY c.id, h.hash, c.source_id, c.side_id, s.name, si.name
        HAVING COUNT(*) > 1
        ORDER BY c.id
    """,
    "find_same_details": """
        SELECT p.id
        FROM paths p
        WHERE p.context_id = %s
          AND p.file_name = %s
          AND p.file_size = %s
          AND p.file_type = %s
          AND p.file_date = %s
        ORDER BY p.id
        LIMIT 1
    """,
    "same_context_groups": """
        SELECT c.id, h.hash, c.source_id, c.side_id,
               COALESCE(s.name, ''), COALESCE(si.name, ''),
               array_agg(p.id ORDER BY p.id),
               array_agg(p.file_name ORDER BY p.id)
        FROM paths p
        JOIN hash_contexts c ON c.id = p.context_id
        JOIN hashs h ON h.id = c.hash_id
        LEFT JOIN sources s ON s.id = c.source_id
        LEFT JOIN sides si ON si.id = c.side_id
        GROUP BY c.id, h.hash, c.source_id, c.side_id, s.name, si.name,
                 p.file_name, p.file_size, p.file_type, p.file_date
        HAVING COUNT(*) > 1
        ORDER BY c.id
    """,
    "delete_path": "DELETE FROM paths WHERE id = %s RETURNING context_id",
    "context_path_count": """
        SELECT COUNT(*) FROM paths WHERE context_id = %s
    """,
    "delete_context": "DELETE FROM hash_contexts WHERE id = %s RETURNING hash_id",
    "context_count_for_hash": """
        SELECT COUNT(*) FROM hash_contexts WHERE hash_id = %s
    """,
    "delete_canonical": "DELETE FROM hashs WHERE id = %s",
    "orphan_contexts": """
        SELECT c.id
        FROM hash_contexts c
        WHERE NOT EXISTS (SELECT 1 FROM paths p WHERE p.context_id = c.id)
    """,
    "orphan_hashes": """
        SELECT h.id
        FROM hashs h
        WHERE NOT EXISTS (
            SELECT 1 FROM hash_contexts c WHERE c.hash_id = h.id
        )
    """,
    "find_repeat_occurrence": """
        SELECT p.id
        FROM paths p
        WHERE p.context_id = %s
          AND COALESCE(p.hierarchy_path, p.file_path) = %s
          AND p.id <> %s
        ORDER BY p.id
        LIMIT 1
    """,
}


def occurrence_location(path_row: Dict[str, Any]) -> Optional[str]:
    """The provenance location that identifies an occurrence.

    In-container members are identified by their hierarchy chain
    (``archive.zip::member.txt``): extracted members live at run-specific
    temporary paths, so ``file_path`` cannot identify them across imports.
    Everything else is identified by its physical ``file_path``.
    """
    hierarchy = path_row.get("hierarchy_path")
    if hierarchy:
        return str(hierarchy)
    file_path = path_row.get("file_path")
    return str(file_path) if file_path else None


class DeduplicationService:
    """All identity resolution, registration and identity lifecycle."""

    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    @contextmanager
    def _cursor(self, commit: bool = False):
        """Yield ``(conn, cursor)`` on the factory's connection.

        Never closes the connection (factories may return pooled or
        caller-owned connections).  Commits only when this call owns the
        transaction (``commit=True``); nested callers leave transaction
        control to their outer unit of work.
        """
        conn = self._connection_factory()
        cur = conn.cursor()
        try:
            yield conn, cur
            if commit:
                conn.commit()
        except BaseException:
            if commit:
                try:
                    conn.rollback()
                except Exception:  # pragma: no cover - rollback of dead conn
                    pass
            raise
        finally:
            try:
                cur.close()
            except Exception:  # pragma: no cover
                pass

    # ------------------------------------------------------------------
    # Layered lookup
    # ------------------------------------------------------------------
    def find_canonical_hash(self, content_hash: str) -> Optional[int]:
        """Canonical content row id for ``content_hash``, or None."""
        with self._cursor() as (_, cur):
            cur.execute(_SQL["get_canonical_id"], (content_hash,))
            row = cur.fetchone()
        return row[0] if row else None

    def find_context(
        self, content_hash: str, source_id: int, side_id: int
    ) -> Optional[int]:
        """Context row id for (hash, source, side), or None."""
        with self._cursor() as (_, cur):
            cur.execute(
                _SQL["get_context_id_by_value"], (content_hash, source_id, side_id)
            )
            row = cur.fetchone()
        return row[0] if row else None

    def extraction_exists(self, hash_id: int) -> bool:
        """True when canonical content-derived extraction already exists.

        This is the process-or-reuse switch of the ingestion pipeline: a
        repeated content hash reuses the canonical extraction instead of
        re-extracting (and instead of duplicating it per context).
        """
        with self._cursor() as (_, cur):
            cur.execute(_SQL["extraction_exists"], (hash_id,))
            row = cur.fetchone()
            if row is None:
                cur.execute(_SQL["raw_extraction_exists"], (hash_id,))
                row = cur.fetchone()
        return row is not None

    def find_live_path(
        self,
        content_hash: str,
        source_id: int,
        side_id: int,
        file_path: Optional[str] = None,
        hierarchy_path: Optional[str] = None,
    ) -> Optional[int]:
        """Return the path id of a matching live occurrence, or None.

        With ``hierarchy_path``/``file_path`` the match is occurrence-level
        (this exact physical encounter).  Without either it answers "is this
        content already present in this context at all?" and returns any live
        occurrence.
        """
        location = hierarchy_path or file_path
        with self._cursor() as (_, cur):
            if location is None:
                cur.execute(
                    _SQL["find_live_path_any"],
                    (content_hash, source_id, side_id),
                )
            else:
                cur.execute(
                    _SQL["find_occurrence"],
                    (content_hash, source_id, side_id, location, location),
                )
            row = cur.fetchone()
        return row[0] if row else None

    def check_duplicate(
        self,
        content_hash: str,
        source_id: int,
        side_id: int,
        file_path: Optional[str] = None,
        hierarchy_path: Optional[str] = None,
    ) -> Tuple[bool, Optional[int]]:
        """Return ``(is_duplicate, existing_path_id)``.

        Duplicate means an *occurrence* duplicate when a location is given
        (same content, same context, same physical encounter) and a *context*
        presence check otherwise.  A canonical or context row with no live
        occurrence never makes content a duplicate (DB-05).
        """
        path_id = self.find_live_path(
            content_hash, source_id, side_id,
            file_path=file_path, hierarchy_path=hierarchy_path,
        )
        return (path_id is not None, path_id)

    def find_committed_occurrence(
        self,
        content_hash: str,
        source_name: str,
        side_name: str,
        location: str,
    ) -> Optional[int]:
        """Return the path id of a *committed* occurrence, or None. Read-only.

        Used before a file is processed: if this exact occurrence (same bytes,
        same source and side, same location) is already stored as processed,
        processing it again can only repeat work. Names are matched, not
        created: a source or side that does not exist yet has no committed
        occurrences, and a lookup must never write identity rows.

        ``location`` is the occurrence key: ``hierarchy_path`` for archive
        members, the physical ``file_path`` otherwise (see occurrence_location).
        """
        if not (content_hash and source_name and side_name and location):
            return None
        with self._cursor(commit=False) as (_, cur):
            cur.execute(
                """
                SELECT p.id
                FROM paths p
                JOIN hash_contexts c ON c.id = p.context_id
                JOIN hashs h ON h.id = c.hash_id
                JOIN sources s ON s.id = c.source_id
                JOIN sides sd ON sd.id = c.side_id
                WHERE h.hash = %s
                  AND s.name = %s
                  AND sd.name = %s
                  AND COALESCE(p.hierarchy_path, p.file_path) = %s
                  AND p.processing_status = 'processed'
                ORDER BY p.id
                LIMIT 1
                """,
                (content_hash, source_name, side_name, location),
            )
            row = cur.fetchone()
        return int(row[0]) if row else None

    def hash_exists_with_live_path(
        self, content_hash: str, source_id: Optional[int] = None,
        side_id: Optional[int] = None,
    ) -> bool:
        """True when the content has any live occurrence (optionally scoped).

        ``side_id`` narrows a source-scoped check to one (source, side)
        context; it is ignored without a ``source_id``.
        """
        with self._cursor() as (_, cur):
            if source_id is None:
                cur.execute(_SQL["hash_live_anywhere"], (content_hash,))
            elif side_id is not None:
                cur.execute(
                    _SQL["hash_live_for_source_side"], (content_hash, source_id, side_id)
                )
            else:
                cur.execute(
                    _SQL["hash_live_for_source"], (content_hash, source_id)
                )
            row = cur.fetchone()
        return row is not None

    def path_identity(self, path_id: int) -> Optional[Dict[str, Any]]:
        """Full identity of a stored occurrence.

        Returns ``{hash, hash_id, context_id, source_id, side_id}`` or None.
        """
        with self._cursor() as (_, cur):
            cur.execute(_SQL["path_identity"], (path_id,))
            row = cur.fetchone()
        if not row:
            return None
        return {
            "hash": row[0],
            "hash_id": row[1],
            "context_id": row[2],
            "source_id": row[3],
            "side_id": row[4],
        }

    def content_contexts(self, content_hash: str) -> List[Dict[str, Any]]:
        """Every (source, side) context of one canonical content."""
        with self._cursor() as (_, cur):
            cur.execute(_SQL["content_contexts"], (content_hash,))
            rows = cur.fetchall()
        return [
            {
                "context_id": r[0],
                "source_id": r[1],
                "side_id": r[2],
                "source_name": r[3],
                "side_name": r[4],
                "date_creation": r[5],
                "occurrence_count": r[6],
            }
            for r in rows
        ]

    def content_occurrences(self, content_hash: str) -> List[Dict[str, Any]]:
        """Every physical occurrence of one canonical content, with context."""
        with self._cursor() as (_, cur):
            cur.execute(_SQL["content_occurrences"], (content_hash,))
            rows = cur.fetchall()
        keys = (
            "path_id", "file_name", "file_path", "hierarchy_path",
            "parent_path_id", "file_size", "file_type", "file_status",
            "processing_status", "date_creation",
            "context_id", "source_id", "side_id", "source_name", "side_name",
        )
        return [dict(zip(keys, r, strict=True)) for r in rows]

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register_content(
        self,
        content_hash: str,
        source_id: int,
        side_id: int,
        path_row: Dict[str, Any],
        commit: bool = True,
    ) -> Dict[str, Any]:
        """Register one occurrence, creating identity rows as needed.

        ``path_row`` must contain: file_name, file_path, file_size, file_type,
        file_date, date_creation.  Optional: file_status, coordinates,
        extraction_provenance, processing_status, status_detail, attempts,
        parent_path_id, hierarchy_path.

        Returns ``{path_id, hash_id, context_id, duplicate, canonical_reused,
        context_reused, extraction_exists}``.  ``duplicate`` is an occurrence
        duplicate: an identical physical encounter already recorded, whose
        existing ``path_id`` is returned and nothing new is written.
        """
        location = occurrence_location(path_row)
        result: Dict[str, Any] = {}

        with self._cursor(commit=commit) as (_, cur):
            # -- content identity (ON CONFLICT: one canonical row, ever) --
            cur.execute(_SQL["insert_canonical"], (content_hash,))
            row = cur.fetchone()
            canonical_reused = row is None
            if row is None:
                cur.execute(_SQL["get_canonical_id"], (content_hash,))
                row = cur.fetchone()
                if row is None:  # pragma: no cover - raced delete
                    raise RuntimeError(
                        f"canonical content row vanished for {content_hash[:16]}..."
                    )
            hash_id = row[0]

            # -- context identity (ON CONFLICT: one context row per triple) --
            cur.execute(_SQL["insert_context"], (hash_id, source_id, side_id))
            row = cur.fetchone()
            context_reused = row is None
            if row is None:
                cur.execute(_SQL["get_context_id"], (hash_id, source_id, side_id))
                row = cur.fetchone()
                if row is None:  # pragma: no cover - raced delete
                    raise RuntimeError(
                        "context row vanished for "
                        f"{content_hash[:16]}.../{source_id}/{side_id}"
                    )
            context_id = row[0]

            # -- occurrence (only a true occurrence duplicate suppresses) --
            existing_path_id = None
            if location is not None:
                cur.execute(_SQL["find_occurrence"],
                            (content_hash, source_id, side_id, location, location))
                existing = cur.fetchone()
                if existing is not None:
                    existing_path_id = existing[0]

            if existing_path_id is None:
                # Same source, same side, same name, same content and same
                # details is one record, whatever folder it came from.
                cur.execute(_SQL["find_same_details"], (
                    context_id, path_row["file_name"], path_row["file_size"],
                    path_row["file_type"], path_row["file_date"],
                ))
                exact = cur.fetchone()
                if exact is not None:
                    existing_path_id = exact[0]

            if existing_path_id is None:
                cur.execute(_SQL["insert_path"], (
                    path_row["file_name"],
                    path_row["file_path"],
                    path_row["file_size"],
                    path_row["file_type"],
                    path_row.get("file_status", "Unread"),
                    path_row["file_date"],
                    path_row["date_creation"],
                    context_id,
                    path_row.get("coordinates"),
                    json.dumps(path_row["extraction_provenance"])
                    if path_row.get("extraction_provenance") is not None else None,
                    path_row.get("processing_status", "discovered"),
                    path_row.get("status_detail"),
                    path_row.get("attempts", 0),
                    path_row.get("parent_path_id"),
                    path_row.get("hierarchy_path"),
                ))
                path_id = cur.fetchone()[0]
            else:
                path_id = existing_path_id

            cur.execute(_SQL["extraction_exists"], (hash_id,))
            extraction_exists = cur.fetchone() is not None

            result = {
                "path_id": path_id,
                "hash_id": hash_id,
                "context_id": context_id,
                "duplicate": existing_path_id is not None,
                "canonical_reused": canonical_reused,
                "context_reused": context_reused,
                "extraction_exists": extraction_exists,
            }
        return result

    # ------------------------------------------------------------------
    # Occurrence reconciliation (in-container re-imports)
    # ------------------------------------------------------------------
    def reconcile_occurrence(
        self, path_id: int, hierarchy_path: str, commit: bool = True
    ) -> Dict[str, Any]:
        """Collapse a repeat in-container occurrence onto its first record.

        When a container is imported again, its members are re-extracted to
        new temporary paths, so they cannot be recognised at insert time.  The
        lineage linker knows each member's stable identity
        (``container::member``).  If the same context already holds an earlier
        occurrence at that location, the just-created row is a repeat of the
        same physical encounter and is removed; the earlier record (with its
        processing history) is kept.  Members with different names keep their
        own rows: two members with identical bytes are two occurrences.

        Returns ``{"removed": bool, "kept_path_id": int}``.
        """
        with self._cursor(commit=commit) as (_, cur):
            cur.execute(_SQL["path_identity"], (path_id,))
            identity = cur.fetchone()
            if identity is None:
                return {"removed": False, "kept_path_id": path_id}
            context_id = identity[2]

            cur.execute(
                _SQL["find_repeat_occurrence"],
                (context_id, hierarchy_path, path_id),
            )
            repeat = cur.fetchone()
            if repeat is None:
                return {"removed": False, "kept_path_id": path_id}

            keeper_id = min(repeat[0], path_id)
            loser_id = max(repeat[0], path_id)
            cur.execute(_SQL["delete_path"], (loser_id,))
        return {"removed": True, "kept_path_id": keeper_id}

    # ------------------------------------------------------------------
    # Delete / orphan lifecycle (DB-05)
    # ------------------------------------------------------------------
    def delete_path(self, path_id: int, commit: bool = True) -> Dict[str, Any]:
        """Delete one occurrence and clean up emptied identity rows.

        Transactional: the occurrence, its context (when no occurrence
        remains) and its canonical content (when no context remains, which
        cascades the derived stores) commit together, so re-ingestion can
        never be blocked by orphaned identity rows.

        Returns ``{"path_deleted", "context_deleted", "hash_deleted"}``.
        """
        result = {"path_deleted": False, "context_deleted": False,
                  "hash_deleted": False}
        with self._cursor(commit=commit) as (_, cur):
            cur.execute(_SQL["delete_path"], (path_id,))
            row = cur.fetchone()
            if row is None:
                return result
            result["path_deleted"] = True
            context_id = row[0]

            cur.execute(_SQL["context_path_count"], (context_id,))
            if cur.fetchone()[0] == 0:
                cur.execute(_SQL["delete_context"], (context_id,))
                row = cur.fetchone()
                if row is not None:
                    result["context_deleted"] = True
                    hash_id = row[0]
                    cur.execute(_SQL["context_count_for_hash"], (hash_id,))
                    if cur.fetchone()[0] == 0:
                        cur.execute(_SQL["delete_canonical"], (hash_id,))
                        result["hash_deleted"] = cur.rowcount > 0
        return result

    # ------------------------------------------------------------------
    # Same-source, same-side repetition (explicit cleanup, operator-run)
    # ------------------------------------------------------------------

    def same_context_duplicates(self) -> List[Dict[str, Any]]:
        """Groups of EXACT repeats inside one (source, side) context.

        An exact repeat has the same source, the same side, the same file name,
        the same content hash and the same details (size, type, date). The
        earliest record (lowest id) is kept; the rest are the repeats. Files
        that only share content but have another name, or another source or
        side, are not repeats and never appear here.
        """
        groups: List[Dict[str, Any]] = []
        with self._cursor(commit=False) as (_, cur):
            cur.execute(_SQL["same_context_groups"])
            for (context_id, content_hash, source_id, side_id, source_name,
                 side_name, path_ids, file_names) in cur.fetchall():
                groups.append({
                    "context_id": context_id,
                    "hash": content_hash,
                    "source_id": source_id,
                    "side_id": side_id,
                    "source": source_name or "",
                    "side": side_name or "",
                    "keep_id": path_ids[0],
                    "remove_ids": list(path_ids[1:]),
                    "file_names": list(file_names),
                })
        return groups

    def remove_same_context_duplicates(self) -> Dict[str, int]:
        """Delete every repeated occurrence inside its (source, side) context.

        Each deletion goes through ``delete_path`` so the identity lifecycle
        stays authoritative. The kept occurrence means no context or content is
        ever emptied by this operation, so no extracted text is lost.
        """
        groups = self.same_context_duplicates()
        removed = 0
        for group in groups:
            for path_id in group["remove_ids"]:
                if self.delete_path(path_id, commit=True)["path_deleted"]:
                    removed += 1
        return {"groups": len(groups), "removed": removed}

    def cleanup_orphaned_hashes(self, dry_run: bool = False) -> int:
        """Remove canonical rows with zero contexts (DB-05 repair tool).

        A canonical row with no context (and therefore no occurrence) can
        never make content a duplicate; these rows are the historical defect
        that made deleted files permanently non-reingestable.
        """
        with self._cursor(commit=not dry_run) as (_, cur):
            cur.execute(_SQL["orphan_hashes"])
            ids = [r[0] for r in cur.fetchall()]
            if ids and not dry_run:
                cur.execute("DELETE FROM hashs WHERE id = ANY(%s)", (ids,))
        return len(ids)

    def cleanup_orphaned_contexts(self, dry_run: bool = False) -> int:
        """Remove context rows with zero occurrences (repair tooling)."""
        with self._cursor(commit=not dry_run) as (_, cur):
            cur.execute(_SQL["orphan_contexts"])
            ids = [r[0] for r in cur.fetchall()]
            if ids and not dry_run:
                cur.execute("DELETE FROM hash_contexts WHERE id = ANY(%s)", (ids,))
        return len(ids)

    def deduplicate_hash_relationships(self, dry_run: bool = False) -> int:
        """Consolidate duplicate relationship records in hash_contexts table.

        Enforces relationship identity (Hash + Source + Side).
        Returns the number of duplicate context rows removed.
        """
        with self._cursor(commit=not dry_run) as (_, cur):
            cur.execute(
                """
                SELECT hash_id, source_id, side_id, COUNT(*), MIN(id) as canonical_id
                FROM hash_contexts
                GROUP BY hash_id, source_id, side_id
                HAVING COUNT(*) > 1
                """
            )
            groups = cur.fetchall()
            removed_count = 0
            for row in groups:
                h_id, src_id, sd_id, cnt, canonical_id = row
                cur.execute(
                    """
                    SELECT id FROM hash_contexts
                    WHERE hash_id = %s AND source_id = %s AND side_id = %s AND id != %s
                    """,
                    (h_id, src_id, sd_id, canonical_id),
                )
                dup_ids = [r[0] for r in cur.fetchall()]
                if dup_ids:
                    removed_count += len(dup_ids)
                    if not dry_run:
                        placeholders = ",".join(["%s"] * len(dup_ids))
                        cur.execute(
                            f"UPDATE paths SET context_id = %s WHERE context_id IN ({placeholders})",
                            (canonical_id, *dup_ids),
                        )
                        cur.execute(
                            f"DELETE FROM hash_contexts WHERE id IN ({placeholders})",
                            tuple(dup_ids),
                        )
        return removed_count
