"""Unit: the status answer says what the counts mean.

A number with no sentence beside it is not a fact an operator can decide about.
"3" in a JSON field could be three pending creations or three memberships that
will never be stored; the difference is the whole of section D of this round.

These tests pin the sentences, not the prose: the shape is a list of strings,
each of which names the count, the reason and the way to act.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.importing.taxonomy_sync import summarise  # noqa: E402


def test_an_in_sync_plan_says_so():
    assert summarise({}) == ["The database matches the workbook."]


def test_a_not_stored_membership_is_stated_as_a_standing_fact():
    plan = {"not_stored_shared": [
        {"sheet": "Beta", "phrase": "gamma delta", "owner": "Alpha"}]}
    sentence = " ".join(summarise(plan))
    assert "1 phrase membership(s)" in sentence
    assert "not stored" in sentence
    assert "unique" in sentence
    assert "Alpha" in sentence
    assert "_Keyword Owners" in sentence


def test_a_missing_category_says_it_is_reported_and_names_the_confirm_path():
    sentence = " ".join(summarise({"missing_categories": ["Gone"]}))
    assert "1 stored category is not in the workbook" in sentence
    assert "not deleted" in sentence
    assert "prune_categories" in sentence


def test_orphan_words_name_their_confirm_path_too():
    sentence = " ".join(summarise({"orphan_words": 4}))
    assert "4 word row(s)" in sentence
    assert "prune_words" in sentence


def test_an_orphan_count_is_accepted_as_a_number_or_a_list():
    assert summarise({"orphan_words": 2}) == summarise({"orphan_words": ["a", "b"]})


def test_stale_phrases_are_reported_as_stale_not_as_removals():
    sentence = " ".join(summarise({"stale_keywords": [{"category": "A", "phrase": "b c"}]}))
    assert "stale" in sentence.lower()
    assert "deleted" not in sentence.lower()


def test_every_sentence_is_a_plain_string_so_the_payload_stays_serialisable():
    plan = {"not_stored_shared": [{"owner": "A", "sheet": "B", "phrase": "x"}],
            "missing_categories": ["C"], "orphan_words": 1,
            "stale_keywords": [{"category": "D", "phrase": "e f"}]}
    lines = summarise(plan)
    assert all(isinstance(line, str) and line.strip() for line in lines)
    assert len(lines) == 4


def test_the_drift_report_names_the_orphan_rows_not_only_their_count():
    """A removal nobody can see is a removal nobody can confirm."""
    from services.importing.taxonomy_sync import SAMPLE_ROWS, Plan

    plan = Plan(orphan_words=[f"word{i}" for i in range(SAMPLE_ROWS + 5)]).as_dict()
    assert plan["orphan_words"] == SAMPLE_ROWS + 5
    assert len(plan["orphan_words_sample"]) == SAMPLE_ROWS
    assert plan["orphan_words_sample"][0] == "word0"


def test_a_short_orphan_list_is_reported_whole():
    from services.importing.taxonomy_sync import Plan

    plan = Plan(orphan_words=["alpha", "beta"]).as_dict()
    assert plan["orphan_words_sample"] == ["alpha", "beta"]


def test_the_plan_carries_no_removal_without_a_confirm_path():
    """Every removal key the plan exposes is named in a sentence with its flag."""
    from services.importing.taxonomy_sync import Plan

    plan = Plan(orphan_words=["alpha"], missing_categories=["Gone"],
                delete_categories=["Gone"]).as_dict()
    text = " ".join(summarise(plan))
    assert "prune_words" in text and "prune_categories" in text
