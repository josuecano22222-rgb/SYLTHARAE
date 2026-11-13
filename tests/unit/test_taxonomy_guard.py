"""Unit: the sync's own guards - a shrink limit and a fingerprint that ages.

Two things an automatic apply needs and did not have.

**A shrink limit.** The workbook is authoritative, so a truncated file, a
half-saved workbook or a file whose sheets were emptied is authoritative too.
Applying it would remove most of the taxonomy, and the only thing standing in the
way was the operator reading the log afterwards. The guard compares what a plan
would *remove* with what is stored and refuses above a fraction that is
configurable - refused, not confirmed, because a process starting on boot has
nobody to ask.

**A fingerprint that ages.** The stored fingerprint was a SHA-256 of the
workbook's bytes. Change the *rules* - how a worksheet becomes terms and
keywords - and the same workbook means something different, but the fingerprint
still matched and the fast path skipped the work. It now covers the workbook, the
rules that interpret it, and the version of the sync logic.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.importing import taxonomy_guard as guard  # noqa: E402
from services.importing import taxonomy_sync as sync  # noqa: E402


class FakePlan:
    """The four fields the guard reads, and nothing else."""

    def __init__(self, *, unlink=0, delete_keywords=0, missing=0,
                 delete_categories=0):
        self.unlink_terms = [("cat", f"t{i}") for i in range(unlink)]
        self.delete_keywords = [{"phrase": f"p{i}"} for i in range(delete_keywords)]
        self.missing_categories = [f"m{i}" for i in range(missing)]
        self.delete_categories = [f"d{i}" for i in range(delete_categories)]


def test_a_plan_that_removes_nothing_is_allowed():
    verdict = guard.check(FakePlan(), {"words_categorys": 100, "keywords": 10})
    assert verdict["ok"] is True
    assert verdict["removed"] == 0


def test_a_plan_that_removes_everything_is_refused():
    plan = FakePlan(unlink=100, delete_keywords=10)
    verdict = guard.check(plan, {"words_categorys": 100, "keywords": 10})
    assert verdict["ok"] is False
    assert "nothing was written" in verdict["reason"].lower()


def test_the_limit_is_a_fraction_and_its_default_is_quarter():
    plan = FakePlan(unlink=26)
    verdict = guard.check(plan, {"words_categorys": 100, "keywords": 0})
    assert verdict["ok"] is False
    assert verdict["limit"] == guard.DEFAULT_MAX_SHRINK


def test_a_plan_just_under_the_limit_is_allowed():
    plan = FakePlan(unlink=25)
    verdict = guard.check(plan, {"words_categorys": 100, "keywords": 0})
    assert verdict["ok"] is True


def test_the_limit_comes_from_the_environment_and_is_validated(monkeypatch):
    monkeypatch.setenv(guard.LIMIT_ENV, "0.5")
    assert guard.max_shrink() == 0.5
    monkeypatch.setenv(guard.LIMIT_ENV, "1")
    assert guard.max_shrink() == 1.0
    monkeypatch.setenv(guard.LIMIT_ENV, "nonsense")
    assert guard.max_shrink() == guard.DEFAULT_MAX_SHRINK
    monkeypatch.setenv(guard.LIMIT_ENV, "-1")
    assert guard.max_shrink() == guard.DEFAULT_MAX_SHRINK


def test_an_empty_database_is_never_a_shrink():
    """The first sync into an empty database removes nothing, whatever the plan."""
    verdict = guard.check(FakePlan(unlink=5), {"words_categorys": 0, "keywords": 0})
    assert verdict["ok"] is True


def test_a_limit_of_one_allows_a_total_removal(monkeypatch):
    monkeypatch.setenv(guard.LIMIT_ENV, "1")
    verdict = guard.check(FakePlan(unlink=100), {"words_categorys": 100, "keywords": 0})
    assert verdict["ok"] is True


def test_the_verdict_names_the_numbers_so_a_log_line_is_enough_to_act():
    verdict = guard.check(FakePlan(unlink=80), {"words_categorys": 100, "keywords": 0})
    assert verdict["removed"] == 80
    assert verdict["stored"] == 100
    assert "80" in verdict["reason"] and "100" in verdict["reason"]


def test_the_fingerprint_covers_the_workbook_bytes(tmp_path):
    book = tmp_path / "domain_data.xlsx"
    book.write_bytes(b"first")
    first = sync.fingerprint(str(book))
    book.write_bytes(b"second")
    assert sync.fingerprint(str(book)) != first


def test_the_fingerprint_covers_the_rules(monkeypatch, tmp_path):
    """Editing a rule changes what the same workbook means."""
    book = tmp_path / "domain_data.xlsx"
    book.write_bytes(b"same bytes")
    first = sync.fingerprint(str(book))
    monkeypatch.setattr(sync, "_rules_digest", lambda: "a-different-rules-digest")
    assert sync.fingerprint(str(book)) != first


def test_the_fingerprint_covers_the_sync_logic_version(monkeypatch, tmp_path):
    book = tmp_path / "domain_data.xlsx"
    book.write_bytes(b"same bytes")
    first = sync.fingerprint(str(book))
    monkeypatch.setattr(sync, "SYNC_LOGIC_VERSION", "999")
    assert sync.fingerprint(str(book)) != first


def test_the_fingerprint_is_still_a_hex_digest(tmp_path):
    book = tmp_path / "domain_data.xlsx"
    book.write_bytes(b"bytes")
    value = sync.fingerprint(str(book))
    assert len(value) == 64 and re.fullmatch(r"[0-9a-f]{64}", value)


def test_the_sync_logic_version_is_declared_and_bumped_by_hand():
    """A version string, not a timestamp: it must change only on purpose."""
    assert isinstance(sync.SYNC_LOGIC_VERSION, str)
    assert sync.SYNC_LOGIC_VERSION.count(".") >= 1


def test_a_small_database_may_lose_its_whole_taxonomy_without_a_decision():
    """One stored row removed is 100%: a fraction alone cannot judge that.

    The guard exists to stop a large curated taxonomy disappearing, not to make
    every edit to a new deployment impossible. Below the absolute floor the
    fraction is not consulted.
    """
    verdict = guard.check(FakePlan(unlink=1), {"words_categorys": 1, "keywords": 0})
    assert verdict["ok"] is True
    assert verdict["allowance"] == guard.MIN_ABSOLUTE


def test_the_floor_is_an_absolute_number_of_rows_not_a_percentage():
    at_floor = guard.check(FakePlan(unlink=guard.MIN_ABSOLUTE),
                           {"words_categorys": guard.MIN_ABSOLUTE + 50,
                            "keywords": 0})
    assert at_floor["ok"] is True
    over = guard.check(FakePlan(unlink=guard.MIN_ABSOLUTE + 1),
                       {"words_categorys": guard.MIN_ABSOLUTE + 50, "keywords": 0})
    assert over["ok"] is False, "the floor must be a limit, not an exemption"


def test_a_sync_that_replaces_a_small_database_with_a_big_workbook_is_allowed():
    """Growth is not a shrink, however many seeded rows it removes.

    This is the case the integration suite caught: a first sync against a small
    hand-seeded database removes every seeded membership and adds thousands.
    Judging it by removals alone refused the run that grows the taxonomy.
    """
    verdict = guard.check(FakePlan(unlink=42), {"words_categorys": 42, "keywords": 0})
    assert verdict["ok"] is False, "with no additions at all this is a wipe"

    class GrowingPlan(FakePlan):
        def __init__(self):
            super().__init__(unlink=42)
            self.link_terms = [("cat", f"t{i}") for i in range(3000)]
            self.create_keywords = [("cat", f"p{i}") for i in range(8000)]

    verdict = guard.check(GrowingPlan(), {"words_categorys": 42, "keywords": 0})
    assert verdict["ok"] is True

    # The numbers say what happened: not "42 removed", but "42 -> 11042".
    assert verdict["removed"] == 42
    assert verdict["added"] == 11000
    assert verdict["after"] == 11000
    assert "42 to 11000" in verdict["reason"]


def test_a_large_database_is_judged_by_the_fraction_not_the_floor():
    verdict = guard.check(FakePlan(unlink=400), {"words_categorys": 1000, "keywords": 0})
    assert verdict["ok"] is False
    assert verdict["allowance"] == 250
