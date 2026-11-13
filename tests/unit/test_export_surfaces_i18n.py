"""i18n contract for export surfaces (file actions, structured export, analyst CSV)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.unit.test_file_action_button_i18n import (  # noqa: E402
    ALL_CATALOG_LANGUAGES,
    FILE_ACTION_ICON_MSGIDS,
    TRANSLATIONS_DIR,
    _catalog,
    _mo_catalog,
)

pytestmark = pytest.mark.unit

STRUCTURED_EXPORT_MSGIDS = [
    "Structured folder export",
    "Organize copies by analyst category, source, and other metadata.",
    "What to export",
    "Folder layout",
    "Include smart (system) category in layout when needed",
    "File contents",
    "Write export manifest (JSON) at destination root",
    "Write metadata sidecar (.meta.json) next to each file",
    "Optional root subfolder name",
    "e.g. Case_2026-03-28",
    "Preview layout",
    "Choose folder & export",
    "Original files",
    "Extracted text",
    "Both (original + extracted text)",
    "Current analyst view filters",
    "All analyst-categorized files",
    "Selected files only",
    "All files loaded on this page",
    "Analyst assignments matching filters",
    "{files} files → {folders} folders",
    "Result limited to the maximum export size",
    "more folders",
    "Could not build layout",
    "No files match this export scope.",
    "Structured export completed.",
    "By analyst category",
    "Source → analyst category",
    "Analyst category → source",
    "Source → side → category",
    "By source",
    "By side",
    "By file type",
    "By year and month",
    "Smart category → analyst category",
    "Structured Export",
    "Export into a folder tree organized by category, source, and other metadata",
]

ANALYST_CSV_EXPORT_MSGIDS = [
    "Export analyst categorizations",
    "Exports respect the filters on this page. Choose which columns to include in the CSV.",
    "Columns to include",
    "Choose at least one column",
    "Default set",
    "CSV export ready.",
    "Assignment ID",
    "File ID",
    "File name",
    "File path",
    "File type",
    "Category color",
    "Analyst Category ID",
    "Assigned by (user ID)",
]

ALL_EXPORT_SURFACE_MSGIDS = sorted(
    set(FILE_ACTION_ICON_MSGIDS)
    | set(STRUCTURED_EXPORT_MSGIDS)
    | set(ANALYST_CSV_EXPORT_MSGIDS)
)

STRUCTURED_EXPORT_I18N = (
    PROJECT_ROOT / "templates" / "components" / "structured_export_i18n.json.jinja"
)
ANALYST_TEMPLATE = PROJECT_ROOT / "templates" / "Analyst" / "analyst_categorization.html"


def _msgids_from_file(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"_\(['\"](.+?)['\"]\)", text))


@pytest.mark.parametrize("lang", ALL_CATALOG_LANGUAGES)
def test_export_surface_msgids_in_catalog(lang):
    catalog = _catalog(lang)
    missing = [m for m in ALL_EXPORT_SURFACE_MSGIDS if catalog.get(m) is None]
    assert not missing, f"[{lang}] missing: {missing[:12]}"


@pytest.mark.parametrize("lang", ("ar", "he", "fa", "hr"))
def test_export_surface_msgids_have_real_translations(lang):
    catalog = _catalog(lang)
    bad = []
    for msgid in ALL_EXPORT_SURFACE_MSGIDS:
        msg = catalog.get(msgid)
        if msg is None or not msg.string or msg.fuzzy:
            bad.append(msgid)
    assert not bad, f"[{lang}] empty/fuzzy: {bad[:10]}"


@pytest.mark.parametrize("lang", ("ar", "he", "fa", "hr"))
def test_export_surface_msgids_in_mo(lang):
    mo = _mo_catalog(lang)
    missing = [m for m in ALL_EXPORT_SURFACE_MSGIDS if mo.get(m) is None]
    assert not missing, f"[{lang}] .mo missing: {missing[:10]}"


def test_structured_export_i18n_on_export_surfaces():
    inc = _msgids_from_file(STRUCTURED_EXPORT_I18N)
    assert set(STRUCTURED_EXPORT_MSGIDS) <= inc, "json.jinja missing structured export msgids"
    for path in (
        ANALYST_TEMPLATE,
        PROJECT_ROOT / "templates" / "file" / "files_list.html",
        PROJECT_ROOT / "templates" / "file" / "File_Management_Analysis_System.html",
    ):
        body = path.read_text(encoding="utf-8")
        assert "structured_export_i18n.json.jinja" in body
        assert 'id="structured-export-i18n"' in body
