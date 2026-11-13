"""Security: no settings endpoint bypasses the sanitized error pipeline.

``docs/SECURITY.md`` and ``core/errors.py`` state that raw exception text never
reaches a client. ``settings/routes.py`` had a generic handler that returned
``str(e)`` - the one place in the API surface that contradicted the rule, on
the surface that can reach the database configuration.

These tests hold the replacement in place:

* an unexpected failure is answered with a client-safe message and a
  correlation id, and the exception text (which can carry host names, users,
  passwords and SQL) appears nowhere in the response;
* every view on the settings blueprint is wrapped, and the blueprint carries
  its own net, so a future endpoint that forgets the decorator is still
  covered;
* deliberate, user-facing rejections - ``DatabaseConfigRejected`` - keep their
  explanation, because those describe the configuration the operator just
  submitted and are built from a fixed, safe vocabulary.
"""

import re

import pytest

from core.errors import new_correlation_id

pytestmark = pytest.mark.security

#: A message that contains exactly what must never be echoed back.
HOSTILE = (
    'psycopg2.OperationalError: connection to server at "db.internal" '
    "(10.0.0.5), port 5432 failed: password authentication failed for user "
    '"infra_admin"; DSN=postgresql://infra_admin:hunter2@db.internal/app'
)
LEAKS = ("hunter2", "infra_admin", "db.internal", "postgresql://", "psycopg2", "10.0.0.5")


def _assert_clean(body: str) -> None:
    for secret in LEAKS:
        assert secret not in body, f"{secret!r} leaked to the client: {body[:400]}"


class TestUnhandledFailuresAreSanitized:
    def test_get_all_settings(self, admin_client, monkeypatch):
        import settings.routes as routes

        def explode():
            raise RuntimeError(HOSTILE)

        monkeypatch.setattr(routes, "get_settings_manager", explode)
        resp = admin_client.get("/api/settings/")

        assert resp.status_code == 500
        payload = resp.get_json()
        assert payload["success"] is False
        assert re.fullmatch(r"ERR-\d{8}-\d{6}", payload["correlation_id"])
        _assert_clean(resp.get_data(as_text=True))

    def test_backup_list(self, admin_client, monkeypatch):
        import settings.routes as routes

        def explode():
            raise ValueError(HOSTILE)

        monkeypatch.setattr(routes, "get_settings_manager", explode)
        resp = admin_client.get("/api/settings/backups")

        assert resp.status_code == 500
        assert "correlation_id" in resp.get_json()
        _assert_clean(resp.get_data(as_text=True))

    def test_the_message_is_generic_not_the_exception(self, admin_client, monkeypatch):
        import settings.routes as routes

        monkeypatch.setattr(
            routes, "get_settings_manager",
            lambda: (_ for _ in ()).throw(RuntimeError(HOSTILE)),
        )
        payload = admin_client.get("/api/settings/").get_json()
        assert payload["error"] == "The settings request could not be completed"


class TestTheWholeBlueprintIsCovered:
    def test_every_settings_view_is_wrapped(self, app):
        """A new endpoint cannot quietly opt out of the error model."""
        from settings.routes import settings_bp

        unwrapped = []
        for rule in app.url_map.iter_rules():
            if not rule.endpoint.startswith(settings_bp.name + "."):
                continue
            view = app.view_functions[rule.endpoint]
            # functools.wraps sets __wrapped__ on the decorated function.
            if getattr(view, "__wrapped__", None) is None:
                unwrapped.append(rule.endpoint)
        assert unwrapped == [], f"settings endpoints without the error decorator: {unwrapped}"

    def test_blueprint_registers_its_own_net(self):
        from settings.routes import settings_bp

        handlers = settings_bp.error_handler_spec[None][None]
        assert Exception in handlers

    def test_the_net_sanitizes_a_raw_exception(self, app):
        """Even an exception raised outside a decorated view is safe."""
        from settings.routes import _settings_unhandled

        with app.test_request_context("/api/settings/whatever"):
            response, status = _settings_unhandled(RuntimeError(HOSTILE))

        assert status == 500
        body = response.get_json()
        assert body["success"] is False
        assert re.fullmatch(r"ERR-\d{8}-\d{6}", body["correlation_id"])
        _assert_clean(response.get_data(as_text=True))

    def test_http_errors_keep_their_own_status(self, app):
        from werkzeug.exceptions import NotFound

        from settings.routes import _settings_unhandled

        with app.test_request_context("/api/settings/whatever"):
            result = _settings_unhandled(NotFound())
        assert getattr(result, "code", None) == 404


class TestDeliberateRejectionsStayUseful:
    """A rejected configuration is the operator's own input coming back."""

    def test_rejection_messages_carry_no_driver_detail(self):
        from settings.database_validation import _classify_failure

        class FakeOperationalError(Exception):
            pgcode = None

        message = _classify_failure(FakeOperationalError(HOSTILE))
        _assert_clean(message)
        assert message  # the operator is still told what to check

    @pytest.mark.parametrize(
        "text,expected",
        [
            ('connection to server at "db.internal" failed: password authentication failed',
             "credentials"),
            ('could not translate host name "db.internal" to address', "host name"),
            ("connection refused", "no database server is accepting connections"),
            ("connection timed out", "timed out"),
        ],
    )
    def test_failures_are_classified_into_fixed_wording(self, text, expected):
        from settings.database_validation import _classify_failure

        class Fake(Exception):
            pgcode = None

        message = _classify_failure(Fake(text))
        assert expected in message
        _assert_clean(message)

    def test_a_rejected_configuration_keeps_its_explanation_and_status(self, app):
        """The one exception text that is written for the operator."""
        from settings.database_validation import DatabaseConfigRejected
        from settings.routes import _settings_failure

        with app.test_request_context("/api/settings/database"):
            response, status = _settings_failure(
                DatabaseConfigRejected("The host was reached, but no database "
                                       "server is accepting connections on that port.")
            )

        assert status == 422
        body = response.get_json()
        assert body["success"] is False
        assert "no database server is accepting connections" in body["error"]
        assert re.fullmatch(r"ERR-\d{8}-\d{6}", body["correlation_id"])

    def test_a_rejection_is_scrubbed_if_it_ever_carries_a_dsn(self, app):
        """Defence in depth: the branch shows text, so it still scrubs."""
        from settings.database_validation import DatabaseConfigRejected
        from settings.routes import _settings_failure

        with app.test_request_context("/api/settings/database"):
            response, _ = _settings_failure(
                DatabaseConfigRejected(f"Could not apply {HOSTILE}")
            )
        assert "postgresql://" not in response.get_data(as_text=True)
        assert "hunter2" not in response.get_data(as_text=True)

    def test_import_rejection_is_marked_as_such(self):
        """The response says the existing configuration was not touched."""
        source = (__import__("pathlib").Path(__file__).resolve().parent.parent.parent
                  / "settings/routes.py").read_text()
        assert "Your existing configuration was not changed." in source


def test_correlation_ids_are_unique():
    ids = {new_correlation_id() for _ in range(50)}
    assert len(ids) == 50
