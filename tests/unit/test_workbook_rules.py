"""The category workbook's four rules, tested on workbooks built here.

The rules live in ``apps.importing.validators.workbook_rules`` and are shared by
the in-app importer, the sync tool and these tests. Each case below is a
workbook that an operator could plausibly produce, so the failure messages are
tested as well as the verdict: a validator that says "invalid" without saying
which cell is worthless.
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from apps.importing.validators import workbook_rules as rules  # noqa: E402


def build(path: Path, sheets: dict, *, owners=None, headers=True) -> Path:
    """Write a workbook from ``{sheet name: {"terms": [...], "keywords": [...]}}``."""
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, content in sheets.items():
        sheet = book.create_sheet(name)
        if headers:
            sheet.cell(row=1, column=1, value=rules.TERM_HEADER)
            sheet.cell(row=1, column=2, value=rules.KEYWORD_HEADER)
        offset = 2 if headers else 1
        for row, term in enumerate(content.get("terms", []), start=offset):
            sheet.cell(row=row, column=1, value=term)
        for row, keyword in enumerate(content.get("keywords", []), start=offset):
            sheet.cell(row=row, column=2, value=keyword)
    if owners:
        sheet = book.create_sheet(rules.OWNERS_SHEET)
        sheet.cell(row=1, column=1, value="Phrase")
        sheet.cell(row=1, column=2, value="Category that owns it")
        for row, (phrase, owner) in enumerate(owners.items(), start=2):
            sheet.cell(row=row, column=1, value=phrase)
            sheet.cell(row=row, column=2, value=owner)
    book.save(path)
    return path


def codes(workbook) -> list:
    return [violation.code for violation in workbook.violations]


# ---------------------------------------------------------------------------
# the happy path, so the failures below mean something
# ---------------------------------------------------------------------------
def test_a_well_formed_workbook_has_no_findings(tmp_path):
    path = build(tmp_path / "ok.xlsx", {
        "Networks": {"terms": ["Server", "Router"], "keywords": ["Load balancing"]},
        "Law": {"terms": ["Statute"], "keywords": ["Due process"]},
    })
    workbook = rules.read(str(path))
    assert workbook.ok
    assert workbook.layout == "two-column"
    assert [category.name for category in workbook.categories] == ["Networks", "Law"]
    assert workbook.categories[0].term_texts == ["Server", "Router"]
    assert workbook.categories[0].keyword_texts == ["Load balancing"]
    assert workbook.total_terms() == 3 and workbook.total_keywords() == 2


def test_the_readme_and_index_sheets_are_not_categories(tmp_path):
    path = build(tmp_path / "ok.xlsx", {"Networks": {"terms": ["Server"]}})
    book = openpyxl.load_workbook(path)
    book.create_sheet(rules.README_SHEET).cell(row=1, column=1, value="docs")
    book.create_sheet(rules.INDEX_SHEET).cell(row=1, column=1, value="index")
    book.save(path)
    workbook = rules.read(str(path))
    assert [category.name for category in workbook.categories] == ["Networks"]


# ---------------------------------------------------------------------------
# R2: a term is one word
# ---------------------------------------------------------------------------
def test_a_multi_word_term_is_refused_and_names_its_cell(tmp_path):
    path = build(tmp_path / "bad.xlsx", {
        "Networks": {"terms": ["Server", "Load balancing"], "keywords": []}})
    workbook = rules.read(str(path))
    assert not workbook.ok
    finding = next(v for v in workbook.errors if v.code == "TERM_HAS_WHITESPACE")
    assert (finding.sheet, finding.cell, finding.value) == ("Networks", "A3", "Load balancing")
    assert "Keyword" in finding.message, "the message must say where it belongs"


def test_a_tab_separated_term_counts_as_two_words(tmp_path):
    path = build(tmp_path / "bad.xlsx", {"Networks": {"terms": ["Load\tbalancing"]}})
    workbook = rules.read(str(path))
    assert "TERM_HAS_WHITESPACE" in codes(workbook)


def test_a_term_longer_than_the_reader_limit_is_refused(tmp_path):
    path = build(tmp_path / "bad.xlsx", {"Networks": {"terms": ["x" * 101]}})
    workbook = rules.read(str(path))
    assert "TERM_TOO_LONG" in codes(workbook)


# ---------------------------------------------------------------------------
# R3: a keyword is two or more words
# ---------------------------------------------------------------------------
def test_a_single_word_keyword_is_refused_and_names_its_cell(tmp_path):
    path = build(tmp_path / "bad.xlsx", {
        "Networks": {"terms": [], "keywords": ["Server"]}})
    workbook = rules.read(str(path))
    finding = next(v for v in workbook.errors if v.code == "KEYWORD_SINGLE_WORD")
    assert (finding.sheet, finding.cell, finding.value) == ("Networks", "B2", "Server")
    assert "Term" in finding.message


def test_a_two_word_keyword_is_accepted(tmp_path):
    path = build(tmp_path / "ok.xlsx", {"Networks": {"keywords": ["Load balancing"]}})
    assert rules.read(str(path)).ok


# ---------------------------------------------------------------------------
# R4: duplicates and contradictions
# ---------------------------------------------------------------------------
def test_a_repeated_term_is_refused_and_points_at_the_first(tmp_path):
    path = build(tmp_path / "bad.xlsx", {"Networks": {"terms": ["Server", "Server"]}})
    workbook = rules.read(str(path))
    finding = next(v for v in workbook.errors if v.code == "DUPLICATE_TERM")
    assert finding.cell == "A3" and "A2" in finding.message


def test_case_does_not_make_a_second_term(tmp_path):
    path = build(tmp_path / "bad.xlsx", {"Networks": {"terms": ["Server", "server"]}})
    assert "DUPLICATE_TERM" in codes(rules.read(str(path)))


def test_import_mode_preserves_repeats_without_relaxing_other_rules(tmp_path):
    path = build(tmp_path / "repeats.xlsx", {
        "Networks": {
            "terms": ["Server", "server", "Load balancing"],
            "keywords": ["Load balancing", "Load balancing"],
        },
        "Cloud": {"keywords": ["Load balancing"]},
    })

    strict = rules.read(str(path))
    imported = rules.read(str(path), allow_duplicates=True)

    assert "DUPLICATE_TERM" in codes(strict)
    assert "DUPLICATE_KEYWORD" in codes(strict)
    assert not imported.ok
    assert "TERM_HAS_WHITESPACE" in codes(imported)
    assert "DUPLICATE_TERM" not in codes(imported)
    assert "DUPLICATE_KEYWORD" not in codes(imported)
    assert imported.category("Networks").term_texts == [
        "Server", "server", "Load balancing"]
    assert imported.category("Networks").keyword_texts == [
        "Load balancing", "Load balancing"]
    assert imported.shared_keywords() == {
        "load balancing": ["Networks", "Cloud"]}


def test_the_same_text_as_term_and_keyword_is_refused(tmp_path):
    path = build(tmp_path / "bad.xlsx", {"Networks": {"terms": ["Server"],
                                                      "keywords": ["server"]}})
    assert "TERM_ALSO_KEYWORD" in codes(rules.read(str(path)))


def test_duplicates_are_per_worksheet_not_global(tmp_path):
    path = build(tmp_path / "ok.xlsx", {
        "Networks": {"terms": ["Server"]},
        "Hardware": {"terms": ["Server"]},
    })
    assert rules.read(str(path)).ok


# ---------------------------------------------------------------------------
# R1: the worksheet name is the category name
# ---------------------------------------------------------------------------
def test_two_sheets_claiming_one_category_name_are_refused():
    """openpyxl and Excel both prevent a case-only duplicate sheet name.

    So the rule is tested on the model rather than through a file: if a workbook
    ever reaches the reader with two sheets that fold together (a converted
    file, a script that writes the XML directly), it must be refused instead of
    silently keeping whichever came last.
    """
    workbook = rules.Workbook(path="memory")
    workbook.categories.append(rules.Category(name="Networks"))
    workbook.categories.append(rules.Category(name="networks"))
    rules.validate_structure(workbook)
    assert "SHEET_NAME" in [v.code for v in workbook.violations]
    assert any("same category name" in v.message for v in workbook.violations)


def test_a_sheet_name_starting_with_underscore_is_not_a_category(tmp_path):
    path = build(tmp_path / "ok.xlsx", {"Networks": {"terms": ["A"]},
                                        "_notes": {"terms": ["B"]}})
    workbook = rules.read(str(path))
    assert [c.name for c in workbook.categories] == ["Networks"]


def test_an_empty_worksheet_is_refused(tmp_path):
    path = build(tmp_path / "bad.xlsx", {"Networks": {}})
    assert "SHEET_EMPTY" in codes(rules.read(str(path)))


# ---------------------------------------------------------------------------
# headers
# ---------------------------------------------------------------------------
def test_a_missing_header_is_refused(tmp_path):
    path = build(tmp_path / "bad.xlsx", {"Networks": {"terms": ["A"]}}, headers=False)
    workbook = rules.read(str(path))
    # headers=False makes the file legacy-shaped, so the reader infers; the
    # header check only applies once a header exists
    assert workbook.layout == "legacy-single-column"
    assert workbook.ok


def test_a_renamed_header_is_refused(tmp_path):
    path = build(tmp_path / "bad.xlsx", {"Networks": {"terms": ["A"]}})
    book = openpyxl.load_workbook(path)
    book["Networks"].cell(row=1, column=1, value="Words")
    book.save(path)
    assert "MISSING_HEADER" in codes(rules.read(str(path)))


def test_a_number_in_a_cell_is_refused(tmp_path):
    path = build(tmp_path / "bad.xlsx", {"Networks": {"terms": ["A"]}})
    book = openpyxl.load_workbook(path)
    book["Networks"].cell(row=2, column=1, value=42)
    book.save(path)
    finding = next(v for v in rules.read(str(path)).errors
                   if v.code == "NON_TEXT_CELL")
    assert finding.cell == "A2"


def test_a_header_repeated_in_the_data_is_refused(tmp_path):
    path = build(tmp_path / "bad.xlsx", {"Networks": {"terms": ["Term"]}})
    assert "HEADER_IN_DATA" in codes(rules.read(str(path)))


# ---------------------------------------------------------------------------
# the legacy single-column layout is still readable (and inferred)
# ---------------------------------------------------------------------------
def test_the_legacy_layout_is_read_by_shape(tmp_path):
    path = build(tmp_path / "legacy.xlsx", {
        "Networks": {"terms": ["Server", "Load balancing"], "keywords": []},
    }, headers=False)
    workbook = rules.read(str(path))
    assert workbook.layout == "legacy-single-column"
    assert workbook.categories[0].term_texts == ["Server"]
    assert workbook.categories[0].keyword_texts == ["Load balancing"]
    assert workbook.ok


# ---------------------------------------------------------------------------
# whitespace normalisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("  Load   balancing \t", "Load balancing"),
    ("Load\u00a0balancing", "Load balancing"),        # non-breaking space
    ("\n Server \n", "Server"),
    (None, ""),
    (123, "123"),
])
def test_cells_are_normalised(raw, expected):
    assert rules.normalise(raw) == expected


# ---------------------------------------------------------------------------
# keywords the schema can only store once
# ---------------------------------------------------------------------------
def test_a_phrase_in_two_sheets_becomes_one_decision(tmp_path):
    path = build(tmp_path / "shared.xlsx", {
        "Networks": {"keywords": ["Load balancing"]},
        "Cloud": {"keywords": ["Load balancing"]},
    })
    workbook = rules.read(str(path))
    assert workbook.ok, "shared phrases are a decision, not an error"
    assert [v.code for v in workbook.decisions] == ["KEYWORD_SHARED"]
    assert workbook.decisions[0].sheet == "Networks", "first sheet in order owns it"
    assert "Cloud" in workbook.decisions[0].message


def test_the_owners_sheet_moves_ownership(tmp_path):
    path = build(tmp_path / "shared.xlsx", {
        "Networks": {"keywords": ["Load balancing"]},
        "Cloud": {"keywords": ["Load balancing"]},
    }, owners={"Load balancing": "Cloud"})
    workbook = rules.read(str(path))
    assert workbook.ok
    assert workbook.decisions[0].sheet == "Cloud"
    assert workbook.owner_of("load balancing") == "Cloud"


def test_an_owner_that_is_not_a_worksheet_is_refused(tmp_path):
    path = build(tmp_path / "shared.xlsx", {
        "Networks": {"keywords": ["Load balancing"]},
        "Cloud": {"keywords": ["Load balancing"]},
    }, owners={"Load balancing": "Nowhere"})
    assert "OWNER_UNKNOWN" in codes(rules.read(str(path)))


def test_an_owner_entry_for_an_unshared_phrase_is_refused(tmp_path):
    path = build(tmp_path / "x.xlsx", {"Networks": {"keywords": ["Load balancing"]}},
                 owners={"Load balancing": "Networks"})
    # listed once, so an owner table entry has nothing to decide
    assert "OWNER_UNKNOWN" in codes(rules.read(str(path)))


# ---------------------------------------------------------------------------
# desired state
# ---------------------------------------------------------------------------
def test_desired_state_assigns_each_phrase_once(tmp_path):
    path = build(tmp_path / "shared.xlsx", {
        "Networks": {"terms": ["Server"], "keywords": ["Load balancing"]},
        "Cloud": {"keywords": ["Load balancing", "Elastic compute"]},
    })
    state = rules.desired_state(rules.read(str(path)))
    assert list(state.categories) == ["Networks", "Cloud"]
    assert state.all_terms() == ["Server"]
    assert state.keyword_owner["load balancing"] == "Networks"
    assert state.keyword_owner["elastic compute"] == "Cloud"
    assert set(state.shared) == {"load balancing"}


def test_a_shared_phrase_is_stored_under_exactly_one_category(tmp_path):
    path = build(tmp_path / "shared.xlsx", {
        "A": {"keywords": ["One two"]},
        "B": {"keywords": ["One two"]},
    })
    state = rules.desired_state(rules.read(str(path)))
    assert sum(1 for owner in state.keyword_owner.values() if owner == "A") == 1
    assert not any(owner == "B" for owner in state.keyword_owner.values())


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------
def test_write_then_read_round_trips(tmp_path):
    source = build(tmp_path / "source.xlsx", {
        "Networks": {"terms": ["Server"], "keywords": ["Load balancing"]},
        "Cloud": {"keywords": ["Load balancing"]},
    })
    workbook = rules.read(str(source))
    target = tmp_path / "written.xlsx"
    rules.write(str(target), workbook)
    again = rules.read(str(target))
    assert again.ok
    assert [c.name for c in again.categories] == [c.name for c in workbook.categories]
    assert again.categories[0].term_texts == ["Server"]
    assert again.categories[0].keyword_texts == ["Load balancing"]
    # Writing materialises the default ownership of a shared phrase into the
    # owner sheet, so the decision is visible in the file the operator edits.
    assert again.owners == {"load balancing": "Networks"}
