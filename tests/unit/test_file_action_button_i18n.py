"""Catalog + template contract for icon-only file action tooltips."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.unit

TRANSLATIONS_DIR = PROJECT_ROOT / "translations"
ALL_CATALOG_LANGUAGES = ("en", "ar", "he", "fa", "hr")

FILES_LIST = PROJECT_ROOT / "templates" / "file" / "files_list.html"
FMAS_TEMPLATE = PROJECT_ROOT / "templates" / "file" / "File_Management_Analysis_System.html"
FILE_VIEW_JS = PROJECT_ROOT / "static" / "js" / "modules" / "views" / "file-view.js"

# Every user-visible tooltip / visually-hidden label on File Library bulk + card chrome.
FILE_ACTION_ICON_MSGIDS = [
    "Selection:",
    "Bulk Actions:",
    "Select All",
    "Select None",
    "Analyze selected files",
    "Analyze",
    "View categories, category terms, and keywords for the selection",
    "Analysis",
    "Export the extracted content of the selection as one archive",
    "Export Content",
    "Export the original files of the selection as one ZIP",
    "Export Originals",
    "Export selected filenames to CSV",
    "Filenames CSV",
    "Export selected filenames to Excel",
    "Filenames Excel",
    "Choose a folder (or create a subfolder) for extracted text",
    "Text to folder",
    "Choose a folder (or create a subfolder) for original files",
    "Originals to folder",
    "Review and rename files before export",
    "Rename…",
    "Open the original file in its native application",
    "Open",
    "Locate the containing folder",
    "Locate",
    "Export names and displayed details with column choice",
    "Details…",
    "Export names of documents loaded on this page",
    "Loaded names",
    "Delete selected files",
    "Delete",
    "List View",
    "Grid View",
    "View:",
    "Filter by one or more file types",
    "File Actions",
    "Open Full View in New Tab",
    "Full View",
    "View Details",
    "Export File",
    "Export",
    "Save As",
    "Save As (rename on export)",
    "Open in native application",
    "Open in native app",
    "Locate folder",
    "Analytical Report",
    "Generate an analytical report with explanatory text for the selection",
    "Report",
    "Show More",
]

# Keys file-view.js reads from page-data when rendering FMAS toolbars/cards.
FMAS_PAGE_DATA_KEYS = [
    "exportSelectedContentTitle",
    "exportOriginalsZipTitle",
    "viewAnalysisTitle",
    "exportTextToFolder",
    "exportOriginalsToFolder",
    "renameOnExport",
    "openOriginalTitle",
    "locateContainingFolder",
    "exportNamesCsvTitle",
    "exportNamesExcelTitle",
    "exportDetailsTitle",
    "exportLoadedNames",
    "analyticalReportTitle",
    "saveAsTitle",
    "openInNativeApp",
    "viewDetails",
    "openFullView",
    "fullView",
    "exportFile",
    "saveAs",
    "openNative",
    "locateFolder",
    "locate",
    "analyticalReport",
    "report",
    "selectAll",
    "deselectAll",
    "showMore",
]


def _catalog(lang: str):
    from babel.messages.pofile import read_po

    with open(TRANSLATIONS_DIR / lang / "LC_MESSAGES" / "messages.po", "rb") as f:
        return read_po(f, locale=lang)


def _mo_catalog(lang: str):
    from babel.messages.mofile import read_mo

    with open(TRANSLATIONS_DIR / lang / "LC_MESSAGES" / "messages.mo", "rb") as f:
        return read_mo(f)


def _extract_msgids_from_template(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"_\(['\"](.+?)['\"]\)", text))


def _fmas_page_data_translations() -> dict[str, str]:
    text = FMAS_TEMPLATE.read_text(encoding="utf-8")
    start = text.find('"translations": {')
    assert start != -1, "FMAS translations block not found"
    chunk = text[start : start + 12000]
    values = {}
    for key in FMAS_PAGE_DATA_KEYS:
        vm = re.search(rf'"{re.escape(key)}"\s*:\s*_\("([^"]+)"\)', chunk)
        if vm:
            values[key] = vm.group(1)
    return values


@pytest.mark.parametrize("lang", ALL_CATALOG_LANGUAGES)
def test_file_action_icon_msgids_in_catalog(lang):
    catalog = _catalog(lang)
    missing = [m for m in FILE_ACTION_ICON_MSGIDS if catalog.get(m) is None]
    assert not missing, f"[{lang}] missing msgids: {missing}"


def test_en_file_action_icon_msgids_use_identity_translation():
    """English catalog must not reuse unrelated msgstr (e.g. Export File -> failed)."""
    catalog = _catalog("en")
    bad = []
    for msgid in FILE_ACTION_ICON_MSGIDS:
        msg = catalog.get(msgid)
        text = str(msg.string) if msg and msg.string else ""
        if msg is None or text != msgid:
            bad.append((msgid, text or None))
    assert not bad, f"en wrong msgstr: {bad[:10]}"


@pytest.mark.parametrize("lang", ("ar", "he", "fa", "hr"))
def test_file_action_icon_msgids_have_nonempty_translation(lang):
    catalog = _catalog(lang)
    bad = []
    for msgid in FILE_ACTION_ICON_MSGIDS:
        msg = catalog.get(msgid)
        if msg is None or not msg.string or msg.fuzzy:
            bad.append(msgid)
    assert not bad, f"[{lang}] empty/fuzzy: {bad[:8]}"


@pytest.mark.parametrize("lang", ("ar", "he", "fa", "hr"))
def test_file_action_icon_msgids_in_compiled_mo(lang):
    mo = _mo_catalog(lang)
    missing = [m for m in FILE_ACTION_ICON_MSGIDS if mo.get(m) is None]
    assert not missing, f"[{lang}] .mo missing (recompile): {missing[:8]}"


def test_files_list_template_uses_gettext_for_icon_tooltips():
    msgids = _extract_msgids_from_template(FILES_LIST)
    missing = [m for m in FILE_ACTION_ICON_MSGIDS if m not in msgids]
    # Card/grid actions may live only in FMAS JS; bulk bar must cover the list.
    bulk_required = [
        m
        for m in FILE_ACTION_ICON_MSGIDS
        if m
        in {
            "Select All",
            "Analyze selected files",
            "View categories, category terms, and keywords for the selection",
            "Export the extracted content of the selection as one archive",
            "Export the original files of the selection as one ZIP",
            "Export selected filenames to CSV",
            "Export selected filenames to Excel",
            "Delete selected files",
            "Open Full View in New Tab",
        }
    ]
    missing_bulk = [m for m in bulk_required if m not in msgids]
    assert not missing_bulk, f"files_list.html missing _() for: {missing_bulk}"


def test_fmas_page_data_includes_file_view_translation_keys():
    values = _fmas_page_data_translations()
    missing_keys = [k for k in FMAS_PAGE_DATA_KEYS if k not in values]
    assert not missing_keys, f"FMAS page-data missing keys: {missing_keys}"


def test_fmas_page_data_values_are_catalog_msgids():
    values = _fmas_page_data_translations()
    catalog = _catalog("en")
    bad = [k for k, v in values.items() if catalog.get(v) is None]
    assert not bad, f"page-data values not in catalog: {bad}"


def test_file_view_uses_page_translations_not_stale_import():
    source = FILE_VIEW_JS.read_text(encoding="utf-8")
    assert "function pageTranslations()" in source
    assert "const translations = pageTranslations();" in source
    # Dynamic toolbar must not rely on a module-scope `translations` binding alone.
    assert "buildFilesToolbar" in source
    toolbar_start = source.index("function buildFilesToolbar")
    toolbar_chunk = source[toolbar_start : toolbar_start + 4000]
    assert "pageTranslations()" in toolbar_chunk
