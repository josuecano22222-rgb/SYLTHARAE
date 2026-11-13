"""Integration: every registry interface page renders one ``<h1>`` heading.

The interface registry promises a page per user-facing capability. A page
without a top-level heading is unannounced to a screen reader (the heading is
how the page name is conveyed) and inconsistent with its siblings, so the
contract is checked here rather than left to a manual browse:

* each registry interface whose route resolves to a parameterless URL must
  answer ``200`` for an administrator;
* the rendered HTML must contain exactly one ``<h1>`` element, carrying
  non-empty text.

Pages whose custom layout deliberately hides the heading keep it for
assistive technology (``visually-hidden``), which this test accepts: presence
and text are what the contract is about, not visual prominence.
"""

from __future__ import annotations

import re

import pytest

from core.interfaces import REGISTRY

_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")


def _interface_urls(app):
    """Map each parameterless registry page endpoint to its URL rule."""
    url_by_endpoint = {r.endpoint: r.rule for r in app.url_map.iter_rules()}
    urls = {}
    for entry in REGISTRY:
        rule = url_by_endpoint.get(entry.route)
        if not rule or "<" in rule:
            continue
        urls[rule] = entry.route
    return sorted(urls.items())


@pytest.fixture(scope="module")
def interface_urls(app):
    urls = _interface_urls(app)
    assert urls, "the registry resolved to no browsable page"
    return urls


def test_every_interface_page_has_one_h1(admin_client, interface_urls):
    """Each registry page renders exactly one non-empty top-level heading."""
    missing = []
    duplicated = []
    failed = []

    for url, endpoint in interface_urls:
        response = admin_client.get(url)
        if response.status_code != 200:
            failed.append((url, endpoint, response.status_code))
            continue
        html = response.get_data(as_text=True)
        headings = _H1.findall(html)
        if not headings:
            missing.append((url, endpoint))
        elif len(headings) > 1:
            duplicated.append((url, endpoint, len(headings)))
        elif not _TAGS.sub("", headings[0]).strip():
            missing.append((url, endpoint, "empty"))

    assert not failed, f"interface pages that did not render: {failed}"
    assert not missing, (
        "interface pages with no non-empty <h1> heading: "
        f"{missing} - add the page heading (visually-hidden is fine for "
        "custom layouts) so the page announces itself")
    assert not duplicated, (
        f"interface pages with more than one <h1>: {duplicated} - one page, "
        "one top-level heading")
