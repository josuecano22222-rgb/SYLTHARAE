"""Provision a disposable PostgreSQL database for runtime verification.

    python tools/verify/runtime_provision.py <state-dir>

Starts a pgserver instance under ``<state-dir>/pg`` (left running), creates a
database through the application's own bootstrap (all migrations), creates
an administrator, a viewer and two analysts, and stores documents through the real
ingestion service (``ContentDBService.process_full_document``: detection runs
at ingestion). Writes ``<state-dir>/env.json`` with the ``DB_*`` variables
and the credentials the server and ``runtime_check_signals.py`` need.

Not a test fixture: the point is to run the *server process* against it.
"""

import datetime
import json
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DOCUMENTS = {
    "alpha": ("The review will be held on 5 October 2026.\n"
              "The deadline will be 1 September 2026.\n"
              "The treaty will expire on 1 June 2027.\n"
              "The meeting is on 03/04/2026.\n"
              "سيعقد الاجتماع في 20 رمضان 1448 هـ.\n"
              "Delegates flew in from Zagreb and Tripoli.\n"),
    "beta": ("השיחות עברו ובירושלים ויתחדשו ב-12 באוקטובר 2026.\n"
             "جلسه در ۱۵ مهر ۱۴۰۵ برگزار خواهد شد.\n"
             "Sastanak će se održati 3. studenoga 2026. u Rijeci.\n"),
}


def main(state_dir: str) -> None:
    import pgserver

    state = Path(state_dir).resolve()
    state.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(str(state / "pg"), cleanup_mode=None)
    parsed = urllib.parse.urlparse(server.get_uri())
    host = urllib.parse.parse_qs(parsed.query).get("host", [None])[0] or parsed.hostname
    env = {"DB_HOST": host, "DB_PORT": str(parsed.port or 5432), "DB_USER": "postgres",
           "DB_PASSWORD": "", "DB_NAME": "syltharae_runtime"}
    os.environ.update(env)

    from database.bootstrap import bootstrap_database

    report = bootstrap_database({"host": host, "port": int(env["DB_PORT"]), "user": "postgres",
                                 "password": "", "database": env["DB_NAME"]})
    print("bootstrap:", {k: report.get(k) for k in ("database_created", "applied_migrations")})

    from core.security.service import get_auth_service

    auth = get_auth_service()
    users = {"admin": ("rt_admin", "runtime-admin-password-1"),
             "viewer": ("rt_viewer", "runtime-viewer-password-1"),
             # Two analysts: monitoring-rule notifications must stay with
             # their owner (tools/verify/runtime_check_rules.py).
             "analyst": ("rt_analyst", "runtime-analyst-password-1"),
             "analyst2": ("rt_analyst2", "runtime-analyst2-password-1")}
    for key, (username, password) in users.items():
        if auth.get_user_by_username(username) is None:
            auth.create_user(username, password, role=key.rstrip("2"))

    import psycopg2

    conn = psycopg2.connect(host=host, port=int(env["DB_PORT"]), user="postgres",
                            dbname=env["DB_NAME"])
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM paths")
        already = cur.fetchone()[0]
        ids = {}
        for key in DOCUMENTS:
            cur.execute("SELECT id FROM sources WHERE name = %s", (f"rt_source_{key}",))
            row = cur.fetchone()
            if row is None:
                cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                            " VALUES (%s, 'rt', 0.5, 'rt', CURRENT_DATE) RETURNING id",
                            (f"rt_source_{key}",))
                row = cur.fetchone()
            ids[f"source_{key}"] = row[0]
            cur.execute("SELECT id FROM sides WHERE name = %s", (f"rt_side_{key}",))
            row = cur.fetchone()
            if row is None:
                cur.execute("INSERT INTO sides (name, importance, date_creation)"
                            " VALUES (%s, 0.5, CURRENT_DATE) RETURNING id", (f"rt_side_{key}",))
                row = cur.fetchone()
            ids[f"side_{key}"] = row[0]
    conn.close()

    if not already:
        from database.services.contents_db_service import ContentDBService

        for key, text in DOCUMENTS.items():
            result = ContentDBService().process_full_document(
                hash_value=f"rt{key}".ljust(64, "0"), source_id=ids[f"source_{key}"],
                side_id=ids[f"side_{key}"], file_name=f"{key}.txt",
                file_path=f"/runtime/{key}.txt", file_size=len(text.encode()), file_type="txt",
                file_status="Read", file_date=datetime.date(2026, 1, 1),
                content_words=["runtime", key], raw_text=text, attempts=1)
            ids[f"hash_{key}"] = result["hash_id"]
            print("stored", key, {k: result.get(k) for k in ("hash_id", "signals",
                                                             "place_signals")})
    (state / "env.json").write_text(json.dumps({"env": env, "users": users, "ids": ids},
                                               indent=2))
    print("wrote", state / "env.json")


if __name__ == "__main__":
    # Without an argument: a fresh private directory (mode 0700) rather than
    # a fixed, predictable path in the shared temp directory (bandit B108).
    main(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="syltharae_runtime_"))
