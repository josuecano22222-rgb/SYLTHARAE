"""No runtime resource may come from the network.

The offline deployment directive forbids CDN scripts, remote stylesheets,
remote fonts and other fetch-at-load resources. The interface must render
from vendored assets alone (links a user *clicks* to read documentation are
not runtime resources and stay allowed). A violation here is a regression
against offline Windows: on a disconnected machine the page would hang or
render unstyled.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"
STATIC_JS = ROOT / "static" / "js"
STATIC_CSS = ROOT / "static" / "css"

#: src=/href= to an absolute URL inside a tag. Allowed: XML namespaces and
#: schema identifiers (never fetched), and documentation/source links.
ALLOWED = re.compile(
    r"^https?://(?:www\.w3\.org|schemas\.|purl\.org|github\.com/)"
)

REMOTE_RESOURCE = re.compile(
    r"""(?:\b(?:src|href|poster|data)\s*=\s*["'])(https?://[^"']+)""",
    re.IGNORECASE,
)
CSS_REMOTE = re.compile(r"""url\(\s*["']?(https?://[^)"']+)""", re.IGNORECASE)
CSS_IMPORT = re.compile(r"""@import\s+["']?(https?://[^)"'\s;]+)""", re.IGNORECASE)
JS_REMOTE_FETCH = re.compile(
    r"""(?:fetch|XMLHttpRequest\(\)\.open|\.src\s*=)\s*\(?["'](https?://[^"']+)""",
    re.IGNORECASE,
)


def _violations(pattern, path, text):
    found = []
    for match in pattern.finditer(text):
        url = match.group(1)
        if ALLOWED.match(url):
            continue
        line = text.count("\n", 0, match.start()) + 1
        found.append(f"{path.relative_to(ROOT)}:{line} -> {url}")
    return found


def _all_violations():
    violations = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        violations += _violations(REMOTE_RESOURCE, path, text)
    for path in sorted(STATIC_CSS.rglob("*.css")):
        if "dist" in path.parts or "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        violations += _violations(CSS_REMOTE, path, text)
        violations += _violations(CSS_IMPORT, path, text)
    for path in sorted(STATIC_JS.rglob("*.js")):
        if "dist" in path.parts or "vendor" in path.parts or ".min." in path.name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        violations += _violations(JS_REMOTE_FETCH, path, text)
    return violations


@pytest.mark.unit
class TestNoRemoteRuntimeResources:
    def test_no_template_or_static_asset_points_at_the_network(self):
        violations = _all_violations()
        assert not violations, (
            "runtime resources must be vendored for offline Windows; "
            "remove or replace:\n  " + "\n  ".join(violations))

    def test_the_audit_covers_all_templates_and_static_code(self):
        assert sum(1 for _ in TEMPLATES.rglob("*.html")) > 50
        assert sum(1 for _ in STATIC_JS.rglob("*.js")) > 10
        assert sum(1 for _ in STATIC_CSS.rglob("*.css")) > 10
