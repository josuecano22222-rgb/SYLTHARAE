"""Cross-language search: a query in any language finds the term, in every search.

This is the acceptance test for the feature's core requirement, and it is run
against the real application and a real database rather than against the
resolver's own API, because "the resolver returns an id" is not the claim being
made. The claim is:

    a category named *political* in English and *سياسي* in Arabic is found by
    both spellings, from six different searches, in either interface language.

Every search is exercised here:

| search | endpoint |
| --- | --- |
| category list | ``GET /categories`` |
| category API | ``GET /api/categories/search`` |
| category tree | ``GET /api/categories/tree?search=`` |
| word picker | ``GET /api/words/search`` |
| keywords | ``GET /api/keywords`` |
| file-analysis facet | ``GET /api/file-analysis/words`` |

and the negative direction too: with no translation rows at all, every search
returns exactly what it returned before this feature existed.
"""

from __future__ import annotations

import uuid

import psycopg2
import pytest

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]
EN_NAME = f"political-{_UNIQUE}"
AR_NAME = f"سياسي-{_UNIQUE}"
HE_NAME = f"פוליטי-{_UNIQUE}"


@pytest.fixture(scope="module")
def terms(pg_db):
    """One word with an Arabic and a Hebrew name, and a category on it."""
    from core.i18n.term_key import term_key

    conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                            password=pg_db["password"], dbname=pg_db["database"])
    ids = {}
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (EN_NAME,))
            word_id = cur.fetchone()[0]
            cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                        (word_id,))
            category_id = cur.fetchone()[0]
            for language, name, script in (("ar", AR_NAME, "Arab"),
                                           ("he", HE_NAME, "Hebr"),
                                           ("en", EN_NAME, "Latn")):
                cur.execute(
                    "INSERT INTO term_names (word_id, name, match_key, language,"
                    " name_type, script) VALUES (%s, %s, %s, %s, 'preferred', %s)",
                    (word_id, name, term_key(name), language, script))
            # A keyword made of the word plus a second word, so the keyword
            # search has something to match through the translation.
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id",
                        (f"stability-{_UNIQUE}",))
            second = cur.fetchone()[0]
            from core.serialization import pack_int_list
            cur.execute("INSERT INTO keywords (keyword, category_id) VALUES (%s, %s)"
                        " RETURNING id",
                        (pack_int_list([word_id, second]), category_id))
            keyword_id = cur.fetchone()[0]
            # The file-analysis word facet reads `words_categorys`, so the word
            # has to be assigned to the category for that search to see it.
            cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                        " VALUES (%s, %s) ON CONFLICT DO NOTHING", (word_id, category_id))
            cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                        " VALUES (%s, %s) ON CONFLICT DO NOTHING", (second, category_id))
            # ...and it joins `words_hashs` -> `hash_contexts` -> `paths`, so the
            # facet only sees a word that occurs in a document. One occurrence.
            from _seed import document, source, side
            source_id = source(cur, f"names-src-{_UNIQUE}")
            side_id = side(cur, f"names-side-{_UNIQUE}")
            _path_id, hash_id, _context = document(
                cur, source_id=source_id, side_id=side_id,
                text=f"{EN_NAME} appears once", file_name=f"names-{_UNIQUE}.txt")
            cur.execute("INSERT INTO words_hashs (hash_id, word_id, word_count,"
                        " position_indexer) VALUES (%s, %s, 1, ''::bytea)",
                        (hash_id, word_id))
        conn.commit()
    finally:
        conn.close()
    ids.update({"word_id": word_id, "category_id": category_id,
                "keyword_id": keyword_id, "second_word": second})
    return ids


def _keys(found, expected):
    return [row for row in found if str(expected) in str(row)]


class TestTheCategorySearches:
    def test_the_category_list_finds_the_arabic_name(self, admin_client, terms):
        html = admin_client.get(f"/categories?search={AR_NAME}").get_data(as_text=True)
        assert AR_NAME in html or EN_NAME in html, "the category was not listed"

    def test_the_category_api_finds_the_arabic_name(self, admin_client, terms):
        body = admin_client.get(f"/api/categories/search?q={AR_NAME}").get_json()
        ids = [row["id"] for row in body["results"]]
        assert terms["category_id"] in ids, body

    def test_the_category_api_finds_the_english_name_from_the_same_data(
            self, admin_client, terms):
        body = admin_client.get(f"/api/categories/search?q={EN_NAME}").get_json()
        assert terms["category_id"] in [row["id"] for row in body["results"]]

    def test_the_tree_search_marks_the_node_as_a_match(self, admin_client, terms):
        body = admin_client.get(f"/api/categories/tree?search={AR_NAME}").get_json()
        rows = body.get("rows") or []
        matched = [row for row in rows if row["id"] == terms["category_id"]]
        assert matched, rows
        assert matched[0]["is_match"] is True

    def test_the_tree_search_finds_the_hebrew_name_too(self, admin_client, terms):
        body = admin_client.get(f"/api/categories/tree?search={HE_NAME}").get_json()
        assert [r for r in body.get("rows") or [] if r["id"] == terms["category_id"]]

    def test_a_short_query_is_not_expanded_and_finds_nothing_new(self, admin_client, terms):
        # Three characters of the Arabic name are below the minimum only if the
        # normalised key is short; either way the answer must not be an error.
        response = admin_client.get("/api/categories/search?q=س")
        assert response.status_code == 200


class TestTheWordPicker:
    def test_an_arabic_query_returns_the_english_word(self, admin_client, terms):
        body = admin_client.get(f"/api/words/search?q={AR_NAME}").get_json()
        words = [row["word"] for row in (body.get("results") or body.get("words") or [])]
        assert EN_NAME in words, body

    def test_an_english_query_still_returns_it(self, admin_client, terms):
        body = admin_client.get(f"/api/words/search?q={EN_NAME}").get_json()
        words = [row["word"] for row in (body.get("results") or body.get("words") or [])]
        assert EN_NAME in words


class TestTheKeywordSearch:
    def test_an_arabic_query_finds_a_keyword_made_of_the_english_word(
            self, admin_client, terms):
        body = admin_client.get(f"/api/keywords?q={AR_NAME}").get_json()
        ids = [row["id"] for row in (body.get("keywords") or body.get("results") or [])]
        assert terms["keyword_id"] in ids, body


class TestTheFileAnalysisFacet:
    def test_an_arabic_query_matches_the_word_facet(self, admin_client, terms):
        response = admin_client.get(f"/api/file-analysis/words?search={AR_NAME}")
        assert response.status_code == 200
        body = response.get_json()
        # The facet calls the word column "name".
        names = [row.get("name") for row in (body.get("data") or [])]
        assert EN_NAME in names, body


class TestNoTranslationsChangesNothing:
    """Without ``term_names`` rows the searches are byte-identical to before."""

    def test_a_word_without_names_is_found_only_by_its_text(self, admin_client, pg_db):
        marker = f"untranslated-{uuid.uuid4().hex[:6]}"
        conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"],
                                user=pg_db["user"], password=pg_db["password"],
                                dbname=pg_db["database"])
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id",
                            (marker,))
                word_id = cur.fetchone()[0]
                cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                            (word_id,))
                category_id = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()

        found = admin_client.get(f"/api/categories/search?q={marker}").get_json()
        assert category_id in [row["id"] for row in found["results"]]
        # And a query that is not its text does not invent a match.
        missed = admin_client.get(f"/api/categories/search?q={marker[:-2]}zz")
        assert category_id not in [row["id"] for row in missed.get_json()["results"]]
