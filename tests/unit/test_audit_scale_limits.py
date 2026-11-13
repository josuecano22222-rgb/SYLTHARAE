"""The harness must survive a file that is larger than its own bookkeeping.

Measured on `Spreadsheets/testRecordSizeExceeded.xlsx`: the reader extracted
10,000,085 characters and then the *audit* ran out of memory three times over -
building 249,990 unit dicts, tokenising a 10 MB oracle, and holding the
reader's content while doing both - and the record said `ERROR MemoryError`,
which reads as a reader failure. The reader was fine.

These tests pin the three bounds, because each one is a silent-corruption risk
if it is dropped: unbounded unit lists exhaust the process, unbounded oracle
tokenisation does too, and a truncated list that does not say it is truncated
misreports coverage.
"""
from tools.audit.archive_extraction import (
    ORACLE_SAMPLE_CHARS,
    UNITS_CAP,
    units_from_content,
)


def _pages(n, chars=40):
    return {"pages": [{"page_number": i + 1, "text": "x" * chars} for i in range(n)]}


def test_units_are_capped_but_the_total_is_kept():
    units = units_from_content(_pages(UNITS_CAP + 250), limit=UNITS_CAP)
    assert len(units) == UNITS_CAP
    assert units.total == UNITS_CAP + 250


def test_an_uncapped_call_still_returns_every_unit():
    units = units_from_content(_pages(5))
    assert len(units) == 5
    assert units.total == 5


def test_empty_units_are_still_evidence_of_absence():
    content = {"pages": [{"page_number": 1, "text": ""},
                         {"page_number": 2, "text": "text"}]}
    units = units_from_content(content)
    assert [u["loc"] for u in units] == ["page 1", "page 2"]
    assert units[0]["chars"] == 0


def test_the_oracle_sample_bound_is_documented_and_smaller_than_a_sheet():
    # Large enough to be representative, small enough that a Counter of its
    # tokens fits next to the extracted text.
    assert 100_000 <= ORACLE_SAMPLE_CHARS <= 10_000_000
