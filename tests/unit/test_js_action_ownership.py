"""One owner per action name per page.

A template resolves its ``data-on-click`` names by string, so whatever publishes
``window.<name>`` last wins. ``function-manager.js`` is imported on every route
(``shell-page-bridge.preparePageShell`` calls ``attachWindowGlobals()``), so any
name it publishes is published on *every* page - including pages whose own page
module publishes a different implementation of the same name. The winner is then
decided by whether the page module publishes at module-evaluation time (and is
overwritten a moment later by the registry) or inside its ``init()`` (and wins).

That is a load-order race, not a design, and it is invisible to ``node --check``
and to every test that loads one file at a time.

This module makes the ownership explicit and checkable without a browser:

* ``SHARED`` - the files every route loads before its page module. These may
  publish a name only if no page module publishes that name.
* ``page_module(template)`` - the single page module a page runs: the template's
  own ``<script>`` reference to ``js/pages/<x>-page.js``, or the module
  ``universal-initializer.js`` imports for the template's ``page_type``.
* For each of the 17 duplicate names that used to have two or more publishers,
  the page that uses the name must have exactly one publisher among the files it
  loads, and it must be the file that is supposed to own it (table below).

Where the only sensible owner was the shared capability rather than one page's
implementation, the shared copy carries a ``Shared`` suffix (``applyFilters`` ->
``applyFiltersShared``) and the page's implementation keeps the plain name. The
browser check that each action still fires its request lives in
``audit_report/r14/browser_actions.json``.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
JS_ROOT = ROOT / "static" / "js"
TEMPLATES = ROOT / "templates"

# Files loaded on every route, in this order: base.html loads the initializer,
# the initializer imports the bridge, the bridge calls attachWindowGlobals().
SHARED = (
    "static/js/pages/universal-initializer.js",
    "static/js/managers/function-manager.js",
    "static/js/modules/core/shell-page-bridge.js",
    # imported by function-manager, so evaluated on every route as well
    "static/js/modules/file-operations/file-management.js",
)

# The registry: publishers here reach every page.
REGISTRY = (
    "static/js/managers/function-manager.js",
    "static/js/modules/core/shell-page-bridge.js",
    "static/js/modules/file-operations/file-management.js",
)

# Templates that declare no page_type and are mapped by the route fallback in
# universal-initializer's pageDetectors rather than by their own script tag.
ROUTE_DETECTED = {
    "file/file_detail.html": "file-detail",
}

_WINDOW_ASSIGN = re.compile(r"\bwindow\.([A-Za-z_$][\w$]*)\s*=(?!=)")
_OBJECT_ASSIGN = re.compile(r"Object\.assign\(\s*window\s*,\s*\{(.*?)\}\s*\)", re.S)
_OBJECT_KEY = re.compile(r"(?:^|[,{\s])([A-Za-z_$][\w$]*)\s*:")
_EXPOSE = re.compile(r"exposeFileManagement\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)")
_TEMPLATE_ACTION = re.compile(r'data-on-click="([A-Za-z_$][\w$]*)\(')
_TEMPLATE_ONCLICK = re.compile(r"onclick=['\"][^'\"]*?([A-Za-z_$][\w$]*)\(")
_PAGE_TYPE = re.compile(r"{%\s*block page_type\s*%}(.*?){%\s*endblock", re.S)
_HANDLER = re.compile(r"'([\w-]+)':\s*\(bust\)\s*=>\s*import\('\./([\w-]+\.js)'")
_SCRIPT_SRC = re.compile(r"<script[^>]*\bsrc=\"[^\"]*?(js/[\w./-]+\.js)")


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
        names |= {global_name for _method, global_name in _EXPOSE.findall(text)}
        rel = str(path.relative_to(ROOT))
        for name in names:
            published.setdefault(name, set()).add(rel)
    return published


def _page_handlers() -> dict[str, str]:
    """{page_type: static/js/pages/<x>-page.js} from universal-initializer.js."""
    text = (JS_ROOT / "pages" / "universal-initializer.js").read_text(encoding="utf-8")
    return {page_type: f"static/js/pages/{module}" for page_type, module in _HANDLER.findall(text)}


def page_module(template: str) -> set[str]:
    """The page module(s) a template runs, as repo-relative paths."""
    text = (TEMPLATES / template).read_text(encoding="utf-8", errors="replace")
    found = set()
    for src in _SCRIPT_SRC.findall(text):
        if re.search(r"/pages/[\w-]+-page\.js$", src):
            found.add("static/" + src)
    if template in ROUTE_DETECTED:
        found.add(_page_handlers()[ROUTE_DETECTED[template]])
    page_type = _PAGE_TYPE.search(text)
    if page_type:
        handler = _page_handlers().get(page_type.group(1).strip())
        if handler:
            found.add(handler)
    return found


def loaded(template: str) -> set[str]:
    """Every file that can publish a global by the time the page is interactive."""
    return set(SHARED) | page_module(template)


def actions_used(template: str) -> set[str]:
    text = (TEMPLATES / template).read_text(encoding="utf-8", errors="replace")
    return set(_TEMPLATE_ACTION.findall(text)) | set(_TEMPLATE_ONCLICK.findall(text))


def _pages_using(name: str) -> list[str]:
    # templates/components/ are partials: the page that includes them publishes
    # the names they use, so they have no module set of their own.
    return [str(p.relative_to(TEMPLATES)) for p in sorted(TEMPLATES.rglob("*.html"))
            if "components" not in p.parts and name in actions_used(str(p.relative_to(TEMPLATES)))]


# The 17 names that had two or more publishers before this round.
DUPLICATES = (
    "applyFilters", "bulkDelete", "clearSearch", "deleteKeyword", "deleteWord",
    "exportSelectedFiles", "openAddWordModal", "performSearch", "refresh",
    "removeAnalystCategoriesFromSelection", "resetFilters", "saveWordToCategory",
    "submitSourceForm", "switchTab", "toggleAllFilesSelection",
    "toggleAllSelection", "updateKeywordAssociations",
)

# The names the shared registry no longer publishes, and the name it publishes
# instead. The shared capability still exists; a page's own implementation owns
# the plain name.
RENAMED_IN_REGISTRY = {
    "applyFilters": "applyFiltersShared",
    "resetFilters": "resetFiltersShared",
    "exportSelectedFiles": "exportSelectedFilesShared",
    "removeAnalystCategoriesFromSelection": "removeAnalystCategoriesFromSelectionShared",
    "toggleAllFilesSelection": "toggleAllFilesSelectionShared",
    "updateKeywordAssociations": "updateKeywordAssociationsShared",
    "submitSourceForm": None,   # sources-list-page.js owns it; nothing shared left
    "bulkDelete": None,         # files-list-page.js publishes the file-list one
}


def test_no_shared_file_publishes_a_name_a_page_module_publishes():
    """The registry must not compete with page modules for any of the 17 names."""
    published = _published()
    offenders = []
    for name in DUPLICATES:
        owners = published.get(name, set())
        page_owners = {owner for owner in owners if "/pages/" in owner}
        registry_owners = owners & set(REGISTRY)
        if page_owners and registry_owners:
            offenders.append(f"{name}: registry {sorted(registry_owners)} vs page {sorted(page_owners)}")
    assert not offenders, (
        "these action names are published both by the always-loaded registry and "
        "by a page module, so the winner depends on load order:\n  "
        + "\n  ".join(offenders))


def test_each_page_has_exactly_one_publisher_for_the_names_it_uses():
    """On the page that uses a name, exactly one loaded file may publish it."""
    published = _published()
    problems = []
    for name in DUPLICATES:
        for template in _pages_using(name):
            if name not in actions_used(template):
                continue
            owners = published.get(name, set()) & loaded(template)
            if len(owners) != 1:
                problems.append(
                    f"{name} on templates/{template}: {len(owners)} publishers "
                    f"{sorted(owners)}")
    assert not problems, (
        "a data-on-click name must resolve to one implementation on the page "
        "that uses it:\n  " + "\n  ".join(problems))


def test_the_page_module_owns_a_name_it_implements():
    """If the page module publishes the name, it - not the registry - owns it."""
    published = _published()
    problems = []
    for name in DUPLICATES:
        for template in _pages_using(name):
            module = page_module(template)
            if not module & published.get(name, set()):
                continue  # the page has no implementation: the registry owns it
            owners = published.get(name, set()) & loaded(template)
            wanted = module & owners
            if owners != wanted:
                problems.append(
                    f"{name} on templates/{template}: page module {sorted(wanted)} "
                    f"loses to {sorted(owners - wanted)}")
    assert not problems, (
        "the page's own implementation must be the one the page's markup "
        "resolves:\n  " + "\n  ".join(problems))


def test_the_shared_capability_survives_under_its_new_name():
    """Renaming the registry copy must not delete the capability it exposed."""
    published = _published()
    for old, new in RENAMED_IN_REGISTRY.items():
        if new is None:
            continue
        assert new in published, f"{new} is not published by anything"
        assert set(REGISTRY) & published[new], (
            f"{new} must be published by the registry, found {sorted(published[new])}")


def test_the_pages_that_used_the_shared_copy_now_name_it():
    """Every markup/JS reference to a moved capability uses the new name."""
    checks = {
        "templates/file/File_Management_Analysis_System.html": ["applyFiltersShared()", "resetFiltersShared()"],
        "static/js/modules/views/file-view.js": ["toggleAllFilesSelectionShared?.()", "removeAnalystCategoriesFromSelectionShared?.()"],
        "static/js/modules/rendering/grid-renderer.js": ["updateKeywordAssociationsShared?.()"],
        "static/js/modules/documents/document-actions.js": ["window.exportSelectedFilesShared"],
    }
    missing = []
    for rel, needles in checks.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                missing.append(f"{rel}: {needle}")
    assert not missing, "references to the moved shared capability were not updated:\n  " + "\n  ".join(missing)


def test_the_scan_sees_the_conditional_exposures():
    """Guard the guard: exposeFileManagement() names are publications too."""
    published = _published()
    assert "static/js/managers/function-manager.js" in published.get("deleteFile", set())
