#!/usr/bin/env python
"""Dump ``content_signals`` for one document through the real ingestion pipeline.

This is the harness behind the M-3 claim that the overlap-scan fixes change the
*time*, not the *output*. Run it against two trees (the pre-fix algorithm and
the fixed one) and diff the two CSVs: the rows are identical when the digests
in ``audit_report/evidence_r6/content_signals_equivalence.json`` are.

Usage::

    python tools/audit/compare_content_signals.py <repo-root> <database> <out.csv> [size-kb]

``<repo-root>`` is imported, so pointing it at another worktree measures that
tree's code. Each run builds a fresh database, applies the frozen migrations,
loads the gazetteer seed (223 places) and ingests a corpus of repeated
sentences that exercises dates, identifiers and place names. ``detected_at`` is
deliberately not selected: it is a timestamp, not a result.
"""

import csv, hashlib, os, pathlib, sys, time

root, db, out = sys.argv[1], sys.argv[2], sys.argv[3]
size_kb = int(sys.argv[4]) if len(sys.argv) > 4 else 512
sys.path.insert(0, root)
os.chdir(root)

import psycopg2
from database.bootstrap import bootstrap_database

cfg = {"host": os.environ["DB_HOST"], "port": int(os.environ.get("DB_PORT") or 5432),
       "user": os.environ.get("DB_USER") or "postgres",
       "password": os.environ.get("DB_PASSWORD") or "", "database": db}
admin = psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                         password=cfg["password"], dbname="postgres")
admin.autocommit = True
with admin.cursor() as cur:
    cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (db,))
    cur.execute(f'DROP DATABASE IF EXISTS "{db}"')
    cur.execute(f'CREATE DATABASE "{db}"')
admin.close()
report = bootstrap_database(cfg)
os.environ.update(DB_HOST=cfg["host"], DB_PORT=str(cfg["port"]), DB_USER=cfg["user"],
                  DB_PASSWORD=cfg["password"], DB_NAME=db)

from services.geo.gazetteer import load_seed_file, sync_seed
_conn = psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"], dbname=db)
with _conn, _conn.cursor() as _cur:
    _load = sync_seed(_cur, load_seed_file(), loaded_by="m3-equivalence")
_conn.close()
print("gazetteer loaded:", _load["place_count"], "places")

import core.initialization as init
init.INIT_MARKER_FILE = pathlib.Path("/tmp/sylth_appdata/.m3_marker")
init.INIT_MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
init.mark_system_initialized()

BASE = ("The quarterly review is scheduled for 3 March 2027 in Rotterdam. "
        "Contact alice@example.org, bob@example.net or 10.0.0.1 and 192.168.0.7. "
        "Payment of 125000 EUR was agreed; see https://example.org/ledger. ")
doc = pathlib.Path(f"/tmp/m3_equiv_{size_kb}kb.txt")
text = "Equivalence document.\n"
while len(text.encode()) < size_kb * 1024:
    text += BASE
doc.write_text(text, encoding="utf-8")

from pipeline.integrated_reader import IntegratedFileReader
reader = IntegratedFileReader(max_workers=1, enable_monitoring=False, enable_storage=True,
                              storage_source="m3 equivalence source",
                              storage_side="m3 equivalence side")
started = time.perf_counter()
result = reader.process_single_file(str(doc))
elapsed = time.perf_counter() - started

conn = psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"], dbname=db)
with conn.cursor() as cur:
    cur.execute("""SELECT detector, detector_ver, signal_type, value, surface, char_start,
                          char_end, language, resolution, date_from, date_to, anchor_date,
                          evidence::text, dedup_key
                   FROM content_signals
                   ORDER BY detector, signal_type, char_start, char_end, value, surface""")
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
conn.close()

with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(cols)
    w.writerows(rows)
print(f"{root}: stored={bool(result)} elapsed={elapsed:.1f}s rows={len(rows)} "
      f"digest={hashlib.sha256(open(out,'rb').read()).hexdigest()[:16]} -> {out}")
