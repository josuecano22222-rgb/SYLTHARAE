"""``/health`` must answer quickly, and must not name the driver.

Measured on a stopped PostgreSQL (round 4, `evidence_scale/failure_injection.json`):
the first probe returned 503 in 4.6 ms and named ``OperationalError`` in the
body, and every probe after it hung for longer than the client's 15 s timeout —
a load balancer that asks "are you healthy?" got no answer at all and a body
that leaked the driver's exception class.

What this file pins down:

* a failing database probe is reported as a fixed ``unavailable`` string, never
  as an exception class name, message, host or SQL fragment;
* the endpoint answers within a bounded time even when the driver does not
  (the probe runs on a worker thread under a deadline);
* a healthy database still yields 200 with ``database: ok``.
"""

from __future__ import annotations

import time

import pytest
from flask import Flask


@pytest.fixture()
def health_app(monkeypatch):
    import concurrent.futures

    import Api.routes.health as health_module

    # a fresh worker pool per test: a probe left hanging by one test must not
    # occupy the workers of the next one
    monkeypatch.setattr(
        health_module, "_probe_executor",
        concurrent.futures.ThreadPoolExecutor(max_workers=2,
                                             thread_name_prefix="health-test"))
    health_bp, _local = health_module.health_bp, health_module._local
    app = Flask(__name__)
    app.register_blueprint(health_bp)
    app.config["TESTING"] = True
    # a connection left in the thread-local by an earlier test would mask the
    # connect path this file is about
    _local.health_conn = None
    yield app
    _local.health_conn = None


class _FakeCursor:
    def execute(self, *_args, **_kwargs):
        return None

    def fetchone(self):
        return (1,)

    def close(self):
        return None


class _FakeConnection:
    closed = 0

    def cursor(self):
        return _FakeCursor()

    def close(self):
        self.closed = 1


def _patch_connect(monkeypatch, factory):
    import psycopg2

    monkeypatch.setattr(psycopg2, "connect", factory)


def test_a_refused_connection_is_reported_without_naming_the_driver(health_app, monkeypatch):
    import psycopg2

    def refuse(**_kwargs):
        raise psycopg2.OperationalError(
            'could not connect to server: No such file or directory '
            'Is the server running locally on /tmp/sylth_pg_data/.s.PGSQL.5432?')

    _patch_connect(monkeypatch, refuse)

    started = time.perf_counter()
    response = health_app.test_client().get("/health")
    elapsed = time.perf_counter() - started
    body = response.get_data(as_text=True)

    assert response.status_code == 503
    assert response.get_json()["status"] == "degraded"
    assert response.get_json()["checks"]["database"] == "unavailable"
    for leak in ("OperationalError", "psycopg2", "PGSQL", "could not connect",
                 "No such file"):
        assert leak not in body
    assert elapsed < 2.5


def test_a_probe_that_never_returns_still_answers_503_in_time(health_app, monkeypatch):
    def hang(**_kwargs):
        time.sleep(3)
        raise AssertionError("the probe should have been abandoned by the deadline")

    _patch_connect(monkeypatch, hang)

    started = time.perf_counter()
    response = health_app.test_client().get("/health")
    elapsed = time.perf_counter() - started

    assert response.status_code == 503
    assert response.get_json()["checks"]["database"] == "unavailable"
    # the deadline is a couple of seconds; 10 s would mean the request waited
    # for the driver, which is what used to happen
    assert elapsed < 10


def test_a_healthy_database_is_still_reported_as_ok(health_app, monkeypatch):
    _patch_connect(monkeypatch, lambda **_kwargs: _FakeConnection())

    response = health_app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "healthy"
    assert response.get_json()["checks"]["database"] == "ok"
