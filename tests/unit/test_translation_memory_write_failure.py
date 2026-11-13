"""A translation memory that cannot be written must say so, not 500.

Found by the round-10 disk-full drill (tmpfs mounted full at
``APP_DATA_DIR/translation``): ``POST /api/translation/memory/correct`` answered

    {"correlation_id": "ERR-20261024-000001",
     "error": "An internal error occurred"}            HTTP 500

and the server log carried ``sqlite3.OperationalError: database or disk is
full``. The API already has the right answer for this - ``503
memory_unavailable``, translated from :class:`MemoryUnavailable` - but only
*opening* the file was translated. A write that fails afterwards (disk full,
read-only file, I/O error) escaped as a raw ``sqlite3`` error.

These tests force the failure deterministically with a read-only file, which is
the same class of failure as a full disk and needs no root.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from services.translation.memory import MemoryUnavailable, TranslationMemory


def _memory(tmp_path: Path) -> TranslationMemory:
    memory = TranslationMemory(path=tmp_path / "translation" / "tm.db")
    memory.remember(source_lang="en", target_lang="ar", source_text="port",
                    target_text="ميناء", origin="engine", engine="stub",
                    engine_version="1")
    return memory


def _make_unwritable(path: Path) -> None:
    """Let sqlite open the file for reading but fail every write."""
    os.chmod(path, 0o444)
    os.chmod(path.parent, 0o555)   # no journal, no temp file either


def _restore(path: Path) -> None:
    os.chmod(path.parent, 0o755)
    os.chmod(path, 0o644)


def test_a_failed_write_is_reported_as_memory_unavailable(tmp_path):
    memory = _memory(tmp_path)
    _make_unwritable(memory.path)
    try:
        with pytest.raises(MemoryUnavailable) as caught:
            memory.correct(source_lang="en", target_lang="ar",
                           source_text="port", target_text="مَرْفَأ",
                           actor="admin")
    finally:
        _restore(memory.path)
    message = str(caught.value).lower()
    assert "translation memory" in message
    assert "disk" in message or "readonly" in message


def test_a_failed_suggestion_read_does_not_leak_sqlite_errors(tmp_path):
    """Lookups read; a broken file must not surface as sqlite3.OperationalError."""
    memory = _memory(tmp_path)
    memory.path.write_bytes(b"not a database")
    with pytest.raises(MemoryUnavailable):
        memory.suggest(source_lang="en", target_lang="ar", source_text="port")


def test_the_raw_sqlite_error_never_escapes(tmp_path):
    memory = _memory(tmp_path)
    _make_unwritable(memory.path)
    try:
        with pytest.raises(Exception) as caught:
            memory.remember(source_lang="en", target_lang="he",
                            source_text="port", target_text="נמל",
                            origin="user", engine="stub", engine_version="1")
    finally:
        _restore(memory.path)
    assert not isinstance(caught.value, sqlite3.Error)
    assert isinstance(caught.value, MemoryUnavailable)
