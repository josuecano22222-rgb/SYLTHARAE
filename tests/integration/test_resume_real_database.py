"""Crash recovery against a real PostgreSQL database.

Each scenario runs the ingestion in a subprocess (``resume_worker.py``), kills
it at a storage boundary, restarts it against the same database and checkpoint
directory, and checks the end state:

* every logical file exists exactly once (no duplicate rows);
* the restart stores exactly the work that was not yet durably marked.

The database is a temporary database created for the test and dropped
afterwards. The tests are skipped when PostgreSQL is not reachable; set
``SYLTHARAE_TEST_PG_HOST`` / ``_PORT`` / ``_USER`` to point them elsewhere.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name("resume_worker.py")

psycopg2 = pytest.importorskip("psycopg2")


@pytest.fixture(scope="module")
def pg(pg_settings):
    """The server the rest of the suite uses, not a guess at ``localhost:5432``.

    This module used to read ``SYLTHARAE_TEST_PG_HOST``/``_PORT`` with
    ``localhost:5432`` defaults at import time. The suite provisions its own
    disposable PostgreSQL - often on a unix socket in a temporary directory - so
    the probe never matched and all 26 crash-recovery tests were skipped, leaving
    the area with no integration coverage at all. A skip that means "we could not
    find the server we ourselves started" is not a limit of the environment.

    ``SYLTHARAE_TEST_PG_*`` still wins when it is set, because pointing these
    tests at a specific server is a legitimate thing to want.
    """
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

# Expected logical files for the fixture folder below.
EXPECTED_FILES = {"a.txt", "b.txt", "m1.txt", "m2.txt", "m3.txt", "bundle.zip"}
# Ingestion makes one storage call per logical file (top-level and members).
TOTAL_STORES = len(EXPECTED_FILES)


@pytest.fixture()
def database(pg):
    name = f"sylt_resume_{uuid.uuid4().hex[:10]}"
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
    root = tmp_path / "in"
    root.mkdir()
    (root / "a.txt").write_text("alpha document text")
    (root / "b.txt").write_text("bravo document text")
    with zipfile.ZipFile(root / "bundle.zip", "w") as zf:
        zf.writestr("m1.txt", "member one")
        zf.writestr("m2.txt", "member two")
        zf.writestr("m3.txt", "member three")
    return root


def _run(folder: Path, checkpoint: Path, cfg: dict, **env_faults):
    env = dict(os.environ)
    env.update({
        "DB_HOST": cfg["host"], "DB_PORT": str(cfg["port"]),
        "DB_NAME": cfg["database"], "DB_USER": cfg["user"],
        "PYTHONUNBUFFERED": "1",
    })
    if cfg.get("password"):
        env["DB_PASSWORD"] = cfg["password"]
    for key in ("KILL_BEFORE_STORE", "KILL_AFTER_STORE"):
        env.pop(key, None)
    env.update({k: str(v) for k, v in env_faults.items()})
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(WORKER), str(folder), str(checkpoint)],
        env=env, capture_output=True, text=True, timeout=600,
    )
    return proc, time.monotonic() - started


def _stores_called(proc) -> int:
    for line in proc.stdout.splitlines():
        if line.startswith("STORE_CALLS="):
            return int(line.split("=", 1)[1])
    raise AssertionError(f"worker did not finish normally:\n{proc.stderr[-2000:]}")


def _rows(cfg):
    conn = psycopg2.connect(dbname=cfg["database"], host=cfg["host"], port=cfg["port"],
                            user=cfg["user"], password=cfg["password"])
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT file_name, file_path FROM paths")
            return cur.fetchall()
    finally:
        conn.close()


def _assert_consistent(cfg):
    rows = _rows(cfg)
    names = [name for name, _ in rows]
    assert sorted(set(names)) == sorted(EXPECTED_FILES), names
    assert len(names) == len(set(names)), f"duplicate logical rows: {sorted(names)}"


# ---------------------------------------------------------------------------
# Full run baseline
# ---------------------------------------------------------------------------
def test_full_run_stores_every_logical_file_once(database, folder, tmp_path):
    ckpt = tmp_path / "ck.json"
    proc, _ = _run(folder, ckpt, database)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert _stores_called(proc) == TOTAL_STORES
    _assert_consistent(database)


def test_completed_job_restarted_does_no_storage_work(database, folder, tmp_path):
    ckpt = tmp_path / "ck.json"
    assert _run(folder, ckpt, database)[0].returncode == 0

    proc, elapsed = _run(folder, ckpt, database)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert _stores_called(proc) == 0
    # Without the ledger fix this restart waited 120 s for skipped members.
    assert elapsed < 60, f"completed restart took {elapsed:.0f}s"
    _assert_consistent(database)


# ---------------------------------------------------------------------------
# Termination before each storage write: the k-th file is not written at all.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kill_at", range(1, TOTAL_STORES + 1))
def test_kill_before_store_resumes_at_the_first_unmarked_file(database, folder, tmp_path, kill_at):
    ckpt = tmp_path / "ck.json"
    first, _ = _run(folder, ckpt, database, KILL_BEFORE_STORE=kill_at)
    assert first.returncode != 0  # killed

    restart, _ = _run(folder, ckpt, database)
    assert restart.returncode == 0, restart.stderr[-2000:]
    # Files 1..k-1 were committed and marked before the kill.
    assert _stores_called(restart) == TOTAL_STORES - (kill_at - 1)
    _assert_consistent(database)


# ---------------------------------------------------------------------------
# Termination after the database commit but before the checkpoint mark.
# The file is re-stored on restart; storage must absorb it, not duplicate it.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kill_at", range(1, TOTAL_STORES + 1))
def test_kill_after_commit_before_mark_creates_no_duplicate_rows(database, folder, tmp_path, kill_at):
    ckpt = tmp_path / "ck.json"
    first, _ = _run(folder, ckpt, database, KILL_AFTER_STORE=kill_at)
    assert first.returncode != 0

    restart, _ = _run(folder, ckpt, database)
    assert restart.returncode == 0, restart.stderr[-2000:]
    # File k was committed but not marked, so it is attempted again.
    # committed leaves are skipped on restart (see the committed-leaf tests),
    # so this is an upper bound rather than an exact count
    assert _stores_called(restart) <= TOTAL_STORES - (kill_at - 1)
    _assert_consistent(database)


# ---------------------------------------------------------------------------
# Repeated interruptions at different positions in one job.
# ---------------------------------------------------------------------------
def test_repeated_interruptions_converge_without_duplicates(database, folder, tmp_path):
    ckpt = tmp_path / "ck.json"
    _run(folder, ckpt, database, KILL_BEFORE_STORE=2)
    _run(folder, ckpt, database, KILL_AFTER_STORE=3)
    _run(folder, ckpt, database, KILL_BEFORE_STORE=4)
    final, _ = _run(folder, ckpt, database)
    assert final.returncode == 0, final.stderr[-2000:]
    _assert_consistent(database)


# ---------------------------------------------------------------------------
# Committed-occurrence recovery: a committed leaf is not read again.
#
# Before the occurrence check, a file that was committed but not yet marked in
# the checkpoint went through extraction, parsing and OCR again on restart,
# and the database then absorbed the duplicate store. These tests assert the
# reader work directly (ROUTER_CALLS) and the terminal ledger counts.
# ---------------------------------------------------------------------------
LEAVES = {"a.txt", "b.txt", "m1.txt", "m2.txt", "m3.txt"}


def _worker_fields(proc) -> dict:
    fields = {}
    for line in proc.stdout.splitlines():
        if "=" in line and line.split("=", 1)[0] in ("STORE_CALLS", "ROUTER_CALLS", "LEDGER"):
            key, value = line.split("=", 1)
            fields[key] = value
    assert "ROUTER_CALLS" in fields, f"worker did not finish normally:\n{proc.stderr[-2000:]}"
    fields["ROUTER_CALLS"] = int(fields["ROUTER_CALLS"])
    fields["STORE_CALLS"] = int(fields["STORE_CALLS"])
    import json
    fields["LEDGER"] = json.loads(fields["LEDGER"])
    return fields


@pytest.mark.parametrize("kill_at", range(1, TOTAL_STORES + 1))
def test_committed_leaves_are_not_read_again_after_a_kill_post_commit(database, folder, tmp_path, kill_at):
    ckpt = tmp_path / "ck.json"
    first, _ = _run(folder, ckpt, database, KILL_AFTER_STORE=kill_at)
    assert first.returncode != 0
    committed_top = {name for name, _ in _rows(database)}
    committed = committed_top & LEAVES
    uncommitted_leaves = len(LEAVES - committed)

    restart, _ = _run(folder, ckpt, database)
    assert restart.returncode == 0, restart.stderr[-2000:]
    fields = _worker_fields(restart)
    # bundle.zip is always opened (containers are never skipped), and every
    # leaf that is not committed is read; committed leaves are not.
    assert fields["ROUTER_CALLS"] == 1 + uncommitted_leaves
    # committed leaves, plus bundle.zip itself when its store was already
    # committed (the duplicate store is recorded as skipped)
    expected_skipped = len(committed) + (1 if "bundle.zip" in committed_top else 0)
    assert fields["LEDGER"]["skipped"] == expected_skipped, (fields["LEDGER"], committed)
    assert fields["LEDGER"]["failed"] == 0
    _assert_consistent(database)


def test_completed_job_restart_reads_only_the_container(database, folder, tmp_path):
    ckpt = tmp_path / "ck.json"
    assert _run(folder, ckpt, database)[0].returncode == 0
    restart, _ = _run(folder, ckpt, database)
    assert restart.returncode == 0, restart.stderr[-2000:]
    fields = _worker_fields(restart)
    # the completed bundle.zip and both top-level leaves are checkpoint-skipped
    # before any read; no member is opened and nothing is stored
    assert fields["ROUTER_CALLS"] == 0, fields
    assert fields["STORE_CALLS"] == 0, fields
    assert fields["LEDGER"]["skipped"] == 3, fields["LEDGER"]
    assert fields["LEDGER"]["failed"] == 0
    _assert_consistent(database)


def test_changed_source_is_processed_again_and_the_old_occurrence_is_kept(database, folder, tmp_path):
    ckpt = tmp_path / "ck.json"
    assert _run(folder, ckpt, database)[0].returncode == 0
    (folder / "a.txt").write_text("alpha document text, revised")

    restart, _ = _run(folder, ckpt, database)
    assert restart.returncode == 0, restart.stderr[-2000:]
    fields = _worker_fields(restart)
    assert fields["ROUTER_CALLS"] == 1  # only the changed a.txt; bundle.zip is complete
    names = [name for name, _ in _rows(database)]
    assert names.count("a.txt") == 2  # new content is a new occurrence; old row is left in place
    assert sorted(set(names)) == sorted(EXPECTED_FILES)


def test_moved_source_is_processed_again(database, folder, tmp_path):
    ckpt = tmp_path / "ck.json"
    assert _run(folder, ckpt, database)[0].returncode == 0
    (folder / "sub").mkdir()
    (folder / "b.txt").rename(folder / "sub" / "b.txt")

    restart, _ = _run(folder, ckpt, database)
    assert restart.returncode == 0, restart.stderr[-2000:]
    fields = _worker_fields(restart)
    assert fields["ROUTER_CALLS"] == 1  # only the moved b.txt; bundle.zip is complete
    paths = [path for _, path in _rows(database)]
    # identical content is one logical row; the moved copy is not stored twice
    _assert_consistent(database)


@pytest.mark.xfail(strict=True, reason="known gap: a moved file whose content is already "
                   "stored is treated as a content duplicate and its new location is not recorded")
def test_moved_duplicate_content_location_is_recorded(database, folder, tmp_path):
    ckpt = tmp_path / "ck.json"
    assert _run(folder, ckpt, database)[0].returncode == 0
    (folder / "sub").mkdir()
    (folder / "b.txt").rename(folder / "sub" / "b.txt")
    restart, _ = _run(folder, ckpt, database)
    assert restart.returncode == 0, restart.stderr[-2000:]
    assert any("sub" in p for p in (path for _, path in _rows(database)))


def test_truncated_journal_tail_still_resumes_without_duplicates(database, folder, tmp_path):
    ckpt = tmp_path / "ck.json"
    first, _ = _run(folder, ckpt, database, KILL_AFTER_STORE=3)
    assert first.returncode != 0
    # Simulate a power cut in the middle of the last journal line.
    journal = ckpt.with_suffix(ckpt.suffix + ".journal")
    data = journal.read_bytes()
    journal.write_bytes(data[:-3])

    restart, _ = _run(folder, ckpt, database)
    assert restart.returncode == 0, restart.stderr[-2000:]
    _assert_consistent(database)
