"""Navigation registry names/descriptions must exist in locale catalogs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.interfaces.domains import DOMAIN_LABELS  # noqa: E402
from core.interfaces.registry import FEATURES, REGISTRY  # noqa: E402
from scripts.fill_interface_registry_i18n import _registry_msgids  # noqa: E402
from tests.unit.test_file_action_button_i18n import (  # noqa: E402
    _catalog,
    _mo_catalog,
)

pytestmark = pytest.mark.unit

REGISTRY_MSGIDS = _registry_msgids()


def _literal(msgid: str) -> bool:
    return not any(ch.islower() for ch in msgid)


@pytest.mark.parametrize("lang", ("en", "ar", "he", "fa", "hr"))
def test_registry_strings_in_catalog(lang):
    catalog = _catalog(lang)
    missing = [m for m in REGISTRY_MSGIDS if m and catalog.get(m) is None]
    assert not missing, f"[{lang}] missing registry strings: {missing[:5]}"


@pytest.mark.parametrize("lang", ("ar", "he", "fa", "hr"))
def test_registry_strings_translated(lang):
    catalog = _catalog(lang)
    bad = []
    for msgid in REGISTRY_MSGIDS:
        if not msgid or _literal(msgid):
            continue
        msg = catalog.get(msgid)
        if msg is None or not msg.string or msg.fuzzy or msg.string == msgid:
            bad.append(msgid[:60])
    assert not bad, f"[{lang}] untranslated registry strings: {bad[:5]}"


@pytest.mark.parametrize("lang", ("ar", "he", "fa", "hr"))
def test_registry_strings_in_mo(lang):
    mo = _mo_catalog(lang)
    missing = [m for m in REGISTRY_MSGIDS if m and mo.get(m) is None]
    assert not missing, f"[{lang}] .mo stale for registry: {missing[:5]}"


def test_registry_msgid_set_covers_navigation_labels():
    names = {i.name for i in REGISTRY} | {f.name for f in FEATURES}
    labels = set(DOMAIN_LABELS.values())
    assert names <= set(REGISTRY_MSGIDS)
    assert labels <= set(REGISTRY_MSGIDS)
