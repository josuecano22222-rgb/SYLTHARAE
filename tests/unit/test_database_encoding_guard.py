"""A database in the wrong encoding is refused at bootstrap, not discovered later.

Found by the round-10 clean-install drill. The cluster was created with
``initdb`` and the application database with ``createdb`` (no ``-E UTF8``), so
``pg_encoding_to_char(encoding)`` was ``SQL_ASCII``. The installer accepted it,
and the first import showed what that costs:

    ERROR database.services.contents_db_service: Optional raw_text step failed
    for file 'testPDFTwoTextBoxes.pdf' (path_id=62), the document itself is
    stored with partial data: Query execution failed: 'ascii' codec can't
    encode character '\\xae' in position 73: ordinal not in range(128)

psycopg2 maps a SQL_ASCII server to the ``ascii`` codec, so every non-ASCII
character degrades the document - for an application whose corpora are
Arabic, Hebrew and Persian that is data loss on ingest.

``ensure_database`` already creates a *new* database with ``ENCODING 'UTF8'
TEMPLATE template0``; the gap was the pre-existing database, which bootstrap
connected to without looking.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from database import bootstrap  # noqa: E402


class _FakeCursor:
    def __init__(self, encoding):
        self._encoding = encoding
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, query, params=None):
        assert "pg_encoding_to_char" in query, query
        self._result = (self._encoding,)

    def fetchone(self):
        return self._result


class _FakeConnection:
    def __init__(self, encoding):
        self._encoding = encoding

    def cursor(self):
        return _FakeCursor(self._encoding)


def test_utf8_database_passes():
    assert bootstrap.preflight_encoding(_FakeConnection("UTF8")) == "UTF8"


@pytest.mark.parametrize("encoding", ["SQL_ASCII", "LATIN1", "WIN1252"])
def test_a_non_utf8_database_is_refused_with_the_reason(encoding):
    with pytest.raises(bootstrap.BootstrapError) as caught:
        bootstrap.bootstrap_encoding_guard(_FakeConnection(encoding))
    message = str(caught.value)
    assert encoding in message
    assert "UTF8" in message
    # The operator needs to know what it costs, not just that it is wrong.
    assert "rebuild" in message.lower() or "recreate" in message.lower()
