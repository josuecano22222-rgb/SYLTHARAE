"""The private, bundled PostgreSQL cluster for offline Windows operation.

SYLTHARAE's data model is PostgreSQL's; the offline deployment ships a real
PostgreSQL instead of swapping in another database engine. The ``pgserver``
wheel carries complete PostgreSQL 16 server binaries (initdb, postgres, client
tools) for Windows, so the bundle needs no database installer, no Windows
service and no network: the cluster lives entirely inside the application data
directory, is created on first start, and is restarted from the same files on
every later start.

Usage:
    python tools/windows/local_postgres.py --data-dir D --init      # first start
    python tools/windows/local_postgres.py --data-dir D --uri       # print DB_* env
    python tools/windows/local_postgres.py --data-dir D --status
    python tools/windows/local_postgres.py --data-dir D --stop

The ``--uri`` output is a set of DB_* environment lines the start script
exports before launching ``run_web.py`` (the same variables the setup wizard
and ``install.py`` consume).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _server(data_dir: Path):
    try:
        import pgserver
    except ImportError as error:  # pragma: no cover - packaging guard
        raise SystemExit(
            "pgserver is not installed; the offline wheelhouse ships it "
            f"(pip install --no-index --find-links wheels pgserver): {error}"
        ) from error
    data_dir.mkdir(parents=True, exist_ok=True)
    # cleanup_mode=None: never delete the data directory — it is the
    # deployment's persistent store, not a disposable test cluster.
    return pgserver.get_server(str(data_dir), cleanup_mode=None)


def _uri_parts(uri: str) -> dict:
    """Split pgserver's URI into the DB_* fields the application consumes."""
    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(uri)
    query = parse_qs(parts.query)
    host = (query.get("host") or [parts.hostname or "127.0.0.1"])[0]
    port = str((query.get("port") or [parts.port or 5432])[0])
    return {
        "DB_HOST": host,
        "DB_PORT": port,
        "DB_USER": parts.username or "postgres",
        "DB_PASSWORD": parts.password or "",
        "DB_NAME": (parts.path or "/postgres").lstrip("/") or "postgres",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, type=Path,
                        help="cluster directory inside the application data directory")
    parser.add_argument("--init", action="store_true",
                        help="create the cluster if it does not exist yet")
    parser.add_argument("--uri", action="store_true",
                        help="print DB_* environment lines for the running cluster")
    parser.add_argument("--json", action="store_true",
                        help="print the DB_* fields as JSON instead of env lines")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()

    if args.stop:
        server = _server(args.data_dir)
        server.stop()
        print("stopped")
        return 0

    server = _server(args.data_dir)
    if args.init:
        # get_server() initialises the datadir on first use; a second --init
        # on an existing cluster is a no-op that proves the files are usable.
        uri = server.get_uri()
        print(f"cluster ready at {args.data_dir}")
        print("DB part of the URI is available through --uri")

    parts = _uri_parts(server.get_uri())
    if args.json:
        print(json.dumps(parts, indent=1))
        return 0
    if args.uri or args.init or not args.status:
        for key, value in parts.items():
            print(f"{key}={value}")
        return 0
    if args.status:
        import psycopg2

        try:
            conn = psycopg2.connect(
                host=parts["DB_HOST"], port=parts["DB_PORT"],
                user=parts["DB_USER"], password=parts["DB_PASSWORD"],
                dbname="postgres", connect_timeout=5,
            )
            with conn.cursor() as cur:
                cur.execute("SHOW server_version;")
                version = cur.fetchone()[0]
            conn.close()
            print(f"running: PostgreSQL {version}")
            return 0
        except Exception as error:
            print(f"not reachable: {error}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
