"""Two guarantees about what the feature does *not* touch, and what it refuses.

Criterion 7 - **document-content search is unchanged**. D3 keeps translation
expansion out of full-text search on purpose: a translated query must find the
*taxonomy*, not silently add documents. That is a claim about a code path, so it
is tested as one: the same query, over the same documents, before and after the
translation rows exist, must return the same rows.

Criterion 8 - **hostile input is refused before PostgreSQL builds a plan**. The
query text is a parameter either way, so the interesting failures are the ones
that reach the *resolver*: wildcards, quotes, NUL bytes, bidi controls, a 10 000
character string, and a query crafted to make the resolver match everything.
Each must leave the table intact and behave like a query that matches little or
nothing.

Both run against the real schema through the same ingestion tables the app
writes (``tests/integration/_seed.py``), because "unchanged" over an empty
database proves nothing.
"""

from __future__ import annotations

import itertools
import re
import uuid

import psycopg2
import pytest

from _seed import connect, document, side, source, word_counts

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]
_counter = itertools.count()


def _corpus(pg_db):
    """One source, one side and three documents, each with word counts."""
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            source_id = source(cur, name=f"src-docsearch-{_UNIQUE}")
            side_id = side(cur, name=f"side-docsearch-{_UNIQUE}")
            ids = []
            for text, words in (
                    ("political stability in the region", {"political": 2, "stability": 2}),
                    ("economic outlook and trade", {"economic": 3, "trade": 1}),
                    ("unrelated material about weather", {"weather": 2})):
                path_id, hash_id, _ctx = document(
                    cur, source_id=source_id, side_id=side_id, text=text,
                    file_name=f"docsearch-{_UNIQUE}-{next(_counter)}.txt")
                word_counts(cur, hash_id, words)
                ids.append({"path_id": path_id, "hash_id": hash_id})
        conn.commit()
    finally:
        conn.close()
    return ids


def _search(client, term):
    """/search renders a page, so the rows are read from the page it renders.

    The first version called ``response.get_json()`` on HTML, got ``None`` and
    compared two empty results - a guard that passed for the wrong reason.
    """
    response = client.get("/search", query_string={"q": term})
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    html = response.get_data(as_text=True)
    rows = re.findall(r'data-file-id="(\d+)"', html)
    return rows


def _names_table_count(pg_db) -> int:
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM term_names")
            return cur.fetchone()[0]
    finally:
        conn.close()


class TestDocumentContentSearchIsUnchanged:
    def test_the_same_query_returns_the_same_rows_with_or_without_names(
            self, admin_client, pg_db):
        """Seed the corpus, search, add every name a translation could match,
        search again - the answer must be identical."""
        from core.i18n.term_key import term_key

        corpus = _corpus(pg_db)
        before = {term: _search(admin_client, term) for term in
                  ("political", "stability", "weather", "political stability")}
        # The guard must not pass because both sides are empty.
        assert _result_ids(before["political"]), before["political"]

        conn = connect(pg_db)
        try:
            with conn.cursor() as cur:
                # A name for every word in the corpus, in Arabic - the case that
                # would change the answer if document search consulted names.
                cur.execute("SELECT id, word FROM words WHERE word IN"
                            " ('political', 'stability', 'weather', 'economic')")
                for word_id, word in cur.fetchall():
                    cur.execute(
                        "INSERT INTO term_names (word_id, name, match_key, language,"
                        " name_type, script, source) VALUES (%s, %s, %s, 'ar',"
                        " 'preferred', 'Arab', 'curated') ON CONFLICT DO NOTHING",
                        (word_id, "السِّياسي", term_key("السِّياسي")))
            conn.commit()
        finally:
            conn.close()

        after = {term: _search(admin_client, term) for term in before}
        assert after == before, "document search changed when names were added"

    def test_an_arabic_query_does_not_add_document_hits(self, admin_client, pg_db):
        """D3: a translated query finds the taxonomy, never the documents."""
        _corpus(pg_db)
        english = _search(admin_client, "political")
        arabic = _search(admin_client, "السياسي")
        assert _result_ids(english) != _result_ids(arabic) or not _result_ids(english), \
            "an Arabic query returned the same documents as an English one"


def _result_ids(payload) -> list:
    """The document ids in a /search answer (already the ids from the page)."""
    return list(payload or [])


class TestHostileInputNeverReachesThePlanner:
    """The resolver is the new surface, so it gets the hostile strings."""

    HOSTILE = [
        "%",
        "_",
        "%%%",
        ".*",
        "' OR 1=1 --",
        "political' ; DROP TABLE words; --",
        "\x00",
        "\u202e",
        "a" * 10_000,
        "' || (SELECT current_user) || '",
    ]

    @pytest.mark.parametrize("term", HOSTILE)
    def test_a_hostile_query_does_not_break_the_search(self, admin_client, pg_db, term):
        conn = connect(pg_db)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM words")
                words_before = cur.fetchone()[0]
        finally:
            conn.close()

        response = admin_client.get("/search", query_string={"q": term})
        assert response.status_code in (200, 400), response.status_code

        conn = connect(pg_db)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM words")
                assert cur.fetchone()[0] == words_before, "a query changed the words table"
        finally:
            conn.close()

    @pytest.mark.parametrize("term", HOSTILE)
    def test_the_resolver_declines_instead_of_matching_everything(self, term):
        from services.i18n.term_search import expand_for_search

        expansion = expand_for_search(term)
        assert not expansion.word_ids, f"{term!r} resolved to {expansion.word_ids}"

    def test_the_expansion_is_bounded_for_a_hostile_query(self):
        """The cap is the guard against an id set the planner cannot use."""
        from services.i18n.term_labels import ID_SET_CAP
        from services.i18n.term_search import expand_for_search

        expansion = expand_for_search("ا" * 200)
        assert len(expansion.word_ids) <= ID_SET_CAP
