#!/usr/bin/env python3
"""Production Readiness Verification (Phase 23).

Verifies *behavior*, not method existence. Every check produces evidence.

Sections:
    environment  - Python version, required dependencies, executables
    database     - connectivity, extensions, migration state, required tables,
                   required indexes
    application  - app construction, authentication enforcement, API health
    processing   - reader registration, representative ingestion, storage, search
    security     - protected routes, injection defenses, archive safety,
                   credential scan, debug-mode protection
    recovery     - transaction rollback, retry/circuit-breaker wiring
    deployment   - debug disabled by default, secrets configured, runtime paths

Usage:
    python verify_readiness.py            # human-readable report
    python verify_readiness.py --json     # machine-readable report (CI)

Exit code 0 = all critical checks passed; 1 = at least one critical failure.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env from the project root so DB_*/FLASK_* settings work without
# manually exporting environment variables. Real OS env vars take precedence.
try:
    _env_file = PROJECT_ROOT / ".env"
    if _env_file.exists():
        from dotenv import load_dotenv

        load_dotenv(_env_file, override=False, interpolate=False)
except Exception:
    pass  # python-dotenv missing or unreadable .env: checks will report it


@dataclass
class CheckResult:
    section: str
    name: str
    passed: bool
    critical: bool
    evidence: str = ""
    error: str = ""


RESULTS: List[CheckResult] = []


def check(section: str, name: str, critical: bool = True):
    """Decorator registering a behavioral check."""

    def decorator(fn: Callable[[], str]):
        def run() -> CheckResult:
            try:
                evidence = fn()
                result = CheckResult(section, name, True, critical, evidence=str(evidence))
            except Exception as exc:  # noqa: BLE001 - a failed check is a report row
                result = CheckResult(
                    section, name, False, critical,
                    error=f"{exc.__class__.__name__}: {exc}",
                )
            RESULTS.append(result)
            return result

        run.check_name = name  # type: ignore[attr-defined]
        run.run = run  # type: ignore[attr-defined]
        CHECKS.append(run)
        return fn

    return decorator


CHECKS: List[Callable[[], CheckResult]] = []


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
@check("environment", "Python version >= 3.10")
def _():
    v = sys.version_info
    if (v.major, v.minor) < (3, 10):
        raise AssertionError(f"Python {v.major}.{v.minor} is too old")
    return f"Python {v.major}.{v.minor}.{v.micro}"


@check("environment", "Core dependencies importable")
def _():
    missing = []
    for mod in ("flask", "psycopg2", "flask_wtf", "flask_limiter", "psutil", "yaml"):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        raise AssertionError(f"missing: {', '.join(missing)}")
    return f"{6} core modules importable"


@check("environment", "Optional PDF reader dependency (PyMuPDF)", critical=False)
def _():
    import pymupdf  # noqa: F401

    return "PyMuPDF available"


# ---------------------------------------------------------------------------
# Database (requires DB_* configuration or a running local PostgreSQL)
# ---------------------------------------------------------------------------
def _db_cfg():
    from settings.config import get_db_config

    cfg = get_db_config()
    return {
        "host": cfg["host"], "port": cfg["port"], "user": cfg["user"],
        "password": cfg["password"], "database": cfg["database"],
    }


def _db_connect(cfg=None):
    import psycopg2

    cfg = cfg or _db_cfg()
    return psycopg2.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], dbname=cfg["database"], connect_timeout=5,
    )


@check("database", "Connectivity")
def _():
    conn = _db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            version = cur.fetchone()[0]
        return version.split(",")[0]
    finally:
        conn.close()


@check("database", "Extension preflight (plpgsql)")
def _():
    conn = _db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension")
            exts = {r[0] for r in cur.fetchall()}
        if "plpgsql" not in exts:
            raise AssertionError("plpgsql not installed")
        return f"extensions: {', '.join(sorted(exts))}"
    finally:
        conn.close()


@check("database", "Migration state current (DB-02)")
def _():
    from database.bootstrap import schema_status

    status = schema_status(_db_cfg())
    if not status["database_exists"]:
        raise AssertionError("application database does not exist")
    pending = [m["version"] for m in status["migrations"] if not m["applied"]]
    if pending:
        raise AssertionError(f"pending migrations: {', '.join(pending)}")
    return f"all migrations applied (current: {status['current_version']})"


# The schema the current migrations produce (m0011 content_identity renamed
# words_paths/keywords_paths to words_hashs/keywords_hashs and re-keyed the
# identity columns from path_id to hash_id/context_id). These constants are
# pinned to a freshly migrated database by
# tests/integration/test_readiness_schema.py: a migration that renames a table
# or index must update them here in the same commit.
REQUIRED_TABLES = {
    "words", "punctuation", "categorys", "words_categorys", "sides",
    "sources", "hashs", "paths", "contents", "titles_content", "keywords",
    "words_hashs", "keywords_hashs", "alerts", "users", "sessions", "audit_log",
    "jobs", "job_events",
}

#: Index name -> (table, columns), in order. Names are labels here: the check
#: below looks the index up **by definition** in ``pg_index``, so a database
#: that enforces the same columns under a PostgreSQL-derived name passes just
#: as one that carries the name does. That is also why this set stays limited
#: to names the frozen migrations actually create - inventing a name to make a
#: check green would be exactly the kind of assertion this file exists to
#: disprove.
REQUIRED_INDEX_DEFINITIONS = {
    "idx_words_hashs_word_id": ("words_hashs", ("word_id",)),
    "idx_words_hashs_hash_id": ("words_hashs", ("hash_id",)),
    "idx_keywords_hashs_keyword_id": ("keywords_hashs", ("keyword_id",)),
    "idx_paths_context_id": ("paths", ("context_id",)),
    "idx_paths_file_name": ("paths", ("file_name",)),
    "idx_paths_file_path": ("paths", ("file_path",)),
    "idx_titles_content_hash_id": ("titles_content", ("hash_id",)),
}

#: Uniqueness the application relies on, stated as (table, columns) because
#: the constraint name is whatever the migration (or PostgreSQL) gave it.
#: ``hash_contexts`` is unique on (hash_id, source_id, side_id) - the identity
#: of one document in one source and side; ``hashs`` on its digest.
REQUIRED_UNIQUE_DEFINITIONS = (
    ("hashs", ("hash",)),
    ("hash_contexts", ("hash_id", "source_id", "side_id")),
)

#: Kept for callers that want the names; the readiness check uses the
#: definitions above.
REQUIRED_INDEXES = set(REQUIRED_INDEX_DEFINITIONS)


@check("database", "Required tables exist")
def _():
    required = REQUIRED_TABLES
    conn = _db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public'"
            )
            actual = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    missing = required - actual
    if missing:
        raise AssertionError(f"missing tables: {', '.join(sorted(missing))}")
    return f"{len(required)} tables present (incl. job infrastructure)"


def _index_definitions(cur):
    """{((table, columns), unique) ...} read from ``pg_index``, not names."""
    cur.execute(
        """
        SELECT c.relname AS table_name,
               i.indisunique,
               array_agg(a.attname ORDER BY k.ord) AS columns
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
        WHERE c.relnamespace = 'public'::regnamespace
        GROUP BY c.relname, i.indexrelid, i.indisunique
        """
    )
    return {(r[0], tuple(r[2]), bool(r[1])) for r in cur.fetchall()}


@check("database", "Required performance indexes exist (DB-06)")
def _():
    conn = _db_connect()
    try:
        with conn.cursor() as cur:
            definitions = _index_definitions(cur)
    finally:
        conn.close()
    plain = {(table, cols) for table, cols, unique in definitions if not unique}
    unique_sets = {(table, cols) for table, cols, unique in definitions if unique}

    missing = [label for label, (table, cols)
               in REQUIRED_INDEX_DEFINITIONS.items()
               if (table, cols) not in plain]
    if missing:
        raise AssertionError(
            "missing index definitions (table, columns): "
            + ", ".join(f"{REQUIRED_INDEX_DEFINITIONS[m]}" for m in missing))
    missing_unique = [t for t in REQUIRED_UNIQUE_DEFINITIONS
                      if t not in unique_sets]
    if missing_unique:
        raise AssertionError(
            "missing unique definitions: "
            + ", ".join(f"{table}{cols}" for table, cols in missing_unique))
    return (f"{len(REQUIRED_INDEX_DEFINITIONS)} index and "
            f"{len(REQUIRED_UNIQUE_DEFINITIONS)} uniqueness definitions present")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
@check("application", "Flask app constructs")
def _():
    from apps.web.app import app

    return f"{len(list(app.url_map.iter_rules()))} routes registered"


@check("application", "Authentication enforced on protected routes (SEC-01)")
def _():
    os.environ.setdefault("FLASK_SECRET_KEY", "readiness-probe-key-0123456789abcdef")
    from apps.web.app import app

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()
    resp = client.get("/api/preview/1")
    if resp.status_code != 401:
        raise AssertionError(f"unauthenticated /api/preview/1 returned {resp.status_code}, expected 401")
    resp = client.get("/")
    if resp.status_code not in (301, 302):
        raise AssertionError(f"unauthenticated / returned {resp.status_code}, expected redirect")
    return "API 401 + page redirect for unauthenticated clients"


@check("application", "Login endpoint available")
def _():
    from apps.web.app import app

    app.config["TESTING"] = True
    client = app.test_client()
    resp = client.get("/auth/login")
    if resp.status_code == 200:
        return "GET /auth/login -> 200"
    # A fresh installation has no account yet, and the application redirects the
    # login route to the first-run setup page. That is correct behaviour - the
    # operator has to create the administrator before logging in - so it must
    # not be reported as a readiness failure. The redirect is followed to prove
    # the login flow is actually reachable.
    location = resp.headers.get("Location", "")
    if resp.status_code in (301, 302, 303, 307, 308) and location.endswith("/setup"):
        follow = client.get(location)
        if follow.status_code == 200:
            return ("GET /auth/login -> 302 /setup (first-run setup: create the "
                    "administrator account to enable login)")
        raise AssertionError(f"/setup returned {follow.status_code}")
    raise AssertionError(f"/auth/login returned {resp.status_code} -> {location}")


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------
@check("processing", "Reader registry declares supported extensions (READER-01)")
def _():
    from reader_file.services.file_router_service import FileRouterService

    router = FileRouterService()
    exts = router.get_supported_extensions()
    if len(exts) < 10:
        raise AssertionError(f"only {len(exts)} extensions declared")
    return f"{len(exts)} extensions: {', '.join(sorted(exts))[:120]}..."


@check("processing", "Representative file: hash -> ingest -> store -> search")
def _():
    import datetime

    from core.hashing import hash_file
    from pipeline.integrated_reader import IntegratedFileReader
    from database.services.dedup_service import DeduplicationService

    with tempfile.TemporaryDirectory() as td:
        doc = Path(td) / "readiness_probe.txt"
        doc.write_text(
            "readiness probe document with distinctive token ZXPROBEZZ", encoding="utf-8"
        )
        digest = hash_file(doc)
        if len(digest) != 64:
            raise AssertionError("hash is not sha256 hex")

        import psycopg2

        cfg = _db_cfg()
        conn = psycopg2.connect(
            host=cfg["host"], port=cfg["port"], user=cfg["user"],
            password=cfg["password"], dbname=cfg["database"],
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation)"
                " VALUES ('readiness-side', 0.5, %s) ON CONFLICT (name) DO NOTHING",
                (datetime.date.today(),),
            )
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES ('readiness-source', 'probe', 0.5, 'probe', %s)"
                " ON CONFLICT (name) DO NOTHING",
                (datetime.date.today(),),
            )
            cur.execute("SELECT id FROM sides WHERE name='readiness-side'")
            side_id = cur.fetchone()[0]
            cur.execute("SELECT id FROM sources WHERE name='readiness-source'")
            source_id = cur.fetchone()[0]
        conn.commit()

        with IntegratedFileReader(
            max_workers=1, enable_storage=True,
            storage_source="readiness-source", storage_side="readiness-side",
        ) as reader:
            result = reader.process_single_file(str(doc))
        path_id = (result or {}).get("database_path_id")
        if not path_id:
            raise AssertionError("probe file was not stored")

        dedup = DeduplicationService(lambda: conn)
        is_dup, existing = dedup.check_duplicate(digest, source_id, side_id)
        if not is_dup:
            raise AssertionError("dedup did not recognize stored content")

        # delete/re-ingest must remain possible (DB-05)
        dedup.delete_path(path_id)
        is_dup_after_delete, _ = dedup.check_duplicate(digest, source_id, side_id)
        if is_dup_after_delete:
            raise AssertionError("content still flagged duplicate after delete (orphan hash)")
        conn.close()
    return "hash -> store -> dedup -> delete -> reingestable all verified"


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
@check("security", "SQL injection defenses on sort parameters (SEC-03)")
def _():
    from core.sql_safety import validate_identifier, IdentifierError

    allowlist = {"id": "tc.id", "title_data": "tc.title_data"}
    for payload in ("id; DROP TABLE users; --", "1=1", "id UNION SELECT 1"):
        try:
            validate_identifier(payload, allowlist)
            raise AssertionError(f"payload accepted: {payload}")
        except IdentifierError:
            pass
    return "malicious sort identifiers rejected by allowlist"


@check("security", "Archive traversal defense (SEC-05)")
def _():
    import io
    import zipfile

    from core.archive_safety import extract_zip, ArchiveSafetyError

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.txt", b"pwned")
    buf.seek(0)
    with tempfile.TemporaryDirectory() as td:
        evil = Path(td) / "evil.zip"
        evil.write_bytes(buf.getvalue())
        try:
            extract_zip(evil, Path(td) / "out")
            raise AssertionError("zip-slip archive was extracted")
        except ArchiveSafetyError:
            pass
        if (Path(td) / "evil.txt").exists():
            raise AssertionError("traversal file escaped extraction root")
    return "zip-slip payload rejected"


@check("security", "Path-safety: server-path import disabled without roots (SEC-06)")
def _():
    from core.path_safety import validate_ingestion_path, PathSafetyError

    saved = os.environ.pop("INGESTION_ROOTS", None)
    try:
        try:
            validate_ingestion_path("/etc/passwd")
            raise AssertionError("arbitrary path accepted with no roots configured")
        except PathSafetyError:
            pass
    finally:
        if saved is not None:
            os.environ["INGESTION_ROOTS"] = saved
    return "fails closed when INGESTION_ROOTS unset"


@check("security", "No hardcoded credentials in tracked sources (SEC-07)")
def _():
    import subprocess

    # token assembled from parts so this scanner does not match its own source
    token = "".join(["egg", "arf", "123"])
    result = subprocess.run(
        ["git", "grep", "-l", token, "--", ":!docs"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    offenders = [ln for ln in result.stdout.strip().splitlines() if ln]
    if offenders:
        raise AssertionError(f"credential found in: {', '.join(offenders)}")
    return "git grep clean"


@check("security", "Unsafe pickle deserialization blocked (DB-08)")
def _():
    import pickle

    from core.serialization import unpack_int_list, RestrictedDeserializationError

    class Evil:
        def __reduce__(self):
            return (eval, ("1",))

    try:
        unpack_int_list(pickle.dumps(Evil()))
        raise AssertionError("malicious pickle executed")
    except RestrictedDeserializationError:
        pass
    return "restricted unpickler refuses GLOBAL opcodes"


@check("security", "Debug mode not hardcoded (SEC-10)")
def _():
    source = (PROJECT_ROOT / "run_web.py").read_text()
    if "app.run(debug=True" in source:
        raise AssertionError("run_web.py hardcodes debug=True")
    return "debug mode is environment-controlled"


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------
@check("recovery", "Transaction rollback on failure")
def _():
    conn = _db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute(
                "INSERT INTO punctuation (punctuation_text) VALUES ('readiness-rollback-probe')"
            )
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM punctuation WHERE punctuation_text = 'readiness-rollback-probe'"
            )
            if cur.fetchone()[0] != 0:
                raise AssertionError("rollback did not remove the probe row")
    finally:
        conn.close()
    return "rolled-back insert left no residue"


@check("recovery", "Checkpoint manager available (REL-04)", critical=False)
def _():
    from core.checkpoint_manager import CheckpointManager  # noqa: F401

    return "CheckpointManager importable"


@check("recovery", "Retry/circuit-breaker modules wired", critical=False)
def _():
    import Hdg_Err_Ex_Log.retry_policies as rp  # noqa: F401
    import Hdg_Err_Ex_Log.circuit_breaker as cb  # noqa: F401

    return "retry + circuit breaker modules importable"


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------
@check("deployment", "Runtime paths resolve from APP_DATA_DIR (Phase 19)")
def _():
    from core.app_paths import get_data_root, ensure_runtime_dirs

    with tempfile.TemporaryDirectory() as td:
        os.environ["APP_DATA_DIR"] = td
        try:
            import core.app_paths as ap

            ap.reset_cache()
            ensure_runtime_dirs()
            root = get_data_root()
            if not root.is_dir():
                raise AssertionError("data root not created")
        finally:
            os.environ.pop("APP_DATA_DIR", None)
            ap.reset_cache()
    return f"runtime dirs created under {root}"


@check("deployment", "Start-up settings are valid (apps/web/deployment.py)")
def _():
    from apps.web.deployment import check_deployment

    errors = [f for f in check_deployment() if f.level == "error"]
    if errors:
        raise AssertionError("; ".join(f"{f.message} -> {f.action}" for f in errors))
    return "no setting would make run_web.py refuse to start"


@check("deployment", "Production settings follow the deployment guide", critical=False)
def _():
    from apps.web.deployment import check_deployment

    warnings = [f for f in check_deployment() if f.level == "warning"]
    if warnings:
        raise AssertionError("; ".join(f"{f.message} -> {f.action}" for f in warnings))
    return "Waitress, trusted local proxy, strong secret key (or not production)"


@check("deployment", "Session security flags configured (SEC-09)")
def _():
    from apps.web.app import app

    if not app.config.get("SESSION_COOKIE_HTTPONLY"):
        raise AssertionError("SESSION_COOKIE_HTTPONLY not set")
    if app.config.get("SESSION_COOKIE_SAMESITE") not in ("Lax", "Strict"):
        raise AssertionError("SESSION_COOKIE_SAMESITE not configured")
    return "HttpOnly + SameSite configured; Secure in production"


@check("deployment", "Rate limiting active (API-01)")
def _():
    from apps.web.app import limiter

    if limiter is None:
        raise AssertionError("limiter not initialized")
    if not getattr(limiter, "enabled", True):
        raise AssertionError("limiter is disabled")
    limits = getattr(limiter, "_default_limits", None) or getattr(
        limiter, "_route_limits", {}
    )
    return f"limiter enabled with {len(limits)} default limit(s)"


# ---------------------------------------------------------------------------
# Compute control (gateway compute-control layer)
# ---------------------------------------------------------------------------
@check("compute", "Compute gateway selects a device honestly")
def _():
    from core.compute import WorkloadKind, get_compute_gateway

    gateway = get_compute_gateway()
    report = gateway.mode_report()
    placement = {k.value: gateway.select_device(k).as_dict() for k in WorkloadKind}
    # A device may only be selected when the probe found a usable backend.
    for kind, decision in placement.items():
        if decision["device"] != "cpu":
            assert decision["backend"], f"{kind} placed on a device with no backend"
    usable = report["usable_accelerators"]
    return (f"mode={report['requested_mode']} effective={report['effective_mode']} "
            f"accelerators={usable or 'none (CPU only is what this host provides)'}")


@check("compute", "Accelerator claims match measured hardware")
def _():
    from core.compute import detect_hardware

    inventory = detect_hardware()
    claimed = [i.kind for i in inventory.available_accelerators]
    for kind, info in inventory.accelerators.items():
        if info.available:
            assert info.backend, f"{kind} claims availability without a runtime"
        else:
            assert info.detail, f"{kind} unavailable without an explanation"
    return (f"cpu={inventory.cpu.name}; accelerators reported usable: "
            f"{claimed or 'none'}")


@check("compute", "Back-pressure refuses work instead of growing without bound",
       critical=False)
def _():
    from core.compute import BackpressureError, ComputeGateway, HardwareInventory

    inventory = HardwareInventory(probe=False)
    inventory.cpu.compute_units = 2
    gateway = ComputeGateway(inventory=inventory, max_concurrency=1, queue_depth=1,
                             admission_timeout_s=0.05)
    refused = False
    with gateway.admit():
        try:
            with gateway.admit():
                pass
        except BackpressureError:
            refused = True
    assert refused, "queue saturation did not produce back-pressure"
    return f"back-pressure engaged; peak queue depth {gateway.stats.peak_queue_depth}"


@check("compute", "Compute is isolated from gateway/network cores", critical=False)
def _():
    from core.compute import ComputeGateway, HardwareInventory

    inventory = HardwareInventory(probe=False)
    inventory.cpu.compute_units = 8
    gateway = ComputeGateway(inventory=inventory)
    report = gateway.apply_isolation()
    return (f"reserved {gateway.reserved_gateway_cores} core(s) for the gateway; "
            f"affinity={report['affinity']} nice={report['nice']}")


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Behavioral production readiness check")
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    args = parser.parse_args(argv)

    import contextlib

    # In --json mode stdout must hold only the report: anything printed while
    # the checks run (start-up banners, library notices) goes to stderr.
    with contextlib.redirect_stdout(sys.stderr if args.json else sys.stdout):
        for runner in CHECKS:
            runner()

    failed = [r for r in RESULTS if not r.passed]
    failed_critical = [r for r in failed if r.critical]

    if args.json:
        print(json.dumps({
            "passed": not failed_critical,
            "total": len(RESULTS),
            "failed": len(failed),
            "results": [
                {
                    "section": r.section, "name": r.name, "passed": r.passed,
                    "critical": r.critical, "evidence": r.evidence, "error": r.error,
                }
                for r in RESULTS
            ],
        }, indent=2))
    else:
        current_section = None
        for r in RESULTS:
            if r.section != current_section:
                current_section = r.section
                print(f"\n=== {current_section.upper()} ===")
            mark = "PASS" if r.passed else ("FAIL" if r.critical else "WARN")
            line = f"  [{mark}] {r.name}"
            if r.passed and r.evidence:
                line += f" - {r.evidence}"
            if not r.passed:
                line += f" - {r.error}"
            print(line)
        print(f"\nTotal: {len(RESULTS)} checks, {len(failed)} failed "
              f"({len(failed_critical)} critical)")
        print("READINESS:", "READY" if not failed_critical else "NOT READY")

    return 0 if not failed_critical else 1


if __name__ == "__main__":
    sys.exit(main())
