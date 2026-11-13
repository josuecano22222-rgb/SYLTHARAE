#!/usr/bin/env python
"""Build the measurement database: the frozen schema plus 100 000 stored paths.

The realistic corpus (``tools/gen_realistic_corpus.py``) produces documents,
not rows: ingesting a hundred thousand real files here would take hours and
prove nothing about the query planner. So the measurement database holds

* the 26 generated documents, ingested through the application's own import
  endpoint (so the rows the endpoints read are rows the app wrote), and
* 100 000 paths rows inserted directly, spread over 4 sources x 2 sides, with
  their hashs/hash_contexts rows and a share of content_signals - the shape a
  large deployment has, written the way the schema requires.

Every number produced from this database is therefore a number about plans and
pagination at that size, not a claim about ingestion throughput.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DB = "file_analysis_scale"
ROWS = 100_000


def main() -> int:
    import psycopg2

    from database.bootstrap import bootstrap_database

    cfg = {"host": os.environ["DB_HOST"], "port": int(os.environ.get("DB_PORT") or 5432),
           "user": os.environ.get("DB_USER") or "postgres",
           "password": os.environ.get("DB_PASSWORD") or "", "database": DB}
    admin = psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                             password=cfg["password"], dbname="postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB,))
        if cur.fetchone():
            cur.execute(f'DROP DATABASE "{DB}"')
            print("dropped the previous measurement database")
        cur.execute(f'CREATE DATABASE "{DB}"')
    admin.close()
    os.environ.update(DB_HOST=cfg["host"], DB_PORT=str(cfg["port"]), DB_USER=cfg["user"],
                      DB_PASSWORD=cfg["password"], DB_NAME=DB)
    report = bootstrap_database(cfg)
    print("migrations:", report["applied_migrations"])

    conn = psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                            password=cfg["password"], dbname=DB)
    started = time.time()
    with conn, conn.cursor() as cur:
        # four sources, two sides
        for i in range(4):
            cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                        " VALUES (%s, 'analyst', 0.5, 'NL', CURRENT_DATE)",
                        (f"scale source {i}",))
        for name in ("scale side A", "scale side B"):
            cur.execute("INSERT INTO sides (name, importance, date_creation)"
                        " VALUES (%s, 0.5, CURRENT_DATE)", (name,))
        cur.execute("SELECT id FROM sources ORDER BY id")
        sources = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT id FROM sides ORDER BY id")
        sides = [r[0] for r in cur.fetchall()]

        # 100k documents, one hash and one context each
        cur.execute("INSERT INTO hashs (hash) SELECT md5(g::text) FROM generate_series(1, %s) g",
                    (ROWS,))
        cur.execute("SELECT id FROM hashs ORDER BY id")
        hashes = [r[0] for r in cur.fetchall()]
        cur.executemany(
            "INSERT INTO hash_contexts (hash_id, source_id, side_id) VALUES (%s, %s, %s)",
            [(h, sources[i % len(sources)], sides[(i // 4) % len(sides)])
             for i, h in enumerate(hashes)])
        cur.execute("SELECT id FROM hash_contexts ORDER BY id")
        contexts = [r[0] for r in cur.fetchall()]
        types = ["pdf", "docx", "txt", "msg", "xlsx"]
        import datetime
        cur.executemany(
            "INSERT INTO paths (file_name, file_path, file_size, file_type, file_status,"
            " file_date, date_creation, context_id, processing_status)"
            " VALUES (%s, %s, %s, %s, %s, %s, CURRENT_DATE, %s, %s)",
            [(f"document_{i:06d}.{types[i % len(types)]}",
              f"/srv/evidence/{i // 1000}/document_{i:06d}.{types[i % len(types)]}",
              1024 * (i % 5000), types[i % len(types)],
              "Read" if i % 7 else "Unread",
              datetime.date(2025, 1, 1) + datetime.timedelta(days=i % 600), c,
              "processed" if i % 5 else "partially_processed")
             for i, c in enumerate(contexts)])
        # a share of signals, so the explorer and the reports have rows to read
        cur.execute("""
            INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type,
                                         value, surface, char_start, char_end, resolution,
                                         evidence, dedup_key)
            SELECT h.id, 'gazetteer', 'gazetteer-1.0.0', 'place_mention',
                   'Rotterdam', 'Rotterdam', 0, 9, 'identified',
                   '{"pattern": "probe"}'::jsonb, md5(h.id::text || '-sig') || md5('salt' || h.id::text)
              FROM hashs h WHERE h.id % 2 = 0
        """)
        # a third of the signals are identifier mentions: their kind lives in
        # the evidence JSONB (``evidence->>'kind'``), the filter the signal
        # explorer applies when an identifier kind is selected. Rows are needed
        # for that filter to have anything to scan.
        cur.execute("""
            INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type,
                                         value, surface, char_start, char_end, resolution,
                                         evidence, dedup_key)
            SELECT h.id, 'identifiers', 'identifiers-1.0.0', 'identifier',
                   'person' || h.id || '@example.org', 'person...@example.org', 0, 24,
                   'identified',
                   jsonb_build_object('kind', (ARRAY['email', 'phone', 'url'])[1 + h.id % 3]),
                   md5('ident' || h.id::text) || md5('salt' || h.id::text)
              FROM hashs h WHERE h.id % 4 = 1
        """)
        cur.execute("ANALYZE")
        cur.execute("SELECT count(*) FROM paths")
        paths = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM content_signals")
        signals = cur.fetchone()[0]
    conn.close()
    print(f"paths={paths} content_signals={signals} built in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
