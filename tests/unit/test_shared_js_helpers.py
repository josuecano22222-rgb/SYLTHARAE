"""Regression guard for the shared JavaScript helpers.

``escapeHtml`` and ``getCSRFToken`` have one canonical implementation in
``static/js/modules/core/utils.js``. Page modules must import it instead of
defining their own copy, because copies drift: an audit found ten copies of
``escapeHtml`` that rendered ``undefined`` as the word "undefined" and a
variant that turned numeric ``0`` into an empty string.

Three things are enforced here:

* the Node contract harness passes (``tests/js/shared_helpers_smoke.mjs``);
* the files consolidated in this change import the canonical helper and
  declare no local copy, and still parse as ES modules;
* the set of files that still define these helpers is pinned to the
  allowlist below, so a new duplicate fails the suite. Entries leave the
  allowlist as they are consolidated; they are never added to it silently.

See docs/JS_ARCHITECTURE_AUDIT.md for why each remaining entry is deferred.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
JS_ROOT = PROJECT_ROOT / "static" / "js"
UTILS = JS_ROOT / "modules" / "core" / "utils.js"
HARNESS = PROJECT_ROOT / "tests" / "js" / "shared_helpers_smoke.mjs"

# Files whose local getCSRFToken / escapeHtml copy was consolidated in this
# change. Each now imports the canonical helper from core/utils.js.
CONSOLIDATED_CSRF = [
    "pages/category-words-page.js",
    "pages/import-export-page.js",
    "pages/keyword-detail-page.js",
    "pages/keywords-list-page.js",
    "pages/saved-searches-page.js",
    "pages/side-detail-page.js",
    "pages/source-detail-page.js",
    "pages/sources-list-page.js",
    "pages/users-page.js",
    "pages/word-detail-page.js",
]
CONSOLIDATED_ESCAPE = [
    "modules/modals/modal-manager.js",
    "pages/category-words-page.js",
    "pages/concurrency-dashboard-page.js",
    "pages/file-classification-page.js",
    "pages/search-advanced-page.js",
    "pages/search-page.js",
    "pages/side-categories-keywords-page.js",
    "pages/source-categories-keywords-page.js",
]

# Files still defining a local copy, with the reason each is deferred.
ALLOWED_LOCAL_ESCAPE = {
    # classic <script> in templates/Category/categories_list.html: cannot import
    "pages/categories-list-page.js",
    # `value || ''` / `!text` guards: numeric 0 renders blank (defect D2)
    "pages/comprehensive-dashboard-page.js",
    "pages/path-analysis-page.js",
    "pages/map-viewer-page.js",
    # other variants; to be reviewed per page
    "modules/analyst-classify.js",
    "modules/content-formatter.js",
    "modules/core/status.js",
    "modules/ui/file-actions.js",
    "modules/ui/multi-select-dropdown.js",
    "modules/ui/progress-tracker.js",
    "modules/ui/unified-table.js",
    "pages/email-words-page.js",
    "pages/file-detail-page.js",
    "pages/job-detail-page.js",
    "pages/keywords-list-page.js",
    "pages/users-page.js",
}
ALLOWED_LOCAL_CSRF = {
    "pages/categories-list-page.js",  # classic script, see above
}


def _node() -> str | None:
    return shutil.which("node")


def _defines(name: str) -> set[str]:
    pattern = re.compile(r"^\s*(export\s+)?(async\s+)?function\s+" + name + r"\s*\(", re.M)
    out = set()
    for path in JS_ROOT.rglob("*.js"):
        if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            out.add(path.relative_to(JS_ROOT).as_posix())
    return out


def test_the_contract_harness_passes():
    node = _node()
    if node is None:
        pytest.skip("node is not installed")
    proc = subprocess.run([node, str(HARNESS)], cwd=str(PROJECT_ROOT),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "shared helper checks passed" in proc.stdout


@pytest.mark.parametrize("relpath", sorted(set(CONSOLIDATED_CSRF) | set(CONSOLIDATED_ESCAPE)))
def test_consolidated_file_imports_the_canonical_helper(relpath):
    source = (JS_ROOT / relpath).read_text(encoding="utf-8")
    for name in (("getCSRFToken",) if relpath in CONSOLIDATED_CSRF else ()) + \
                (("escapeHtml",) if relpath in CONSOLIDATED_ESCAPE else ()):
        assert not re.search(r"^\s*(async\s+)?function\s+" + name + r"\s*\(", source, re.M), \
            f"{relpath} still defines a local {name}"
        imports = re.findall(r"import\s*\{([^}]*)\}\s*from\s*['\"]([^'\"]*core/utils\.js)['\"]", source)
        names = {n.strip() for block, _ in imports for n in block.split(",")}
        assert name in names, f"{relpath} does not import {name} from core/utils.js"


@pytest.mark.parametrize("relpath", sorted(set(CONSOLIDATED_CSRF) | set(CONSOLIDATED_ESCAPE)))
def test_consolidated_file_parses_as_a_module(relpath):
    node = _node()
    if node is None:
        pytest.skip("node is not installed")
    proc = subprocess.run([node, "--experimental-default-type=module", "--check",
                           str(JS_ROOT / relpath)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_no_new_local_escape_html_copies():
    found = {p for p in _defines("escapeHtml") if p != "modules/core/utils.js"}
    new = found - ALLOWED_LOCAL_ESCAPE
    assert not new, f"new local escapeHtml copies (import core/utils.js instead): {sorted(new)}"


def test_no_new_local_get_csrf_token_copies():
    found = {p for p in _defines("getCSRFToken") if p != "modules/core/utils.js"}
    new = found - ALLOWED_LOCAL_CSRF
    assert not new, f"new local getCSRFToken copies (import core/utils.js instead): {sorted(new)}"


def test_canonical_helpers_are_defined_in_utils():
    text = UTILS.read_text(encoding="utf-8")
    assert "export function escapeHtml(" in text
    assert "export function getCSRFToken(" in text
