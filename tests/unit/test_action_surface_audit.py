"""The action surface audit: that it measures, and that it keeps measuring.

The audit is only worth having if it cannot drift from the product. Three
things are protected here:

* **The document is generated.** The block between the markers must equal what
  the module produces now, so a count cannot be typed by hand and a gap cannot
  be quietly left in after it was closed.
* **The gaps are anchored.** Every "cannot say" row names code that exists, and
  the places it names are registry interfaces, declared screens, or part of its
  own evidence - so the list of things the model cannot express cannot turn
  into a list of opinions.
* **The two measurements agree.** The component library counts action bars by
  what the markup looks like; this audit counts them by what they are. The
  numbers are reconciled here, because two measurements of the same bars that
  disagree are worse than one.
"""

from __future__ import annotations

import pathlib

import pytest

from core.experience import declarations
from core.experience.action_audit import (
    BEGIN,
    END,
    GAPS,
    SCREEN_TEMPLATES,
    counts,
    declared_actions,
    library_toolbar,
    reference_markdown,
    surfaces,
    undescribed_interfaces,
)
from core.experience.model import ACTION_SCOPES
from core.interfaces import REGISTRY

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = PROJECT_ROOT / "docs/ACTION_SURFACE_AUDIT.md"

INTERFACE_IDS = {interface.interface_id for interface in REGISTRY}


def _generated_block() -> str:
    document = DOC.read_text(encoding="utf-8")
    assert BEGIN in document and END in document, "the document lost its markers"
    return document.split(BEGIN, 1)[1].split(END, 1)[0]


class TestTheDocumentIsGenerated:
    def test_the_document_is_what_the_product_now_says(self):
        assert _generated_block().strip() == reference_markdown().strip()

    def test_the_document_explains_itself_without_a_count_in_prose(self):
        head, _ = DOC.read_text(encoding="utf-8").split(BEGIN, 1)
        assert "python3 -m core.experience.action_audit" in head
        assert "not the Action Registry" in head


class TestTheGapsAreAnchored:
    @pytest.mark.parametrize("gap", GAPS, ids=lambda gap: str(gap["what"]))
    def test_every_gap_names_code_that_exists(self, gap):
        assert set(gap) == {"what", "where", "cannot_say", "evidence"}
        for relative in gap["evidence"]:
            assert (PROJECT_ROOT / relative).exists(), relative
        assert str(gap["cannot_say"]).strip()

    @pytest.mark.parametrize("gap", GAPS, ids=lambda gap: str(gap["what"]))
    def test_every_place_a_gap_names_is_a_screen_or_its_own_evidence(self, gap):
        """A gap may point at interfaces, declared screens, or its evidence."""
        for token in str(gap["where"]).split(", "):
            known = (token in INTERFACE_IDS or token in SCREEN_TEMPLATES
                     or token in {"every registered action"}
                     or any(token in relative.lower()
                            for relative in gap["evidence"]))
            assert known, f"{gap['what']}: {token!r} names nothing"

    def test_the_list_is_not_empty_and_says_something_specific(self):
        assert len(GAPS) >= 5
        for gap in GAPS:
            assert len(str(gap["cannot_say"])) > 80, gap["what"]


class TestTheNumbersAreTheProduct:
    def test_the_registered_actions_are_the_catalog(self):
        from core.experience.action_registry import registered

        rows = declared_actions()
        assert len(rows) == len(registered())
        assert len(rows) == counts()["registered_actions"]
        assert set(counts()["by_scope"]) == set(ACTION_SCOPES)
        for scope in ACTION_SCOPES:
            assert counts()["by_scope"][scope] == sum(
                1 for row in rows if row["scope"] == scope)

    def test_an_action_reports_the_component_that_renders_it(self):
        components = {row["component"] for row in declared_actions()}
        assert "ActionToolbar" in components
        assert "record row" in components
        for row in declared_actions():
            assert row["component"] in {
                "ActionToolbar", "record row", "record + toolbar",
                "hand-written bar", "no described screen", "page"}

    def test_the_screens_nobody_described_are_counted_not_implied(self):
        assert counts()["undescribed_interfaces"] == len(undescribed_interfaces())
        assert counts()["described_interfaces"] == len(declarations.DECLARED_SCREENS)
        assert set(SCREEN_TEMPLATES) == set(declarations.DECLARED_SCREENS)

    def test_an_action_with_no_operation_is_reported_by_name(self):
        from core.experience.action_registry import without_operation

        missing = counts()["without_execution_names"]
        for item in without_operation():
            assert item.action_id in missing
        assert counts()["without_execution"] == len(without_operation())

    def test_the_two_reports_reconcile(self):
        """The audit's bars and the component library's bars are the same bars."""
        scanned = surfaces()
        library = library_toolbar()
        assert library["standardized"] == len(scanned["toolbar_component"])
        assert library["hand_written"] == (len(scanned["hand_written_bar"])
                                           + len(scanned["viewer_controls"]))

    def test_every_scan_matches_something(self):
        for key, files in surfaces().items():
            assert files, f"the {key} scan matches nothing and measures nothing"
