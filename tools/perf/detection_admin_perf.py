"""Measure the Detection page's queries on a large content_signal_runs.

    python tools/perf/detection_admin_perf.py <pgserver-dir> [contents]

Creates (or reuses) database ``detection_perf`` on the pgserver instance in
``<pgserver-dir>`` through the application bootstrap (all migrations), then
bulk-loads ``contents`` content hashes (default 500,000), each with one
context, one file and one stored text chunk, and their detection runs:

* temporal: 80 % at the current version, 10 % at an older version, 2 %
  failed at the current version (error set), 8 % never analysed; 1 % of the
  contents' temporal runs were triggered by re-detection.
* places: 60 % at a version stamped with a gazetteer load that is not the
  current one (bootstrap loads the bundled gazetteer), so every content is
  stale for places - the worst case for the stale count.

That is 760,000 runs at the default size. It times ``detection_admin.detection_status`` and
``list_runs`` for each filter shape the page can send (median of 5 after
one warm-up) and prints the plan nodes of the list query. Synthetic data
measures query shape and index use, not detector behaviour.
"""

import os
import statistics
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def load(pg_dir, contents):
    import pgserver
    import psycopg2

    server = pgserver.get_server(pg_dir, cleanup_mode=None)
    parsed = urllib.parse.urlparse(server.get_uri())
    host = urllib.parse.parse_qs(parsed.query).get("host", [None])[0] or parsed.hostname
    cfg = {"host": host, "port": parsed.port or 5432, "user": "postgres", "password": "",
           "database": "detection_perf"}
    os.environ.update({"DB_HOST": host, "DB_PORT": str(cfg["port"]), "DB_USER": "postgres",
                       "DB_PASSWORD": "", "DB_NAME": cfg["database"]})
    from database.bootstrap import bootstrap_database
    from services.detection import detectors as registry

    bootstrap_database(cfg)
    conn = psycopg2.connect(host=host, port=cfg["port"], user="postgres", dbname=cfg["database"])
    current = registry.get("temporal").version(None)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM content_signal_runs")
        if cur.fetchone()[0] == 0:
            t0 = time.perf_counter()
            cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                        " VALUES ('perf_src', 'p', 0.5, 'p', CURRENT_DATE)")
            cur.execute("INSERT INTO sides (name, importance, date_creation)"
                        " VALUES ('perf_side', 0.5, CURRENT_DATE)")
            cur.execute("INSERT INTO hashs (hash) SELECT md5('perf' || g) || md5('x' || g)"
                        " FROM generate_series(1, %s) g", (contents,))
            cur.execute("INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                        " SELECT h.id, (SELECT min(id) FROM sources), (SELECT min(id) FROM sides)"
                        " FROM hashs h")
            cur.execute("INSERT INTO paths (file_name, file_path, file_size, file_type, file_status,"
                        " file_date, date_creation, context_id, processing_status)"
                        " SELECT 'doc_' || hc.id || '.txt', '/perf/doc_' || hc.id || '.txt', 100,"
                        " 'txt', 'Read', DATE '2026-01-01', CURRENT_DATE, hc.id, 'processed'"
                        " FROM hash_contexts hc")
            cur.execute("SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'contents_raw' AND column_name = 'path_id'")
            if cur.fetchone():
                cur.execute("INSERT INTO contents_raw (hash_id, path_id, chunk_seq, content, char_count)"
                            " SELECT hc.hash_id, p.id, 0, 'text', 4 FROM paths p"
                            " JOIN hash_contexts hc ON hc.id = p.context_id")
            else:
                cur.execute("INSERT INTO contents_raw (hash_id, chunk_seq, content, char_count)"
                            " SELECT id, 0, 'text', 4 FROM hashs")
            cur.execute("""
                INSERT INTO content_signal_runs (hash_id, detector, detector_ver, status,
                    chars_total, chars_scanned, signal_count, trigger, error, ran_at)
                SELECT id, 'temporal',
                       CASE WHEN id %% 100 < 10 THEN 'temporal-0.9.0' ELSE %s END,
                       CASE WHEN id %% 100 BETWEEN 10 AND 11 THEN 'failed' ELSE 'complete' END,
                       1000, 1000,
                       CASE WHEN id %% 100 BETWEEN 10 AND 11 THEN 0 ELSE 10 END,
                       CASE WHEN id %% 100 = 50 THEN 'redetection' ELSE 'ingestion' END,
                       CASE WHEN id %% 100 BETWEEN 10 AND 11 THEN 'synthetic failure' END,
                       TIMESTAMPTZ '2028-01-01' + id * INTERVAL '1 minute'
                FROM hashs WHERE id %% 100 < 92""", (current,))
            cur.execute("""
                INSERT INTO content_signal_runs (hash_id, detector, detector_ver, status,
                    chars_total, chars_scanned, signal_count, trigger, ran_at)
                SELECT id, 'places', 'places-1.0.0+g0001', 'complete', 1000, 1000, 2,
                       'ingestion', TIMESTAMPTZ '2028-01-01' + id * INTERVAL '1 minute'
                FROM hashs WHERE id % 10 < 6""")
            print(f"loaded {contents:,} contents in {time.perf_counter() - t0:.1f} s")
        cur.execute("ANALYZE")
        cur.execute("SELECT count(*) FROM content_signal_runs")
        print(f"runs: {cur.fetchone()[0]:,}")
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'content_signal_runs'"
                    " ORDER BY 1")
        print("indexes:", ", ".join(r[0] for r in cur.fetchall()))
    return conn


def timed(fn, *args):
    fn(*args)
    times = []
    for _ in range(5):
        t = time.perf_counter()
        out = fn(*args)
        times.append((time.perf_counter() - t) * 1000)
    return statistics.median(times), out


def main(pg_dir, contents=500_000):
    from services.detection import detection_admin as da

    conn = load(pg_dir, contents)
    with conn.cursor() as cur:   # ids are not 1..n (identities survive rollbacks)
        cur.execute("SELECT hash_id FROM content_signal_runs ORDER BY hash_id"
                    " OFFSET (SELECT count(*) / 2 FROM content_signal_runs) LIMIT 1")
        some_hash = cur.fetchone()[0]
    conn.rollback()
    ms, status = timed(da.detection_status, conn)
    print(f"\ndetection_status: {ms:.0f} ms  analysable={status['analysable_contents']:,}"
          f" stale_any={status['stale_any_detector']:,}")
    for d in status["detectors"]:
        print(f"  {d['detector']:9} current={d['current_version']} never={d['never_analysed']:,}"
              f" stale={d['stale']:,} at_current={d['at_current_version']}"
              f" older={d['at_older_versions']}")

    cases = [
        ("first page, no filter", {}),
        ("deep page (offset 100000)", {"offset": "100000"}),
        ("detector=temporal", {"detector": "temporal"}),
        ("temporal, status=failed (2 %)", {"detector": "temporal", "status": "failed"}),
        ("temporal, version=older (10 %)", {"detector": "temporal", "version": "older"}),
        ("temporal, version=current", {"detector": "temporal", "version": "current"}),
        ("trigger=redetection (0.5 %)", {"trigger": "redetection"}),
        ("hash_id exact", {"hash_id": str(some_hash)}),
        ("exact version, no match", {"version": "nope-0"}),
        ("status=truncated (0 runs)", {"status": "truncated"}),
        ("places, status=failed (0 runs)", {"detector": "places", "status": "failed"}),
    ]
    print(f"\n{'case':38} {'median ms':>10} {'rows':>5}  plan")
    for name, args in cases:
        filters = da.parse_run_filters(dict(args, limit="50"))
        ms, page = timed(da.list_runs, conn, filters)
        print(f"{name:38} {ms:10.1f} {len(page['items']):5}  {explain(conn, filters)}")
    conn.close()


def explain(conn, filters):
    """Plan node types (with index names) of the inner paged query."""
    import json

    where, params = [], []
    for column in ("detector", "status", "trigger", "hash_id"):
        if filters.get(column) is not None:
            where.append(f"{column} = %s")
            params.append(filters[column])
    v = filters.get("version")
    if v in ("current", "older"):
        from services.detection import detectors as registry
        where.append("detector_ver = %s" if v == "current" else "detector_ver <> %s")
        params.append(registry.get("temporal").version(None))
    elif v:
        where.append("detector_ver = %s")
        params.append(v)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with conn.cursor() as cur:
        cur.execute("EXPLAIN (FORMAT JSON) SELECT * FROM content_signal_runs" + clause  # nosec B608 # developer benchmark; filter columns from a fixed tuple, values bound
                    + " ORDER BY ran_at DESC, hash_id DESC, detector LIMIT %s OFFSET %s",
                    params + [filters["limit"] + 1, filters["offset"]])
        plan = cur.fetchone()[0][0]["Plan"]
    conn.rollback()
    nodes = []

    def walk(n):
        nodes.append(n["Node Type"] + (f"({n['Index Name']})" if "Index Name" in n else ""))
        for c in n.get("Plans", []):
            walk(c)
    walk(plan)
    return " > ".join(nodes)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 500_000)
