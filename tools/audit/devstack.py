"""Start a disposable PostgreSQL + the real app, for browser reproduction.

Round 18 needs a live server to reproduce bugs the way the user hit them. This
does what the integration fixtures do, outside pytest:

  1. ``pgserver`` starts a throw-away PostgreSQL;
  2. ``database.bootstrap.bootstrap_database`` creates and migrates the app
     database;
  3. an admin user is created (or reset) with a known password;
  4. the Flask app is served on FLASK_HOST:FLASK_PORT.

Writes the connection settings and the admin credentials to
``/tmp/r18_bootstrap.json`` so the browser scripts can log in.

Usage: python tools/audit/devstack.py [--port 5000] [--password ...]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APP_DATA_DIR", str(ROOT / ".test_runtime"))
os.environ.setdefault("FLASK_SECRET_KEY", "devstack-secret-not-for-production-0123456789")
os.environ.setdefault("FLASK_ENV", "development")
# A browser walk loads dozens of pages; the default 600/hour read limit turns
# that into fake 429s. Raised for the same reason browser_walk.py needs it.
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "6000")
os.environ.setdefault("RATE_LIMIT_PER_HOUR", "100000")
os.environ.setdefault("RATE_LIMIT_LOGIN_PER_MINUTE", "600")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--password", default="devstack-admin-password-123")
    parser.add_argument("--out", default="/tmp/r18_bootstrap.json")
    args = parser.parse_args()

    import pgserver

    from tests.conftest import pg_connection_settings

    data_dir = pathlib.Path(tempfile.mkdtemp(prefix="pgdata_devstack_"))
    server = pgserver.get_server(str(data_dir))
    db_name = "syltharae_devstack"
    cfg = pg_connection_settings(server, db_name)

    os.environ["DB_HOST"] = str(cfg["host"])
    os.environ["DB_PORT"] = str(cfg["port"])
    os.environ["DB_USER"] = str(cfg["user"])
    os.environ["DB_PASSWORD"] = str(cfg["password"])
    os.environ["DB_NAME"] = db_name

    # The application keeps settings in a file as well; seed the database
    # section so lazy pools do not fall back to localhost:5432.
    settings_path = ROOT / "data" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["database"] = {"host": str(cfg["host"]), "port": int(cfg["port"]),
                        "database": db_name, "user": str(cfg["user"]),
                        "password": str(cfg["password"])}
    settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    from database.bootstrap import bootstrap_database

    report = bootstrap_database(cfg)
    print("migrations applied:", len(report["applied_migrations"]), flush=True)

    from core.security.service import get_auth_service

    auth = get_auth_service()
    user = auth.get_user_by_username("admin")
    if user is None:
        auth.create_user("admin", args.password, role="admin")
    else:  # keep the run reproducible
        try:
            auth.set_password(getattr(user, "id", user), args.password)
        except Exception as exc:  # pragma: no cover - API shape differs
            print("could not reset admin password:", exc, flush=True)
    print("admin ready:", auth.get_user_by_username("admin") is not None, flush=True)

    # Without this marker every request is redirected to /setup, including
    # /auth/login, which is why the first attempt at this stack could not log in.
    from core.initialization import mark_system_initialized

    mark_system_initialized()

    pathlib.Path(args.out).write_text(json.dumps({
        "base": f"http://127.0.0.1:{args.port}",
        "user": "admin", "password": args.password,
        "db": {k: cfg[k] for k in ("host", "port", "user", "database")},
    }, indent=2), encoding="utf-8")

    from apps.web.app import app

    app.config["TESTING"] = False
    print(f"serving on {args.host}:{args.port}", flush=True)
    app.run(host=args.host, port=args.port, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
