"""Measure the keyness report (step 16) on the synthetic perf corpus.

    python tools/perf/keyness_perf.py <pgserver-dir> [contents] [words_per_content]

Uses the corpus of tools/perf/signal_query_perf.py (``syltharae_perf``) and
adds word frequencies once: a vocabulary of 20,000 words and
``words_per_content`` distinct words per content, drawn with a strong skew
(a few very frequent words, a long tail), counts 1-7, plus one NULL-count
row per 100 contents. Then times, with the real runner:

1. each keyness dataset query alone (``EXPLAIN (ANALYZE)`` execution time) for
   a narrow selection (one source of 20, ~5 % of the contents) and a broad one
   (half the sources);
2. a complete ``term_keyness@1`` run (submit + execute: both datasets in one
   snapshot, the analysis with its SQL/reference cross-check, storage) -
   median of 3, narrow selection.

Synthetic data measures query shape and scaling, not a real vocabulary. The
cost is dominated by aggregating ``words_hashs`` over every visible content
(the reference corpus), so it grows with the corpus, not with the selection.
"""

import statistics
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

VOCABULARY = 20_000


def main(pg_dir: str, contents: int = 20_000, per_content: int = 100) -> None:
    import psycopg2

    from signal_query_perf import load_corpus

    cfg, first_source = load_corpus(pg_dir, contents)

    def connect():
        return psycopg2.connect(host=cfg["host"], port=cfg["port"], user="postgres",
                                dbname=cfg["database"])

    from core.criteria.compiler import AccessScope
    from core.reporting import REGISTRY
    from database.migration_runner import run_migrations
    from services.reporting import runs

    conn = connect()
    run_migrations(conn)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM words_hashs")
        if cur.fetchone()[0] == 0:
            started = time.perf_counter()
            cur.execute("INSERT INTO words (word) SELECT 'pw' || g FROM generate_series(1, %s) g"
                        " ON CONFLICT (word) DO NOTHING", (VOCABULARY,))
            cur.execute("SELECT min(id) FROM words WHERE word LIKE 'pw%%'")
            base = cur.fetchone()[0]
            # Skewed draw: index = V * u^3 for a deterministic pseudo-uniform u.
            cur.execute("""
                INSERT INTO words_hashs (hash_id, word_id, word_count, position_indexer)
                SELECT h.id, %s + floor(%s * power(((h.id * 0.6180339887 + k * 0.4142135623)
                         - floor(h.id * 0.6180339887 + k * 0.4142135623)), 3))::int,
                       CASE WHEN k = 1 AND h.id %% 100 = 0 THEN NULL ELSE 1 + (h.id + k) %% 7 END,
                       ''::bytea
                FROM hashs h CROSS JOIN generate_series(1, %s) k
                ON CONFLICT (hash_id, word_id) DO NOTHING
            """, (base, VOCABULARY, per_content))
            cur.execute("ANALYZE words_hashs")
            cur.execute("ANALYZE words")
            print(f"loaded word frequencies in {time.perf_counter() - started:.1f} s")
        cur.execute("SELECT count(*), count(DISTINCT hash_id) FROM words_hashs")
        rows, hashes = cur.fetchone()
        cur.execute("SELECT count(*) FROM paths")
        paths = cur.fetchone()[0]
        cur.execute("SELECT id FROM sources WHERE name LIKE 'perf_src_%%' ORDER BY id")
        sources = [r[0] for r in cur.fetchall()]
        name = f"perf_{uuid.uuid4().hex[:6]}"
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x',"
                    " 'analyst') RETURNING id", (name,))
        uid = cur.fetchone()[0]
    print(f"corpus: {paths} files, {hashes} contents with words, {rows} words_hashs rows")
    user = SimpleNamespace(id=uid, role="analyst", username=name,
                           has_role=lambda *roles: "analyst" in roles)
    report = REGISTRY.report("term_keyness")
    selections = {"narrow (1 of 20 sources)": {"sources": [first_source]},
                  "broad (10 of 20 sources)": {"sources": sources[:10]}}

    for label, criteria in selections.items():
        values = report.normalize_parameters({"criteria": criteria})
        for key in report.datasets:
            bound = REGISTRY.dataset(key).bind(values, AccessScope.unrestricted())
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 0")
                cur.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + bound.sql, bound.params)
                plan = cur.fetchone()[0][0]
            conn.rollback()
            print(f"1. {label}: {key} executes in {plan['Execution Time']:.0f} ms")

    timings, out = [], None
    for _ in range(3):
        started = time.perf_counter()
        sub = runs.submit_run(conn, user=user, report_id="term_keyness",
                              parameters={"criteria": selections["narrow (1 of 20 sources)"]})
        out = runs.execute_run(conn, sub["id"])
        timings.append((time.perf_counter() - started) * 1000)
    assert out["status"] == "completed", out
    [analysis] = runs.get_run(conn, sub["id"], user=user)["analyses"]
    m = analysis["measures"]
    print(f"2. full run (narrow): median {statistics.median(timings):.0f} ms "
          f"(runs {[round(t) for t in timings]}); state={analysis['state']}, "
          f"target {m.get('target_tokens')} / reference {m.get('reference_tokens')} words, "
          f"significant={m.get('significant_terms')}, unknown rows={m.get('unknown_count_rows')}, "
          f"listed={len(analysis['rows'])}")
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20_000,
         int(sys.argv[3]) if len(sys.argv) > 3 else 100)
