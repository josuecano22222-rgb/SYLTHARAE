"""Unit: keyword phrase matching must stay equivalent to the naive scan.

``ContentDBService.process_keywords_for_content`` and
``refresh_keyword_associations`` used to rescan the whole document for every
keyword (O(document x keywords)), which showed up as CPU saturation during
large ingests.  The replacement indexes positions by the pattern's first id;
these tests pin the result to a straightforward reference implementation so
the optimisation cannot silently change which files match a keyword.
"""

from __future__ import annotations

import pathlib
import random
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _keyword_occurrence_counts(sequence, patterns):
    """The matcher under test (imported lazily, like the other test modules)."""
    from database.services.contents_db_service import (
        _keyword_occurrence_counts as matcher,
    )
    return matcher(sequence, patterns)


def _naive_counts(sequence, patterns):
    """Reference implementation: scan every position for every pattern."""
    counts = {}
    for keyword_id, pattern in patterns.items():
        if not pattern:
            continue
        length = len(pattern)
        count = sum(
            1 for i in range(len(sequence) - length + 1)
            if sequence[i:i + length] == pattern
        )
        if count:
            counts[keyword_id] = count
    return counts


def test_single_word_and_phrase_counts():
    sequence = [1, 2, 3, 1, 2, 3, 9]
    patterns = {10: [1], 11: [2, 3], 12: [1, 2, 3], 13: [4]}
    assert _keyword_occurrence_counts(sequence, patterns) == _naive_counts(sequence, patterns)
    assert _keyword_occurrence_counts(sequence, patterns) == {10: 2, 11: 2, 12: 2}


def test_overlapping_occurrences_are_counted_like_before():
    sequence = [7, 7, 7, 7]
    patterns = {1: [7, 7]}
    # Three overlapping windows start at 0, 1 and 2.
    assert _keyword_occurrence_counts(sequence, patterns) == {1: 3}
    assert _keyword_occurrence_counts(sequence, patterns) == _naive_counts(sequence, patterns)


def test_sequence_with_gaps_and_empty_inputs():
    sequence = [5, None, None, 5, 6]
    patterns = {1: [5, 6], 2: [None, 5]}
    assert _keyword_occurrence_counts(sequence, patterns) == _naive_counts(sequence, patterns)
    assert _keyword_occurrence_counts([], {1: [5]}) == {}
    assert _keyword_occurrence_counts([1, 2], {}) == {}
    assert _keyword_occurrence_counts([1, 2], {1: []}) == {}


def test_randomised_equivalence():
    """Fuzz the matcher against the reference implementation."""
    rng = random.Random(0xC0FFEE)
    for _ in range(200):
        sequence = [rng.randint(0, 4) for _ in range(rng.randint(0, 40))]
        patterns = {
            keyword_id: [rng.randint(0, 4) for _ in range(rng.randint(1, 5))]
            for keyword_id in range(6)
        }
        assert _keyword_occurrence_counts(sequence, patterns) == _naive_counts(
            sequence, patterns
        ), (sequence, patterns)
