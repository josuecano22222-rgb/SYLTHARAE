"""The scenarios page's wiring, read from the shipped files.

Recorded defects, all visible only to a browser:

* ``templates/Monitoring/monitoring.html`` loaded ``js/pages/scenarios-page.js``
  with a plain ``<script src>`` although the module uses ``import`` — the
  browser refuses to parse it as a classic script, so the page had no
  behaviour at all (no list, no create, no dry-run).
* the same template emitted an inline ``<script>`` that copied ``page_data``
  onto ``window``; the page reads its JSON blocks instead, and the inline
  block is what a Content-Security-Policy without ``script-src 'unsafe-inline'``
  blocks.
* the page module carried inline ``onclick=`` attributes, which the shipped
  CSP does not allow either; the declarative dispatcher resolves names in the
  global scope, so the four actions must be published there.
* ``schedules-page.js`` had a private ``escapeCell`` whose replacement table
  mapped every character to itself — cells were never escaped, and the object
  literal did not even parse.
* ``categories_list.html`` concatenated the subcategory class onto
  ``category-item`` without a separator, so the subcategory style never
  applied.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEMPLATE = ROOT / "templates" / "Monitoring" / "monitoring.html"
PAGE = ROOT / "static" / "js" / "pages" / "scenarios-page.js"
SCHEDULES = ROOT / "static" / "js" / "pages" / "schedules-page.js"
CATEGORIES = ROOT / "templates" / "Category" / "categories_list.html"


def test_the_page_module_is_loaded_as_a_module():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r'<script[^>]*type="module"[^>]*scenarios-page\.js', html), html[-800:]


def test_the_template_carries_no_inline_script():
    html = TEMPLATE.read_text(encoding="utf-8")
    inline = [m for m in re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")[^>]*>",
                                    html)]
    assert inline == [], inline


def test_the_page_has_no_inline_event_handlers():
    js = PAGE.read_text(encoding="utf-8")
    assert "onclick=" not in js
    assert 'data-on-click' in js


def test_the_dispatched_actions_are_published_globally():
    js = PAGE.read_text(encoding="utf-8")
    for action in ("viewScenario", "dryRunScenario", "evaluateScenario", "archiveScenario"):
        assert f"window.{action} = {action};" in js, action


def test_table_cells_are_escaped_through_the_shared_helper():
    js = SCHEDULES.read_text(encoding="utf-8")
    assert "escapeHtml" in js.split("\n")[11] or "import { escapeHtml" in js
    assert "function escapeCell(value)" in js
    # the broken private entity table is gone
    assert "{\n        '&'" not in js


def test_the_subcategory_class_has_a_separator():
    html = CATEGORIES.read_text(encoding="utf-8")
    assert "category-item{% if category.parent_id %} subcategory{% endif %}" in html
    assert "category-itemsubcategory" not in html
