"""``WordRepository.insert`` must not split one word into two rows.

``words.word`` is UNIQUE **case-sensitively**, and ``categorys.word_id`` is
UNIQUE, so two spellings of one word become two rows and then two categories.
The repository used to lower-case every input: a database filled by the sync
tool (which keeps the workbook's spelling, because that is what the interface
displays) then grew a lowercase twin the first time the in-app importer ran.

A fake transaction manager stands in for PostgreSQL here. It answers only the
two queries the repository makes, and it models the table's case-sensitive
unique index, so a case-fold duplicate shows up as the second row it really is.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from apps.importing.database.repositories.word_repository import WordRepository  # noqa: E402


class FakeWords:
    """The ``words`` table: an ordered list of (id, word), unique on word."""

    def __init__(self, rows=()):
        self.rows = [(int(i), w) for i, w in rows]
        self.queries = []

    def execute_query(self, sql, params=None, fetch=False):
        self.queries.append((sql, params))

        if "INSERT INTO words" in sql:
            word = params[0]
            if any(stored == word for _, stored in self.rows):
                return []                      # ON CONFLICT DO NOTHING
            new_id = max((i for i, _ in self.rows), default=0) + 1
            self.rows.append((new_id, word))
            return [(new_id,)]

        if "lower(word) = lower(%s)" in sql:
            word = params[0]
            matches = [row for row in self.rows if row[1].lower() == word.lower()]
            # the exact spelling comes first, then the oldest row
            matches.sort(key=lambda row: (row[1] != word, row[0]))
            return [(matches[0][0],)] if matches else []

        if "WHERE word = %s" in sql:
            word = params[0]
            return [(i,) for i, stored in self.rows if stored == word]

        raise AssertionError(f"unexpected SQL: {sql}")


def test_a_capitalised_word_reuses_the_stored_row_of_another_casing():
    table = FakeWords([(1, "networks")])
    repo = WordRepository(table)
    assert repo.insert("Networks") == 1
    assert table.rows == [(1, "networks")], "a second row was created"


def test_a_new_word_keeps_the_spelling_it_was_given():
    table = FakeWords()
    repo = WordRepository(table)
    assert repo.insert("AI & Machine Learning") is not None
    assert table.rows[0][1] == "AI & Machine Learning", "the display spelling was lost"


def test_the_exact_spelling_wins_when_both_casings_exist():
    """Two rows can already exist; each spelling finds its own row.

    A fresh repository per lookup, because the cache is keyed case-insensitively
    on purpose: within one import the first answer is reused for every casing,
    which is what keeps one word to one row.
    """
    table = FakeWords([(1, "networks"), (2, "Networks")])
    assert WordRepository(table).insert("Networks") == 2
    assert WordRepository(table).insert("networks") == 1


def test_the_cache_answers_a_second_call_in_another_casing():
    table = FakeWords()
    repo = WordRepository(table)
    first = repo.insert("Networks")
    lookups_before = len(table.queries)
    assert repo.insert("NETWORKS") == first
    assert len(table.queries) == lookups_before, "the cache did not answer"


def test_whitespace_is_trimmed_but_case_is_not_changed():
    table = FakeWords()
    repo = WordRepository(table)
    repo.insert("  Load   Balancing  ")
    assert table.rows[0][1] == "Load Balancing"


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_an_empty_word_is_refused_without_a_query(empty):
    table = FakeWords()
    assert WordRepository(table).insert(empty) is None
    assert table.queries == []
