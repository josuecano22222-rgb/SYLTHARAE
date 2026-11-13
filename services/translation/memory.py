"""Translation memory: a local SQLite file, outside PostgreSQL, by design.

Why not a table
===============

The schema is frozen, and for a good reason: most of what the system needs can
be derived from the rows it already stores. Translation memory is the exception
- a corrected translation is *not* derivable from anything - so it lives in its
own file, under ``APP_DATA_DIR``, the way ``map_data/registry.db`` holds the
offline map registry. Nothing in PostgreSQL changes, nothing in
``database/migrations`` changes, and a translation memory can be copied between
installations (or deleted) with one file operation.

What a row records
==================

``origin`` is ``engine`` (a machine translation) or ``user`` (an analyst's
correction). Every row also records the engine id and version that produced it,
so a translation can always be attributed, and a correction keeps
``actor`` - the username that made it.

The rules this module enforces
==============================

1. an exact match is reused, and counts its uses;
2. a user correction is final: :meth:`TranslationMemory.remember` will not
   overwrite a ``user`` row with an ``engine`` one, however many times the
   engine is asked again;
3. fuzzy matches are a *suggestion* (:meth:`suggest`), never a translation: a
   near-identical sentence is offered to the analyst, and
   :meth:`TranslationMemory.lookup` never returns one.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

__all__ = ["TranslationMemory", "MemoryUnavailable", "default_memory_path"]

_DB_NAME = "translation_memory.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tm_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_lang TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('engine', 'user', 'imported')),
    engine TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    actor TEXT,
    uses INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_lang, target_lang, source_hash)
);
CREATE INDEX IF NOT EXISTS idx_tm_entries_pair ON tm_entries (source_lang, target_lang);
CREATE TABLE IF NOT EXISTS tm_glossary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_lang TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    term TEXT NOT NULL,
    translation TEXT NOT NULL,
    actor TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_lang, target_lang, term)
);
"""


class MemoryUnavailable(RuntimeError):
    """The memory file cannot be opened.

    Deliberately not silent: the file holds the analyst's corrections, so a
    memory that has gone missing is reported, not recreated empty behind the
    caller's back.
    """


class _MemoryConnection(sqlite3.Connection):
    """A connection that reports I/O failures as :class:`MemoryUnavailable`.

    Opening the file is not the only way a memory can fail: a full disk, a
    read-only file or a bad block all surface later, while writing. Those used
    to escape as raw ``sqlite3.OperationalError`` and reached the analyst as
    "An internal error occurred" (HTTP 500) - measured in the round-10 disk-full
    drill. The API already knows how to report an unavailable memory (503
    ``memory_unavailable``); it just needs to be told.
    """

    def _guard(self, call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            raise MemoryUnavailable(
                f"the translation memory cannot be written: {exc}") from exc
        except sqlite3.DatabaseError as exc:
            raise MemoryUnavailable(
                f"the translation memory cannot be used: {exc}") from exc

    def execute(self, *args, **kwargs):
        return self._guard(super().execute, *args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._guard(super().executemany, *args, **kwargs)

    def executescript(self, *args, **kwargs):
        return self._guard(super().executescript, *args, **kwargs)

    def commit(self):
        return self._guard(super().commit)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Match sqlite3.Connection's behaviour (commit on success, roll back on
        # error), but translate a failing commit instead of leaking it.
        if exc_type is None:
            self.commit()
        else:
            try:
                super().rollback()
            except sqlite3.Error:
                pass
            return False
        return False


def default_memory_path() -> Path:
    """``APP_DATA_DIR/translation/translation_memory.db`` (created on demand)."""
    from core.app_paths import get_data_root

    return Path(get_data_root()) / "translation" / _DB_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(text: str) -> str:
    return hashlib.sha256(" ".join((text or "").split()).encode("utf-8")).hexdigest()


class TranslationMemory:
    """The translation memory file. Cheap to construct; it opens per call."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else default_memory_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect(initialising=True) as conn:
            conn.executescript(_SCHEMA)
        # From here on the file is expected to exist: if it does not, it was
        # lost, and saying so is the only honest answer - silently starting an
        # empty memory would throw the analyst's corrections away without a word.
        self._initialised = True

    # -- plumbing ----------------------------------------------------------
    def _connect(self, *, initialising: bool = False) -> sqlite3.Connection:
        """Open the memory file, or raise :class:`MemoryUnavailable`.

        Three cases, three answers:

        * the directory is missing - it is ours (``APP_DATA_DIR/translation``),
          so create it and carry on;
        * the file is missing after this object was constructed - the memory was
          lost; refuse, and do not quietly start an empty one;
        * the file is there but cannot be opened, or its tables are gone - refuse
          with the reason.
        """
        if not initialising and getattr(self, "_initialised", False) \
                and not self.path.exists():
            raise MemoryUnavailable(
                f"the translation memory file has disappeared ({self.path}); "
                "the stored corrections are not available")
        try:
            if not self.path.parent.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, factory=_MemoryConnection)
        except (OSError, sqlite3.Error) as exc:
            raise MemoryUnavailable(
                f"the translation memory is unavailable ({self.path}): {exc}") from exc
        conn.row_factory = sqlite3.Row
        if not initialising:
            try:
                conn.execute("SELECT 1 FROM tm_entries LIMIT 1")
            except sqlite3.Error as exc:
                conn.close()
                raise MemoryUnavailable(
                    f"the translation memory at {self.path} cannot be used: {exc}") from exc
        return conn

    # -- translations ------------------------------------------------------
    def remember(self, *, source_lang: str, target_lang: str, source_text: str,
                 target_text: str, origin: str, engine: str, engine_version: str,
                 actor: Optional[str] = None) -> Dict:
        """Store (or refresh) one translation.

        An ``engine`` write never replaces a ``user`` row - the analyst's
        correction is the more authoritative record.
        """
        source_hash = _hash(source_text)
        now = _now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT origin FROM tm_entries WHERE source_lang = ? AND"
                " target_lang = ? AND source_hash = ?",
                (source_lang, target_lang, source_hash)).fetchone()
            if existing is not None:
                stored_origin = existing["origin"]
                if stored_origin == "user" and origin != "user":
                    return self._row(conn, source_lang, target_lang, source_hash)
                conn.execute(
                    "UPDATE tm_entries SET target_text = ?, origin = ?, engine = ?,"
                    " engine_version = ?, actor = ?, updated_at = ?"
                    " WHERE source_lang = ? AND target_lang = ? AND source_hash = ?",
                    (target_text, origin, engine, engine_version,
                     actor if origin == "user" else None, now,
                     source_lang, target_lang, source_hash))
            else:
                conn.execute(
                    "INSERT INTO tm_entries (source_lang, target_lang, source_hash,"
                    " source_text, target_text, origin, engine, engine_version,"
                    " actor, uses, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                    (source_lang, target_lang, source_hash,
                     " ".join(source_text.split()), target_text, origin, engine,
                     engine_version, actor if origin == "user" else None, now, now))
            conn.commit()
            return self._row(conn, source_lang, target_lang, source_hash)

    def correct(self, *, source_lang: str, target_lang: str, source_text: str,
                target_text: str, actor: Optional[str] = None) -> Dict:
        """Record an analyst's correction - always ``origin = 'user'``."""
        return self.remember(source_lang=source_lang, target_lang=target_lang,
                             source_text=source_text, target_text=target_text,
                             origin="user", engine="user", engine_version="user",
                             actor=actor)

    def lookup(self, *, source_lang: str, target_lang: str,
               source_text: str) -> Optional[Dict]:
        """The stored translation for this exact sentence, or ``None``."""
        source_hash = _hash(source_text)
        with self._connect() as conn:
            row = self._row(conn, source_lang, target_lang, source_hash)
            if row is None:
                return None
            conn.execute(
                "UPDATE tm_entries SET uses = uses + 1 WHERE id = ?", (row["id"],))
            conn.commit()
            row["uses"] += 1
            return row

    def suggest(self, *, source_lang: str, target_lang: str, source_text: str,
                limit: int = 5, threshold: float = 0.6) -> List[Dict]:
        """Fuzzy matches for *source_text* - suggestions, never translations."""
        wanted = " ".join((source_text or "").split())
        if not wanted:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tm_entries WHERE source_lang = ? AND target_lang = ?",
                (source_lang, target_lang)).fetchall()
        scored = []
        for row in rows:
            score = difflib.SequenceMatcher(
                None, wanted, row["source_text"]).ratio()
            if score >= threshold and row["source_text"] != wanted:
                entry = dict(row)
                entry["score"] = round(score, 4)
                scored.append(entry)
        scored.sort(key=lambda entry: entry["score"], reverse=True)
        return scored[:limit]

    def _row(self, conn, source_lang: str, target_lang: str,
             source_hash: str) -> Optional[Dict]:
        row = conn.execute(
            "SELECT * FROM tm_entries WHERE source_lang = ? AND target_lang = ?"
            " AND source_hash = ?", (source_lang, target_lang, source_hash)).fetchone()
        return dict(row) if row is not None else None

    # -- glossary ----------------------------------------------------------
    def set_glossary_term(self, *, source_lang: str, target_lang: str, term: str,
                          translation: str, actor: Optional[str] = None) -> Dict:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tm_glossary (source_lang, target_lang, term,"
                " translation, actor, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (source_lang, target_lang, term) DO UPDATE SET"
                " translation = excluded.translation, actor = excluded.actor,"
                " updated_at = excluded.updated_at",
                (source_lang, target_lang, term, translation, actor, now, now))
            conn.commit()
        return {"term": term, "translation": translation}

    def glossary(self, *, source_lang: str, target_lang: str) -> Dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT term, translation FROM tm_glossary WHERE source_lang = ?"
                " AND target_lang = ? ORDER BY term",
                (source_lang, target_lang)).fetchall()
        return {row["term"]: row["translation"] for row in rows}

    def apply_glossary_terms(self, text: str, glossary: Dict[str, str]) -> List[str]:
        """Terms of *glossary* that occur in *text* (case-insensitive words)."""
        found = []
        for term in glossary:
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.IGNORECASE):
                found.append(term)
        return found

    def apply_glossary(self, text: str, glossary: Dict[str, str]) -> str:
        """Replace every glossary term in *text* with its agreed translation.

        Applied to the *translation*, never to the source: the original is what
        the analyst selected and must come back unchanged.
        """
        result = text
        for term, translation in glossary.items():
            result = re.sub(rf"(?<!\w){re.escape(term)}(?!\w)", translation, result,
                            flags=re.IGNORECASE)
        return result

    # -- reporting ---------------------------------------------------------
    def stats(self) -> Dict:
        with self._connect() as conn:
            total = conn.execute("SELECT count(*) AS n FROM tm_entries").fetchone()["n"]
            user = conn.execute(
                "SELECT count(*) AS n FROM tm_entries WHERE origin = 'user'"
            ).fetchone()["n"]
            glossary = conn.execute(
                "SELECT count(*) AS n FROM tm_glossary").fetchone()["n"]
        return {"entries": total, "corrections": user, "glossary_terms": glossary,
                "path": str(self.path)}
