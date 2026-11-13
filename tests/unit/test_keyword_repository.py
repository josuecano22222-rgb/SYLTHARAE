"""A phrase is stored once, so a second category's claim has to be refused aloud.

``keywords.keyword`` is UNIQUE **globally** - not per category. The
``create_keyword`` method used to catch the resulting duplicate-key error,
look up the *other* category's keyword id and return it as if the insert had
succeeded. The orchestrator then counted the term as imported into a category
that does not contain it, and nothing was logged at a level anybody reads.

The behaviour asserted here: a phrase already owned by another category is not
created again, ``None`` comes back, and the warning names both categories. A
repeat inside the same category is still a quiet success, because that one is
genuinely a no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from apps.importing.database.repositories.keyword_repository import (  # noqa: E402
    KeywordRepository,
)
from apps.importing.utils.exceptions import RepositoryException  # noqa: E402
from core.serialization import pack_int_list  # noqa: E402


class Duplicate(Exception):
    """The message psycopg2 produces for ``keywords_keyword_key``."""

    def __init__(self):
        super().__init__(
            'duplicate key value violates unique constraint "keywords_keyword_key"'
            "\nDETAIL:  Key (keyword)=(\\x5b312c20325d) already exists."
        )


class FakeKeywords:
    """The ``keywords`` table: unique on the blob, whatever the category."""

    def __init__(self, rows=()):
        self.rows = list(rows)                 # [(id, blob, category_id)]
        self.log = []

    def execute_query(self, sql, params=None, fetch=False):
        self.log.append((sql, params))

        if sql.strip().startswith("INSERT INTO keywords"):
            blob, category_id = params
            if any(stored == blob for _, stored, _ in self.rows):
                raise Duplicate()
            new_id = max((i for i, _, _ in self.rows), default=0) + 1
            self.rows.append((new_id, blob, category_id))
            return [(new_id,)]

        if "FROM keywords WHERE keyword = %s AND category_id = %s" in sql:
            blob, category_id = params
            return [(i,) for i, stored, owner in self.rows
                    if stored == blob and owner == category_id]

        if "FROM keywords WHERE keyword = %s" in sql:
            blob = params[0]
            return [(i, owner) for i, stored, owner in self.rows if stored == blob]

        raise AssertionError(f"unexpected SQL: {sql}")


BLOB = pack_int_list([1, 2])


def test_the_same_phrase_in_the_same_category_is_a_quiet_no_op():
    table = FakeKeywords([(7, BLOB, 3)])
    assert KeywordRepository(table).create_keyword([1, 2], 3) == 7


def test_a_phrase_owned_by_another_category_is_not_reported_as_created(caplog):
    """The defect: this used to return the other category's keyword id."""
    table = FakeKeywords([(7, BLOB, 99)])
    with caplog.at_level("WARNING"):
        result = KeywordRepository(table).create_keyword([1, 2], 3)

    assert result is None, "a phrase in another category must not look imported"
    assert not any(owner == 3 for _, _, owner in table.rows), "the row was stolen"
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("category 99" in m and "category 3" in m for m in warnings), warnings


def test_a_new_phrase_is_created_and_returned():
    table = FakeKeywords()
    assert KeywordRepository(table).create_keyword([1, 2], 3) is not None
    assert table.rows[0][1] == BLOB and table.rows[0][2] == 3


@pytest.mark.parametrize("word_ids", [[], [1]])
def test_a_phrase_needs_two_words(word_ids):
    table = FakeKeywords()
    assert KeywordRepository(table).create_keyword(word_ids, 3) is None
    assert table.log == []


def test_an_unrelated_database_error_still_raises():
    class Broken:
        def execute_query(self, sql, params=None, fetch=False):
            raise RuntimeError("connection lost")

    with pytest.raises(RepositoryException):
        KeywordRepository(Broken()).create_keyword([1, 2], 3)
