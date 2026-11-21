"""The dashboard reports a thread-only mode instead of an error stub.

``concurrency.manager_start`` bounds the start of ``multiprocessing.Manager()``,
which turns a hung request into a reported failure. That failure used to be the
*whole* answer for the affected card: ``{"status": "error", "error": "Internal
error (ERR-…)"}`` - a reference an operator can correlate with the log and
nothing else.

The two managers that need a multiprocessing manager cannot exist without one,
but the thread and async managers do not need one at all, and the numbers they
carry are real. So the endpoint now answers with what it has:

* the thread and async cards, live;
* ``unavailable`` for the process and pool cards, with a fixed sentence saying
  why and a machine-readable ``reason_code``;
* ``mode: "threads-only"``, ``degraded: true``, and the measured worker capacity
  of the host (the same number the ingestion path asks the resource coordinator
  for);
* the per-manager list endpoints answer ``available: false`` with the same
  sentence instead of a 500.

The page side is tests/js/concurrency_fallback_smoke.mjs.
"""

from __future__ import annotations

import pytest

UNAVAILABLE_CODE = "multiprocessing_unavailable"


def _refuse(*_args, **_kwargs):
    raise TimeoutError("the multiprocessing manager did not start within 5s")


@pytest.fixture()
def thread_only(monkeypatch):
    """The hub with the two multiprocessing-backed managers refusing to build."""
    from concurrency import hub

    monkeypatch.setattr(type(hub), "process_mgr", property(_refuse))
    monkeypatch.setattr(type(hub), "pool_mgr", property(_refuse))
    return hub


def test_the_metrics_answer_reports_the_thread_only_mode(admin_client, thread_only):
    payload = admin_client.get("/concurrency/api/metrics").get_json()
    assert payload["success"] is True
    assert payload["mode"] == "threads-only"
    assert payload["degraded"] is True
    assert payload["unavailable"] == ["pool_manager", "process_manager"]


def test_the_thread_figures_are_real_not_stubbed(admin_client, thread_only):
    payload = admin_client.get("/concurrency/api/metrics").get_json()
    thread_card = payload["metrics"]["thread_manager"]
    assert thread_card, "the thread manager must still be reported"
    assert thread_card.get("status") in ("active", "inactive")
    # The keys the page renders, so the card is complete rather than a stub.
    for key in ("active_threads", "total_threads", "completed", "errors"):
        assert key in thread_card, key
    assert payload["metrics"]["async_manager"]


def test_an_unavailable_manager_explains_itself_without_leaking(admin_client, thread_only):
    payload = admin_client.get("/concurrency/api/metrics").get_json()
    for key in ("process_manager", "pool_manager"):
        card = payload["metrics"][key]
        assert card["status"] == "unavailable", key
        assert card["reason_code"] == UNAVAILABLE_CODE, key
        assert "multiprocessing manager could not be started" in card["reason"], key
        # The disclosure rule: no exception text, no error reference standing in
        # for an explanation.
        assert "TimeoutError" not in card["reason"], key
        assert "5s" not in card["reason"], key


def test_the_host_capacity_is_measured(admin_client, thread_only):
    payload = admin_client.get("/concurrency/api/metrics").get_json()
    capacity = payload["metrics"]["worker_capacity"]
    assert isinstance(capacity.get("recommended_workers"), int)
    assert capacity["recommended_workers"] >= 1


def test_a_healthy_host_still_reports_the_full_mode(admin_client, monkeypatch):
    from concurrency import hub

    class _Fake:
        def get_statistics(self):
            return {"running": 0, "total": 0, "pools": {}}

    for name in ("thread_mgr", "async_mgr", "process_mgr", "pool_mgr"):
        monkeypatch.setattr(type(hub), name, property(lambda self, _make=_Fake: _make()))

    payload = admin_client.get("/concurrency/api/metrics").get_json()
    assert payload["mode"] == "full"
    assert payload["degraded"] is False
    assert payload["unavailable"] == []
    assert "worker_capacity" not in payload["metrics"]


def test_the_process_list_says_it_is_unavailable_instead_of_failing(admin_client, thread_only):
    response = admin_client.get("/concurrency/api/processes")
    assert response.status_code == 200, response.status_code
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["available"] is False
    assert payload["processes"] == []
    assert payload["reason_code"] == UNAVAILABLE_CODE


def test_the_pool_list_says_it_is_unavailable_instead_of_failing(admin_client, thread_only):
    response = admin_client.get("/concurrency/api/pools")
    assert response.status_code == 200, response.status_code
    payload = response.get_json()
    assert payload["available"] is False
    assert payload["pools"] == []
    assert payload["reason_code"] == UNAVAILABLE_CODE


def test_the_thread_list_is_unaffected(admin_client, thread_only):
    response = admin_client.get("/concurrency/api/threads")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert isinstance(payload["threads"], list)
