"""i18n contract for Map Viewer template + page-data strings."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fill_map_viewer_i18n import MAP_VIEWER_MSGIDS  # noqa: E402
from tests.unit.test_file_action_button_i18n import (  # noqa: E402
    ALL_CATALOG_LANGUAGES,
    TRANSLATIONS_DIR,
    _catalog,
    _mo_catalog,
)

pytestmark = pytest.mark.unit

MAP_VIEWER_TEMPLATE = PROJECT_ROOT / "templates" / "map" / "map_viewer.html"


def _literal(msgid: str) -> bool:
    return not any(ch.islower() for ch in msgid)


def _template_msgids() -> set[str]:
    text = MAP_VIEWER_TEMPLATE.read_text(encoding="utf-8")
    return set(re.findall(r"_\(['\"](.+?)['\"]\)", text))


@pytest.mark.parametrize("lang", ALL_CATALOG_LANGUAGES)
def test_map_viewer_msgids_in_catalog(lang):
    catalog = _catalog(lang)
    missing = [m for m in MAP_VIEWER_MSGIDS if catalog.get(m) is None]
    assert not missing, f"[{lang}] missing: {missing[:8]}"


@pytest.mark.parametrize("lang", ("ar", "he", "fa", "hr"))
def test_map_viewer_msgids_translated(lang):
    catalog = _catalog(lang)
    bad = []
    for msgid in MAP_VIEWER_MSGIDS:
        msg = catalog.get(msgid)
        if msg is None or not msg.string or msg.fuzzy:
            bad.append(msgid)
        elif msg.string == msgid and not _literal(msgid):
            bad.append(msgid)
    assert not bad, f"[{lang}] untranslated: {bad[:8]}"


@pytest.mark.parametrize("lang", ("ar", "he", "fa", "hr"))
def test_map_viewer_msgids_in_mo(lang):
    mo = _mo_catalog(lang)
    missing = [m for m in MAP_VIEWER_MSGIDS if mo.get(m) is None]
    assert not missing, f"[{lang}] .mo stale: {missing[:8]}"


def test_map_viewer_template_strings_are_catalog_msgids():
    template_ids = _template_msgids()
    missing = sorted(template_ids - set(MAP_VIEWER_MSGIDS))
    assert not missing, f"template _() not in MAP_VIEWER_MSGIDS: {missing[:8]}"
