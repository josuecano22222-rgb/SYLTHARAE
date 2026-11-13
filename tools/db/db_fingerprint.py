#!/usr/bin/env python3
"""Per-table row counts and row-content checksums for one PostgreSQL database.

Usage: db_fingerprint.py <dbname> <out.json> [--socket DIR] [--port N]

For every ordinary table (public schema) it records:
  * ``rows``  - SELECT count(*)
  * ``md5``   - md5 over the table's rows rendered as text and sorted, so the
               digest changes when a value changes, not only when a row count
               changes. Sorted, so it is independent of physical row order.
Views, matviews and foreign tables are skipped (they derive from tables).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psycopg2


def connect(dbname: str, host: str | None, port: int, user: str, password: str):
    kwargs = dict(dbname=dbname, port=port, user=user, password=password)
    if host:
        kwargs["host"] = host
    return psycopg2.connect(**kwargs)


def tables(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r' AND n.nspname = 'public' "
            "ORDER BY c.relname")
        return [row[0] for row in cur.fetchall()]


def fingerprint(conn, dbname: str) -> dict:
    out = {"database": dbname, "tables": {}, "total_rows": 0}
    with conn.cursor() as cur:
        for name in tables(conn):
            cur.execute(f'SELECT count(*) FROM public."{name}"')
            rows = cur.fetchone()[0]
            cur.execute(
                f"SELECT md5(coalesce(string_agg(t.row_text, E'\\n' ORDER BY t.row_text), '')) "
                f"FROM (SELECT x::text AS row_text FROM public.\"{name}\" x) t")
            digest = cur.fetchone()[0]
            out["tables"][name] = {"rows": rows, "md5": digest}
            out["total_rows"] += rows
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("database")
    ap.add_argument("output")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--password", default="")
    args = ap.parse_args()

    conn = connect(args.database, args.host, args.port, args.user, args.password)
    try:
        result = fingerprint(conn, args.database)
    finally:
        conn.close()
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    nonzero = {k: v for k, v in result["tables"].items() if v["rows"]}
    print(f"{args.database}: {len(result['tables'])} tables, {result['total_rows']} rows, "
          f"{len(nonzero)} non-empty")
    for name, info in sorted(nonzero.items(), key=lambda kv: -kv[1]["rows"])[:10]:
        print(f"  {name}: {info['rows']} rows md5={info['md5'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
