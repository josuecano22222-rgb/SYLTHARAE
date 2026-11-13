"""Every URL the front-end fetches must exist in the application's url map.

A module that fetches a path nobody serves fails only at the moment a user
clicks the button: the network 404s, the JS falls back to its "success" branch
because the response is not JSON, and the UI reports "Relationship added
successfully" while nothing was written. ``saveWordsCategorys`` posted to
``/api/words-categorys`` while the route is ``/api/words-categorys/add``.

The check is a static one, so it covers every page without a browser.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.unit

URL_CALL = re.compile(
    r"(?:fetch|apiCall|apiFetch|apiPost|apiGet|getJSON|axios\.get|axios\.post)"
    r"\s*\(\s*[`'\"](/[^`'\"]*)[`'\"]")


def _routes(app):
    from tools.audit.check_frontend_wiring import normalize

    return {normalize(re.sub(r"<[^>]+>", ":x", r.rule))
            for r in app.url_map.iter_rules()}


def _js_urls():
    for path in sorted((ROOT / "static" / "js").rglob("*.js")):
        if ".min." in path.name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in URL_CALL.finditer(text):
            yield path, text[:m.start()].count("\n") + 1, m.group(1)


def test_every_fetched_path_is_served():
    from apps.web.app import app
    from tools.audit.check_frontend_wiring import matches, normalize

    patterns = {p: {"GET", "POST"} for p in _routes(app)}
    missing = []
    for path, line, url in _js_urls():
        if not url.startswith("/") or url.startswith("//"):
            continue
        if "${" in url or "{{" in url:
            continue
        # ``'/api/file/' + id`` - the identifier is concatenated at the call
        # site, so a literal that ends with ``/`` is a prefix of a real route.
        # A literal without the trailing slash is a complete path: that is the
        # difference between the correct call and the broken one this test
        # exists for.
        concatenated = url.endswith("/")
        url = normalize(url)
        if url.startswith("/static/"):
            continue
        if concatenated and any(p.startswith(url) for p in patterns):
            continue
        if not matches(url, patterns):
            missing.append(f"{path.relative_to(ROOT)}:{line} -> {url}")
    assert not missing, "the front-end fetches paths that no route serves:\n  " + \
        "\n  ".join(missing)
