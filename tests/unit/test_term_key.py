"""The one normalisation the term-name feature depends on.

Every assertion here is a claim from ``docs/plans/CATEGORY_TERM_I18N.md``: that
the analyst's query and the stored name meet regardless of language, that the
Arabic definite article is optional in both directions, and that the script rule
the migration enforces is the same one the API applies.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.geo.names import match_key  # noqa: E402
from core.i18n.term_key import (  # noqa: E402
    ARABIC_ARTICLE,
    key_variants,
    script_ok,
    term_key,
)

pytestmark = pytest.mark.unit


class TestTheFoldingRulesAreTheGazetteers:
    """No second folding table: the same input gives the same key in both."""

    def test_harakat_and_tatweel_are_dropped(self):
        # السياسي with a shadda, a kasra and a tatweel between two letters.
        assert term_key("السِّـياسي") == term_key("السياسي") == "السياسي"

    def test_arabic_letter_variants_meet(self):
        assert term_key("سیاسی") == term_key("سياسي")          # Persian yeh, Arabic yeh
        assert term_key("سياسى") == term_key("سياسي")          # alef maqsura
        assert term_key("کتاب") == match_key("كتاب")           # keheh -> kaf

    def test_persian_half_space_is_not_a_letter(self):
        assert term_key("می\u200cخواهم") == term_key("میخواهم")

    def test_hebrew_niqqudand_geresh(self):
        assert term_key("שָׁלוֹם") == term_key("שלום")
        assert term_key("ג'דה") == term_key("ג'דה")

    def test_presentation_forms_fold(self):
        # Arabic presentation form of the same word (NFKC).
        assert term_key("ﺳﻴﺎﺳﻲ") == term_key("سياسي")

    def test_whitespace_runs_collapse(self):
        assert term_key("  political   stability ") == "political stability"


class TestCaseIsFoldedForTermsButNotForPlaces:
    """The one deliberate difference from place names, asserted on both sides."""

    def test_the_place_key_still_separates_case(self):
        assert match_key("Split") != match_key("split")

    def test_the_term_key_does_not(self):
        assert term_key("Political") == term_key("political") == "political"


class TestTheDefiniteArticleIsOptional:
    def test_a_query_with_the_article_finds_the_bare_name(self):
        assert term_key("سياسي") in key_variants("السياسي")

    def test_a_bare_query_also_finds_the_name_with_the_article(self):
        assert term_key("السياسي") in key_variants("سياسي")

    def test_the_first_variant_is_always_the_key_itself(self):
        assert key_variants("السياسي")[0] == term_key("السياسي")

    def test_variants_are_deduplicated(self):
        variants = key_variants("سياسي")
        assert len(variants) == len(set(variants))

    def test_a_bare_article_is_not_a_stem(self):
        # "الة" normalises to "اله" (teh marbuta folds to heh, per the shared
        # rules), so stripping the article would leave a single letter. The
        # only variant is therefore the one with another article added.
        assert term_key("الة") == "اله"
        assert key_variants("الة") == ("اله", ARABIC_ARTICLE + "اله")

    def test_an_empty_query_has_no_variants(self):
        assert key_variants("") == ()
        assert key_variants("   ") == ()


class TestTheScriptRuleMatchesTheMigration:
    """The same rule as the CHECK on ``term_names.script``."""

    @pytest.mark.parametrize("language,script", [
        ("ar", "Arab"), ("fa", "Arab"), ("he", "Hebr"), ("en", "Latn"), ("hr", "Latn"),
    ])
    def test_the_language_script_is_accepted(self, language, script):
        assert script_ok(language, script, "preferred")

    @pytest.mark.parametrize("language,script", [
        ("ar", "Latn"), ("he", "Arab"), ("en", "Hebr"), ("fa", "Hebr"),
    ])
    def test_a_foreign_script_is_refused(self, language, script):
        assert not script_ok(language, script, "preferred")

    def test_a_transliteration_may_be_latin_in_any_language(self):
        assert script_ok("ar", "Latn", "transliteration")
        assert not script_ok("ar", "Arab", "transliteration")

    def test_an_unknown_language_is_refused(self):
        assert not script_ok("xx", "Latn", "preferred")

    def test_an_empty_script_is_refused(self):
        assert not script_ok("ar", "", "preferred")
