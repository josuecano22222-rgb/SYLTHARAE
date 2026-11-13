"""Integration: the operator learns about an unseeded gazetteer early.

``POST /api/places`` answers 503 when the gazetteer has no feature categories,
and that stays the last line of defence. It is a late line: the reader has
already chosen *Add place* and filled a form by the time the application says
its seed is missing. ``/health`` is polled from the first minute an instance is
up, so it is the cheapest place to say it - and it says it without naming a
table, a file or an exception.

Two states are measured here:

* a database with the migrations applied and no seed  -> ``not-seeded``
* the same database with one active place            -> ``ok``
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration


@pytest.fixture()
def uncached(monkeypatch):
    """Health must not answer from a cache while this test changes the seed."""
    monkeypatch.setenv("HEALTH_GAZETTEER_CACHE_SECONDS", "0")
    from Api.routes import health

    health._gazetteer_cache.update(at=0.0, value=None)
    yield
    health._gazetteer_cache.update(at=0.0, value=None)


def _db(app):
    from database import get_db_config

    cfg = get_db_config()
    return psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                            password=cfg["password"], dbname=cfg["database"])


def test_health_reports_an_unseeded_gazetteer(client, pg_db, uncached):
    payload = client.get("/health").get_json()
    assert payload["checks"]["gazetteer"] == "not-seeded"
    # An unseeded gazetteer is a degraded feature, not a dead service: the
    # endpoint stays 200 so a load balancer does not take the instance out.
    assert payload["status"] == "healthy"


def test_health_reports_a_seeded_gazetteer(client, pg_db, uncached):
    from settings import get_settings  # noqa: F401  (ensures config is loaded)

    conn = _db(None)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM categorys ORDER BY id LIMIT 1")
            row = cur.fetchone()
            if row is None:
                # categorys hang off words (a category is a named word), so a
                # place needs both.
                cur.execute(
                    "INSERT INTO words (word) VALUES ('Health Test')"
                    " ON CONFLICT (word) DO UPDATE SET word = EXCLUDED.word"
                    " RETURNING id")
                word_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO categorys (word_id, parent_id) VALUES (%s, NULL)"
                    " RETURNING id", (word_id,))
                category_id = cur.fetchone()[0]
            else:
                category_id = row[0]
            cur.execute(
                "INSERT INTO geo_places (label, category_id, country_codes)"
                " VALUES ('Health Probe Place', %s, '{}')"
                " ON CONFLICT (label, category_id, country_codes) DO NOTHING",
                (category_id,))
        conn.commit()
    finally:
        conn.close()

    payload = client.get("/health").get_json()
    assert payload["checks"]["gazetteer"] == "ok"


def test_the_answer_carries_no_schema_or_path(client, pg_db, uncached):
    body = client.get("/health").get_data(as_text=True)
    for fragment in ("geo_places", "categorys", "psycopg2", "Traceback",
                     "/home/", "SELECT "):
        assert fragment not in body, f"/health leaks {fragment!r}"
