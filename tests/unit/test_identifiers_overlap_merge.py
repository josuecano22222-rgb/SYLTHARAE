"""The identifier overlap merge, without a database.

``_merge_overlaps`` keeps the longest candidate of every overlapping group,
breaking ties by identifier priority and then by start offset. The original
implementation scanned every kept interval for every candidate (O(n^2)); on a
1 MB document with ~41 000 candidates that dominated ingestion
(audit_report/evidence_scale/ingest_profile.json).

The replacement keeps the kept intervals sorted and looks only at the two that
could overlap a new candidate. These tests pin the result to the quadratic
rule - a reference implementation of it is kept here - on random inputs, and
check the merge no longer degrades quadratically.
"""

from __future__ import annotations

import random
import time

from core.detection.identifiers_intel import _merge_overlaps

KINDS = ("email", "url", "ipv6", "ipv4", "phone")


def _reference(cands):
    """The rule as first written: compare against every kept interval."""
    ranked = sorted(cands, key=lambda c: (-(c[1] - c[0]), -_priority(c[2]), c[0]))
    taken, kept = [], []
    for cand in ranked:
        start, end = cand[0], cand[1]
        if any(start < e and s < end for s, e in taken):
            continue
        taken.append((start, end))
        kept.append(cand)
    kept.sort(key=lambda c: (c[0], c[1], c[2], c[4]))
    return kept


def _priority(kind: str) -> int:
    return {"email": 50, "url": 45, "ipv6": 40, "ipv4": 35, "phone": 30}[kind]


def _random_candidates(rng, count, width):
    out = []
    for i in range(count):
        start = rng.randrange(width)
        end = start + rng.randrange(1, 40)
        kind = rng.choice(KINDS)
        surface = f"surface{i}"
        out.append((start, end, kind, surface, surface, "pattern"))
    return out


def test_matches_the_quadratic_rule_on_random_input():
    rng = random.Random(20261023)
    for trial in range(25):
        cands = _random_candidates(rng, rng.randrange(0, 400), rng.randrange(50, 4000))
        assert _merge_overlaps(cands) == _reference(cands), f"trial {trial}"


def test_identical_spans_and_ties_resolve_as_before():
    cands = [
        (100, 120, "email", "a@b.c", "a@b.c", "pattern"),
        (100, 120, "url", "http://x", "x", "pattern"),        # same span: priority decides
        (110, 115, "phone", "12345", "12345", "pattern"),     # inside the kept span: dropped
        (120, 140, "ipv4", "1.2.3.4", "1.2.3.4", "pattern"),  # touches only: kept
    ]
    assert _merge_overlaps(cands) == _reference(cands)
    kept = _merge_overlaps(cands)
    assert [c[2] for c in kept] == ["email", "ipv4"]


def test_merging_many_disjoint_candidates_is_not_quadratic():
    # 20 000 non-overlapping candidates: the quadratic rule compares each one
    # with every kept interval before it (~2e8 comparisons, minutes).
    cands = [(i * 50, i * 50 + 20, "email", f"e{i}@x.io", f"e{i}@x.io", "pattern")
             for i in range(20_000)]
    started = time.perf_counter()
    kept = _merge_overlaps(cands)
    elapsed = time.perf_counter() - started
    assert len(kept) == 20_000
    assert elapsed < 5.0, f"merge took {elapsed:.2f}s for 20 000 candidates"
