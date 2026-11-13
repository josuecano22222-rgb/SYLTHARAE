"""i18n acceptance tests for the analyst-categorization feature (PR #11).

Proves that every user-visible string the feature introduced is served
through the project's translation mechanism:

* the five catalogs (en/ar/he/fa/hr) contain every new msgid, with real
  translations (no empty/fuzzy entries) and compiled .mo files;
* the pages render translated (headings, buttons, placeholders, titles,
  aria-labels) with correct lang/dir attributes for the RTL languages;
* the page-data JSON blocks that drive client-side strings carry the same
  translations, so dynamically generated JS strings are localized too.
"""

import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

RTL_LANGUAGES = ("ar", "he", "fa")
ALL_CATALOG_LANGUAGES = ("en", "ar", "he", "fa", "hr")

#: msgids introduced by the feature (server-rendered + page-data + JS).
NEW_MSGIDS = [
    "Analyst Categorization",
    "Analyst Categories",
    "Analyst Category",
    "Analyst View",
    "Manage Analyst Categories",
    "Audit Log",
    "Assignments",
    "Analysts",
    "All Analysts",
    "All Files",
    "Categorize",
    "Categorize from Search",
    "Categorized Files",
    "Categorized From",
    "Categorized To",
    "Uncategorized Files Only",
    "Uncategorized Files Remaining",
    "Analyst-Categorized Files Only",
    "Originating Query",
    "Originating Search Query",
    "Search Scope",
    "Analyst categorization search scope",
    "Export CSV",
    "Open Analyst View",
    "File Name / Path",
    "File name or path…",
    "Filter by the query that surfaced the files…",
    "Choose analyst category…",
    "New analyst category name…",
    "…or new category name",
    "Smart Categories",
    "Smart",
    "Select all results on this page",
    "Show files in this category",
    "Delete category (files return to uncategorized)",
    "Select for manual categorization",
    "Select {file} for manual categorization",
    "Remove all analyst categories from {count} selected file(s)? They will return to \"uncategorized\" for analyst search scope. Smart categories are not affected.",
    "Assigned \"{category}\" to {count} file(s)",
    "{count} file(s)",
    "Page {page} of {pages} ({total} assignments)",
    "(no query)",
    "unknown",
    "default",
    "manually assigned",
    "system-generated",
    "Loading…",
    "Failed to load assignments",
    "Failed to load audit log.",
    "No categorization actions recorded yet.",
    "No analyst categories yet — create one from a search or above.",
    "No analyst-categorized files match the current filters.",
    "Analyst category assigned",
    "Analyst categories removed",
    "Analyst categorization failed",
    "Analyst category created",
    "Analyst category deleted",
    "Assignment removed",
    "Remove failed",
    "Could not create category",
    "Could not delete category",
    "Select one or more files first",
    "Choose an analyst category or type a new one",
    "Delete analyst category \"{name}\"? Its assignments are removed and affected files return to uncategorized status.",
    "Remove analyst category from this file? It returns to uncategorized status for search scope.",
]

TRANSLATIONS_DIR = PROJECT_ROOT / "translations"


@pytest.fixture(autouse=True)
def _restore_english(admin_client):
    """``/set_language`` is SYSTEM-WIDE: it persists ``system.language`` to
    the settings store, not just the session. Restore English after every
    test in this module so page-rendering tests in other modules (which
    assert English strings) stay hermetic regardless of run order."""
    yield
    admin_client.get("/set_language/en")


def _catalog(lang):
    from babel.messages.pofile import read_po

    with open(TRANSLATIONS_DIR / lang / "LC_MESSAGES" / "messages.po", "rb") as f:
        return read_po(f, locale=lang)


def _mo_catalog(lang):
    from babel.messages.mofile import read_mo

    with open(TRANSLATIONS_DIR / lang / "LC_MESSAGES" / "messages.mo", "rb") as f:
        return read_mo(f)


# ---------------------------------------------------------------------------
# Catalog completeness
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang", ALL_CATALOG_LANGUAGES)
def test_every_new_msgid_exists_in_catalog(lang):
    catalog = _catalog(lang)
    missing = [m for m in NEW_MSGIDS if catalog.get(m) is None]
    assert not missing, f"[{lang}] missing msgids: {missing}"


@pytest.mark.parametrize("lang", ("ar", "he", "fa", "hr"))
def test_new_msgids_have_real_translations(lang):
    """Translation-fallback is not translation: every non-English catalog
    must carry an actual, non-empty, non-fuzzy translation."""
    catalog = _catalog(lang)
    bad = []
    for msgid in NEW_MSGIDS:
        msg = catalog.get(msgid)
        if msg is None or not msg.string or msg.fuzzy:
            bad.append(msgid)
        elif msg.string == msgid and lang != "en":
            bad.append(f"UNCHANGED(msgid as translation): {msgid}")
    assert not bad, f"[{lang}] untranslated entries: {bad[:5]}"


@pytest.mark.parametrize("lang", ALL_CATALOG_LANGUAGES)
def test_mo_files_are_compiled_with_new_msgids(lang):
    mo = _mo_catalog(lang)
    if lang == "en":
        # en is the source language: the .mo may legitimately be header-only
        # (gettext falls back to the msgid, which IS English) - it must not
        # contain stale/different text.
        return
    missing = [m for m in NEW_MSGIDS if mo.get(m) is None]
    assert not missing, f"[{lang}] .mo missing (needs recompilation): {missing[:5]}"


def test_hr_catalog_is_maintained_but_not_exposed():
    """Documented ambiguity: a Croatian catalog exists and is kept in sync,
    but 'hr' is not registered in SUPPORTED_LANGUAGES (en/ar/he/fa only), so
    it is not offered in the UI. This test pins the current registry state;
    if hr is intentionally exposed later, update this test together with
    settings/languages.py."""
    from settings.languages import SUPPORTED_LANGUAGES, RTL_LANGUAGES

    assert "hr" not in SUPPORTED_LANGUAGES
    assert set(SUPPORTED_LANGUAGES) == {"en", "ar", "he", "fa"}
    # RTL handling covers the exposed RTL languages.
    assert {"ar", "he", "fa"} <= set(RTL_LANGUAGES)
    # ...and the dormant hr catalog is complete for the feature strings.
    catalog = _catalog("hr")
    assert all(catalog.get(m) is not None for m in NEW_MSGIDS)


# ---------------------------------------------------------------------------
# Rendered pages
# ---------------------------------------------------------------------------

def _set_language(client, lang):
    resp = client.get(f"/set_language/{lang}")
    assert resp.status_code in (200, 302)


def _page_data(html, script_id):
    m = re.search(
        rf'<script type="application/json" id="{script_id}">(.*?)</script>',
        html, re.S,
    )
    assert m, f"page-data block {script_id} not found"
    return json.loads(m.group(1))


@pytest.mark.parametrize("lang", RTL_LANGUAGES)
def test_analyst_pages_fully_translated_rtl(admin_client, lang):
    _set_language(admin_client, lang)

    resp = admin_client.get("/analyst/categorization")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert f'<html lang="{lang}"' in html
    assert 'dir="rtl"' in html
    # A key heading is translated (not the English msgid).
    assert ">Analyst Categorization<" not in html
    assert ">Audit Log<" not in html
    assert ">Manage Analyst Categories<" not in html

    # Page-data JSON that feeds JS-generated strings is translated too.
    data = _page_data(html, "analyst-categorization-page-data")
    tr = data["translations"]
    for key, english in (
        ("loading", "Loading…"),
        ("failedToLoadAssignments", "Failed to load assignments"),
        ("failedAuditLog", "Failed to load audit log."),
        ("noActionsYet", "No categorization actions recorded yet."),
        ("allAnalysts", "All Analysts"),
        ("noCategoriesYet", "No analyst categories yet — create one from a search or above."),
        ("showFilesInCategory", "Show files in this category"),
        ("deleteCategoryTitle", "Delete category (files return to uncategorized)"),
    ):
        assert key in tr, f"missing page-data key {key}"
        assert tr[key] != english, f"[{lang}] page-data key {key} fell back to English"
        assert "{count}" not in tr[key] or key == "fileCount"


def test_english_pages_render_english_ltr(admin_client):
    _set_language(admin_client, "en")
    resp = admin_client.get("/analyst/categorization")
    html = resp.get_data(as_text=True)
    assert '<html lang="en"' in html
    assert 'dir="ltr"' in html
    assert ">Analyst Categorization<" in html


@pytest.mark.parametrize("lang", RTL_LANGUAGES)
def test_advanced_search_scope_ui_translated(admin_client, lang):
    _set_language(admin_client, lang)
    resp = admin_client.get("/search/advanced")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'dir="rtl"' in html

    # The (audit-corrected) select-all tooltip must be localized.
    m = re.search(r'<input[^>]*id="selectAllResults"[^>]*>', html, re.S)
    assert m, "select-all checkbox missing"
    title = re.search(r'title="([^"]*)"', m.group(0)).group(1)
    assert title, "select-all title missing"
    assert title != "Select all results on this page", f"[{lang}] select-all title untranslated"

    # Scope radio labels must be localized (not English msgids).
    for english in (">Uncategorized Files Only<", ">All Files<",
                    ">Analyst-Categorized Files Only<"):
        assert english not in html, f"[{lang}] untranslated scope label {english}"

    # Client-side categorization strings via page-data.
    data = _page_data(html, "search-advanced-page-data")
    tr = data["translations"]
    for key, english in (
        ("selectForCategorization", "Select for manual categorization"),
        ("selectFileForCategorization", "Select {file} for manual categorization"),
        ("removeAllConfirm", "Remove all analyst categories from {count} selected file(s)? They will return to \"uncategorized\" for analyst search scope. Smart categories are not affected."),
        ("assignedToast", "Assigned \"{category}\" to {count} file(s)"),
    ):
        assert key in tr, f"missing page-data key {key}"
        assert tr[key] != english, f"[{lang}] page-data key {key} fell back to English"
