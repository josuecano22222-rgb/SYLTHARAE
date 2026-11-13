"""Scenario evaluation and dry-run.

One SQL statement decides the outcome of every content in the population
(``decision_sql``); evaluation and dry-run run the *same* statement, so a
dry-run measures exactly what activation will do. There is no per-content or
per-case Python loop:

1. ``pop``   - contents with a file occurrence matching the scenario's
               criteria, compiled by the single compiler under the owner's
               access scope (core/criteria).
2. ``sx``/``sig`` - one pass over those contents' signals: a boolean per
               signal condition, then per content and condition the count,
               best confidence, "high and imminent", earliest event date,
               unrecorded confidence and a sample signal id (FILTER
               aggregates).
3. ``f``     - one boolean per condition (``count >= min_count``; document
               conditions: an occurrence matching their criteria).
4. ``d``     - one boolean per case (``all`` / ``any`` / ``none``).
5. ``out``   - outcomes by strategy, matched and decisive cases, and the
               evidence of the decisive case(s) reduced to the facts of the
               derived priority (core/monitoring/priority.py; the SQL form
               is tested against ``priority_from_summary``).

**Evaluation** (active scenarios; JobManager type ``scenario_evaluation``):
one READ COMMITTED transaction with an advisory lock per scenario. The owner
is re-read first; an owner who lost the privilege disables the scenario and
nothing is evaluated. Outcomes go to the append-only ``scenario_outcomes``:
a row only when a content's outcome set differs from its latest row, and a
return to the default for contents that left the population. Notifications
go to the owner for contents *entering* an outcome with the ``notify``
action - at most 49 individual ones per evaluation, highest derived priority
first, and one explicit overflow summary for the rest. The first evaluation
of a version is the baseline (recorded, not notified) unless
``notify_existing``.

**Dry-run** (JobManager type ``scenario_dry_run``): the same statement in a
REPEATABLE READ, READ ONLY transaction; writes only the dry-run record. The
report: population, matches per case, decisions per case, contents per
outcome, what activation would record and notify, derived priorities, top
sources / categories / analyst categories (top-N with totals, marked
truncated), estimated daily notification volume with its basis, the
snapshot identity, and the validation verdict (errors block activation;
warnings do not).
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

from core.criteria.access import scope_for
from core.criteria.sql import CANONICAL_FROM
from core.monitoring import priority as priority_rules
from core.monitoring.notification_service import (Notification, NotificationPriority,
                                                  NotificationType, insert_alerts)
from services.detection import signal_query
from services.monitoring import rule_engine
from services.monitoring import scenarios as store
from services.monitoring.rules import owner_eligibility

logger = logging.getLogger(__name__)

SCENARIO_LOCK_CLASS = 0x5343454E          # "SCEN"
TRIGGERS = ("manual", "ingestion", "redetection", "all_scenarios", "schedule")
MAX_ALERTS_PER_EVALUATION = rule_engine.MAX_ALERTS_PER_EVALUATION
MAX_SAMPLE = 20
DRY_RUN_TOP = 20
#: The dry-run's volume estimate: contents in notify outcomes first ingested
#: in the last N days, divided by N.
VOLUME_WINDOW_DAYS = 30
STATEMENT_TIMEOUT_MS = 120_000
DRY_RUN_STATEMENT_TIMEOUT_MS = 120_000


class _Q:
    """SQL text with its parameters, kept in order of appearance."""

    def __init__(self):
        self.parts: List[str] = []
        self.params: List[Any] = []

    def add(self, text: str, params=()) -> "_Q":
        self.parts.append(text)
        self.params.extend(params)
        return self

    @property
    def sql(self) -> str:
        return "".join(self.parts)


def _lit(identifier: str) -> str:
    """A validated identifier (condition/case/outcome id) as a SQL literal.
    The model restricts them to ``[a-z][a-z0-9_]*``; checked again here."""
    if not identifier.replace("_", "").isalnum() or not identifier.isascii() \
            or identifier != identifier.lower():
        raise ValueError(f"unsafe identifier {identifier!r}")
    return f"'{identifier}'"


@dataclass
class Decision:
    """The compiled decision statement (a WITH ... prefix ending in ``out``)."""
    q: _Q
    population: Any                 # CompiledQuery of the population criteria
    criteria_fingerprint: str
    notify_outcomes: List[str]
    default_outcome: str


def decision_sql(definition, scope, reference_date: datetime.date) -> Decision:
    pop = signal_query._compile(definition.criteria, scope)
    q = _Q()
    q.add(f"WITH pop AS (SELECT DISTINCT hc.hash_id FROM {CANONICAL_FROM}"  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
          f" WHERE ({pop.where_sql}) AND hc.hash_id IS NOT NULL)", pop.params)

    signal_conds = [c for c in definition.conditions if c.kind == "signal"]
    idx = {c.name: i for i, c in enumerate(signal_conds)}
    # ``imminent_sql`` renders "date_from <= %s + IMMINENT_DAYS AND date_to >= %s":
    # the window is [reference_date, reference_date + IMMINENT_DAYS], so both
    # placeholders are the reference date. Binding the window *end* second
    # compared ``date_to`` against reference+7 and nothing inside the window
    # counted as imminent (see tests/unit/test_scenario_imminent_window.py).
    imminent = (reference_date, reference_date)
    if signal_conds:
        q.add(", sx AS (SELECT s.hash_id, s.id, s.confidence, s.date_from,"
              f" {rule_engine._CONF_RANK_SQL} AS rank,"
              f" {priority_rules.imminent_sql('s.date_from', 's.date_to')} AS imm", imminent)
        for c in signal_conds:
            conds, params = rule_engine._conditions(c, None, reference_date)
            q.add(f", ({' AND '.join(conds) if conds else 'TRUE'}) AS m{idx[c.name]}", params)
        q.add(" FROM content_signals s WHERE s.hash_id IN (SELECT hash_id FROM pop))")
        q.add(", sig AS (SELECT hash_id")
        for c in signal_conds:
            i = idx[c.name]
            q.add(f", count(*) FILTER (WHERE m{i}) AS n{i}"
                  f", max(rank) FILTER (WHERE m{i}) AS best{i}"
                  f", bool_or(rank = 3 AND imm) FILTER (WHERE m{i}) AS hi{i}"
                  f", min(date_from) FILTER (WHERE m{i}) AS early{i}"
                  f", count(*) FILTER (WHERE m{i} AND confidence IS NULL) AS unrec{i}"
                  f", min(id) FILTER (WHERE m{i}) AS sample{i}")
        q.add(" FROM sx WHERE " + " OR ".join(f"m{idx[c.name]}" for c in signal_conds)
              + " GROUP BY hash_id)")

    q.add(", f AS (SELECT pop.hash_id")
    for c in definition.conditions:
        if c.kind == "signal":
            i = idx[c.name]
            q.add(f", COALESCE(sig.n{i}, 0) >= %s AS f_{c.name}", (c.min_count,))
            q.add(f", COALESCE(sig.n{i}, 0) AS n{i}, COALESCE(sig.best{i}, 0) AS best{i},"
                  f" COALESCE(sig.hi{i}, FALSE) AS hi{i}, sig.early{i} AS early{i},"
                  f" COALESCE(sig.unrec{i}, 0) AS unrec{i}, sig.sample{i} AS sample{i}")
        else:
            doc = signal_query._compile(c.criteria, scope)
            q.add(f", EXISTS (SELECT 1 FROM {CANONICAL_FROM} WHERE hc.hash_id = pop.hash_id"  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
                  f" AND ({doc.where_sql})) AS f_{c.name}", doc.params)
    q.add(" FROM pop" + (" LEFT JOIN sig ON sig.hash_id = pop.hash_id" if signal_conds else "")
          + ")")

    def case_expr(case) -> str:
        parts = [f"f_{n}" for n in case.all]
        if case.any:
            parts.append("(" + " OR ".join(f"f_{n}" for n in case.any) + ")")
        if case.none:
            parts.append("NOT (" + " OR ".join(f"f_{n}" for n in case.none) + ")")
        return "(" + " AND ".join(parts) + ")"

    cases = list(definition.cases)
    kcol = {c.id: f"k{j}" for j, c in enumerate(cases)}
    q.add(", d AS (SELECT f.*")
    for c in cases:
        q.add(f", {case_expr(c)} AS {kcol[c.id]}")
    q.add(" FROM f)")

    # Evidence of one case: its positive signal conditions that hold.
    def ev(case, what: str) -> str:
        pos = [n for n in case.all + case.any if n in idx]
        if what == "hi":
            return "(" + " OR ".join(["FALSE"] + [f"(f_{n} AND hi{idx[n]})" for n in pos]) + ")"
        if what == "best":
            return "GREATEST(" + ", ".join(
                ["0"] + [f"CASE WHEN f_{n} THEN best{idx[n]} ELSE 0 END" for n in pos]) + ")"
        if what == "early":
            return "LEAST(" + ", ".join(
                ["NULL::date"] + [f"CASE WHEN f_{n} THEN early{idx[n]} END" for n in pos]) + ")"
        col = {"n": "n", "unrec": "unrec"}[what]
        return "(" + " + ".join(["0"] + [f"CASE WHEN f_{n} THEN {col}{idx[n]} ELSE 0 END"
                                         for n in pos]) + ")"

    default = definition.default_outcome
    ordered = definition.ordered_cases()
    all_matching = definition.strategy == "all_matching"

    def decisive(what: str) -> str:
        if all_matching:
            if what == "hi":
                return "(" + " OR ".join(["FALSE"] + [f"({kcol[c.id]} AND {ev(c, 'hi')})"
                                                      for c in cases]) + ")"
            if what == "best":
                return "GREATEST(" + ", ".join(
                    ["0"] + [f"CASE WHEN {kcol[c.id]} THEN {ev(c, 'best')} ELSE 0 END"
                             for c in cases]) + ")"
            if what == "early":
                return "LEAST(" + ", ".join(
                    ["NULL::date"] + [f"CASE WHEN {kcol[c.id]} THEN {ev(c, 'early')} END"
                                      for c in cases]) + ")"
            return "(" + " + ".join(["0"] + [f"CASE WHEN {kcol[c.id]} THEN {ev(c, what)}"
                                             " ELSE 0 END" for c in cases]) + ")"
        empty = {"hi": "FALSE", "best": "0", "early": "NULL::date", "n": "0", "unrec": "0"}[what]
        return ("CASE " + " ".join(f"WHEN {kcol[c.id]} THEN {ev(c, what)}" for c in ordered)
                + f" ELSE {empty} END")

    if all_matching:
        outcomes = ("COALESCE(NULLIF(ARRAY(SELECT DISTINCT o FROM unnest(ARRAY["  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
                    + ", ".join(f"CASE WHEN {kcol[c.id]} THEN {_lit(c.outcome)} END"
                                for c in cases)
                    + f"]::text[]) o WHERE o IS NOT NULL ORDER BY o), '{{}}'::text[]),"
                    f" ARRAY[{_lit(default)}]::text[])")
        decisive_cases = "matched_cases"
    else:
        outcomes = ("ARRAY[CASE " + " ".join(f"WHEN {kcol[c.id]} THEN {_lit(c.outcome)}"
                                              for c in ordered)
                    + f" ELSE {_lit(default)} END]::text[]")
        decisive_cases = ("CASE " + " ".join(f"WHEN {kcol[c.id]} THEN ARRAY[{_lit(c.id)}]"
                                             for c in ordered) + " ELSE '{}' END::text[]")
    matched = ("array_remove(ARRAY[" + ", ".join(f"CASE WHEN {kcol[c.id]} THEN {_lit(c.id)} END"
                                                 for c in cases) + "]::text[], NULL)")
    held = ("array_remove(ARRAY[" + ", ".join(f"CASE WHEN f_{c.name} THEN {_lit(c.name)} END"
                                              for c in definition.conditions)
            + "]::text[], NULL)")
    samples = ("jsonb_strip_nulls(jsonb_build_object(" + ", ".join(
        f"{_lit(c.name)}, CASE WHEN f_{c.name} THEN sample{idx[c.name]} END"
        for c in signal_conds) + "))") if signal_conds else "'{}'::jsonb"

    if decisive_cases == "matched_cases":
        decisive_cases = matched
    q.add(", e AS (SELECT hash_id, " + outcomes + " AS outcomes, " + matched  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
          + " AS matched_cases, " + decisive_cases + " AS decisive_cases, " + held + " AS conditions_held, " + samples
          + " AS sample_signal_ids, " + decisive("hi") + " AS ev_hi, " + decisive("best")
          + " AS ev_best, " + decisive("early") + " AS ev_early, " + decisive("n")
          + " AS ev_n, " + decisive("unrec") + " AS ev_unrec FROM d)")
    # The documented ladder (core/monitoring/priority.py) keyed on the
    # strongest confidence among the decisive cases. ``ev_hi`` is only
    # "a high-confidence signal is imminent"; using it as the first rung
    # labelled a high-confidence, non-imminent match "medium". The outcome
    # column admits high/medium/low only, so the summary rule is capped
    # at high (see ``priority_from_summary``).
    q.add(", out AS (SELECT e.*,"
          " CASE WHEN ev_best = 3 THEN 'high' WHEN ev_best = 2 THEN 'medium'"
          " ELSE 'low' END"
          " AS priority,"
          " jsonb_build_object('rule', %s::text, 'high_and_imminent', ev_hi,"
          " 'highest_confidence', CASE ev_best WHEN 3 THEN 'high' WHEN 2 THEN 'medium'"
          " WHEN 1 THEN 'low' END, 'unrecorded_confidence', ev_unrec, 'matches', ev_n,"
          " 'earliest_event_date', ev_early, 'reference_date', %s::text,"
          " 'imminent_days', %s::int) AS priority_basis FROM e)",
          (priority_rules.PRIORITY_RULE_VERSION, reference_date.isoformat(),
           priority_rules.IMMINENT_DAYS))
    return Decision(q=q, population=pop, criteria_fingerprint=pop.criteria_fingerprint,
                    notify_outcomes=definition.notify_outcomes(), default_outcome=default)


_LATEST = ("SELECT DISTINCT ON (hash_id) hash_id, outcomes FROM scenario_outcomes"
           " WHERE scenario_id = %s ORDER BY hash_id, id DESC")
_ENTERS_NOTIFY = ("EXISTS (SELECT 1 FROM unnest(o.outcomes) x WHERE x = ANY(%s::text[])"
                  " AND NOT (x = ANY(COALESCE(prev.outcomes, ARRAY[%s]::text[]))))")
_CHANGES = "o.outcomes IS DISTINCT FROM COALESCE(prev.outcomes, ARRAY[%s]::text[])"


def _scope_json(scope) -> Dict[str, Any]:
    return rule_engine._scope_json(scope)


def _record(cur, row, *, kind, status, trigger, job_id, now, reference_date, requested_by=None,
            owner_role=None, scope=None, criteria_fingerprint=None, counts=None, report=None,
            error=None, finished=True) -> int:
    cur.execute(
        "INSERT INTO scenario_evaluations (scenario_id, scenario_version, kind, trigger, job_id,"
        " requested_by_user_id, status, owner_role, access_scope, criteria_fingerprint,"
        " definition_fingerprint, reference_date, evaluated_at, counts, report, error,"
        " finished_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " CASE WHEN %s THEN clock_timestamp() END) RETURNING id",
        (row["id"], row["version"], kind, trigger, job_id, requested_by, status, owner_role,
         psycopg2.extras.Json(scope) if scope is not None else None, criteria_fingerprint,
         row["definition_fingerprint"], reference_date, now,
         psycopg2.extras.Json(counts or {}),
         psycopg2.extras.Json(report) if report is not None else None, error, finished))
    return cur.fetchone()["id"]


def _now(now):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now, now.astimezone(datetime.timezone.utc).date()


def _status_alert(cur, row, reason, eid, now):
    insert_alerts(cur, [Notification(
        id=None, type=NotificationType.SCENARIO_STATUS, priority=NotificationPriority.MEDIUM,
        title=row["name"], message=f"Scenario disabled: {reason}", file_id=None,
        event_date=None,
        metadata={"scenario_id": row["id"], "scenario_name": row["name"],
                  "disabled_reason": reason, "scenario_evaluation_id": eid},
        created_at=now, recipient_user_id=row["owner_user_id"], scenario_id=row["id"])])


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_scenario(conn, scenario_id: int, *, now: Optional[datetime.datetime] = None,
                      trigger: str = "manual", job_id: Optional[str] = None) -> Dict[str, Any]:
    """Evaluate one active scenario. Returns ``{"scenario_id", "status",
    "evaluation_id", "counts"[, "error"]}``; a failure is recorded and
    returned (one broken scenario does not stop the others)."""
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {TRIGGERS}")
    now, reference_date = _now(now)
    conn.rollback()
    row = None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cur.execute("SET LOCAL statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))
            cur.execute("SELECT pg_try_advisory_xact_lock(%s, %s) AS got",
                        (SCENARIO_LOCK_CLASS, scenario_id))
            got = cur.fetchone()["got"]
            row = store.fetch_scenario(cur, scenario_id, for_update=got)
            if row is None:
                conn.rollback()
                raise LookupError(f"scenario {scenario_id} does not exist")
            common = dict(kind="evaluation", trigger=trigger, job_id=job_id, now=now,
                          reference_date=reference_date)
            if not got:
                eid = _record(cur, row, status="skipped_busy", **common)
                conn.commit()
                return {"scenario_id": scenario_id, "status": "skipped_busy",
                        "evaluation_id": eid, "counts": {}}
            if row["status"] != "active":
                counts = {"scenario_status": row["status"]}
                eid = _record(cur, row, status="not_active", counts=counts, **common)
                conn.commit()
                return {"scenario_id": scenario_id, "status": "not_active",
                        "evaluation_id": eid, "counts": counts}

            eligible, reason, role = owner_eligibility(cur, row["owner_user_id"])
            if not eligible:
                cur.execute("UPDATE scenarios SET status = 'disabled', disabled_reason = %s,"
                            " updated_at = %s WHERE id = %s", (reason, now, scenario_id))
                counts = {"disabled_reason": reason}
                eid = _record(cur, row, status="owner_revoked", owner_role=role, counts=counts,
                              **common)
                _status_alert(cur, row, reason, eid, now)
                conn.commit()
                return {"scenario_id": scenario_id, "status": "owner_revoked",
                        "evaluation_id": eid, "counts": counts}

            definition = store.current_definition(row)
            scope = scope_for(SimpleNamespace(id=row["owner_user_id"], role=role))
            decision = decision_sql(definition, scope, reference_date)
            eid = _record(cur, row, status="completed", owner_role=role,
                          scope=_scope_json(scope),
                          criteria_fingerprint=decision.criteria_fingerprint, finished=False,
                          **common)
            counts = _apply(cur, row, definition, decision, eid, now)
            alerts = _deliver(cur, row, definition, decision, eid, now, reference_date, counts)
            counts["notifications"] = alerts
            cur.execute("UPDATE scenarios SET last_evaluated_at = %s,"
                        " baselined_version = version WHERE id = %s", (now, scenario_id))
            cur.execute("UPDATE scenario_evaluations SET counts = %s,"
                        " finished_at = clock_timestamp() WHERE id = %s",
                        (psycopg2.extras.Json(counts), eid))
        conn.commit()
        return {"scenario_id": scenario_id, "status": "completed", "evaluation_id": eid,
                "counts": counts}
    except LookupError:
        raise
    except Exception as exc:
        conn.rollback()
        logger.exception("scenario %s evaluation failed", scenario_id)
        message = f"{type(exc).__name__}: {exc}"[:2000]
        if row is None:
            raise
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            eid = _record(cur, row, kind="evaluation", status="failed", trigger=trigger,
                          job_id=job_id, now=now, reference_date=reference_date, error=message)
        conn.commit()
        return {"scenario_id": scenario_id, "status": "failed", "evaluation_id": eid,
                "counts": {}, "error": message}


def _apply(cur, row, definition, decision, eid, now) -> Dict[str, Any]:
    """Record changed outcomes (append-only). Returns counts."""
    sid, version = row["id"], row["version"]
    baseline = row["baselined_version"] < version and not definition.notify_existing
    default = decision.default_outcome
    cur.execute("CREATE TEMP TABLE _scenario_out ON COMMIT DROP AS "  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
                + decision.q.sql + " SELECT * FROM out", decision.q.params)
    population = cur.rowcount
    cur.execute("CREATE INDEX ON _scenario_out (hash_id)")
    cur.execute("ANALYZE _scenario_out")

    # Contents whose outcome changed (or first left the default).
    cur.execute(
        "WITH prev AS (" + _LATEST + "),"  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
        " cand AS (SELECT o.*, prev.outcomes AS prev_outcomes, " + _ENTERS_NOTIFY
        + " AS enters FROM _scenario_out o LEFT JOIN prev ON prev.hash_id = o.hash_id"
        " WHERE " + _CHANGES + "),"
        " ranked AS (SELECT cand.*, row_number() OVER (PARTITION BY enters ORDER BY"
        " CASE priority WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC, hash_id)"
        " AS rn, count(*) FILTER (WHERE enters) OVER () AS n_enter FROM cand)"
        " INSERT INTO scenario_outcomes (scenario_id, evaluation_id, scenario_version, hash_id,"
        " outcomes, matched_cases, previous_outcomes, delivery, priority, priority_basis,"
        " evidence, recorded_at)"
        " SELECT %s, %s, %s, hash_id, outcomes, matched_cases, prev_outcomes,"
        " CASE WHEN %s THEN 'baseline'"
        " WHEN enters AND (n_enter <= %s OR rn < %s) THEN 'notified'"
        " WHEN enters THEN 'overflow' ELSE 'recorded' END,"
        " priority, priority_basis,"
        " jsonb_build_object('decisive_cases', decisive_cases, 'conditions_held',"
        " conditions_held, 'sample_signal_ids', sample_signal_ids), %s"
        " FROM ranked ORDER BY hash_id",
        [sid, decision.notify_outcomes, default, default, sid, eid, version, baseline,
         MAX_ALERTS_PER_EVALUATION, MAX_ALERTS_PER_EVALUATION, now])
    # Contents that left the population return to the default outcome.
    cur.execute(
        "WITH prev AS (" + _LATEST + ")"  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
        " INSERT INTO scenario_outcomes (scenario_id, evaluation_id, scenario_version, hash_id,"
        " outcomes, matched_cases, previous_outcomes, delivery, evidence, recorded_at)"
        " SELECT %s, %s, %s, prev.hash_id, ARRAY[%s]::text[], '{}'::text[], prev.outcomes,"
        " CASE WHEN %s THEN 'baseline' ELSE 'recorded' END,"
        " jsonb_build_object('left_population', TRUE), %s FROM prev"
        " WHERE prev.outcomes <> ARRAY[%s]::text[]"
        " AND NOT EXISTS (SELECT 1 FROM _scenario_out o WHERE o.hash_id = prev.hash_id)"
        " ORDER BY prev.hash_id",
        [sid, sid, eid, version, default, baseline, now, default])
    left = cur.rowcount
    cur.execute("SELECT delivery, count(*) AS n FROM scenario_outcomes WHERE evaluation_id = %s"
                " GROUP BY delivery", (eid,))
    recorded = {r["delivery"]: r["n"] for r in cur.fetchall()}
    cur.execute("SELECT o, count(*) AS n FROM _scenario_out, unnest(outcomes) o GROUP BY o")
    per_outcome = {r["o"]: r["n"] for r in cur.fetchall()}
    return {"population": population, "outcomes": per_outcome, "recorded": recorded,
            "left_population": left, "baseline": baseline}


def _deliver(cur, row, definition, decision, eid, now, reference_date, counts) -> int:
    cur.execute(
        "SELECT so.id, so.hash_id, so.outcomes, so.previous_outcomes, so.matched_cases,"  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
        " so.priority, so.priority_basis, so.evidence, fp.path_id, fp.file_name, fp.file_path"
        " FROM scenario_outcomes so LEFT JOIN LATERAL (SELECT p.id AS path_id, p.file_name,"
        f" p.file_path FROM {CANONICAL_FROM} WHERE hc.hash_id = so.hash_id"
        f" AND ({decision.population.where_sql}) ORDER BY p.id LIMIT 1) fp ON TRUE"
        " WHERE so.evaluation_id = %s AND so.delivery = 'notified' ORDER BY so.id",
        list(decision.population.params) + [eid])
    rows = cur.fetchall()
    labels = {o.id: o.label for o in definition.outcomes}
    base = {"scenario_id": row["id"], "scenario_name": row["name"],
            "scenario_version": row["version"],
            "definition_fingerprint": row["definition_fingerprint"],
            "criteria_fingerprint": decision.criteria_fingerprint,
            "scenario_evaluation_id": eid, "strategy": definition.strategy,
            "reference_date": reference_date.isoformat()}
    notifications = []
    for r in rows:
        previous = r["previous_outcomes"] or [decision.default_outcome]
        entered = [o for o in r["outcomes"]
                   if o in decision.notify_outcomes and o not in previous]
        basis = r["priority_basis"]
        earliest = basis.get("earliest_event_date")
        notifications.append(Notification(
            id=None, type=NotificationType.SCENARIO_OUTCOME,
            priority=NotificationPriority(r["priority"]), title=row["name"],
            message=", ".join(labels[o] for o in entered), file_id=r["path_id"],
            event_date=datetime.date.fromisoformat(earliest) if earliest else None,
            metadata={**base, "delivery": "immediate", "outcome_record_id": r["id"],
                      "hash_id": r["hash_id"], "outcomes": r["outcomes"],
                      "entered_outcomes": entered,
                      "outcome_labels": {o: labels[o] for o in r["outcomes"]},
                      "previous_outcomes": previous, "matched_cases": r["matched_cases"],
                      **r["evidence"], "priority_basis": basis},
            created_at=now, recipient_user_id=row["owner_user_id"], scenario_id=row["id"]))
    overflow = counts["recorded"].get("overflow", 0)
    if overflow:
        cur.execute(
            "SELECT count(*) AS n, max(CASE priority WHEN 'high' THEN 3 WHEN 'medium' THEN 2"
            " ELSE 1 END) AS best FROM scenario_outcomes WHERE evaluation_id = %s"
            " AND delivery = 'overflow'", (eid,))
        agg = cur.fetchone()
        cur.execute("SELECT hash_id FROM scenario_outcomes WHERE evaluation_id = %s"
                    " AND delivery = 'overflow' ORDER BY hash_id LIMIT %s", (eid, MAX_SAMPLE))
        sample = [x["hash_id"] for x in cur.fetchall()]
        cur.execute("SELECT o, count(*) AS n FROM scenario_outcomes, unnest(outcomes) o"
                    " WHERE evaluation_id = %s AND delivery = 'overflow' GROUP BY o ORDER BY o",
                    (eid,))
        per = {x["o"]: x["n"] for x in cur.fetchall()}
        prio = {3: "high", 2: "medium", 1: "low"}[agg["best"]]
        notifications.append(Notification(
            id=None, type=NotificationType.SCENARIO_OUTCOME,
            priority=NotificationPriority(prio), title=row["name"],
            message=f"{agg['n']} further contents", file_id=None, event_date=None,
            metadata={**base, "delivery": "overflow", "content_count": agg["n"],
                      "outcome_counts": per, "sample_hash_ids": sample,
                      "sample_truncated": agg["n"] > len(sample),
                      "limit": MAX_ALERTS_PER_EVALUATION,
                      # The summary's priority is the highest derived
                      # priority among the contents it summarises.
                      "priority_basis": {"rule": "highest_of_summarised", "priority": prio}},
            created_at=now, recipient_user_id=row["owner_user_id"], scenario_id=row["id"]))
    insert_alerts(cur, notifications)
    return len(notifications)


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

def _missing_ids(cur, definition) -> List[str]:
    """Referenced ids that do not exist: a mistyped id would silently match
    nothing, so it is a validation error."""
    wanted: Dict[str, set] = {}
    crits = [("criteria", definition.criteria)] + [
        (f"condition '{c.name}'", c.criteria) for c in definition.conditions
        if c.kind == "document"]
    tables = {"sources": "sources", "sides": "sides", "categories": "categorys",
              "analyst_categories": "analyst_categories", "keywords": "keywords"}
    errors = []
    for where, crit in crits:
        for attr, table in tables.items():
            ids = sorted(set(getattr(crit, attr) or ()))
            if not ids:
                continue
            cur.execute(f"SELECT array_agg(x ORDER BY x) AS missing FROM unnest(%s::int[]) x"  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
                        f" WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.id = x)", (ids,))
            missing = cur.fetchone()["missing"]
            if missing:
                errors.append(f"{where}: {attr} {', '.join(map(str, missing))} do not exist")
    return errors


def _report_sql(decision, scenario_id: int, reference_date: datetime.date) -> _Q:
    q = _Q()
    q.parts, q.params = list(decision.q.parts), list(decision.q.params)
    default, notify = decision.default_outcome, decision.notify_outcomes
    pw, pp = decision.population.where_sql, list(decision.population.params)
    q.add(", prev AS (" + _LATEST + ")", (scenario_id,))
    q.add(", cur AS (SELECT o.*, " + _ENTERS_NOTIFY + " AS enters, " + _CHANGES  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
          + " AS changes FROM out o LEFT JOIN prev ON prev.hash_id = o.hash_id)",
          (notify, default, default))
    q.add(", nd AS (SELECT hash_id FROM cur WHERE outcomes <> ARRAY[%s]::text[])", (default,))

    def top(sql: str, params=()):
        q.add("(SELECT jsonb_build_object('items', COALESCE(jsonb_agg(z ORDER BY z.n DESC,"  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
              f" z.id) FILTER (WHERE z.r <= {DRY_RUN_TOP}), '[]'::jsonb), 'total', count(*),"
              f" 'truncated', count(*) > {DRY_RUN_TOP}) FROM (SELECT y.*, row_number() OVER"
              " (ORDER BY y.n DESC, y.id) AS r FROM (" + sql + ") y) z)", params)

    q.add(" SELECT jsonb_build_object("  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
          "'population', (SELECT count(*) FROM cur),"
          "'case_matches', (SELECT COALESCE(jsonb_object_agg(c, n), '{}'::jsonb) FROM"
          " (SELECT c, count(*) AS n FROM cur, unnest(matched_cases) c GROUP BY c) z),"
          "'decided_by_case', (SELECT COALESCE(jsonb_object_agg(c, n), '{}'::jsonb) FROM"
          " (SELECT c, count(*) AS n FROM cur, unnest(decisive_cases) c GROUP BY c) z),"
          "'outcomes', (SELECT COALESCE(jsonb_object_agg(o, n), '{}'::jsonb) FROM"
          " (SELECT o, count(*) AS n FROM cur, unnest(outcomes) o GROUP BY o) z),"
          "'non_default_contents', (SELECT count(*) FROM nd),"
          "'would_record', (SELECT count(*) FROM cur WHERE changes),"
          "'entering_notify', (SELECT count(*) FROM cur WHERE changes AND enters),"
          "'entering_notify_priorities', (SELECT COALESCE(jsonb_object_agg(priority, n),"
          " '{}'::jsonb) FROM (SELECT priority, count(*) AS n FROM cur WHERE changes AND enters"
          " GROUP BY priority) z),"
          "'recent_notify_contents', (SELECT count(*) FROM cur WHERE outcomes && %s::text[]"
          f" AND (SELECT min(p.date_creation) FROM {CANONICAL_FROM} WHERE"
          f" hc.hash_id = cur.hash_id AND ({pw})) >= %s),"
          "'sources', ",
          [notify] + pp + [reference_date - datetime.timedelta(days=VOLUME_WINDOW_DAYS)])
    top("SELECT s.id, s.name, count(DISTINCT hc.hash_id) AS n FROM " + CANONICAL_FROM  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
        + " JOIN sources s ON s.id = hc.source_id WHERE hc.hash_id IN (SELECT hash_id FROM nd)"  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
        f" AND ({pw}) GROUP BY s.id, s.name", pp)
    q.add(", 'categories', ")
    top("SELECT c.id, w.word AS name, count(DISTINCT wh.hash_id) AS n FROM words_hashs wh"
        " JOIN words_categorys wc ON wc.word_id = wh.word_id JOIN categorys c"
        " ON c.id = wc.category_id JOIN words w ON w.id = c.word_id"
        " WHERE wh.hash_id IN (SELECT hash_id FROM nd) GROUP BY c.id, w.word")
    q.add(", 'analyst_categories', ")
    top("SELECT ac.id, ac.name, count(DISTINCT hc.hash_id) AS n FROM analyst_file_categories"  # nosec B608 # identifiers validated by scenario_model (^[a-z][a-z0-9_]{0,39}\Z) and re-checked by _lit; criteria SQL from the shared compiler; values are bound parameters
        " afc JOIN paths p ON p.id = afc.path_id JOIN hash_contexts hc ON hc.id = p.context_id"
        " JOIN analyst_categories ac ON ac.id = afc.category_id"
        f" WHERE hc.hash_id IN (SELECT hash_id FROM nd) AND ({pw}) GROUP BY ac.id, ac.name", pp)
    q.add(") AS report")
    return q


def dry_run(conn, scenario_id: int, *, requested_by: Optional[int] = None,
            now: Optional[datetime.datetime] = None, job_id: Optional[str] = None
            ) -> Dict[str, Any]:
    """Dry-run the scenario's current definition. Returns ``{"scenario_id",
    "status", "evaluation_id", "report"[, "error"]}``."""
    now, reference_date = _now(now)
    conn.rollback()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        row = store.fetch_scenario(cur, scenario_id)
    conn.rollback()
    if row is None:
        raise LookupError(f"scenario {scenario_id} does not exist")
    common = dict(kind="dry_run", trigger="manual", job_id=job_id, now=now,
                  reference_date=reference_date, requested_by=requested_by)
    report: Dict[str, Any] = {}
    role = scope = decision = None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cur.execute("SET LOCAL statement_timeout = %s", (DRY_RUN_STATEMENT_TIMEOUT_MS,))
            cur.execute("SELECT pg_current_snapshot()::text AS snapshot,"
                        " transaction_timestamp() AS snapshot_at")
            snap = cur.fetchone()
            eligible, reason, role = owner_eligibility(cur, row["owner_user_id"])
            if not eligible:
                conn.rollback()
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as wcur:
                    eid = _record(wcur, row, status="owner_revoked", owner_role=role,
                                  counts={"owner_ineligible": reason}, **common)
                conn.commit()
                return {"scenario_id": scenario_id, "status": "owner_revoked",
                        "evaluation_id": eid, "report": None}
            definition = store.current_definition(row)
            scope = scope_for(SimpleNamespace(id=row["owner_user_id"], role=role))
            decision = decision_sql(definition, scope, reference_date)
            errors = _missing_ids(cur, definition)
            q = _report_sql(decision, scenario_id, reference_date)
            cur.execute(q.sql, q.params)
            measured = cur.fetchone()["report"]
        conn.rollback()

        baseline = (row["status"] == "draft" or row["baselined_version"] < row["version"]) \
            and not definition.notify_existing
        entering = measured["entering_notify"]
        if baseline or entering == 0:
            on_activation = {"notifications": 0, "individual": 0, "overflow_summary": False,
                             "overflow_contents": 0,
                             "basis": "baseline" if baseline else "nothing_entering"}
        else:
            over = entering > MAX_ALERTS_PER_EVALUATION
            individual = MAX_ALERTS_PER_EVALUATION - 1 if over else entering
            on_activation = {"notifications": individual + (1 if over else 0),
                             "individual": individual, "overflow_summary": over,
                             "overflow_contents": entering - individual if over else 0,
                             "basis": "notify_existing"}
        warnings = definition.warnings()
        for c in definition.cases:
            if not measured["case_matches"].get(c.id):
                warnings.append(f"case '{c.id}' matches no content in the current data")
        if measured["population"] == 0:
            warnings.append("the population is empty: the criteria match no content the owner"
                            " may read")
        if entering > MAX_ALERTS_PER_EVALUATION:
            warnings.append(f"{entering} contents would enter notify outcomes at once: at most"
                            f" {MAX_ALERTS_PER_EVALUATION - 1} individual notifications and one"
                            " summary per evaluation")
        recent = measured.pop("recent_notify_contents")
        report = {
            **measured,
            "reference_date": reference_date.isoformat(),
            "snapshot": {"isolation": "repeatable_read_read_only", "id": snap["snapshot"],
                         "at": snap["snapshot_at"].isoformat()},
            "strategy": definition.strategy,
            "default_outcome": definition.default_outcome,
            "notify_outcomes": decision.notify_outcomes,
            "notifications_on_activation": on_activation,
            "estimated_volume": {
                "per_day": round(recent / VOLUME_WINDOW_DAYS, 2),
                "contents": recent, "window_days": VOLUME_WINDOW_DAYS,
                "basis": (f"contents currently in a notify outcome whose first file"
                          f" occurrence was ingested in the last {VOLUME_WINDOW_DAYS} days,"
                          f" divided by {VOLUME_WINDOW_DAYS}; assumes arrivals continue at"
                          " that rate")},
            "top_n": DRY_RUN_TOP,
            "criteria_fingerprint": decision.criteria_fingerprint,
            "definition_fingerprint": row["definition_fingerprint"],
            "scenario_version": row["version"],
            "validation": {"valid": not errors, "errors": errors, "warnings": warnings},
        }
        status = "passed" if not errors else "invalid"
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            eid = _record(cur, row, status=status, owner_role=role, scope=_scope_json(scope),
                          criteria_fingerprint=decision.criteria_fingerprint,
                          counts={"population": measured["population"],
                                  "entering_notify": entering},
                          report=report, **common)
        conn.commit()
        return {"scenario_id": scenario_id, "status": status, "evaluation_id": eid,
                "report": report}
    except Exception as exc:
        conn.rollback()
        logger.exception("scenario %s dry-run failed", scenario_id)
        message = f"{type(exc).__name__}: {exc}"[:2000]
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            eid = _record(cur, row, status="failed", owner_role=role, error=message, **common)
        conn.commit()
        return {"scenario_id": scenario_id, "status": "failed", "evaluation_id": eid,
                "report": None, "error": message}


# ---------------------------------------------------------------------------
# Job bodies
# ---------------------------------------------------------------------------

@dataclass
class ScenarioJobResult:
    """Shaped like the other JobManager results (stats/errors/warnings)."""
    stats: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    evaluations: List[Dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False


def active_scenario_ids(conn) -> List[int]:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM scenarios WHERE status = 'active' ORDER BY id")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.rollback()


def run_scenario_evaluation(get_connection: Callable, *,
                            scenario_ids: Optional[List[int]] = None,
                            trigger: str = "all_scenarios", job_id: Optional[str] = None,
                            now: Optional[datetime.datetime] = None,
                            progress_cb: Optional[Callable] = None,
                            cancel_cb: Optional[Callable[[], bool]] = None
                            ) -> ScenarioJobResult:
    result = ScenarioJobResult()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if scenario_ids is None:
        with get_connection() as conn:
            scenario_ids = active_scenario_ids(conn)
    by_status: Dict[str, int] = {}
    notifications = 0
    for i, sid in enumerate(scenario_ids):
        if cancel_cb and cancel_cb():
            result.cancelled = True
            break
        with get_connection() as conn:
            try:
                outcome = evaluate_scenario(conn, sid, now=now, trigger=trigger, job_id=job_id)
            except LookupError as exc:
                outcome = {"scenario_id": sid, "status": "missing", "error": str(exc)}
        by_status[outcome["status"]] = by_status.get(outcome["status"], 0) + 1
        notifications += (outcome.get("counts") or {}).get("notifications", 0)
        result.evaluations.append(outcome)
        if outcome["status"] in ("failed", "missing"):
            result.errors.append(f"scenario {sid}: {outcome['error']}")
        elif outcome["status"] in ("owner_revoked", "skipped_busy"):
            result.warnings.append(f"scenario {sid}: {outcome['status']}")
        if progress_cb:
            progress_cb({"percent": int((i + 1) * 100 / max(len(scenario_ids), 1)),
                         "current_phase": "Evaluating scenarios",
                         "files_processed": i + 1, "files_total": len(scenario_ids)})
    result.stats = {"scenarios": len(scenario_ids), "evaluated": len(result.evaluations),
                    "by_status": by_status, "notifications": notifications,
                    "trigger": trigger, "evaluated_at": now.isoformat()}
    return result


def run_scenario_dry_run(get_connection: Callable, *, scenario_id: int,
                         requested_by: Optional[int] = None, job_id: Optional[str] = None,
                         progress_cb: Optional[Callable] = None) -> ScenarioJobResult:
    result = ScenarioJobResult()
    with get_connection() as conn:
        outcome = dry_run(conn, scenario_id, requested_by=requested_by, job_id=job_id)
    result.evaluations.append(outcome)
    result.stats = {"scenario_id": scenario_id, "dry_run_id": outcome["evaluation_id"],
                    "status": outcome["status"]}
    if outcome["status"] == "failed":
        result.errors.append(f"scenario {scenario_id}: {outcome['error']}")
    elif outcome["status"] != "passed":
        result.warnings.append(f"scenario {scenario_id}: dry-run {outcome['status']}")
    if progress_cb:
        progress_cb({"percent": 100, "current_phase": "Scenario dry-run",
                     "files_processed": 1, "files_total": 1})
    return result
