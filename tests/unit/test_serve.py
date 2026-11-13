"""apps/web/serve.py: the opt-in production server and its fallbacks."""

import sys
import types

import pytest

from apps.web import serve as serving


class FakeApp:
    def __init__(self):
        self.ran = None

    def run(self, **kwargs):
        self.ran = kwargs


@pytest.fixture
def fake_waitress(monkeypatch):
    calls = {}
    module = types.ModuleType("waitress")
    module.serve = lambda app, **kwargs: calls.update(kwargs, app=app)
    monkeypatch.setitem(sys.modules, "waitress", module)
    return calls


def test_default_is_the_builtin_server(monkeypatch):
    monkeypatch.delenv("WSGI_SERVER", raising=False)
    app = FakeApp()
    assert serving.serve(app, "0.0.0.0", 5000) == "flask"
    assert app.ran == {"debug": False, "host": "0.0.0.0", "port": 5000, "threaded": True}


def test_waitress_when_selected(monkeypatch, fake_waitress):
    monkeypatch.setenv("WSGI_SERVER", "waitress")
    monkeypatch.delenv("WAITRESS_THREADS", raising=False)
    app = FakeApp()
    assert serving.serve(app, "127.0.0.1", 8080) == "waitress"
    assert fake_waitress["app"] is app and app.ran is None
    assert fake_waitress["threads"] == serving.DEFAULT_WAITRESS_THREADS
    assert (fake_waitress["host"], fake_waitress["port"]) == ("127.0.0.1", 8080)
    # ProxyFix (TRUSTED_PROXY_COUNT) decides trust; Waitress must not strip
    # the headers before it sees them.
    assert fake_waitress["clear_untrusted_proxy_headers"] is False


@pytest.mark.parametrize("value,expected", [("64", 64), ("1", 4), ("junk", 32)])
def test_thread_setting_is_bounded(monkeypatch, fake_waitress, value, expected):
    monkeypatch.setenv("WSGI_SERVER", "waitress")
    monkeypatch.setenv("WAITRESS_THREADS", value)
    serving.serve(FakeApp(), "h", 1)
    assert fake_waitress["threads"] == expected


def test_missing_waitress_falls_back(monkeypatch):
    monkeypatch.setenv("WSGI_SERVER", "waitress")
    monkeypatch.setitem(sys.modules, "waitress", None)  # import raises ImportError
    app = FakeApp()
    assert serving.serve(app, "h", 1) == "flask"
    assert app.ran is not None


def test_debug_always_uses_the_builtin_server(monkeypatch, fake_waitress):
    monkeypatch.setenv("WSGI_SERVER", "waitress")
    app = FakeApp()
    assert serving.serve(app, "h", 1, debug=True) == "flask"
    assert fake_waitress == {}


def test_unknown_value_means_builtin(monkeypatch):
    monkeypatch.setenv("WSGI_SERVER", "gunicorn")
    assert serving.selected_server() == "flask"
