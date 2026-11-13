"""``/health`` must not wait on the database before the probe even runs.

Measured on the running application with PostgreSQL stopped (round 5): the
endpoint itself was fast (``X-Response-Time: 0.003``) but ``curl /health`` took
**30.0 s**, twice in a row. The time was spent before the route:
``Api/routes/common.py``'s ``before_request`` overlays the database-held
translation overrides onto every request that is not ``static``, and with the
pool exhausted that read waits out the pool timeout (30 s) and only then logs
"Translation overrides unavailable" and lets the request continue. A liveness
probe has no user-facing strings to translate, so the wait bought nothing.

The fixture below mounts the real ``register_common_routes`` hook chain and the
real health blueprint on an app of its own. The blueprint-only app in
``test_health_probe.py`` has no ``before_request`` hooks - which is why the
30 s was invisible there - and driving the application singleton
(``apps.web.app.app``) from a unit test would freeze it for every later test in
the same run.
"""

from __future__ import annotations

import time

import pytest
from flask import Flask


@pytest.fixture()
def hooked_app():
    import Api.routes.health as health_module
    from Api.routes.common import register_common_routes

    app = Flask(__name__)
    app.config.update(LANGUAGES={"en": "English"}, BABEL_DEFAULT_LOCALE="en")
    app.config["SECRET_KEY"] = "test-secret-key-not-for-production-0123456789"

    @app.route("/some-page")
    def some_page():          # a page that renders: the overlay must still run
        return "page"

    register_common_routes(app, None)
    app.register_blueprint(health_module.health_bp)
    return app


def test_health_does_not_wait_for_translation_overrides(hooked_app, monkeypatch):
    calls = []

    def blocked(locale):
        calls.append(locale)
        time.sleep(5)

    monkeypatch.setattr(
        "Api.services.translation_management.apply_runtime_translation_overrides",
        blocked)

    client = hooked_app.test_client()
    started = time.perf_counter()
    response = client.get("/health")
    elapsed = time.perf_counter() - started

    assert response.status_code in (200, 503), response.status_code
    assert elapsed < 2.0, (
        f"/health took {elapsed:.1f}s while the translation overlay was blocked;"
        " the probe has no translations to overlay and must not wait for them")
    assert calls == [], f"the translation overlay ran for /health: {calls}"


def test_other_pages_still_overlay_the_translations(hooked_app, monkeypatch):
    """The exemption is for the probe, not a quiet removal of the overlay."""
    import settings

    class _OpenGate:
        def is_interface_enabled_by_endpoint(self, endpoint):
            return True

    monkeypatch.setattr(settings, "get_interface_manager", lambda: _OpenGate())
    calls = []

    monkeypatch.setattr(
        "Api.services.translation_management.apply_runtime_translation_overrides",
        lambda locale: calls.append(locale))

    hooked_app.test_client().get("/some-page")
    assert calls, "a page request no longer overlays runtime translations"


def test_the_exemption_is_named_per_endpoint():
    from Api.routes.common import _answers_without_templates

    assert _answers_without_templates("health.health") is True
    assert _answers_without_templates("files.files") is False
    assert _answers_without_templates("auth.login_page") is False
    assert _answers_without_templates(None) is False
