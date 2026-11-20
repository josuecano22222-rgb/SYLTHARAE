"""Starting the multiprocessing manager must not be able to hang a request.

``concurrency.process_manager.ProcessManager.__init__`` calls
``multiprocessing.Manager()``, which blocks until the manager process answers.
The call is reached lazily from ``/concurrency/api/metrics`` (``hub.process_mgr``
inside a GET), and ``/concurrency/`` is a page an administrator can open from the
monitoring dashboard.

On a host where that start cannot succeed - a container without ``/dev/shm``, an
exhausted semaphore or descriptor budget, a fork the seccomp profile refuses -
the call does not fail, it blocks forever, and the worker serving the request
blocks with it. That is what happened here: a full unit run stopped making
progress at 49% with three processes asleep in ``Server.serve_forever`` inside a
test that was only asking pages for their HTML.

The start is now bounded. These tests pin the three outcomes: success, a start
that raises, and a start that never answers.
"""

from __future__ import annotations

import time

import pytest

from concurrency import process_manager as pm


class _FakeDict:
    def __init__(self, *args, **kwargs):
        self._data = dict(*args, **kwargs)

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def keys(self):
        return self._data.keys()


class _FakeManager:
    def dict(self, *args, **kwargs):
        return _FakeDict(*args, **kwargs)


def test_a_manager_that_starts_is_returned_unchanged(monkeypatch):
    fake = _FakeManager()
    monkeypatch.setattr(pm.mp, "Manager", lambda: fake)
    assert pm.start_manager_with_timeout(0.2) is fake


def test_a_manager_that_raises_reports_why(monkeypatch):
    def refuse():
        raise OSError("no space left on device")

    monkeypatch.setattr(pm.mp, "Manager", refuse)
    with pytest.raises(RuntimeError) as caught:
        pm.start_manager_with_timeout(0.2)
    assert "no space left on device" in str(caught.value)


def test_a_manager_that_never_answers_times_out(monkeypatch):
    """The measure: the call returns an error, it does not wait for the manager."""
    def sleep_forever():
        time.sleep(30)

    monkeypatch.setattr(pm.mp, "Manager", sleep_forever)
    started = time.monotonic()
    with pytest.raises(TimeoutError) as caught:
        pm.start_manager_with_timeout(0.05)
    elapsed = time.monotonic() - started
    assert "did not start within" in str(caught.value)
    assert elapsed < 2.0, f"the bounded start took {elapsed:.1f}s"


def test_the_process_manager_carries_the_timeout_into_the_start(monkeypatch):
    """ProcessManager does not have its own copy of the blocking call."""
    seen = {}

    def fake_start(timeout):
        seen["timeout"] = timeout
        raise TimeoutError("stop here")

    monkeypatch.setattr(pm, "start_manager_with_timeout", fake_start)
    with pytest.raises(TimeoutError):
        pm.ProcessManager(manager_start_timeout=0.01)
    assert seen["timeout"] == 0.01


def test_the_metrics_route_reports_a_dead_manager_instead_of_crashing(admin_client, monkeypatch):
    """The endpoint answers with a degraded report; it does not 500 or hang.

    ``hub.process_mgr`` is what a GET reaches, so the failure a bounded start
    raises has to become a JSON answer the dashboard can show. The route already
    wraps each manager in its own try/except; this pins that the wrapper covers
    the *construction* and not only the call after it.
    """
    from concurrency import hub  # the shared ConcurrencyHub instance

    def refuse(self):
        raise TimeoutError("the multiprocessing manager did not start within 5s")

    monkeypatch.setattr(type(hub), "process_mgr", property(refuse))
    response = admin_client.get("/concurrency/api/metrics")
    assert response.status_code == 200, response.status_code
    payload = response.get_json()
    assert payload is not None, "the endpoint did not answer JSON"
    assert payload["success"] is True
    assert payload["metrics"]["process_manager"]["status"] == "error"
    # The operator gets a reference, not the exception text (the disclosure
    # rule): the dashboard can report "error" and correlate it with the log.
    assert payload["metrics"]["process_manager"]["error"]
