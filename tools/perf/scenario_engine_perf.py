"""Measure scenario dry-runs and evaluations on the synthetic perf corpus.

    python tools/perf/scenario_engine_perf.py <pgserver-dir> [contents]

Uses the corpus of tools/perf/signal_query_perf.py (``syltharae_perf``:
``contents`` contents x 10 dated signals + 2 place signals). Times, with the
real engine (services/monitoring/scenario_engine):

1. the dry-run of a broad three-case scenario over every content (one
   REPEATABLE READ snapshot, the full report),
2. its first evaluation (every decision recorded as the baseline),
3. the steady-state re-evaluation when nothing changed (median of 5),
4. an evaluation after 1,000 contents receive an imminent high-confidence
   date (their outcome changes; the notification cap and overflow apply),
5. a narrow scenario (one source) dry-run and steady state,

and prints the plan of the dry-run report statement. Synthetic data
measures query shape and index use, not detector behaviour. Rerunning
reuses the corpus; each run creates its own owner and scenarios.
"""

import datetime
import statistics
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def definition(criteria=None):
    return {
        "criteria": criteria or {},
        "conditions": {
            "imminent": {"signals": {"signal_types": ["date_reference"]},
                         "min_confidence": "high", "event_window_days": {"from": 0, "to": 7}},
            "month": {"signals": {"signal_types": ["date_reference"]},
                      "event_window_days": {"from": 0, "to": 30}},
            "placed": {"signals": {"signal_types": ["place_mention"]}},
        },
        "cases": [
            {"id": "urgent", "when": {"all": ["imminent"]}, "outcome": "urgent"},
            {"id": "soon", "when": {"all": ["month"], "none": ["placed"]}, "outcome": "soon"},
            {"id": "soon_placed", "when": {"all": ["month", "placed"]}, "outcome": "soon"},
        ],
        "outcomes": {"urgent": {"label": "Urgent", "actions": ["notify"]},
                     "soon": {"label": "Soon", "actions": []},
                     "none": {"label": "Nothing soon", "actions": []}},
        "default_outcome": "none",
        "strategy": "first_match",
    }


def main(pg_dir: str, contents: int = 20_000) -> None:
    import psycopg2
    import psycopg2.extras

    from signal_query_perf import load_corpus

    cfg, first_source = load_corpus(pg_dir, contents)

    def connect():
        return psycopg2.connect(host=cfg["host"], port=cfg["port"], user="postgres",
                                dbname=cfg["database"])

    from database.migration_runner import run_migrations
    from services.monitoring import scenario_engine, scenarios

    conn = connect()
    run_migrations(conn)          # the perf corpus may predate m0021
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x',"
                    " 'analyst') RETURNING id", (f"perf_{uuid.uuid4().hex[:6]}",))
        owner = cur.fetchone()[0]
        cur.execute("SELECT count(*), count(DISTINCT hash_id) FROM content_signals")
        signals, with_signals = cur.fetchone()
    print(f"corpus: {signals} signals on {with_signals} contents")
    now = datetime.datetime(2026, 10, 1, 12, tzinfo=datetime.timezone.utc)

    def create(name, criteria=None):
        return scenarios.create_scenario(conn, owner_id=owner,
                                         name=f"perf {name} {uuid.uuid4().hex[:6]}",
                                         definition=definition(criteria))

    def timed(fn, *args, **kwargs):
        started = time.perf_counter()
        out = fn(*args, **kwargs)
        return (time.perf_counter() - started) * 1000, out

    broad = create("broad")
    ms, dry = timed(scenario_engine.dry_run, conn, broad["id"], requested_by=owner, now=now)
    r = dry["report"]
    print(f"1. dry-run (broad): {ms:.0f} ms, status={dry['status']}, population="
          f"{r['population']}, outcomes={r['outcomes']}, on_activation="
          f"{r['notifications_on_activation']['notifications']}")
    scenarios.activate(conn, broad["id"], user_id=owner, is_admin=False, now=now)
    ms, out = timed(scenario_engine.evaluate_scenario, conn, broad["id"], now=now)
    print(f"2. first evaluation (baseline): {ms:.0f} ms, counts={out['counts']}")
    runs = []
    for i in range(5):
        ms, out = timed(scenario_engine.evaluate_scenario, conn, broad["id"],
                        now=now + datetime.timedelta(minutes=i + 1))
        runs.append(ms)
    print(f"3. steady state, nothing changed: median {statistics.median(runs):.0f} ms"
          f" (runs {[round(x) for x in runs]}), recorded={out['counts']['recorded']},"
          f" notifications={out['counts']['notifications']}")

    with conn, conn.cursor() as cur:
        # 1,000 contents not yet urgent get an imminent high-confidence date. A
        # distinct value per run, or a rerun's signals would be duplicates.
        tag = f"perf-{uuid.uuid4().hex[:8]}-"
        cur.execute("""
            INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type, value,
                surface, char_start, char_end, language, calendar, resolution, date_from,
                date_to, evidence, dedup_key, method, confidence, confidence_basis,
                evidence_sentence, sentence_start, sentence_end)
            SELECT h.id, 'temporal', 'temporal-1.1.0', 'date_reference', %s || h.id,
                'new date', 900, 908, 'en', 'gregorian', 'absolute', DATE '2026-10-03',
                DATE '2026-10-03', '{}', md5(%s || h.id) || md5(h.id || %s),
                'day_month_year', 'high', 'basis', 'new date', 900, 908
            FROM hashs h
            WHERE NOT EXISTS (SELECT 1 FROM scenario_outcomes o WHERE o.scenario_id = %s
                              AND o.hash_id = h.id AND o.outcomes = ARRAY['urgent'])
            ORDER BY h.id LIMIT 1000""", (tag, tag, tag, broad["id"]))
        print(f"   inserted {cur.rowcount} imminent signals")
    ms, out = timed(scenario_engine.evaluate_scenario, conn, broad["id"],
                    now=now + datetime.timedelta(minutes=10))
    print(f"4. after 1,000 contents change outcome: {ms:.0f} ms, recorded="
          f"{out['counts']['recorded']}, notifications={out['counts']['notifications']}")

    narrow = create("narrow", {"sources": [first_source]})
    ms, dry = timed(scenario_engine.dry_run, conn, narrow["id"], requested_by=owner, now=now)
    print(f"5. narrow dry-run: {ms:.0f} ms, population={dry['report']['population']}")
    scenarios.activate(conn, narrow["id"], user_id=owner, is_admin=False, now=now)
    scenario_engine.evaluate_scenario(conn, narrow["id"], now=now)
    runs = [timed(scenario_engine.evaluate_scenario, conn, narrow["id"],
                  now=now + datetime.timedelta(minutes=i + 1))[0] for i in range(5)]
    print(f"   narrow steady state: median {statistics.median(runs):.0f} ms"
          f" (runs {[round(x) for x in runs]})")

    # The dry-run report statement's plan (broad scenario), rolled back.
    from types import SimpleNamespace

    from core.criteria.access import scope_for

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        row = scenarios.fetch_scenario(cur, broad["id"])
    parsed = scenarios.current_definition(row)
    decision = scenario_engine.decision_sql(
        parsed, scope_for(SimpleNamespace(id=owner, role="analyst")), now.date())
    q = scenario_engine._report_sql(decision, broad["id"], now.date())
    with conn.cursor() as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + q.sql, q.params)
        lines = [line for (line,) in cur.fetchall()]
    conn.rollback()
    print("\nplan of the dry-run report statement (broad scenario), top and totals:")
    for line in lines[:25] + ["  ..."] + lines[-3:]:
        print("  " + line)
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20_000)
