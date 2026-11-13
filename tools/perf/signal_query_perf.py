"""Measure Horizon / Signal Explorer queries on a synthetic corpus.

    python tools/perf/signal_query_perf.py <pgserver-dir> [contents]

Creates (or reuses) database ``syltharae_perf`` on the pgserver instance in
``<pgserver-dir>`` through the application bootstrap, bulk-loads ``contents``
content hashes (default 20,000) with one occurrence each across 20 sources and
5 sides, 10 dated temporal signals and 2 place signals per content, then
times the service calls the API makes (median of 5 after one warm-up) and
prints the plan of the main page query. Synthetic data measures query shape
and index use, not detector behaviour.
"""

import datetime
import statistics
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def load_corpus(pg_dir: str, contents: int = 20_000):
    """Create/reuse ``syltharae_perf`` with the synthetic corpus. Returns
    ``(connection settings, id of the first perf source)``. Also used by
    tools/perf/rule_engine_perf.py."""
    import os

    import pgserver
    import psycopg2

    server = pgserver.get_server(pg_dir, cleanup_mode=None)
    parsed = urllib.parse.urlparse(server.get_uri())
    host = urllib.parse.parse_qs(parsed.query).get("host", [None])[0] or parsed.hostname
    cfg = {"host": host, "port": parsed.port or 5432, "user": "postgres", "password": "",
           "database": "syltharae_perf"}
    os.environ.update({"DB_HOST": host, "DB_PORT": str(cfg["port"]), "DB_USER": "postgres",
                       "DB_PASSWORD": "", "DB_NAME": cfg["database"]})
    from database.bootstrap import bootstrap_database

    bootstrap_database(cfg)
    conn = psycopg2.connect(host=host, port=cfg["port"], user="postgres", dbname=cfg["database"])
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM content_signals")
        if cur.fetchone()[0] == 0:
            started = time.perf_counter()
            cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                        " SELECT 'perf_src_' || g, 'p', 0.5, 'p', CURRENT_DATE"
                        " FROM generate_series(1, 20) g")
            cur.execute("INSERT INTO sides (name, importance, date_creation)"
                        " SELECT 'perf_side_' || g, 0.5, CURRENT_DATE FROM generate_series(1, 5) g")
            cur.execute("INSERT INTO hashs (hash) SELECT md5('perf' || g) || md5('x' || g)"
                        " FROM generate_series(1, %s) g", (contents,))
            cur.execute("INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                        " SELECT h.id, (SELECT min(id) FROM sources) + (h.id % 20),"
                        " (SELECT min(id) FROM sides) + (h.id % 5) FROM hashs h")
            cur.execute("INSERT INTO paths (file_name, file_path, file_size, file_type, file_status,"
                        " file_date, date_creation, context_id, processing_status)"
                        " SELECT 'doc_' || hc.id || '.txt', '/perf/doc_' || hc.id || '.txt', 100,"
                        " 'txt', 'Read', DATE '2026-01-01', CURRENT_DATE, hc.id, 'processed'"
                        " FROM hash_contexts hc")
            cur.execute("""
                INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type, value,
                    surface, char_start, char_end, language, calendar, resolution, date_from,
                    date_to, text_orientation, evidence, dedup_key, method, confidence,
                    confidence_basis, evidence_sentence, sentence_start, sentence_end)
                SELECT h.id, 'temporal', 'temporal-1.1.0', 'date_reference', 'd' || k,
                    'date ' || k, k * 20, k * 20 + 10,
                    (ARRAY['en','ar','he','fa','hr'])[1 + (h.id + k) % 5],
                    (ARRAY['gregorian','hijri','jalali'])[1 + (h.id * k) % 3], 'absolute',
                    DATE '2026-06-01' + ((h.id * 7 + k * 31) % 700),
                    DATE '2026-06-01' + ((h.id * 7 + k * 31) % 700),
                    (ARRAY['future','past',NULL])[1 + (h.id + k) % 3], '{}',
                    md5(h.id || ':' || k) || md5(k || ':' || h.id), 'day_month_year',
                    (ARRAY['high','medium','low'])[1 + (h.id + 2 * k) % 3], 'basis',
                    rpad('date ' || k, 10, '.'), k * 20, k * 20 + 10
                FROM hashs h CROSS JOIN generate_series(1, 10) k
            """)
            cur.execute("""
                INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type, value,
                    surface, char_start, char_end, language, resolution, evidence, dedup_key,
                    method, confidence, confidence_basis, evidence_sentence, sentence_start,
                    sentence_end)
                SELECT h.id, 'places', 'places-1.0.0+gperf', 'place_mention', 'place:x' || k,
                    'Place' || k, 500 + k * 20, 510 + k * 20, 'en', 'identified', '{}',
                    md5('p' || h.id || ':' || k) || md5(k || ':p' || h.id), 'exact', 'high',
                    'exact_unique', rpad('Place' || k, 10, '.'), 500 + k * 20, 510 + k * 20
                FROM hashs h CROSS JOIN generate_series(1, 2) k
            """)
            cur.execute("INSERT INTO content_signal_runs (hash_id, detector, detector_ver, status,"
                        " chars_total, chars_scanned, signal_count, trigger)"
                        " SELECT id, 'temporal', 'temporal-1.1.0', 'complete', 1000, 1000, 10,"
                        " 'ingestion' FROM hashs")
            print(f"loaded {contents} contents in {time.perf_counter() - started:.1f} s")
        cur.execute("ANALYZE")
        cur.execute("SELECT count(*) FROM content_signals")
        print("signals:", cur.fetchone()[0])
        cur.execute("SELECT min(id) FROM sources")
        first_source = cur.fetchone()[0]
    conn.close()
    return cfg, first_source


def main(pg_dir: str, contents: int = 20_000) -> None:
    import psycopg2

    cfg, first_source = load_corpus(pg_dir, contents)
    conn = psycopg2.connect(host=cfg["host"], port=cfg["port"], user="postgres",
                            dbname=cfg["database"])

    from core.criteria.compiler import AccessScope
    from core.criteria.model import Criteria, from_dict
    from services.detection import signal_query as sq

    class Args(dict):
        def getlist(self, k):
            v = self.get(k)
            return v if isinstance(v, list) else ([] if v is None else [v])

    ref = datetime.date(2026, 10, 1)
    scope = AccessScope.unrestricted(user_id=1, role="viewer")
    one_source = from_dict({"sources": [first_source]})
    cases = [
        ("horizon, all content", lambda cur: sq.horizon(cur, sq.parse_filter(Args()), Criteria(),
                                                       scope, ref)),
        ("horizon, one source", lambda cur: sq.horizon(cur, sq.parse_filter(Args()), one_source,
                                                      scope, ref)),
        ("horizon, week, hijri+high", lambda cur: sq.horizon(
            cur, sq.parse_filter(Args(calendar="hijri", confidence="high")), Criteria(), scope,
            ref, buckets=["week"])),
        ("explorer, all content (facets)", lambda cur: sq.explore(
            cur, sq.parse_filter(Args()), Criteria(), scope, ref)),
        ("explorer, one source", lambda cur: sq.explore(cur, sq.parse_filter(Args()), one_source,
                                                       scope, ref)),
        ("explorer, evidence_text", lambda cur: sq.explore(
            cur, sq.parse_filter(Args(evidence_text="Place1")), Criteria(), scope, ref)),
        ("explorer, deep page offset 10000", lambda cur: sq.explore(
            cur, sq.parse_filter(Args()), Criteria(), scope, ref, sort="document", offset=10000)),
    ]
    print(f"\n{'case':38} {'median ms':>10} {'max ms':>8}  result")
    for name, fn in cases:
        timings = []
        for i in range(6):
            with conn.cursor() as cur:
                t = time.perf_counter()
                body = fn(cur)
                elapsed = (time.perf_counter() - t) * 1000
            conn.rollback()
            if i:
                timings.append(elapsed)
        print(f"{name:38} {statistics.median(timings):10.0f} {max(timings):8.0f}  "
              f"total={body['total']} items={len(body['items'])}")

    compiled = sq._compile(Criteria(), scope)
    conds, params = sq._where(sq.parse_filter(Args()), compiled)
    with conn.cursor() as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS OFF) SELECT s.id FROM content_signals s WHERE "  # nosec B608 # developer benchmark; conditions from signal_query._where, values bound
                    + " AND ".join(conds) + " ORDER BY s.date_from, s.date_to, s.hash_id,"
                    " s.char_start, s.id LIMIT 50", tuple(params))
        print("\nplan (explorer page, event_date order):")
        for (line,) in cur.fetchall():
            print("  " + line)
    conn.rollback()
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20_000)
