"""Scenario storage, lifecycle and permissions (tables of m0021).

Lifecycle::

    draft --dry-run passed + activate--> active <--pause/resume--> paused
      ^                                    |
      +------ definition changed ----------+          (any) --archive--> archived
    active --owner lost the privilege--> disabled --resume (owner eligible)--> active

* A scenario is created as a ``draft``. It becomes ``active`` only through
  ``activate``, which requires a **passed dry-run of the current definition
  fingerprint and version**, no older than ``DRY_RUN_MAX_AGE``. The dry-run
  id is recorded on the scenario (``activated_dry_run_id``).
* Changing the definition of an active or paused scenario creates a new
  version and returns it to ``draft``: the new logic has not been dry-run,
  so it may not notify anyone yet. Renaming is not a semantic change.
* Permissions follow monitoring rules (services/monitoring/rules.py): an
  active analyst or administrator creates and edits their own scenarios;
  the owner or an administrator may dry-run, activate, pause, resume,
  archive and evaluate; others get 404.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.errors
import psycopg2.extras

from services.monitoring.rules import (RuleError, _as_dict, _iso, _not_found,
                                       owner_eligibility)
from services.monitoring.scenario_model import (ScenarioDefinitionError, definition_from_stored,
                                                parse_definition, validate_name)

STATUSES = ("draft", "active", "paused", "disabled", "archived")
#: A dry-run older than this does not authorise activation: the data it was
#: measured on may no longer resemble the data the scenario will run on.
#: (Product decision, not an empirical threshold.)
DRY_RUN_MAX_AGE = datetime.timedelta(hours=24)
MAX_LIST = 500

SCENARIO_COLUMNS = (
    "s.id, s.owner_user_id, u.username AS owner_username, s.name, s.version, s.definition,"
    " s.definition_fingerprint, s.status, s.disabled_reason, s.activated_dry_run_id,"
    " s.activated_at, s.baselined_version, s.last_evaluated_at, s.created_at, s.updated_at")
_FROM = "scenarios s JOIN users u ON u.id = s.owner_user_id"


def _dict_cur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def fetch_scenario(cur, scenario_id: int, *, for_update: bool = False) -> Optional[Dict[str, Any]]:
    cur.execute(f"SELECT {SCENARIO_COLUMNS} FROM {_FROM} WHERE s.id = %s"  # nosec B608 # column lists and _FROM are constants, criteria SQL from the shared compiler; values are bound parameters
                + (" FOR UPDATE OF s" if for_update else ""), (scenario_id,))
    return _as_dict(cur, cur.fetchone())


def can_view(row: Dict[str, Any], user_id: Optional[int], is_admin: bool) -> bool:
    return is_admin or (user_id is not None and row["owner_user_id"] == user_id)


def scenario_to_api(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"], "owner_user_id": row["owner_user_id"],
        "owner_username": row.get("owner_username"), "name": row["name"],
        "version": row["version"], "definition": row["definition"],
        "definition_fingerprint": row["definition_fingerprint"], "status": row["status"],
        "disabled_reason": row["disabled_reason"],
        "activated_dry_run_id": row["activated_dry_run_id"],
        "activated_at": _iso(row["activated_at"]),
        "baselined": row["baselined_version"] == row["version"],
        "last_evaluated_at": _iso(row["last_evaluated_at"]),
        "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
    }


def _unique_name_error(exc) -> bool:
    return (isinstance(exc, psycopg2.errors.UniqueViolation)
            and "uq_scenarios_owner_name" in str(exc))


def _record_version(cur, scenario_id: int, version: int, definition, actor_id):
    cur.execute(
        "INSERT INTO scenario_versions (scenario_id, version, definition,"
        " definition_fingerprint, created_by_user_id) VALUES (%s, %s, %s, %s, %s)",
        (scenario_id, version, psycopg2.extras.Json(definition.canonical()),
         definition.fingerprint(), actor_id))


def _writes(conn, fn):
    """Run ``fn(cur)`` in one transaction with the error mapping of the API."""
    try:
        with _dict_cur(conn) as cur:
            result = fn(cur)
        conn.commit()
        return result
    except ScenarioDefinitionError as exc:
        conn.rollback()
        raise RuleError("VALIDATION_FAILED", str(exc))
    except psycopg2.Error as exc:
        conn.rollback()
        if _unique_name_error(exc):
            raise RuleError("CONFLICT", "you already have a scenario with this name", 409)
        raise
    except BaseException:
        conn.rollback()
        raise


def create_scenario(conn, *, owner_id: int, name: Any, definition: Any) -> Dict[str, Any]:
    def run(cur):
        clean = validate_name(name)
        eligible, _reason, _role = owner_eligibility(cur, owner_id)
        if not eligible:
            raise RuleError("FORBIDDEN", "scenarios need an active analyst or administrator"
                            " account", 403)
        parsed = parse_definition(definition)
        cur.execute(
            "INSERT INTO scenarios (owner_user_id, name, version, definition,"
            " definition_fingerprint) VALUES (%s, %s, 1, %s, %s) RETURNING id",
            (owner_id, clean, psycopg2.extras.Json(parsed.canonical()), parsed.fingerprint()))
        sid = cur.fetchone()["id"]
        _record_version(cur, sid, 1, parsed, owner_id)
        return fetch_scenario(cur, sid)
    return _writes(conn, run)


def get_scenario(conn, scenario_id: int, *, user_id: Optional[int], is_admin: bool
                 ) -> Dict[str, Any]:
    try:
        with _dict_cur(conn) as cur:
            row = fetch_scenario(cur, scenario_id)
    finally:
        conn.rollback()
    if row is None or not can_view(row, user_id, is_admin):
        raise _not_found("scenario")
    return row


def list_scenarios(conn, *, user_id: int, is_admin: bool, all_users: bool = False,
                   include_archived: bool = False) -> List[Dict[str, Any]]:
    where, params = [], []
    if not (all_users and is_admin):
        where.append("s.owner_user_id = %s")
        params.append(user_id)
    if not include_archived:
        where.append("s.status <> 'archived'")
    sql = (f"SELECT {SCENARIO_COLUMNS} FROM {_FROM}"  # nosec B608 # column lists and _FROM are constants, criteria SQL from the shared compiler; values are bound parameters
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY lower(s.name), s.id LIMIT %s")
    try:
        with _dict_cur(conn) as cur:
            cur.execute(sql, params + [MAX_LIST + 1])
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.rollback()


def update_scenario(conn, scenario_id: int, *, user_id: int, is_admin: bool, name: Any = None,
                    definition: Any = None) -> Dict[str, Any]:
    """Rename and/or replace the whole definition (owner only). A semantic
    change creates a new version; an active or paused scenario returns to
    ``draft`` and needs a new dry-run before it runs again."""
    def run(cur):
        row = fetch_scenario(cur, scenario_id, for_update=True)
        if row is None or not can_view(row, user_id, is_admin):
            raise _not_found("scenario")
        if row["owner_user_id"] != user_id:
            raise RuleError("FORBIDDEN", "only the owner may change a scenario", 403)
        if row["status"] == "archived":
            raise RuleError("CONFLICT", "an archived scenario cannot be changed", 409)
        if name is None and definition is None:
            raise RuleError("VALIDATION_FAILED", "nothing to change")
        new_name = validate_name(name) if name is not None else row["name"]
        if definition is not None:
            parsed = parse_definition(definition)
            if parsed.fingerprint() != row["definition_fingerprint"]:
                version = row["version"] + 1
                _record_version(cur, scenario_id, version, parsed, user_id)
                status = "draft" if row["status"] in ("active", "paused") else row["status"]
                cur.execute(
                    "UPDATE scenarios SET version = %s, definition = %s,"
                    " definition_fingerprint = %s, status = %s,"
                    " activated_dry_run_id = CASE WHEN %s = 'draft' THEN NULL"
                    " ELSE activated_dry_run_id END WHERE id = %s",
                    (version, psycopg2.extras.Json(parsed.canonical()), parsed.fingerprint(),
                     status, status, scenario_id))
        cur.execute("UPDATE scenarios SET name = %s, updated_at = NOW() WHERE id = %s",
                    (new_name, scenario_id))
        return fetch_scenario(cur, scenario_id)
    return _writes(conn, run)


def latest_dry_run(cur, scenario_id: int) -> Optional[Dict[str, Any]]:
    cur.execute("SELECT id, scenario_version, definition_fingerprint, status, evaluated_at,"
                " report FROM scenario_evaluations WHERE scenario_id = %s AND kind = 'dry_run'"
                " ORDER BY id DESC LIMIT 1", (scenario_id,))
    return _as_dict(cur, cur.fetchone())


def activation_blocker(row: Dict[str, Any], dry_run: Optional[Dict[str, Any]],
                       now: datetime.datetime) -> Optional[str]:
    """Why ``row`` may not be activated with ``dry_run``, or None."""
    if dry_run is None:
        return "a dry-run is required before activation"
    if dry_run["definition_fingerprint"] != row["definition_fingerprint"] \
            or dry_run["scenario_version"] != row["version"]:
        return "the latest dry-run was for another version of the definition; run it again"
    if dry_run["status"] != "passed":
        return f"the latest dry-run did not pass ({dry_run['status']})"
    if now - dry_run["evaluated_at"] > DRY_RUN_MAX_AGE:
        return "the latest dry-run is older than 24 hours; run it again"
    return None


def activate(conn, scenario_id: int, *, user_id: int, is_admin: bool,
             now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    now = now or datetime.datetime.now(datetime.timezone.utc)

    def run(cur):
        row = fetch_scenario(cur, scenario_id, for_update=True)
        if row is None or not can_view(row, user_id, is_admin):
            raise _not_found("scenario")
        if row["status"] != "draft":
            raise RuleError("CONFLICT", f"a {row['status']} scenario cannot be activated", 409)
        eligible, reason, _ = owner_eligibility(cur, row["owner_user_id"])
        if not eligible:
            raise RuleError("CONFLICT", f"the scenario's owner may not run scenarios ({reason})",
                            409)
        dry = latest_dry_run(cur, scenario_id)
        blocker = activation_blocker(row, dry, now)
        if blocker:
            raise RuleError("DRY_RUN_REQUIRED", blocker, 409)
        cur.execute("UPDATE scenarios SET status = 'active', activated_dry_run_id = %s,"
                    " activated_at = %s, updated_at = NOW() WHERE id = %s",
                    (dry["id"], now, scenario_id))
        return fetch_scenario(cur, scenario_id)
    return _writes(conn, run)


_TRANSITIONS = {
    "pause": (("active",), "paused"),
    "resume": (("paused", "disabled"), "active"),
    "archive": (("draft", "active", "paused", "disabled"), "archived"),
}


def set_status(conn, scenario_id: int, action: str, *, user_id: int, is_admin: bool
               ) -> Dict[str, Any]:
    if action not in _TRANSITIONS:
        raise RuleError("VALIDATION_FAILED", f"unknown action {action!r}")
    allowed, target = _TRANSITIONS[action]

    def run(cur):
        row = fetch_scenario(cur, scenario_id, for_update=True)
        if row is None or not can_view(row, user_id, is_admin):
            raise _not_found("scenario")
        if row["status"] not in allowed:
            raise RuleError("CONFLICT", f"a {row['status']} scenario cannot be {action}d"
                            if action != "pause" else
                            f"a {row['status']} scenario cannot be paused", 409)
        if target == "active":
            eligible, reason, _ = owner_eligibility(cur, row["owner_user_id"])
            if not eligible:
                raise RuleError("CONFLICT", "the scenario's owner may not run scenarios"
                                f" ({reason}); it cannot be resumed", 409)
            # Resuming is not activating: the definition is the one whose
            # dry-run activated it (edits return a scenario to draft).
            if row["activated_dry_run_id"] is None:
                raise RuleError("DRY_RUN_REQUIRED", "this scenario was never activated", 409)
        cur.execute("UPDATE scenarios SET status = %s, disabled_reason = NULL,"
                    " updated_at = NOW() WHERE id = %s", (target, scenario_id))
        return fetch_scenario(cur, scenario_id)
    return _writes(conn, run)


_EVALUATION_COLUMNS = (
    "id, scenario_version, kind, trigger, job_id, requested_by_user_id, status, owner_role,"
    " access_scope, criteria_fingerprint, definition_fingerprint, reference_date,"
    " evaluated_at, counts, report, error, started_at, finished_at")


def _evaluation_to_api(r: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(r)
    for k in ("reference_date", "evaluated_at", "started_at", "finished_at"):
        r[k] = _iso(r[k])
    return r


def list_evaluations(conn, scenario_id: int, *, kind: Optional[str] = None, limit: int = 50,
                     offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
    where = "scenario_id = %s" + (" AND kind = %s" if kind else "")
    params: list = [scenario_id] + ([kind] if kind else [])
    try:
        with _dict_cur(conn) as cur:
            cur.execute(f"SELECT count(*) AS n FROM scenario_evaluations WHERE {where}", params)  # nosec B608 # column lists and _FROM are constants, criteria SQL from the shared compiler; values are bound parameters
            total = cur.fetchone()["n"]
            cur.execute(f"SELECT {_EVALUATION_COLUMNS} FROM scenario_evaluations WHERE {where}"  # nosec B608 # column lists and _FROM are constants, criteria SQL from the shared compiler; values are bound parameters
                        " ORDER BY id DESC LIMIT %s OFFSET %s", params + [limit, offset])
            rows = [_evaluation_to_api(r) for r in cur.fetchall()]
    finally:
        conn.rollback()
    return rows, total


def get_evaluation(conn, scenario_id: int, evaluation_id: int) -> Dict[str, Any]:
    try:
        with _dict_cur(conn) as cur:
            cur.execute(f"SELECT {_EVALUATION_COLUMNS} FROM scenario_evaluations"  # nosec B608 # column lists and _FROM are constants, criteria SQL from the shared compiler; values are bound parameters
                        " WHERE scenario_id = %s AND id = %s", (scenario_id, evaluation_id))
            row = cur.fetchone()
    finally:
        conn.rollback()
    if row is None:
        raise _not_found("evaluation")
    return _evaluation_to_api(row)


def list_versions(conn, scenario_id: int) -> List[Dict[str, Any]]:
    try:
        with _dict_cur(conn) as cur:
            cur.execute("SELECT version, definition, definition_fingerprint, created_by_user_id,"
                        " created_at FROM scenario_versions WHERE scenario_id = %s"
                        " ORDER BY version", (scenario_id,))
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.rollback()
    for r in rows:
        r["created_at"] = _iso(r["created_at"])
    return rows


def list_outcomes(conn, scenario_id: int, *, hash_id: Optional[int] = None,
                  evaluation_id: Optional[int] = None, limit: int = 50, offset: int = 0,
                  scope=None) -> Tuple[List[Dict[str, Any]], int]:
    """The append-only outcome history, newest first, optionally for one
    content or one evaluation.

    With the viewer's ``scope``, each row also names one file occurrence of
    the content the viewer may read (lowest path id, for a stable link):
    ``path_id``/``file_name``, both None when there is none."""
    doc_sql, doc_params = ", NULL::bigint AS path_id, NULL::text AS file_name", []
    if scope is not None:
        from core.criteria.compiler import compile_criteria
        from core.criteria.model import Criteria
        from core.criteria.sql import CANONICAL_FROM

        visible = compile_criteria(Criteria(), scope)
        doc_sql = (", d.path_id, d.file_name")
        doc_params = list(visible.params)
        lateral = (f" LEFT JOIN LATERAL (SELECT p.id AS path_id, p.file_name FROM {CANONICAL_FROM}"  # nosec B608 # column lists and _FROM are constants, criteria SQL from the shared compiler; values are bound parameters
                   f" WHERE hc.hash_id = so.hash_id AND ({visible.where_sql})"
                   " ORDER BY p.id LIMIT 1) d ON TRUE")
    else:
        lateral = ""
    where, params = ["scenario_id = %s"], [scenario_id]
    if hash_id is not None:
        where.append("hash_id = %s")
        params.append(hash_id)
    if evaluation_id is not None:
        where.append("evaluation_id = %s")
        params.append(evaluation_id)
    clause = " AND ".join(where)
    try:
        with _dict_cur(conn) as cur:
            cur.execute(f"SELECT count(*) AS n FROM scenario_outcomes WHERE {clause}", params)  # nosec B608 # column lists and _FROM are constants, criteria SQL from the shared compiler; values are bound parameters
            total = cur.fetchone()["n"]
            cur.execute(
                "SELECT so.id, so.evaluation_id, so.scenario_version, so.hash_id, so.outcomes,"  # nosec B608 # column lists and _FROM are constants, criteria SQL from the shared compiler; values are bound parameters
                " so.matched_cases, so.previous_outcomes, so.delivery, so.priority,"
                " so.priority_basis, so.evidence, so.recorded_at" + doc_sql
                + " FROM (SELECT * FROM scenario_outcomes WHERE " + clause
                + " ORDER BY id DESC LIMIT %s OFFSET %s) so" + lateral + " ORDER BY so.id DESC",
                params + [limit, offset] + doc_params)
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.rollback()
    for r in rows:
        r["recorded_at"] = _iso(r["recorded_at"])
    return rows, total


def current_definition(row: Dict[str, Any]):
    definition = definition_from_stored(row["definition"])
    if definition.fingerprint() != row["definition_fingerprint"]:
        raise RuntimeError(f"scenario {row['id']}: stored definition does not match its"
                           " fingerprint")
    return definition
