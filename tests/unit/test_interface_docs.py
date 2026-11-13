"""Unit: the interface documentation cannot drift from the registry.

The productization map once contained three numbers for the same thing (57,
40 and 41 endpoints) because they were typed by hand at different times. The
fix is not discipline, it is generation: the reference table in
``docs/INTERFACE_REGISTRY.md`` is produced from the registry, and this test
fails when the two disagree.
"""

from __future__ import annotations

import pathlib

import pytest

from core.interfaces.docgen import BEGIN, END, reference_markdown, splice, summary_lines

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DOC = PROJECT_ROOT / "docs/INTERFACE_REGISTRY.md"


@pytest.fixture(scope="module")
def document():
    assert DOC.exists(), "the registry document is missing"
    return DOC.read_text()


class TestGeneratedBlock:
    def test_the_document_embeds_a_generated_block(self, document):
        assert BEGIN in document and END in document

    def test_the_block_matches_the_registry(self, document):
        embedded = document.split(BEGIN, 1)[1].split(END, 1)[0].strip()
        assert embedded == reference_markdown().strip(), (
            "docs/INTERFACE_REGISTRY.md is out of date; regenerate it with "
            "python3 -m core.interfaces.docgen docs/INTERFACE_REGISTRY.md")

    def test_regeneration_is_idempotent(self, document):
        assert splice(splice(document)) == splice(document)

    def test_splicing_preserves_the_prose(self, document):
        updated = splice(document)
        assert updated.startswith(document.split(BEGIN, 1)[0])
        assert updated.endswith(document.split(END, 1)[1])

    def test_splicing_refuses_a_document_without_markers(self):
        with pytest.raises(ValueError):
            splice("a document with no generated block")


class TestCountsAreGenerated:
    def test_summary_lines_come_from_the_registry(self):
        from core.interfaces import summary

        lines = "\n".join(summary_lines())
        data = summary()
        assert str(data["interfaces"]) in lines
        assert str(data["endpoints_owned"]) in lines

    def test_every_interface_appears_in_the_reference(self):
        from core.interfaces import REGISTRY

        reference = reference_markdown()
        for interface in REGISTRY:
            assert f"`{interface.interface_id}`" in reference, interface.interface_id

    def test_the_document_states_no_endpoint_count_of_its_own(self, document):
        """Counts outside the generated block must be labelled as past.

        A count that is not generated goes stale, which is how the same thing
        came to be described as 57, 40 and 41 endpoints in one document. A
        *historical* figure is fine as long as the reader is told that is what
        it is.
        """
        import re

        prose = document.split(BEGIN, 1)[0] + document.split(END, 1)[1]
        historical = ("historical", "at the time", "before the registry", "legacy")
        offenders = []
        for sentence in re.split(r"(?<=[.!?])\s+", prose):
            if not re.search(r"\b\d{2,3}\s+(?:page )?endpoints\b", sentence):
                continue
            if not any(word in sentence.lower() for word in historical):
                offenders.append(" ".join(sentence.split()))
        assert offenders == [], (
            "live endpoint counts belong in the generated block; label historical "
            "figures as historical:\n  " + "\n  ".join(offenders))


class TestOtherDocuments:
    """Documents that describe the registry must not restate its numbers."""

    def test_the_productization_map_points_at_the_registry(self):
        text = (PROJECT_ROOT / "docs/PRODUCTIZATION_MAP.md").read_text()
        # It may describe the gap it measured, but it must name the generated
        # sources rather than presenting itself as the source of truth.
        assert "core/interfaces" in text or "INTERFACE_REGISTRY.md" in text
        assert "legacy" in text.lower()

    def test_the_domain_model_exists(self):
        assert (PROJECT_ROOT / "docs/DOMAIN_MODEL.md").exists()

class TestRegistryEvidence:
    """`docs/REGISTRY_EVIDENCE.md`, part A, describes the registry as it is.

    The evidence the directive requires before the shell work begins has two
    halves: the declared product (this test) and the application it describes
    (asserted in the integration suite, which can build the application). Both
    are generated, so neither can drift.
    """

    DOC = PROJECT_ROOT / "docs/REGISTRY_EVIDENCE.md"

    @pytest.fixture(scope="class")
    def evidence(self):
        assert self.DOC.exists(), "the registry evidence document is missing"
        return self.DOC.read_text()

    def test_the_document_embeds_both_generated_blocks(self, evidence):
        from core.interfaces.evidence import (
            BEGIN_APPLICATION,
            BEGIN_REGISTRY,
            END_APPLICATION,
            END_REGISTRY,
        )

        for marker in (BEGIN_REGISTRY, END_REGISTRY, BEGIN_APPLICATION, END_APPLICATION):
            assert marker in evidence, marker

    def test_the_registry_half_matches_the_registry(self, evidence):
        from core.interfaces.evidence import (
            BEGIN_REGISTRY,
            END_REGISTRY,
            registry_block,
        )

        embedded = evidence.split(BEGIN_REGISTRY, 1)[1].split(END_REGISTRY, 1)[0].strip()
        assert embedded == registry_block().strip(), (
            "docs/REGISTRY_EVIDENCE.md is out of date; regenerate it with "
            "python3 -m core.interfaces.evidence --write docs/REGISTRY_EVIDENCE.md")

    def test_regeneration_is_idempotent(self, evidence):
        from core.interfaces.evidence import splice

        once = splice(evidence, self._block(), None)
        assert splice(once, self._block(), None) == once

    @staticmethod
    def _block():
        from core.interfaces.evidence import registry_block

        return registry_block()

    def test_every_required_evidence_item_is_present(self, evidence):
        """The ten items the directive names, each stated in the document."""
        for item in ("Registry inventory", "Endpoint coverage", "Aliases",
                     "Dependency validation", "Registry integrity",
                     "Rendered navigation"):
            assert item.lower() in evidence.lower(), item

    def test_the_coverage_claim_is_stated_as_zero_unmanaged(self, evidence):
        assert "Unmanaged user-facing endpoints**: **0**" in evidence

    def test_the_document_reports_what_remains_legacy(self, evidence):
        assert "INTERFACE_METADATA" in evidence
        assert "LEGACY_INTERFACE_IDS" in evidence


class TestEndpointInventory:
    """The committed inventory is the directive's deterministic snapshot."""

    INVENTORY = PROJECT_ROOT / "docs/endpoint_inventory.json"

    def test_the_inventory_is_committed_and_parses(self):
        import json

        assert self.INVENTORY.exists(), (
            "docs/endpoint_inventory.json is missing; it is regenerated from the "
            "application by the integration suite")
        records = json.loads(self.INVENTORY.read_text())
        assert records, "the inventory is empty"

    def test_every_record_names_an_owner_or_an_exception(self):
        import json

        from core.interfaces.inventory import EndpointClass

        records = json.loads(self.INVENTORY.read_text())
        interface_classes = {
            str(EndpointClass.USER_INTERFACE),
            str(EndpointClass.INTERNAL_PAGE),
            str(EndpointClass.REDIRECT),
        }
        unnamed = [
            r["endpoint"] for r in records
            if r["classification"] in interface_classes and not r["owned_by"]
        ]
        assert unnamed == [], f"user-facing endpoints with no owner: {unnamed}"

    def test_the_inventory_is_sorted_so_a_new_page_shows_up_as_a_diff(self):
        import json

        records = json.loads(self.INVENTORY.read_text())
        names = [r["endpoint"] for r in records]
        assert names == sorted(names)

    def test_every_record_carries_the_fields_the_directive_asks_for(self):
        import json

        records = json.loads(self.INVENTORY.read_text())
        for record in records:
            for field in ("endpoint", "rule", "methods", "blueprint",
                          "classification", "internal", "owned_by"):
                assert field in record, (record.get("endpoint"), field)
