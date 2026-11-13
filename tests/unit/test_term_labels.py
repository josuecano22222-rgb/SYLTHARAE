"""The resolver's rules, tested without a database.

Every guard here exists because the alternative is a plausible-looking wrong
answer: a label that is silently English, a one-letter query that matches half
the taxonomy, an id set with no upper bound, or an ambiguous key quietly
resolved to the first row. Each is asserted against a fake ``run`` so the rule is
tested on its own, and the integration suite tests the same rules through SQL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.i18n.term_labels import (  # noqa: E402
    DEFAULT_LANGUAGE,
    ID_SET_CAP,
    MIN_QUERY_CHARS,
    describe,
    expand_query,
    label_map,
    labels_for,
    language_chain,
)



class FakeRun:
    """A ``run`` that answers the two SQL shapes the resolver issues."""

    def __init__(self, names=(), canonical=(), expansion=()):
        self.names = list(names)          # (word_id, language, name_type, name)
        self.canonical = list(canonical)  # (word_id, word)
        self.expansion = list(expansion)  # (word_id, word, language)
        self.calls = []

    def __call__(self, sql, params=None, fetch="all"):
        self.calls.append((sql, params))
        if "term_names" in sql and "match_key" in sql:
            return self.expansion
        if "term_names" in sql:
            return self.names
        return self.canonical


class TestTheFallbackChain:
    def test_a_requested_locale_is_tried_first_then_its_base_language_then_default(self):
        assert language_chain("ar-EG") == ("ar-eg", "ar", DEFAULT_LANGUAGE)

    def test_a_language_without_a_region_is_not_duplicated(self):
        assert language_chain("ar") == ("ar", DEFAULT_LANGUAGE)
        assert language_chain("en") == ("en",)

    def test_an_underscore_locale_is_normalised(self):
        assert language_chain("ar_EG") == ("ar-eg", "ar", DEFAULT_LANGUAGE)

    def test_no_locale_is_the_default(self):
        assert language_chain(None) == (DEFAULT_LANGUAGE,)
        assert language_chain("") == (DEFAULT_LANGUAGE,)


class TestLabels:
    def test_a_translated_name_is_marked_translated(self):
        run = FakeRun(names=[(1, "ar", "preferred", "سياسي")])
        label = labels_for(run, [1], "ar")[1]
        assert label.name == "سياسي"
        assert label.source == "translated"
        assert label.translated

    def test_a_missing_language_falls_back_to_the_default_language(self):
        run = FakeRun(names=[(1, "en", "preferred", "political")])
        label = labels_for(run, [1], "ar")[1]
        assert label.name == "political"
        assert label.source == "default-language"
        assert not label.translated

    def test_a_missing_language_falls_back_to_the_canonical_word(self):
        run = FakeRun(names=[], canonical=[(1, "political")])
        label = labels_for(run, [1], "ar")[1]
        assert label.name == "political"
        assert label.source == "canonical"
        assert label.language is None

    def test_a_base_language_name_is_preferred_over_the_default_language(self):
        run = FakeRun(names=[(1, "ar", "preferred", "سياسي"),
                             (1, "en", "preferred", "political")])
        label = labels_for(run, [1], "ar-EG")[1]
        assert label.name == "سياسي"
        assert label.source == "base-language"

    def test_preferred_beats_alternate_within_a_language(self):
        # The SQL orders preferred first; the resolver must not override it.
        run = FakeRun(names=[(1, "ar", "preferred", "سياسي"),
                             (1, "ar", "alternate", "سياسيون")])
        assert labels_for(run, [1], "ar")[1].name == "سياسي"

    def test_one_query_serves_a_whole_page(self):
        run = FakeRun(names=[(i, "ar", "preferred", f"n{i}") for i in range(1, 51)],
                      canonical=[])
        labels = labels_for(run, range(1, 51), "ar")
        assert len(labels) == 50
        assert len(run.calls) == 1, "a page of labels must not be N+1"

    def test_the_canonical_lookup_only_asks_for_the_gaps(self):
        run = FakeRun(names=[(1, "ar", "preferred", "سياسي")],
                      canonical=[(2, "political")])
        labels = labels_for(run, [1, 2], "ar")
        assert labels[2].name == "political"
        _sql, params = run.calls[1]
        assert params[0] == [2], "words that already had a label must not be queried again"

    def test_an_empty_page_asks_the_database_nothing(self):
        run = FakeRun()
        assert labels_for(run, [], "ar") == {}
        assert run.calls == []

    def test_label_map_is_the_plain_shape(self):
        run = FakeRun(names=[(1, "ar", "preferred", "سياسي")], canonical=[(2, "political")])
        assert label_map(run, [1, 2], "ar") == {1: "سياسي", 2: "political"}


class TestExpansionGuards:
    def test_a_query_shorter_than_the_minimum_is_not_expanded(self):
        run = FakeRun(expansion=[(1, "political", "en")])
        out = expand_query(run, "س")
        assert out.word_ids == ()
        assert out.too_short
        assert run.calls == [], "a one-letter query must not reach the database"

    def test_the_minimum_is_measured_on_the_normalised_key(self):
        # Two Hebrew letters plus niqqud: the niqqud is dropped, so the key is
        # short and the query is refused.
        assert expand_query(FakeRun(), "שָׁם").too_short

    def test_an_empty_query_expands_to_nothing_and_asks_nothing(self):
        # Empty is "nothing was typed", not "too short to expand": the note for
        # too_short would be a misleading thing to show for an empty box.
        run = FakeRun()
        out = expand_query(run, "   ")
        assert out.word_ids == ()
        assert not out.too_short
        assert run.calls == []

    def test_a_normal_query_expands_to_its_word_ids(self):
        run = FakeRun(expansion=[(7, "political", "ar")])
        out = expand_query(run, "سياسي")
        assert out.word_ids == (7,)
        assert not out.truncated
        assert out.key == "سياسي"

    def test_the_id_set_is_capped(self):
        run = FakeRun(expansion=[(i, f"w{i}", "en") for i in range(1, ID_SET_CAP + 20)])
        out = expand_query(run, "political")
        assert len(out.word_ids) == ID_SET_CAP
        assert out.truncated
        assert "truncated" in describe(out)

    def test_ambiguity_is_returned_not_resolved(self):
        run = FakeRun(expansion=[(1, "political", "en"), (2, "politics", "en")])
        out = expand_query(run, "political")
        assert out.word_ids == (1, 2)
        assert out.ambiguous
        assert {c.word_id for c in out.candidates} == {1, 2}
        assert describe(out)["ambiguous"] is True

    def test_the_candidate_list_is_bounded_but_says_so(self):
        run = FakeRun(expansion=[(i, f"w{i}", "en") for i in range(1, 60)])
        out = expand_query(run, "political")
        assert len(out.candidates) <= 20
        assert out.candidates_truncated

    def test_a_language_filter_is_optional_and_narrowing(self):
        run = FakeRun(expansion=[(1, "political", "en")])
        expand_query(run, "political", languages=["en"])
        sql, params = run.calls[0]
        assert "t.language = ANY(%s)" in sql
        assert list(params[1]) == ["en"]

    def test_expansion_is_falsey_when_it_finds_nothing(self):
        assert not expand_query(FakeRun(expansion=[]), "nonexistent-term")

    def test_a_refusal_explains_itself(self):
        note = describe(expand_query(FakeRun(), "س"))
        assert "note" in note and str(MIN_QUERY_CHARS) in note["note"]
