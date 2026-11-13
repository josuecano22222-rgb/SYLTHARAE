"""Every ``data-on-click`` action a template uses must have a publisher.

Found on ``/categories``: the page's own ``saveCategory`` (which posts to
``/api/categories/tree``) and a legacy modal handler of the same name (which
posts to ``/category/add`` and reads ``#categoryWordInput``) both published
``window.saveCategory``. The last module to load won. On the Categories page
that was the legacy one, so pressing Save in the "Add category" dialog answered
"Form elements not found" and issued no request at all: creating a category
from the list page was impossible.

The general hazard is a name a template resolves by string being published by
more than one file - the winner is load-order dependent and invisible to every
test that loads one script at a time. Two things this module can check
honestly, and one it cannot:

* **checked** - every action a template uses has at least one publisher. A
  template action with no publisher is a button that does nothing, and it is
  detectable without a browser.
* **checked** - ``saveCategory`` is published by the Categories page alone (the
  specific collision, pinned so it cannot come back).
* **not checked** - the other names more than one file publishes. They are real
  (a shared module and a page module publish ``resetFilters``,
  ``submitSourceForm`` and six more), but whether the page's version or the
  shared one wins is a question about script order on each page, and static
  text cannot answer it. They are listed in the round's report as open, to be
  settled by clicking each one in a browser.

Two publishing idioms are read: ``window.name = ...`` and
``Object.assign(window, {...})``. Reading only the first reported five
template actions as unpublished (the retention and scheduling pages) that are
in fact published by the second - the test has to see the whole contract, not
one spelling of it.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
JS_ROOT = ROOT / "static" / "js"
TEMPLATES = ROOT / "templates"

_WINDOW_ASSIGN = re.compile(r"\bwindow\.([A-Za-z_$][\w$]*)\s*=(?!=)")
_OBJECT_ASSIGN = re.compile(r"Object\.assign\(\s*window\s*,\s*\{(.*?)\}\s*\)", re.S)
_OBJECT_KEY = re.compile(r"(?:^|[,{\s])([A-Za-z_$][\w$]*)\s*:")
_TEMPLATE_ACTION = re.compile(r'data-on-click="([A-Za-z_$][\w$]*)\(')


def _published() -> dict[str, set[str]]:
    """{global name: {files that publish it}}."""
    published: dict[str, set[str]] = {}
    for path in sorted(JS_ROOT.rglob("*.js")):
        if "dist" in path.parts or path.name.endswith(".min.js"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        names = set(_WINDOW_ASSIGN.findall(text))
        for block in _OBJECT_ASSIGN.findall(text):
            names |= set(_OBJECT_KEY.findall(block))
        for name in names:
            published.setdefault(name, set()).add(str(path.relative_to(ROOT)))
    return published


def _used_actions() -> dict[str, set[str]]:
    """{action: {templates that call it}}."""
    used: dict[str, set[str]] = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in _TEMPLATE_ACTION.findall(text):
            used.setdefault(name, set()).add(str(path.relative_to(ROOT)))
    return used


def test_every_template_action_has_a_publisher():
    """A ``data-on-click`` name nobody publishes is a dead button."""
    published = _published()
    used = _used_actions()
    assert used, "no data-on-click actions were found - the scan is broken"
    missing = {name: sorted(templates) for name, templates in used.items()
               if name not in published}
    assert not missing, (
        "these template actions are not published on window by any module, so "
        "the declarative dispatcher cannot resolve them:\n"
        + "\n".join(f"  {name}: {templates}" for name, templates in sorted(missing.items())))


def test_the_scan_reads_both_publishing_idioms():
    """Guard the guard: the two idioms the codebase uses are both understood."""
    published = _published()
    assert "saveCategory" in published, "window.name = ... was not read"
    # Object.assign(window, {...}) - the retention and scheduling pages
    assert "runAll" in published, "Object.assign(window, {...}) was not read"
    assert published["runAll"] == {"static/js/pages/retention-page.js"}


def test_the_categories_page_owns_save_category():
    """The specific collision that made the list page unable to save."""
    published = _published()
    owners = published.get("saveCategory", set())
    assert owners == {"static/js/pages/categories-list-page.js"}, (
        "saveCategory must be published by the Categories page alone; the "
        f"legacy modal handler publishes saveAddCategoryModal now. Found: {sorted(owners)}")


def test_the_page_whose_dialog_posts_to_the_legacy_endpoint_uses_its_own_name():
    """The two Save buttons must not name the same handler."""
    published = _published()
    assert published.get("saveAddCategoryModal") == {
        "static/js/modules/modals/modal-manager.js"}
    categories = (ROOT / "templates" / "Category" / "categories_list.html").read_text(encoding="utf-8")
    fmas = (ROOT / "templates" / "file" / "File_Management_Analysis_System.html").read_text(encoding="utf-8")
    assert 'data-on-click="saveCategory()"' in categories
    assert 'data-on-click="saveAddCategoryModal()"' in fmas
