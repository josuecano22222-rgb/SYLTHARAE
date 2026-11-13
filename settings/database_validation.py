"""
OPS-02: pre-commit validation and connectivity testing for database configuration.

The database settings endpoint (``POST /api/settings/database``) used to
persist whatever it was given, which meant a single malformed submission could
write an unusable configuration to ``data/settings.json`` and render the whole
application unbootable - every later login failed and the only recovery was to
hand-edit the settings file on disk.

This module implements the required lifecycle:

    input -> validate syntax -> test connectivity -> persist -> activate

It is deliberately import-light and side-effect free: nothing here mutates
application state. The caller applies a validated candidate only after
:func:`test_database_connection` reports success.

Security notes
--------------
* ``settings.settings_models.DatabaseConfig`` is **not** used to hold the
  candidate. Its ``__post_init__`` calls ``apply_env_overrides()``, which would
  silently overwrite the proposed values with the current ``DB_*`` environment
  variables - so a candidate built that way would always test the *running*
  configuration and always pass. The candidate is a plain dict instead.
* Failure messages are classified and generic. Raw driver text, DSNs,
  filesystem paths and credentials are never returned to the client.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

#: libpq connect timeout for the probe. Bounds the TCP/TLS handshake.
DEFAULT_CONNECT_TIMEOUT = int(os.environ.get("DB_TEST_CONNECT_TIMEOUT", "5"))

#: Hard wall-clock ceiling for the whole probe. ``connect_timeout`` covers the
#: handshake but not every pathological name-resolution stall, so the probe
#: also runs on a daemon thread that we abandon if it overruns. This is what
#: guarantees an unreachable host cannot hang the HTTP request (or leak a
#: request thread) indefinitely.
HARD_TIMEOUT_MARGIN = 5

MAX_HOST_LENGTH = 255
MAX_IDENTIFIER_LENGTH = 63        # PostgreSQL NAMEDATALEN - 1
MAX_PASSWORD_LENGTH = 512

_HOST_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_DBNAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_USER_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")

#: Numeric fields: name -> (minimum, maximum). Upper bounds stop an absurd
#: value from being persisted and then applied to the connection pool.
_INT_FIELDS: Dict[str, Tuple[int, int]] = {
    "pool_min_conn": (1, 1000),
    "pool_max_conn": (1, 1000),
    "pool_timeout": (1, 3600),
    "query_timeout": (1, 86400),
    "batch_size": (1, 10_000_000),
    "chunk_size": (1, 10_000_000),
}


class DatabaseConfigError(Exception):
    """Raised when a proposed configuration cannot be accepted."""


class DatabaseConfigRejected(DatabaseConfigError):
    """Raised when a proposed configuration fails validation or connectivity.

    OPS-03 / OPS-04: raised by the settings manager *before* any state is
    mutated, so that the settings import and backup-restore paths can reject
    a configuration change without touching the live settings object, the
    environment or ``data/settings.json``. Route handlers translate this into
    HTTP 422; every other failure stays a generic 400.
    """


# --------------------------------------------------------------------------
# Step 1 - syntax / range validation
# --------------------------------------------------------------------------

def _reject_control_chars(value: str, field: str, errors: List[str]) -> bool:
    """Reject NUL, newlines and other control characters.

    Values are passed to psycopg2 as bound connection parameters, so this is
    not an injection defence - it stops malformed input from producing
    confusing downstream driver behaviour and from polluting logs.
    """
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        errors.append(f"{field}: contains invalid control characters.")
        return True
    return False


def _coerce_int(field: str, raw: Any, errors: List[str]) -> Optional[int]:
    if isinstance(raw, bool) or raw is None:
        errors.append(f"{field}: must be an integer.")
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        errors.append(f"{field}: must be an integer.")
        return None
    low, high = _INT_FIELDS[field]
    if value < low or value > high:
        errors.append(f"{field}: must be between {low} and {high}.")
        return None
    return value


def build_database_candidate(
    data: Dict[str, Any],
    current: Any,
) -> Tuple[Dict[str, Any], List[str]]:
    """Validate a proposed database configuration.

    Args:
        data: raw JSON body from the request.
        current: the live ``DatabaseConfig`` (for values the request omits).

    Returns:
        ``(candidate, errors)``. ``errors`` empty means ``candidate`` is safe
        to hand to :func:`test_database_connection`.
    """
    errors: List[str] = []

    if not isinstance(data, dict):
        return {}, ["Request body must be a JSON object."]

    # Unknown keys are ignored rather than rejected: the settings page posts a
    # superset of fields, and silently dropping extras is the existing contract.

    candidate: Dict[str, Any] = {}

    # ---- host -------------------------------------------------------------
    raw_host = data.get("host", current.host)
    host = "" if raw_host is None else str(raw_host).strip()
    if not host:
        errors.append("host: is required.")
    elif len(host) > MAX_HOST_LENGTH:
        errors.append(f"host: must be at most {MAX_HOST_LENGTH} characters.")
    elif not _reject_control_chars(host, "host", errors) and not _HOST_RE.match(host):
        errors.append("host: must be a hostname or IP address.")
    candidate["host"] = host

    # ---- port -------------------------------------------------------------
    raw_port = data.get("port", current.port)
    if isinstance(raw_port, bool) or raw_port is None:
        errors.append("port: must be an integer.")
        port = None
    else:
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            errors.append("port: must be an integer.")
            port = None
        else:
            if port < 1 or port > 65535:
                errors.append("port: must be between 1 and 65535.")
                port = None
    candidate["port"] = port

    # ---- database name ----------------------------------------------------
    raw_db = data.get("database", current.database)
    dbname = "" if raw_db is None else str(raw_db).strip()
    if not dbname:
        errors.append("database: is required.")
    elif len(dbname) > MAX_IDENTIFIER_LENGTH:
        errors.append(f"database: must be at most {MAX_IDENTIFIER_LENGTH} characters.")
    elif not _reject_control_chars(dbname, "database", errors) and not _DBNAME_RE.match(dbname):
        errors.append("database: may contain only letters, digits, underscore and hyphen.")
    candidate["database"] = dbname

    # ---- user -------------------------------------------------------------
    raw_user = data.get("user", current.user)
    user = "" if raw_user is None else str(raw_user).strip()
    if not user:
        errors.append("user: is required.")
    elif len(user) > MAX_IDENTIFIER_LENGTH:
        errors.append(f"user: must be at most {MAX_IDENTIFIER_LENGTH} characters.")
    elif not _reject_control_chars(user, "user", errors) and not _USER_RE.match(user):
        errors.append("user: contains unsupported characters.")
    candidate["user"] = user

    # ---- password ---------------------------------------------------------
    # An absent or empty password is the existing "keep the stored password"
    # contract - the UI never echoes the current password back, and a blank
    # field must not wipe it.
    raw_password = data.get("password", None)
    if raw_password is None or (isinstance(raw_password, str) and raw_password.strip() == ""):
        password = current.password or ""
    else:
        password = str(raw_password)
        if len(password) > MAX_PASSWORD_LENGTH:
            errors.append(f"password: must be at most {MAX_PASSWORD_LENGTH} characters.")
        elif _reject_control_chars(password, "password", errors):
            password = ""
    candidate["password"] = password

    # ---- numeric pool / tuning fields -------------------------------------
    for field in _INT_FIELDS:
        if field in data:
            value = _coerce_int(field, data[field], errors)
            if value is not None:
                candidate[field] = value
        else:
            candidate[field] = getattr(current, field)

    pool_min = candidate.get("pool_min_conn")
    pool_max = candidate.get("pool_max_conn")
    if pool_min is not None and pool_max is not None and pool_max < pool_min:
        errors.append("pool_max_conn: must be greater than or equal to pool_min_conn.")

    return candidate, errors


#: Fields that make up the effective database target (excluding tuning knobs
#: that cannot make the database unreachable on their own).
_TARGET_FIELDS = ("host", "port", "database", "user", "password")


def candidate_differs(candidate: Dict[str, Any], current: Any) -> bool:
    """True when the candidate would change any database field.

    Used to skip the connectivity probe when an operation does not actually
    touch the database configuration, so an unrelated settings import is not
    blocked by a transient database outage.
    """
    for field in _TARGET_FIELDS + tuple(_INT_FIELDS):
        if candidate.get(field) != getattr(current, field, None):
            return True
    return False


def apply_candidate_to_environment(candidate: Dict[str, Any]) -> None:
    """Sync the ``DB_*`` environment variables to an accepted candidate.

    OPS-03: settings import and backup restore replace the persisted document
    but never touched the environment, which left disk, memory and environment
    disagreeing - the classic "disk says A, env says B" state this release
    gate forbids. Every accepted configuration change now goes through here.
    """
    os.environ["DB_HOST"] = str(candidate["host"])
    os.environ["DB_PORT"] = str(candidate["port"])
    os.environ["DB_USER"] = str(candidate["user"])
    os.environ["DB_NAME"] = str(candidate["database"])
    if candidate.get("password"):
        os.environ["DB_PASSWORD"] = str(candidate["password"])


def guard_database_config_change(
    proposed: Optional[Dict[str, Any]],
    current: Any,
    *,
    require_test: bool = True,
) -> Dict[str, Any]:
    """Validate and, if it changes anything, connectivity-test a proposal.

    Shared gate for every path that can replace the database configuration
    (``POST /api/settings/database``, settings import, backup restore).

    Args:
        proposed: raw proposed ``database`` block. ``None``/empty means "not
            supplied", in which case every field falls back to ``current``
            rather than to dataclass defaults - importing a theme export must
            not silently repoint the database at ``localhost:5432``.
        current: the live ``DatabaseConfig``.

    Returns:
        The accepted candidate.

    Raises:
        DatabaseConfigRejected: on a syntax or connectivity failure. No state
            has been mutated when this is raised.
    """
    candidate, errors = build_database_candidate(proposed or {}, current)
    if errors:
        raise DatabaseConfigRejected(
            "The database configuration in the submitted settings is not valid. "
            + " ".join(errors)
        )

    if require_test and candidate_differs(candidate, current):
        connected, message = test_database_connection(candidate)
        if not connected:
            raise DatabaseConfigRejected(message)

    return candidate


# --------------------------------------------------------------------------
# Step 2 - connectivity test
# --------------------------------------------------------------------------

def _classify_failure(exc: BaseException) -> str:
    """Map a driver exception onto a fixed, safe, human-readable message.

    The raw exception text is deliberately discarded: psycopg2 messages can
    echo the host, port, user, database name and local socket paths. Only the
    exception *class* and a redacted summary are written to the server log.
    """
    sqlstate = getattr(exc, "pgcode", None) or ""
    text = str(exc).lower()

    if "timeout" in text or "timed out" in text:
        return "The connection attempt timed out before the server responded. Check the host and port, and any firewall between the application and the database."

    if (
        "could not translate" in text
        or "name or service not known" in text
        or "getaddrinfo" in text
        or "nodename nor servname" in text
        or "temporary failure in name resolution" in text
    ):
        return "The host name could not be resolved. Check that the host is spelled correctly and is reachable from this server."

    if sqlstate == "28P01" or "password authentication failed" in text or "authentication failed" in text:
        return "The server rejected the credentials. Check the username and password."

    if sqlstate == "28000" or "pg_hba.conf" in text:
        return "The server refused the connection for this user and host. Check the server's client authentication configuration."

    if sqlstate == "3D000" or ("does not exist" in text and "database" in text):
        return "The server was reached, but the specified database does not exist on it."

    if "connection refused" in text:
        return "The host was reached, but no database server is accepting connections on that port."

    if "ssl" in text or "tls" in text:
        return "The connection could not be established because the TLS negotiation failed."

    return "Unable to connect to the database with the supplied configuration. Check the host, port, database name, username and password."


def _probe(candidate: Dict[str, Any], connect_timeout: int, out: Dict[str, Any]) -> None:
    """Run the actual connection attempt. Executed on a daemon thread."""
    conn = None
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=candidate["host"],
            port=candidate["port"],
            dbname=candidate["database"],
            user=candidate["user"],
            password=candidate["password"],
            connect_timeout=connect_timeout,
        )

        cur = conn.cursor()
        try:
            # Minimum required verification: the server accepts the session and
            # executes a query. ``version()`` additionally lets us confirm the
            # server really is PostgreSQL rather than some other service that
            # happens to be listening.
            cur.execute("SELECT version()")
            row = cur.fetchone()
        finally:
            cur.close()

        banner = (row[0] if row and row[0] else "") or ""
        out["ok"] = True
        out["is_postgres"] = "postgres" in banner.lower()
        out["server_version"] = banner.strip().split(" ", 2)[-1] if out["is_postgres"] else ""

    except BaseException as exc:  # noqa: BLE001 - reclassified by the caller
        out["ok"] = False
        out["error"] = exc
    finally:
        # Connection resources are always released, including on the abandoned
        # (timed-out) path, so repeated failed attempts cannot leak sockets.
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.debug("Failed to close probe connection", exc_info=True)


def test_database_connection(
    candidate: Dict[str, Any],
    connect_timeout: Optional[int] = None,
) -> Tuple[bool, str]:
    """Attempt a real connection using the proposed configuration.

    Returns:
        ``(True, "")`` when the connection succeeds, authenticates, reaches the
        target database, executes the verification query and is a PostgreSQL
        server. Otherwise ``(False, safe_message)`` containing no credentials,
        connection strings, filesystem paths or raw driver diagnostics.
    """
    timeout = int(connect_timeout or DEFAULT_CONNECT_TIMEOUT)
    hard_timeout = timeout + HARD_TIMEOUT_MARGIN

    out: Dict[str, Any] = {}
    thread = threading.Thread(
        target=_probe,
        args=(candidate, timeout, out),
        name="db-config-probe",
        daemon=True,
    )
    thread.start()
    thread.join(hard_timeout)

    if thread.is_alive():
        # The probe is abandoned rather than waited on. It is a daemon thread
        # with a libpq connect_timeout, so it will unwind on its own without
        # holding the request thread or the process.
        logger.warning(
            "Database configuration probe exceeded %ss (host=%s port=%s database=%s user=%s)",
            hard_timeout, candidate.get("host"), candidate.get("port"),
            candidate.get("database"), candidate.get("user"),
        )
        return False, "The connection attempt timed out before the server responded. Check the host and port, and any firewall between the application and the database."

    if out.get("ok"):
        if not out.get("is_postgres"):
            logger.warning(
                "Database configuration probe reached a non-PostgreSQL server (host=%s port=%s)",
                candidate.get("host"), candidate.get("port"),
            )
            return False, "The server was reached, but it does not appear to be a PostgreSQL server."
        return True, ""

    exc = out.get("error")
    # Redacted server-side log: identifies the target but never the password
    # and never the raw driver text (which can embed local socket paths).
    logger.warning(
        "Database configuration probe failed (host=%s port=%s database=%s user=%s): %s",
        candidate.get("host"), candidate.get("port"), candidate.get("database"),
        candidate.get("user"), type(exc).__name__ if exc else "unknown",
    )
    return False, _classify_failure(exc) if exc else "Unable to connect to the database with the supplied configuration."
