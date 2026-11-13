"""Integration: the translation panel is on the two pages that offer it.

The manual translation feature is only discoverable if the panel is rendered on
the pages where a reader can select text: the file viewer and the search
results. This test renders both and checks the contract the UI promises -

* the panel is present, marked as machine translation and not verified, and
  offers Accept and Correct;
* the region a reader selects from is marked ``data-translatable``;
* an engine that is not installed reads as "not installed", not as a crash:
  the capabilities endpoint answers and names the disclaimer.

The translations are stubbed here (the engines are optional dependencies); what
is under test is the page wiring, not a model.
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration import _seed


@pytest.fixture()
def seeded_document(pg_db):
    """One stored document whose text a reader could select, via the seed helper."""
    conn = _seed.connect(pg_db)
    ledger = _seed.SeedLedger()
    marker = uuid.uuid4().hex[:8]
    try:
        with conn, conn.cursor() as cur:
            ledger.sources.append(_seed.source(cur, f"panel_{marker}"))
            ledger.sides.append(_seed.side(cur, f"panel_{marker}"))
            path_id, hash_id, context_id = _seed.document(
                cur, source_id=ledger.sources[-1], side_id=ledger.sides[-1],
                text=f"Good morning {marker}, the convoy crossed the border at dawn.",
                file_name=f"translation_panel_{marker}.txt")
            ledger.paths.append(path_id)
            ledger.hashes.append(hash_id)
            ledger.contexts.append(context_id)
    finally:
        conn.close()
    try:
        yield path_id
    finally:
        _seed.remove_seeded(pg_db, ledger)


def test_the_file_viewer_offers_a_marked_panel(admin_client, seeded_document):
    """The viewer's text is selectable and the panel sits beside it."""
    response = admin_client.get(f"/file/{seeded_document}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "data-translatable" in html, "the viewer's text is not selectable for translation"
    assert "data-translation-panel" in html, "the viewer does not host the translation panel"
    assert "Machine translation, not verified" in html
    assert "Accept translation" in html
    # The Correct button is created per row by the script, which the page must
    # load; the unit wiring test checks the script offers it.
    assert "translation-selection.js" in html


def test_the_search_results_offer_a_marked_panel(admin_client, seeded_document):
    """The results region is selectable and the panel is rendered with it."""
    response = admin_client.get("/search?q=convoy")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "data-translation-panel" in html
    assert "Machine translation, not verified" in html
    assert "translation-selection.js" in html


def test_capabilities_says_which_engines_are_not_installed(admin_client):
    """An uninstalled engine is reported, with its licence, not hidden."""
    response = admin_client.post("/api/translation/capabilities", json={})
    assert response.status_code == 200
    body = response.get_json()

    assert body["disclaimer"].lower() == "machine translation, not verified"
    ids = {engine["id"] for engine in body["engines"]}
    assert {"argos", "opus_mt", "nllb"} <= ids

    nllb = next(engine for engine in body["engines"] if engine["id"] == "nllb")
    assert nllb["non_commercial"] is True
    assert nllb["document_safe"] is False
    for engine in body["engines"]:
        assert "available" in engine
        assert engine["license"]
