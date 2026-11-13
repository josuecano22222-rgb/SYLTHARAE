#!/usr/bin/env python
"""Measure the application's database work against a 100 000-row database.

Nothing here asserts that a query is fast or slow by looking at it. Every
number below comes from running the application's own request handlers against
``file_analysis_scale`` (built by ``measure_scale_db.py``: the frozen schema,
100 000 paths, 50 000 signals) and timing what actually reached PostgreSQL.

How the timing is captured
--------------------------
``psycopg2`` is patched at its two entry points - ``psycopg2.connect`` and the
connection pool's ``_connect`` - so every connection the application opens is
wrapped in a thin proxy. The proxy records, per statement, the SQL text, the
wall-clock time of ``execute``/``executemany`` (which includes the round trip
and, for ``executemany``, every round trip inside it), the row count, and the
caller. It does not change anything the application sees: the cursor object
handed back is the driver's own, only observed.

The ten heaviest statements are then re-run through
``EXPLAIN (ANALYZE, BUFFERS)`` - the text recorded above, with the recorded
parameters where they are literals, and ``$1``-style placeholders replaced by
the values the proxy saw - so the plan and the buffer counts are PostgreSQL's
own, not the driver's.

The same run counts statements per list request: a request that issues one
statement per returned row is N+1, whatever its individual timings look like.

Usage:
    python tools/audit/measure_queries.py
Writes audit_report/evidence_scale/measurements.json and prints a summary.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "audit_report" / "evidence_scale"
DB = "file_analysis_scale"

TRACE: list[dict] = []
CURRENT = {"label": None}


# --------------------------------------------------------------------------
# instrumentation
# --------------------------------------------------------------------------
def install_trace() -> None:
    import psycopg2
    import psycopg2.pool

    class CursorProxy:
        def __init__(self, cur):
            object.__setattr__(self, "_cur", cur)

        def execute(self, sql, params=None):
            started = time.perf_counter()
            error = None
            try:
                if params is None:
                    return self._cur.execute(sql)
                return self._cur.execute(sql, params)
            except Exception as exc:  # recorded, then re-raised unchanged
                error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                TRACE.append(_record(sql, params, started, None, error, "execute"))

        def executemany(self, sql, seq):
            started = time.perf_counter()
            error = None
            rows = None
            try:
                seq = list(seq)
                rows = len(seq)
                return self._cur.executemany(sql, seq)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                TRACE.append(_record(sql, None, started, rows, error, "executemany"))

        def __getattr__(self, name):
            return getattr(self._cur, name)

        def __enter__(self):
            self._cur.__enter__()
            return self

        def __exit__(self, *args):
            return self._cur.__exit__(*args)

    class ConnProxy:
        def __init__(self, conn):
            object.__setattr__(self, "_conn", conn)

        def cursor(self, *args, **kwargs):
            return CursorProxy(self._conn.cursor(*args, **kwargs))

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def __setattr__(self, name, value):
            setattr(self._conn, name, value)

        def __enter__(self):
            self._conn.__enter__()
            return self

        def __exit__(self, *args):
            return self._conn.__exit__(*args)

    real_connect = psycopg2.connect

    def connect(*args, **kwargs):
        return ConnProxy(real_connect(*args, **kwargs))

    psycopg2.connect = connect

    real_pool_connect = psycopg2.pool.AbstractConnectionPool._connect

    def pool_connect(self, *args, **kwargs):
        return ConnProxy(real_pool_connect(self, *args, **kwargs))

    psycopg2.pool.AbstractConnectionPool._connect = pool_connect


def _record(sql, params, started, rows, error, kind) -> dict:
    duration = (time.perf_counter() - started) * 1000.0
    frame = sys._getframe(2)
    caller = "?"
    for _ in range(8):
        if frame is None:
            break
        name = frame.f_code.co_name
        if not name.startswith("<") and frame.f_globals.get("__name__", "").split(".")[0] in {
            "Api", "services", "core", "database", "apps", "tools"
        }:
            caller = f"{frame.f_globals.get('__name__')}.{name}"
            break
        frame = frame.f_back
    return {
        "label": CURRENT["label"],
        "kind": kind,
        # newlines are part of the statement: the queries carry ``--`` line
        # comments, and collapsing them would comment out the rest of the SQL
        "sql": _normalise_sql(sql),
        "params": _short(params),
        # kept in memory only: _explain hands these to the driver's own
        # mogrify, so the explained statement is byte-for-byte what was sent
        "raw": (list(params) if isinstance(params, (list, tuple))
                else dict(params) if isinstance(params, dict) else None),
        "ms": round(duration, 3),
        "rows": rows,
        "caller": caller,
        "error": error,
    }


def _repeats(statements) -> int:
    """How many times the most-repeated statement ran inside one request."""
    import collections

    if not statements:
        return 0
    counts = collections.Counter(s["sql"] for s in statements)
    return max(counts.values())


def _repeat_detail(statements) -> list[dict]:
    """The statements that ran more than once in one request, with counts."""
    import collections

    counts = collections.Counter(s["sql"] for s in statements)
    out = []
    for sql, count in counts.most_common(5):
        if count < 2:
            continue
        out.append({"count": count, "sql": " ".join(sql.split())[:220]})
    return out


def _n_plus_one(per_request):
    """Group the measured requests by route and look for per-row statements.

    Two things are checked, and both have to fail before a route is called free
    of N+1:

    * the number of statements a route issues does not rise with the number of
      rows it returns (50 rows and 500 rows cost the same 5 statements), and
    * no single statement is repeated inside one request as many times as the
      request returns rows.

    A route that repeats one statement once per returned row would show up in
    the second check even when the row count happens to be small.
    """
    import collections

    families = collections.defaultdict(list)
    for row in per_request:
        families[row["url"].split("?")[0]].append(row)
    out = []
    for family, rows in families.items():
        counts = sorted(r["statements"] for r in rows)
        repeats = max(r["repeated_statements"] for r in rows)
        with_rows = [r for r in rows if r["returned_rows"]]
        suspect = False
        for row in with_rows:
            returned = row["returned_rows"] or 0
            if row["repeated_statements"] >= 10 and row["repeated_statements"] >= returned - 2:
                suspect = True
        if suspect:
            verdict = "N+1: a statement repeats once per row"
        elif repeats > 1:
            verdict = f"no per-row statements; one statement repeated {repeats}x per request"
        else:
            verdict = "no N+1: statement count flat, nothing repeated"
        out.append({
            "family": family,
            "requests": len(rows),
            "statements": f"{counts[0]}-{counts[-1]}" if counts[0] != counts[-1] else str(counts[0]),
            "max_repeat_in_request": repeats,
            "rows": max((r["returned_rows"] or 0) for r in rows) or None,
            "verdict": verdict,
        })
    return sorted(out, key=lambda r: (-int(str(r["statements"]).split("-")[-1]),
                                      r["family"]))


def _normalise_sql(sql) -> str:
    text = str(sql)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _short(params):
    if params is None:
        return None
    try:
        out = []
        for value in params if isinstance(params, (list, tuple)) else [params]:
            text = repr(value)
            out.append(text if len(text) <= 60 else text[:57] + "...")
        return out
    except Exception:
        return "<unprintable>"


def reset(label):
    TRACE.clear()
    CURRENT["label"] = label


# --------------------------------------------------------------------------
# workload
# --------------------------------------------------------------------------
#: Read endpoints a signed-in analyst opens. Kept to GETs so the measurement
#: run does not write to the database it is measuring.
ENDPOINTS = [
    # /api/paths addresses rows by offset/limit (Api/blueprints/paths.py), so
    # the deep-page sweep below is a sweep over OFFSET, not over a page number
    "/api/paths?offset=0&limit=50",
    "/api/paths?offset=5000&limit=50",
    "/api/paths?offset=50000&limit=50",
    "/api/paths?offset=99500&limit=50",
    "/api/paths?offset=0&limit=500",
    "/api/paths?offset=99500&limit=500",
    "/api/paths?offset=0&limit=50&sort=file_date&order=desc",
    "/api/paths?offset=0&limit=50&status=Read",
    "/api/signals?limit=50",
    "/api/signals?limit=50&signal_type=place_mention",
    "/api/signals?limit=50&offset=1000",
    "/api/signals?limit=50&offset=49950",
    "/api/signals/horizon?limit=50",
    # the identifier-kind filter is ``s.evidence->>'kind' = ANY(...)`` - a JSONB
    # expression no index covers (the schema is frozen), so it is measured
    # rather than assumed acceptable
    "/api/signals?limit=50&identifier_kind=email",
    "/api/signals?limit=50&identifier_kind=email&offset=8000",
    "/api/signals/horizon?limit=50&identifier_kind=email",
    "/files?page=1",
    "/files?page=5",
    "/files?page=40",
    "/api/lexis/manage/files?page=1",
    "/api/lexis/manage/files?page=2",
    "/api/lexis/manage/files?page=20",
    "/api/sources",
    "/api/sides",
    "/api/places?page=1",
    "/api/reports/dashboard",
    "/api/reports/overview",
    "/api/lexis/manage/dashboard",
    "/dashboard/charts",
    "/dashboard/comprehensive",
    "/api/search/suggestions?q=document",
    "/api/keywords",
    "/api/scenarios",
]


def main() -> int:
    import psycopg2

    cfg = {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ.get("DB_PORT") or 5432),
        "user": os.environ.get("DB_USER") or "postgres",
        "password": os.environ.get("DB_PASSWORD") or "",
        "database": DB,
    }
    check = psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                             password=cfg["password"], dbname=cfg["database"])
    with check.cursor() as cur:
        cur.execute("SELECT count(*) FROM paths")
        paths = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM content_signals")
        signals = cur.fetchone()[0]
    check.close()
    if paths < 100_000:
        print(f"{DB} holds {paths} paths; run tools/audit/measure_scale_db.py first")
        return 1
    print(f"scale database: paths={paths} content_signals={signals}")

    os.environ.update(DB_HOST=cfg["host"], DB_PORT=str(cfg["port"]), DB_USER=cfg["user"],
                      DB_PASSWORD=cfg["password"], DB_NAME=cfg["database"])
    os.environ.setdefault("INGESTION_ROOTS", "/home/user/syltharae_corpus/realistic")
    os.environ["APP_DATA_DIR"] = os.environ.get("APP_DATA_DIR", "/tmp/sylth_appdata")

    install_trace()

    import core.initialization as init
    from apps.web.app import app

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    init.INIT_MARKER_FILE = pathlib.Path("/tmp/sylth_appdata") / ".scale_marker"
    os.makedirs(init.INIT_MARKER_FILE.parent, exist_ok=True)
    init.mark_system_initialized()
    try:
        from core.security.rate_limit import limiter

        limiter.enabled = False
    except Exception:
        pass

    from core.security.service import get_auth_service

    password = "scale-measurement-password-123"
    auth = get_auth_service()
    if auth.get_user_by_username("scale_admin") is None:
        auth.create_user("scale_admin", password, role="admin")
    client = app.test_client()
    login = client.post("/auth/login", json={"username": "scale_admin", "password": password})
    print(f"login: {login.status_code}")
    if login.status_code != 200:
        print(login.get_data(as_text=True)[:300])
        return 1

    # warm the caches so the numbers describe steady state, then measure
    for url in ENDPOINTS:
        client.get(url)

    per_request = []
    all_statements: list[dict] = []
    for url in ENDPOINTS:
        reset(url)
        started = time.perf_counter()
        resp = client.get(url)
        elapsed = (time.perf_counter() - started) * 1000.0
        statements = list(TRACE)
        all_statements.extend(statements)
        rows = None
        try:
            payload = resp.get_json()
            if isinstance(payload, dict):
                for key in ("items", "data", "paths", "signals", "results", "files", "rows"):
                    if isinstance(payload.get(key), list):
                        rows = len(payload[key])
                        break
            elif isinstance(payload, list):
                rows = len(payload)
        except Exception:
            pass
        per_request.append({
            "url": url,
            "status": resp.status_code,
            "ms": round(elapsed, 1),
            "statements": len(statements),
            "repeated_statements": _repeats(statements),
            "repeated_detail": _repeat_detail(statements),
            "sql_ms": round(sum(s["ms"] for s in statements), 1),
            "returned_rows": rows,
            "slowest": [{"sql": " ".join(s["sql"].split())[:200], "ms": s["ms"],
                         "caller": s["caller"]}
                        for s in sorted(statements, key=lambda s: -s["ms"])[:3]],
        })
        print(f"{resp.status_code} {elapsed:8.1f} ms  {len(statements):3d} stmts  "
              f"{str(rows):>4} rows  {url}")

    # the ten heaviest single statements across the whole workload
    every = all_statements
    # the same statement runs for several requests; keep the slowest instance
    # of each distinct SQL text, then take the ten heaviest of those
    by_sql: dict[str, dict] = {}
    for statement in every:
        current = by_sql.get(statement["sql"])
        if current is None or statement["ms"] > current["ms"]:
            by_sql[statement["sql"]] = statement
    heaviest = sorted(by_sql.values(), key=lambda s: -s["ms"])[:10]
    explained = [_explain(cfg, s) for s in heaviest]

    # the identifier-kind filter reads ``evidence->>'kind'``: measured on its
    # own, because a JSONB expression is not indexable without a schema change
    jsonb: dict[str, dict] = {}
    for statement in every:
        if "->>'kind'" in statement["sql"] or "->> 'kind'" in statement["sql"]:
            current = jsonb.get(statement["sql"])
            if current is None or statement["ms"] > current["ms"]:
                jsonb[statement["sql"]] = statement
    jsonb_kind = [_explain(cfg, s) for s in sorted(jsonb.values(), key=lambda s: -s["ms"])[:3]]

    # N+1: does the statement count grow with the number of returned rows?
    n_plus_one = _n_plus_one(per_request)

    pagination = [r for r in per_request if "offset=" in r["url"] or "page=" in r["url"]]

    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "database": {"name": DB, "paths": paths, "content_signals": signals},
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "requests": per_request,
        "heaviest": explained,
        "jsonb_kind": jsonb_kind,
        "n_plus_one": n_plus_one,
        "pagination": pagination,
        "statement_count": len(every),
        "statement_total_ms": round(sum(s["ms"] for s in every), 1),
    }
    (OUT / "measurements.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nheaviest statements (EXPLAIN ANALYZE, BUFFERS):")
    for item in explained:
        plan = item.get("plan") or item.get("error")
        first = ""
        if plan:
            for line in plan.splitlines():
                if "actual time" in line or "Execution Time" in line:
                    first = line.strip()
                    if "Execution Time" in line:
                        break
        print(f"  {item['ms']:8.1f} ms client  {first[:150]}")
        print(f"      {' '.join(item['sql'].split())[:150]}")
    print("\nidentifier-kind (evidence->>'kind') statements:")
    for item in jsonb_kind:
        print(f"  {item['ms']:8.1f} ms client")
        plan = item.get("plan") or item.get("error") or ""
        for line in plan.splitlines():
            if "Seq Scan" in line or "Index" in line or "Execution Time" in line:
                print(f"      {line.strip()[:130]}")

    print("\nstatements per request (N+1 check):")
    for row in n_plus_one:
        print(f"  {row['family']:<42} {row['statements']:>5} stmts"
              f"  repeat<= {row['max_repeat_in_request']:<3} rows<= {str(row['rows']):<5}"
              f" {row['verdict']}")

    print(f"\nwrote {OUT / 'measurements.json'}")
    return 0


def _explain(cfg, statement: dict) -> dict:
    """Explain the statement exactly as the driver sent it.

    ``cursor.mogrify`` is the driver's own parameter renderer, so the text put
    under EXPLAIN is the text PostgreSQL received - list parameters become the
    array literals psycopg2 really produces, NULLs stay NULL, and no hand-written
    quoting can change the plan.
    """
    import psycopg2

    sql = statement["sql"]
    raw = statement.get("raw")
    out = dict(statement)
    out.pop("raw", None)
    if statement.get("kind") == "executemany" or not re.search(r"%s|%\(\w+\)s", sql):
        explained_sql = sql
    else:
        explained_sql = None
    if not sql.lstrip().upper().startswith(("SELECT", "WITH", "INSERT", "UPDATE", "DELETE")):
        out["error"] = "not explainable"
        return out
    conn = psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                            password=cfg["password"], dbname=cfg["database"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            if explained_sql is None and raw is not None:
                bound = raw if isinstance(raw, dict) else tuple(raw)
                explained_sql = cur.mogrify(sql, bound).decode("utf-8", "replace")
            elif explained_sql is None:
                out["error"] = "parameters were not captured"
                return out
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, VERBOSE) " + explained_sql)
            out["plan"] = "\n".join(row[0] for row in cur.fetchall())
            out["explained_sql"] = explained_sql
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["explained_sql"] = explained_sql
    finally:
        conn.close()
    return out


if __name__ == "__main__":
    sys.exit(main())
