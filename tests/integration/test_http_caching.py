"""Cache-Control per response class (CACHE-01, CACHE-02).

CACHE-02: static assets were sent ``no-cache, max-age=31536000, public,
immutable`` - self-contradictory. Browsers obey ``no-cache`` and revalidated
anyway, but the one-year ``immutable`` half was a trap: most asset URLs and all
ES-module imports are unversioned, so honouring it would keep the previous
release's JavaScript in browsers for a year after an upgrade.
"""
from __future__ import annotations


def _directives(response):
    return {d.strip().split("=")[0]: d.strip() for d in response.headers.get("Cache-Control", "").split(",") if d.strip()}


def test_static_assets_are_revalidated_and_never_immutable(app):
    response = app.test_client().get("/static/js/theme-initializer.js")
    assert response.status_code == 200
    cc = _directives(response)
    assert "no-cache" in cc, response.headers.get("Cache-Control")
    assert "immutable" not in cc and "max-age" not in cc, response.headers.get("Cache-Control")
    assert response.headers.get("ETag") or response.headers.get("Last-Modified"), "nothing to revalidate against"


def test_static_revalidation_answers_304(app):
    client = app.test_client()
    etag = client.get("/static/js/theme-initializer.js").headers["ETag"]
    again = client.get("/static/js/theme-initializer.js", headers={"If-None-Match": etag})
    assert again.status_code == 304


def test_api_responses_are_never_stored(app):
    response = app.test_client().get("/api/csrf-token")
    cc = _directives(response)
    assert {"no-cache", "no-store"} <= set(cc), response.headers.get("Cache-Control")


def test_pages_are_never_stored(app):
    response = app.test_client().get("/auth/login")
    assert response.status_code == 200
    assert "no-store" in _directives(response), response.headers.get("Cache-Control")
