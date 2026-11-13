"""The UI: select text, translate, compare, accept or correct.

The browser side is small enough to test as text, and the things that go wrong
with "small enough" UI code are all visible in the text:

* the panel is not on the pages that need it (the file viewer and the search
  results), so a selection has nowhere to go;
* the script posts to a GET or forgets the CSRF token, so the request fails in
  the browser and works in the test;
* the label "machine translation, not verified" is missing, and unverified
  machine output is presented as if an analyst had read it;
* a new message id is written into the templates and never listed for the human
  translator.

Every message the panel shows must therefore be either an existing msgid or one
of the rows in ``services/translation/msgids_for_translation.csv``.
"""

import csv
import pathlib
import re

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

PANEL = PROJECT_ROOT / "templates/Translation/_selection_panel.html"
SCRIPT = PROJECT_ROOT / "static/js/pages/translation-selection.js"
MSGIDS = PROJECT_ROOT / "services/translation/msgids_for_translation.csv"

HOSTED_BY = (
    PROJECT_ROOT / "templates/file/file_detail.html",
    PROJECT_ROOT / "templates/Search/search.html",
)

EXPECTED_MSGIDS = {
    "Translate selection",
    "Translating…",
    "Machine translation, not verified",
    "Original",
    "Translation",
    "Accept translation",
    "Correct translation",
    "Translation engine is not installed",
    "Translation failed",
    "from translation memory (corrected)",
}


def test_the_panel_and_its_script_exist():
    assert PANEL.exists(), "the selection panel template is missing"
    assert SCRIPT.exists(), "the selection panel script is missing"


def test_both_pages_include_the_panel_and_mark_translatable_regions():
    for page in HOSTED_BY:
        source = page.read_text(encoding="utf-8")
        assert "Translation/_selection_panel.html" in source, (
            f"{page.name} does not include the translation panel")
        assert "data-translatable" in source, (
            f"{page.name} marks no region as translatable")


def test_the_panel_labels_machine_output_as_unverified():
    panel = PANEL.read_text(encoding="utf-8")
    assert "Machine translation, not verified" in panel
    assert "not verified" in panel


def test_the_script_posts_with_a_csrf_token_and_never_uses_get():
    script = SCRIPT.read_text(encoding="utf-8")
    endpoints = set(re.findall(r"['\"](/api/translation/[a-z/]+)['\"]", script))
    assert endpoints, "the script posts to no translation endpoint"
    assert all(path.startswith("/api/translation/") for path in endpoints)
    assert "method: 'POST'" in script or 'method: "POST"' in script
    assert "csrf" in script.lower(), "the POST needs the CSRF token"
    assert not re.search(r"method:\s*['\"]GET['\"]", script), (
        "the translation API is POST only")


def test_the_script_offers_accept_and_correct():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "/api/translation/memory/correct" in script
    assert "accept" in script.lower()
    assert "correct" in script.lower()


def test_a_memory_answer_is_not_labelled_with_a_model():
    """``served_from_memory`` says so; printing the stored engine id would name
    a model that did not run (a correction is delivered by the memory)."""
    script = SCRIPT.read_text(encoding="utf-8")
    assert "served_from_memory" in script, (
        "the panel must distinguish a memory answer from an engine answer")
    assert "from translation memory (corrected)" in script


def _function_source(name: str) -> str:
    """The text of one top-level ``function name(...) {`` in the script."""
    script = SCRIPT.read_text(encoding="utf-8")
    start = script.index(f"function {name}(")
    end = script.index("\n}\n", start) + 3
    return script[start:end]


def test_accepting_a_translation_asks_the_analyst_nothing():
    """Accept is one click: the engine's wording is confirmed, not re-typed.

    Correct is where the analyst types; if Accept also opened the prompt the
    two buttons would do the same thing, and accepting would look like a task.
    """
    body = _function_source("acceptTranslation")
    assert "window.prompt" not in body, "Accept must not open the correction prompt"
    # Nor may it reach the prompt through the correction path.
    assert "saveCorrection" not in body, (
        "Accept must record the approval itself instead of calling the "
        "correcting path, which asks the analyst for text")
    assert "/api/translation/memory/correct" in body, (
        "Accept must store the approval as the analyst's wording")


def test_correcting_a_translation_asks_for_the_analysts_wording():
    body = _function_source("saveCorrection")
    assert "window.prompt(" in body, "Correct must ask for the analyst's own wording"


def test_new_msgids_are_listed_for_the_human_translator():
    assert MSGIDS.exists(), "new msgids were not listed for translation"
    with MSGIDS.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows, "the msgid list is empty"
    assert set(rows[0]) >= {"msgid", "english", "used_in", "status"}

    listed = {row["msgid"] for row in rows}
    missing = EXPECTED_MSGIDS - listed
    assert not missing, f"msgids the panel shows but a translator cannot find: {missing}"
    assert all(row["status"] for row in rows), "every row needs a status"


def test_the_panel_uses_no_message_it_did_not_declare():
    """Every string the panel/script shows must be declared in the CSV."""
    with MSGIDS.open(encoding="utf-8") as handle:
        listed = {row["english"] for row in csv.DictReader(handle)}
    panel = PANEL.read_text(encoding="utf-8")
    shown = set(re.findall(r"_\(\s*['\"]([^'\"]{3,})['\"]\s*\)", panel))
    undeclared = {text for text in shown if text not in listed}
    assert not undeclared, (
        "these strings are shown to the analyst but not listed for translation: "
        f"{sorted(undeclared)}")
