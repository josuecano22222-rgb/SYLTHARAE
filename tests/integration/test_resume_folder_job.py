"""Retrying an interrupted **folder** ingestion must resume, not restart.

The operator report: *"if the system crashes, shuts down, or if operations are
interrupted while processing a batch of files, clicking 'Retry' causes the
process to start from the beginning rather than resuming from the last
completed checkpoint."*

Cause (fixed in ``IngestionService._make_reader``)
--------------------------------------------------
The service decided whether to hand the reader a ``checkpoint_file`` by asking
for a parameter of that *name*::

    if "checkpoint_file" in inspect.signature(factory).parameters:

but its own default factory is ``def _default_reader_factory(**kwargs)``, and
``inspect.signature`` reports ``**kwargs`` as a single VAR_KEYWORD entry called
``kwargs``. The name was never found, so **no ingestion started through the job
manager ever received a checkpoint**. Nothing was written to the checkpoints
directory, discovery reported ``skipped=0`` on every restart, and containers
(archives, mailboxes) — which the database fallback in ``_skip_committed``
deliberately does not cover — were re-extracted in full.

Why the existing tests missed it
--------------------------------
``tests/integration/test_resume_real_database.py`` builds
``IntegratedFileReader`` directly and passes ``checkpoint_file=`` itself, so it
exercised the reader but never the service. These tests drive the real path:
``JobManager.create_job`` -> crash -> ``JobManager.retry``.

They are skipped when PostgreSQL is not reachable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name("resume_job_worker.py")

psycopg2 = pytest.importorskip("psycopg2")


@pytest.fixture(scope="module")
def pg(pg_settings):
    """The disposable PostgreSQL the suite provisions (see the sibling module)."""
    return {
        "host": os.environ.get("SYLTHARAE_TEST_PG_HOST") or pg_settings["host"],
        "port": int(os.environ.get("SYLTHARAE_TEST_PG_PORT") or pg_settings["port"]),
        "user": os.environ.get("SYLTHARAE_TEST_PG_USER") or pg_settings["user"],
        "password": (os.environ.get("SYLTHARAE_TEST_PG_PASSWORD")
                     if os.environ.get("SYLTHARAE_TEST_PG_PASSWORD") is not None
                     else pg_settings.get("password") or ""),
    }


def _admin_conn(pg, dbname="postgres"):
    conn = psycopg2.connect(dbname=dbname, host=pg["host"], port=pg["port"],
                            user=pg["user"], password=pg["password"], connect_timeout=3)
    conn.autocommit = True
    return conn


@pytest.fixture()
def database(pg):
    name = f"sylt_jobresume_{uuid.uuid4().hex[:10]}"
    admin = _admin_conn(pg)
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()

    cfg = dict(pg, database=name)
    sys.path.insert(0, str(REPO))
    from database.bootstrap import bootstrap_database
    bootstrap_database(cfg)
    try:
        yield cfg
    finally:
        admin = _admin_conn(pg)
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


@pytest.fixture()
def folder(tmp_path):
    """Plain files plus archive containers.

    Containers matter: ``_skip_committed`` refuses to skip them, so without a
    checkpoint every archive is re-extracted on a retry even when it was fully
    stored before the crash.
    """
    root = tmp_path / "in"
    root.mkdir()
    for i in range(1, 5):
        (root / f"plain{i}.txt").write_text(f"plain document {i} " * 60)
    for z in range(1, 4):
        with zipfile.ZipFile(root / f"bundle{z}.zip", "w") as zf:
            for m in range(1, 4):
                zf.writestr(f"m{z}{m}.txt", f"member {z}{m} " * 60)
    return root


def _run(mode, folder, cfg, data_dir, job_id=None, kill_after=None):
    env = dict(os.environ)
    env.update({
        "DB_HOST": cfg["host"], "DB_PORT": str(cfg["port"]),
        "DB_NAME": cfg["database"], "DB_USER": cfg["user"],
        "PYTHONUNBUFFERED": "1",
        # Server-path ingestion is allow-listed (SEC-06); /tmp is the fixture root.
        "INGESTION_ROOTS": str(folder.parent),
        "APP_DATA_DIR": str(data_dir),
    })
    if cfg.get("password"):
        env["DB_PASSWORD"] = cfg["password"]
    env.pop("KILL_AFTER_STORE", None)
    if kill_after:
        env["KILL_AFTER_STORE"] = str(kill_after)

    args = [sys.executable, str(WORKER), mode, str(folder)]
    if job_id:
        args.append(job_id)
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=900)


def _field(proc, key):
    for line in proc.stdout.splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    raise AssertionError(f"worker did not report {key}:\n{proc.stdout}\n{proc.stderr[-2000:]}")


def _rows(cfg):
    conn = psycopg2.connect(dbname=cfg["database"], host=cfg["host"], port=cfg["port"],
                            user=cfg["user"], password=cfg["password"])
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT file_name FROM paths ORDER BY 1")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def test_a_job_ingestion_writes_a_checkpoint(database, folder, tmp_path):
    """The defect, stated directly: without this, resume has nothing to resume."""
    data_dir = tmp_path / "appdata"
    proc = _run("create", folder, database, data_dir)
    assert proc.returncode == 0, proc.stderr[-2000:]

    files = json.loads(_field(proc, "CHECKPOINT_FILES"))
    # Paths are relative to the checkpoints directory: the run's own snapshot
    # (``checkpoint_<digest>_<source>_<side>.json``) and one file per container
    # under ``<snapshot>.members/``.
    assert any(f.endswith("_resume-test_resume-test.json") for f in files), (
        "a folder ingestion started through the job manager must write a "
        f"checkpoint; it wrote {files!r}. Without one, an interrupted run has "
        "nothing to resume from and starts again from the beginning."
    )


def test_retry_after_a_crash_resumes_instead_of_restarting(database, folder, tmp_path):
    """Crash mid-folder, click Retry: already-processed work must not be redone."""
    data_dir = tmp_path / "appdata"

    first = _run("create", folder, database, data_dir, kill_after=7)
    assert first.returncode != 0, "the crash run should have died"
    stored_before = len(_rows(database))
    assert stored_before > 0, "the crash should have committed some files"

    retry = _run("retry", folder, database, data_dir, job_id=_field(first, "JOB_ID"))
    assert retry.returncode == 0, retry.stderr[-2000:]
    assert _field(retry, "STATUS") == "COMPLETED"

    # Discovery must recognise the work that is already done.
    skipped = int(_field(retry, "TOP_LEVEL_SKIPPED"))
    assert skipped > 0, (
        "the retry walked the whole folder again (0 files recognised as "
        "already processed) instead of resuming from the checkpoint"
    )

    # And the store count must cover only what was left.
    stores = int(_field(retry, "STORE_CALLS"))
    assert stores < 13, f"the retry stored {stores} of 13 logical files; it did not resume"


def test_retry_after_a_crash_stores_every_file_exactly_once(database, folder, tmp_path):
    """Resume must not lose or duplicate work."""
    data_dir = tmp_path / "appdata"
    first = _run("create", folder, database, data_dir, kill_after=7)
    assert first.returncode != 0

    retry = _run("retry", folder, database, data_dir, job_id=_field(first, "JOB_ID"))
    assert retry.returncode == 0, retry.stderr[-2000:]

    rows = _rows(database)
    expected = {"plain1.txt", "plain2.txt", "plain3.txt", "plain4.txt",
                "bundle1.zip", "bundle2.zip", "bundle3.zip",
                "m11.txt", "m12.txt", "m13.txt",
                "m21.txt", "m22.txt", "m23.txt",
                "m31.txt", "m32.txt", "m33.txt"}
    assert set(rows) == expected, f"missing={expected - set(rows)} extra={set(rows) - expected}"
    assert len(rows) == len(expected), f"duplicate rows: {len(rows)} != {len(expected)}"
