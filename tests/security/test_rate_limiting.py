"""API-01 regression: login brute force is rate limited (429)."""
import pytest


@pytest.fixture()
def rate_limited_app(app):
    from core.security.rate_limit import limiter

    prev_enabled = limiter.enabled
    limiter.enabled = True
    try:
        limiter.reset()
    except Exception:
        pass
    try:
        yield app
    finally:
        limiter.enabled = prev_enabled
        try:
            limiter.reset()
        except Exception:
            pass


@pytest.mark.usefixtures("rate_limited_app")
def test_login_brute_force_rate_limited(client, admin_credentials):
    username, password = admin_credentials
    codes = []
    for _ in range(12):
        resp = client.post(
            "/auth/login",
            json={"username": "nobody", "password": "wrong-password"},
        )
        codes.append(resp.status_code)
    assert 429 in codes, f"expected a 429 among {codes}"
    # Before the limit tripped, invalid credentials must have been 401s.
    assert codes[0] == 401


@pytest.mark.usefixtures("rate_limited_app")
def test_job_progress_polling_is_not_throttled_during_a_run(admin_client):
    """The panel that explains a failure must survive the run it reports on.

    The Jobs page polls the job's status and error endpoints while a run is in
    progress. Under the default 60/minute limit every poll of the error
    endpoint answered 429 for the whole duration of a production ingest (the
    log shows one 429 per poll), so the operator could not see *why* files
    failed - including the archive that reported no decoder.

    These read-only endpoints therefore carry their own, higher limit. This
    test polls them past the default budget and asserts none is refused.
    """
    for _ in range(75):
        assert admin_client.get("/api/jobs/NOPE/errors").status_code == 404
        assert admin_client.get("/api/jobs/NOPE").status_code == 404


@pytest.mark.usefixtures("rate_limited_app")
def test_the_default_limit_still_applies_to_other_endpoints(admin_client):
    """Polling endpoints are the exception, not the rule.

    ``/api/jobs`` is deliberately on INTERACTIVE_READ_LIMIT (600/minute) and
    cannot demonstrate the default, so this uses ``/api/keywords``, which keeps
    the strict default limit.
    """
    codes = [admin_client.get("/api/keywords?limit=1").status_code for _ in range(75)]
    assert 429 in codes, f"expected the default limit to still apply: {set(codes)}"


# ---------------------------------------------------------------------------
# Interactive read endpoints (data an open page fetches over and over)
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("rate_limited_app")
def test_archives_section_pagination_is_not_throttled_while_browsing(admin_client):
    """Browsing a section must not rate-limit itself out of existence.

    Reported from the running application: jumping through the archives
    explorer produced

        GET /api/archives/keywords?limit=10&sort_by=file_count&... 429

    for the sections themselves. Two things multiply here - a section page
    load fetches several endpoints at once, and a jump to page N walks the
    cursors from page 1 - so the 60/minute default refused the operator's own
    browsing. These endpoints carry an explicit, still-bounded limit.
    """
    for _ in range(75):
        resp = admin_client.get("/api/archives/keywords?limit=10")
        assert resp.status_code == 200, resp.get_data(as_text=True)[:200]


@pytest.mark.usefixtures("rate_limited_app")
def test_the_archives_explorer_can_load_every_section_in_one_burst(admin_client):
    """A single page load fetches all sections; that burst must fit too."""
    endpoints = (
        "/api/archives/categories?limit=10",
        "/api/archives/keywords?limit=10",
        "/api/archives/titles?limit=10",
        "/api/archives/sources?limit=10",
        "/api/archives/sides?limit=10",
        "/api/archives/hashs?limit=10",
        "/api/archives/geolocation?limit=10",
    )
    for _ in range(10):  # ten page changes' worth of section loads
        for endpoint in endpoints:
            assert admin_client.get(endpoint).status_code == 200, endpoint


@pytest.mark.usefixtures("rate_limited_app")
def test_the_expensive_search_endpoint_keeps_the_strict_default(admin_client):
    """Read-only is not the same as cheap: search is not in the browsing set."""
    codes = [
        admin_client.get("/api/archives/search?q=anything").status_code
        for _ in range(75)
    ]
    assert 429 in codes, f"expected the strict default on search: {set(codes)}"


@pytest.mark.usefixtures("rate_limited_app")
def test_the_file_details_modal_call_is_not_throttled_per_file(admin_client, pg_db):
    """Opening files one after another in the pop-up is normal review work."""
    for file_id in range(1, 76):
        status = admin_client.get(f"/api/file/{file_id}/details").status_code
        assert status in (200, 404), status
        assert status != 429


@pytest.mark.usefixtures("rate_limited_app")
def test_the_theme_every_page_loads_is_not_throttled_while_browsing(admin_client):
    """Each page load reads /api/settings/theme (get_theme_settings); 75 a minute pass.

    Under the default limit the live browser smoke saw 429s here, and behind
    a proxy every user of one NAT address shares that budget.
    """
    codes = {admin_client.get("/api/settings/theme").status_code for _ in range(75)}
    assert 429 not in codes, codes


@pytest.mark.usefixtures("rate_limited_app")
@pytest.mark.parametrize("path", ["/api/i18n/catalog?locale=en", "/api/notifications/stats"])
def test_the_reads_every_page_boot_makes_are_not_throttled_while_browsing(admin_client, path):
    """RATE-01 covered the theme but not the other per-page reads.

    The live browser smoke through nginx got 429 from both after ~600 page
    views from one address (the hourly default); the notification badge also
    polls every 30 s per open tab, so five idle tabs alone reach it. 75 calls
    exceed the 60/minute default, so this fails without the interactive limit.
    """
    codes = {admin_client.get(path).status_code for _ in range(75)}
    assert 429 not in codes, codes


@pytest.mark.usefixtures("rate_limited_app")
def test_a_rate_limited_sign_in_says_so_in_json(client):
    """The sign-in form parses the answer as JSON. The limiter's default 429 is
    an HTML page, so the form failed to parse it and told a locked-out user
    "An internal error occurred" (found by the browser smoke). The 429 is JSON
    for JSON callers and names the condition."""
    responses = [client.post("/auth/login", json={"username": "nobody", "password": "wrong"})
                 for _ in range(12)]
    limited = [r for r in responses if r.status_code == 429]
    assert limited, [r.status_code for r in responses]
    body = limited[-1].get_json()
    assert body == {"error": "Too many attempts. Wait a minute and try again.",
                    "code": "rate_limited"}


def test_a_rate_limited_page_request_gets_a_plain_answer(app):
    """A browser navigation is not an API caller: a plain answer, not JSON."""
    from werkzeug.exceptions import TooManyRequests

    with app.test_request_context("/sources", headers={"Accept": "text/html"}):
        response = app.make_response(app.handle_user_exception(TooManyRequests()))
    assert response.status_code == 429
    assert response.content_type.startswith("text/plain")
    assert "Too many attempts" in response.get_data(as_text=True)

    with app.test_request_context("/api/search", headers={"Accept": "text/html"}):
        response = app.make_response(app.handle_user_exception(TooManyRequests()))
    assert response.get_json()["code"] == "rate_limited", "API paths always answer JSON"


def test_the_sign_in_page_explains_a_rate_limit_it_cannot_parse():
    """Behind a proxy the 429 may still be HTML; the form keeps the status."""
    from pathlib import Path
    script = (Path(__file__).resolve().parents[2] / "static/js/pages/login-page.js").read_text(encoding="utf-8")
    assert "r.json().catch(" in script
    assert "res.status === 429 ? tr('Too many attempts. Wait a minute and try again.')" in script


def test_the_rate_limit_message_is_translated():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "translations"
    for language in ("ar", "fa", "he"):
        catalog = (root / language / "LC_MESSAGES" / "messages.po").read_text(encoding="utf-8")
        entry = catalog.split('msgid "Too many attempts. Wait a minute and try again."', 1)
        assert len(entry) == 2, language
        assert not entry[1].lstrip().startswith('msgstr ""'), f"{language}: untranslated"


@pytest.mark.usefixtures("rate_limited_app")
def test_change_password_guessing_is_rate_limited(app):
    """RES-AUTH-02: the current-password check must not be a guessing oracle."""
    import uuid
    from core.security.service import get_auth_service

    username = f"ratelimit_{uuid.uuid4().hex[:8]}"
    get_auth_service().create_user(username, "rate-limit-password-123", role="viewer")
    c = app.test_client()
    assert c.post("/auth/login", json={"username": username,
                                       "password": "rate-limit-password-123"}).status_code == 200
    codes = [c.post("/auth/change-password", json={
        "current_password": f"guess-{i}", "new_password": "another-password-456"}).status_code
        for i in range(12)]
    assert 429 in codes, codes


def test_login_rate_limit_default_and_override(monkeypatch):
    """The login limiter defaults to 10/minute; the env override exists for
    the runtime-acceptance harness (one server session signing in several
    roles) and is bounded + sanitised."""
    from Api.routes.auth import _login_rate_limit

    monkeypatch.delenv("RATE_LIMIT_LOGIN_PER_MINUTE", raising=False)
    assert _login_rate_limit() == "10 per minute"
    monkeypatch.setenv("RATE_LIMIT_LOGIN_PER_MINUTE", "120")
    assert _login_rate_limit() == "120 per minute"
    monkeypatch.setenv("RATE_LIMIT_LOGIN_PER_MINUTE", "not-a-number")
    assert _login_rate_limit() == "10 per minute"
    monkeypatch.setenv("RATE_LIMIT_LOGIN_PER_MINUTE", "0")
    assert _login_rate_limit() == "1 per minute"


@pytest.mark.usefixtures("rate_limited_app")
def test_the_system_settings_every_page_loads_is_not_throttled(admin_client):
    """Each page load reads /api/settings/system (system-settings.js).

    Same reasoning as the theme: it is an interactive read, so browsing must not
    be refused. This is the route that 403'd for analysts in the browser smoke;
    once it answers, it is read on every page, and the 60/minute default would
    have replaced a permissions error with a 429.
    """
    codes = {admin_client.get("/api/settings/system").status_code for _ in range(75)}
    assert 429 not in codes, codes
    assert 200 in codes, codes
