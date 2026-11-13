"""The accepted-span set temporal detection uses to drop overlapping matches.

``temporal_intel.detect`` keeps the longest candidate of every overlapping
group and then drops orientation cues that fall inside an accepted date span.
It used to test each candidate against every span already accepted, which is
quadratic: the M-3 profile counts 106.7 million generator steps on a 2 MB
document (audit_report/evidence_scale/ingest_profile_before.json).

``_Spans`` keeps accepted spans sorted and bisects. These tests pin its answers
to the quadratic rule on random input and check it does not degrade.
"""

from __future__ import annotations

import random
import time

from core.detection.temporal_intel import _Spans


class _Reference:
    """The rule as first written: compare against every accepted span."""

    def __init__(self):
        self.taken = []

    def overlaps(self, start, end):
        return any(start < e and s < end for s, e in self.taken)

    def add(self, start, end):
        self.taken.append((start, end))


def test_answers_match_the_quadratic_rule_on_random_spans():
    rng = random.Random(20261024)
    for trial in range(50):
        spans, reference, subject = [], _Reference(), _Spans()
        for _ in range(rng.randrange(0, 300)):
            start = rng.randrange(2000)
            end = start + rng.randrange(1, 60)
            got, want = subject.overlaps(start, end), reference.overlaps(start, end)
            assert got == want, f"trial {trial} span {(start, end)}"
            if not got:
                subject.add(start, end)
                reference.add(start, end)
        assert sorted(subject._starts) == subject._starts
        assert sorted(reference.taken) == sorted(zip(subject._starts, subject._ends))


def test_touching_spans_do_not_overlap():
    spans = _Spans()
    spans.add(10, 20)
    assert spans.overlaps(0, 10) is False      # ends where the kept span starts
    assert spans.overlaps(20, 30) is False     # starts where it ends
    assert spans.overlaps(19, 30) is True
    assert spans.overlaps(0, 11) is True


def test_many_accepted_spans_stay_fast():
    # 20 000 accepted spans, each probed once: the quadratic rule compares
    # every probe with every accepted span (~2e8 comparisons).
    spans = _Spans()
    for i in range(20_000):
        assert spans.overlaps(i * 10, i * 10 + 5) is False
        spans.add(i * 10, i * 10 + 5)
    started = time.perf_counter()
    for i in range(0, 200_000, 10):
        spans.overlaps(i + 1, i + 4)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"20 000 probes against 20 000 spans took {elapsed:.2f}s"
