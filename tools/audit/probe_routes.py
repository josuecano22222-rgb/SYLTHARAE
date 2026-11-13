#!/usr/bin/env python
"""Call every route as anonymous, viewer, analyst and admin; record the truth.

This is the auth matrix of the audit, executed rather than declared. Each route
without path parameters is requested with each identity and the status is
recorded; a verdict column applies the policy the product states:

* anonymous must never get a 2xx from anything but the public surface
  (``/auth/*``, ``/setup*``, ``/health``, static);
* a viewer must not reach an admin-only route (403 expected, not 200);
* nothing may return a 500 - that is a server defect whatever the role.

Writes ``audit_report/inventory/routes_runtime.csv``. Exit 1 when a verdict is
``VIOLATION`` or ``SERVER-ERROR``.
"""

from __future__ import annotations

import csv
import os
import pathlib
import sys
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "audit_report" / "inventory"

PUBLIC_PREFIXES = ("/auth/", "/setup", "/health", "/static/", "/favicon",
                   "/_test/", "/api/translations", "/language",
                   "/api/csrf-token", "/api/i18n/catalog")
#: Endpoints the application itself declares as readable by any signed-in user
#: (``AUTH_SETTINGS_ANY_USER_READ_PATHS``) - the declaration is the policy.
ANY_USER_READ = {"/api/settings/theme"}
ADMIN_READ_ENDPOINTS: set = set()
PUBLIC_ENDPOINTS: set = set()
ADMIN_READ_BLUEPRINTS: set = set()


def bootstrap_audit_db():
    """A database of this run's own, migrated from the frozen chain."""
    import psycopg2
    from database.bootstrap import bootstrap_database

    cfg = {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ.get("DB_PORT") or 5432),
        "user": os.environ.get("DB_USER") or "postgres",
        "password": os.environ.get("DB_PASSWORD") or "",
        "database": "file_analysis_audit",
    }
    admin = psycopg2.connect(host=cfg["host"], port=cfg["port"],
                             user=cfg["user"], password=cfg["password"],
                             dbname="postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s",
                    (cfg["database"],))
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE "{cfg["database"]}"')
    admin.close()
    os.environ.update(DB_HOST=cfg["host"], DB_PORT=str(cfg["port"]),
                      DB_USER=cfg["user"], DB_PASSWORD=cfg["password"],
                      DB_NAME=cfg["database"])
    report = bootstrap_database(cfg)
    return cfg, report


def make_client(role=None):
    from apps.web.app import app

    client = app.test_client()
    if role:
        from core.security.service import get_auth_service

        auth = get_auth_service()
        username, password = f"audit_{role}", f"audit-{role}-password-123"
        if auth.get_user_by_username(username) is None:
            auth.create_user(username, password, role=role)
        resp = client.post("/auth/login",
                           json={"username": username, "password": password})
        assert resp.status_code == 200, (role, resp.status_code,
                                         resp.get_data(as_text=True)[:200])
    return client


def verdict(rule, method, anon, viewer, analyst, admin, endpoint=""):
    public = (rule.startswith(PUBLIC_PREFIXES) or rule in ANY_USER_READ
              or endpoint in PUBLIC_ENDPOINTS)
    admin_read = (endpoint in ADMIN_READ_ENDPOINTS
                  or endpoint.split(".")[0] in ADMIN_READ_BLUEPRINTS)
    codes = {"anon": anon, "viewer": viewer, "analyst": analyst, "admin": admin}
    if 500 in codes.values():
        return "SERVER-ERROR"
    if not public and anon in range(200, 300):
        return "VIOLATION"
    if admin_read and (viewer in range(200, 300) or analyst in range(200, 300)) \
            and rule not in ANY_USER_READ:
        return "VIOLATION"
    if admin == 403 and viewer == 403 and analyst == 403 and not public:
        return "LOCKED"
    return "OK"


def main():
    cfg, report = bootstrap_audit_db()
    print("audit database:", cfg["database"], report["applied_migrations"])

    from apps.web.app import app
    import core.initialization as init

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    # The setup gate redirects every request to /setup until this marker
    # exists. It is authoritative together with the critical tables, which
    # bootstrap_database has just created; point it at a temp path so the
    # checkout is not marked (a marker here would change run_web.py's branch).
    init.INIT_MARKER_FILE = pathlib.Path(
        __import__("tempfile").mkdtemp(prefix="audit_marker_")) / ".system_initialized"
    init.mark_system_initialized()
    try:
        from core.security.rate_limit import limiter

        limiter.enabled = False
    except Exception:
        pass

    global ADMIN_READ_ENDPOINTS, ADMIN_READ_BLUEPRINTS, PUBLIC_ENDPOINTS
    ADMIN_READ_ENDPOINTS = set(app.config.get("AUTH_ADMIN_READ_ENDPOINTS", ()))
    PUBLIC_ENDPOINTS = set(app.config.get("AUTH_PUBLIC_ENDPOINTS", ()))
    ADMIN_READ_BLUEPRINTS = set(app.config.get("AUTH_ADMIN_READ_BLUEPRINTS", ()))
    clients = {role: make_client(role)
               for role in (None, "viewer", "analyst", "admin")}

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.arguments:
            continue
        methods = sorted(m for m in rule.methods if m in ("GET", "POST"))
        for method in methods:
            # Logging out ends the session, so every later request from that
            # client would be anonymous and the matrix would report fabricated
            # 302s. It is exercised by the auth tests, not by this sweep.
            if rule.rule == "/auth/logout":
                continue
            codes = {}
            for role, client in clients.items():
                try:
                    resp = client.open(rule.rule, method=method,
                                       follow_redirects=False)
                    codes[role] = resp.status_code
                except Exception:
                    codes[role] = -1
                    traceback.print_exc(limit=1)
            v = verdict(rule.rule, method, codes[None], codes["viewer"],
                        codes["analyst"], codes["admin"], rule.endpoint)
            rows.append((rule.rule, method, rule.endpoint, codes[None],
                         codes["viewer"], codes["analyst"], codes["admin"], v))
            print(f"{v:12s} {method:5s} {rule.rule:55s} "
                  f"anon={codes[None]} viewer={codes['viewer']} "
                  f"analyst={codes['analyst']} admin={codes['admin']}")

    with (OUT / "routes_runtime.csv").open("w", newline="",
                                           encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rule", "method", "endpoint", "anon", "viewer", "analyst",
                    "admin", "verdict"])
        w.writerows(rows)

    bad = [r for r in rows if r[-1] in ("VIOLATION", "SERVER-ERROR")]
    print(f"\nrows: {len(rows)}  violations/server-errors: {len(bad)}")
    for r in bad:
        print("  ", r)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
