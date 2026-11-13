"""Step 12 integration: scenarios evaluated and dry-run on PostgreSQL.

Documents go through the real ingestion path (signals detected once, at
ingestion). Every evaluation has an explicit clock. The database is shared
with other modules, so every scenario's population is restricted to this
module's own sources.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid

import psycopg2
import psycopg2.extras
import pytest

from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_N = iter(range(100_000))

IMMINENT = "The review will be held on 5 October 2026."        # high confidence, this week
LATER = "The summit will convene on 20 November 2026."          # high confidence, later
UNDATED = "Nothing in this memo refers to any particular day."


@pytest.fixture(scope="module")
def world(pg_db):
    conn = connect(pg_db)
    ids = {"pg_db": pg_db}
    with conn, conn.cursor() as cur:
        for key in ("a", "b", "c", "d"):
            cur.execute("INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5,"
                        " CURRENT_DATE) RETURNING id", (f"sc_side_{key}_{_U}",))
            ids[f"side_{key}"] = cur.fetchone()[0]
            cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                        " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id",
                        (f"sc_src_{key}_{_U}",))
            ids[f"source_{key}"] = cur.fetchone()[0]
    conn.close()
    ids["A"] = store(ids, IMMINENT)
    ids["B"] = store(ids, LATER)
    ids["C"] = store(ids, UNDATED)
    ids["D"] = store(ids, IMMINENT, key="b")        # outside the population
    return ids


def store(world, text, key="a"):
    from database.services.contents_db_service import ContentDBService

    marker = f"{_U}sc{next(_N)}"
    return ContentDBService().process_full_document(
        hash_value=hashlib.sha256(marker.encode()).hexdigest(), source_id=world[f"source_{key}"],
        side_id=world[f"side_{key}"], file_name=f"{marker}.txt",
        file_path=f"/tmp/sc/{marker}.txt", file_size=100, file_type="txt",
        file_status="Read", file_date=dt.date(2026, 1, 1), content_words=["sc", marker],
        raw_text=text, attempts=1)["hash_id"]


@pytest.fixture()
def db(world):
    conn = connect(world["pg_db"])
    yield conn
    conn.rollback()
    conn.close()


def user(db, role="analyst", active=True):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role, is_active)"
                    " VALUES (%s, 'x', %s, %s) RETURNING id",
                    (f"sc_{role}_{uuid.uuid4().hex[:8]}", role, active))
        uid = cur.fetchone()[0]
    db.commit()
    return uid


def definition(world, strategy="first_match", key="a", **over):
    d = {
        "criteria": {"sources": [world[f"source_{key}"]]},
        "conditions": {
            "soon": {"signals": {"signal_types": ["date_reference"]},
                     "event_window_days": {"from": 0, "to": 10}},
            "dated": {"signals": {"signal_types": ["date_reference"]}},
        },
        "cases": [
            {"id": "c_soon", "when": {"all": ["soon"]}, "outcome": "urgent"},
            {"id": "c_dated", "when": {"all": ["dated"]}, "outcome": "dated"},
            {"id": "c_undated", "when": {"none": ["dated"]}, "outcome": "undated"},
        ],
        "outcomes": {"urgent": {"label": "Urgent", "actions": ["notify"]},
                     "dated": {"label": "Dated", "actions": []},
                     "undated": {"label": "Undated", "actions": []},
                     "none": {"label": "Nothing", "actions": []}},
        "default_outcome": "none",
        "strategy": strategy,
    }
    if strategy == "highest_priority":
        for c, p in zip(d["cases"], (10, 50, 5)):
            c["priority"] = p
    d.update(over)
    return d


def make(db, world, owner, **kw):
    from services.monitoring import scenarios

    return scenarios.create_scenario(db, owner_id=owner, name=f"sc {uuid.uuid4().hex[:6]}",
                                     definition=definition(world, **kw))


def activate(db, row, now=NOW):
    from services.monitoring import scenario_engine, scenarios

    result = scenario_engine.dry_run(db, row["id"], requested_by=row["owner_user_id"], now=now)
    assert result["status"] == "passed", result
    return scenarios.activate(db, row["id"], user_id=row["owner_user_id"], is_admin=False,
                              now=now)


def evaluate(db, row, now=NOW):
    from services.monitoring.scenario_engine import evaluate_scenario

    return evaluate_scenario(db, row["id"], now=now)


def outcome_rows(db, row, evaluation_id=None):
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM scenario_outcomes WHERE scenario_id = %s"
                    + (" AND evaluation_id = %s" if evaluation_id else "") + " ORDER BY id",
                    (row["id"], evaluation_id) if evaluation_id else (row["id"],))
        out = [dict(r) for r in cur.fetchall()]
    db.rollback()
    return out


def current(db, row):
    latest = {}
    for r in outcome_rows(db, row):
        latest[r["hash_id"]] = r["outcomes"]
    return latest


def scenario_alerts(db, row, type_="scenario_outcome"):
    """Return this scenario's alerts, keyed by the frozen schema's columns.

    ``alerts`` has no ``rule_id`` / ``rule_evaluation_id`` column: the baseline
    (database/migrations/m0001_baseline.py) stores what fired in ``metadata``
    (``definition_fingerprint``, ``matched_cases``, ``priority_basis``). The two
    keys are therefore reported as None - there is no column that could hold
    them, which is exactly what the assertions on them say. Selecting columns
    that do not exist made this helper fail with UndefinedColumn instead of
    exercising the engine.
    """
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, priority, recipient_user_id, scenario_id,"
                    " scenario_evaluation_id, file_id, metadata"
                    " FROM alerts WHERE scenario_id = %s AND type = %s ORDER BY id",
                    (row["id"], type_))
        out = []
        for record in cur.fetchall():
            alert = dict(record)
            alert["rule_id"] = None
            alert["rule_evaluation_id"] = None
            out.append(alert)
    db.rollback()
    return out


# ---------------------------------------------------------------------------
# Strategies and absence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("strategy,expected", [
    ("first_match", {"A": ["urgent"], "B": ["dated"], "C": ["undated"]}),
    ("highest_priority", {"A": ["dated"], "B": ["dated"], "C": ["undated"]}),
    ("all_matching", {"A": ["dated", "urgent"], "B": ["dated"], "C": ["undated"]}),
])
def test_strategies_decide_as_specified(db, world, strategy, expected):
    owner = user(db)
    row = activate(db, make(db, world, owner, strategy=strategy,
                            notify_existing=False))
    result = evaluate(db, row)
    assert result["status"] == "completed", result
    got = current(db, row)
    assert {k: got[world[k]] for k in "ABC"} == expected
    assert world["D"] not in got                     # outside the population
    # Absence is a condition: C matched *because* no dated signal exists.
    rows = {r["hash_id"]: r for r in outcome_rows(db, row)}
    assert rows[world["C"]]["matched_cases"] == ["c_undated"]
    if strategy == "all_matching":
        assert rows[world["A"]]["matched_cases"] == ["c_soon", "c_dated"]
        assert rows[world["A"]]["evidence"]["decisive_cases"] == ["c_soon", "c_dated"]
    elif strategy == "first_match":
        assert rows[world["A"]]["evidence"]["decisive_cases"] == ["c_soon"]
    else:
        assert rows[world["A"]]["evidence"]["decisive_cases"] == ["c_dated"]
    assert result["counts"]["population"] == 3


def test_document_conditions_and_any(db, world):
    owner = user(db)
    d = definition(world)
    d["criteria"] = {"sources": [world["source_a"], world["source_b"]]}
    d["conditions"]["in_b"] = {"criteria": {"sources": [world["source_b"]]}}
    d["cases"] = [{"id": "c_b_or_soon", "when": {"any": ["in_b", "soon"]}, "outcome": "urgent"}]
    from services.monitoring import scenarios

    row = scenarios.create_scenario(db, owner_id=owner, name=f"sc {uuid.uuid4().hex[:6]}",
                                    definition=d)
    row = activate(db, row)
    evaluate(db, row)
    got = current(db, row)
    assert got[world["A"]] == ["urgent"] and got[world["D"]] == ["urgent"]
    assert world["B"] not in got and world["C"] not in got     # default: no row


# ---------------------------------------------------------------------------
# Dry-run and activation
# ---------------------------------------------------------------------------

def test_dry_run_reports_without_writing_outcomes(db, world):
    from services.monitoring import scenario_engine

    owner = user(db)
    row = make(db, world, owner)
    result = scenario_engine.dry_run(db, row["id"], requested_by=owner, now=NOW)
    assert result["status"] == "passed"
    r = result["report"]
    assert r["population"] == 3
    assert r["case_matches"] == {"c_soon": 1, "c_dated": 2, "c_undated": 1}
    assert r["decided_by_case"] == {"c_soon": 1, "c_dated": 1, "c_undated": 1}
    assert r["outcomes"] == {"urgent": 1, "dated": 1, "undated": 1}
    assert r["entering_notify"] == 1 and r["entering_notify_priorities"] == {"high": 1}
    # A draft starts with a baseline: activation notifies nobody.
    assert r["notifications_on_activation"]["notifications"] == 0
    assert r["notifications_on_activation"]["basis"] == "baseline"
    assert r["sources"]["items"][0]["id"] == world["source_a"]
    assert r["sources"]["total"] == 1 and r["sources"]["truncated"] is False
    assert r["snapshot"]["isolation"] == "repeatable_read_read_only" and r["snapshot"]["id"]
    assert r["estimated_volume"]["window_days"] == 30 and "basis" in r["estimated_volume"]
    assert r["validation"] == {"valid": True, "errors": [],
                               "warnings": r["validation"]["warnings"]}
    assert r["definition_fingerprint"] == row["definition_fingerprint"]
    assert outcome_rows(db, row) == []                 # nothing recorded
    with db.cursor() as cur:
        cur.execute("SELECT kind, status, report IS NOT NULL FROM scenario_evaluations"
                    " WHERE id = %s", (result["evaluation_id"],))
        assert cur.fetchone() == ("dry_run", "passed", True)
    db.rollback()


def test_notify_existing_dry_run_counts_the_notifications(db, world):
    from services.monitoring import scenario_engine

    row = make(db, world, user(db), notify_existing=True)
    r = scenario_engine.dry_run(db, row["id"], now=NOW)["report"]
    assert r["notifications_on_activation"] == {
        "notifications": 1, "individual": 1, "overflow_summary": False,
        "overflow_contents": 0, "basis": "notify_existing"}
    # The prediction is what activation then does.
    row = activate(db, row)
    result = evaluate(db, row)
    assert result["counts"]["notifications"] == r["notifications_on_activation"]["notifications"]
    assert len(scenario_alerts(db, row)) == 1


def test_unknown_ids_make_the_dry_run_invalid_and_block_activation(db, world):
    from services.monitoring import scenario_engine, scenarios
    from services.monitoring.rules import RuleError

    owner = user(db)
    d = definition(world)
    d["criteria"] = {"sources": [world["source_a"], 2_000_000_000]}
    row = scenarios.create_scenario(db, owner_id=owner, name=f"sc {uuid.uuid4().hex[:6]}",
                                    definition=d)
    result = scenario_engine.dry_run(db, row["id"], now=NOW)
    assert result["status"] == "invalid"
    assert "2000000000" in result["report"]["validation"]["errors"][0]
    with pytest.raises(RuleError) as exc:
        scenarios.activate(db, row["id"], user_id=owner, is_admin=False, now=NOW)
    assert exc.value.code == "DRY_RUN_REQUIRED" and "did not pass" in exc.value.message


def test_activation_needs_a_fresh_dry_run_of_this_exact_definition(db, world):
    from services.monitoring import scenario_engine, scenarios
    from services.monitoring.rules import RuleError

    owner = user(db)
    row = make(db, world, owner)
    with pytest.raises(RuleError) as exc:
        scenarios.activate(db, row["id"], user_id=owner, is_admin=False, now=NOW)
    assert exc.value.code == "DRY_RUN_REQUIRED"

    scenario_engine.dry_run(db, row["id"], now=NOW)
    # Too old.
    with pytest.raises(RuleError) as exc:
        scenarios.activate(db, row["id"], user_id=owner, is_admin=False,
                           now=NOW + dt.timedelta(hours=25))
    assert "older than 24 hours" in exc.value.message
    # Another definition.
    changed = definition(world)
    changed["cases"] = changed["cases"][:2]
    row = scenarios.update_scenario(db, row["id"], user_id=owner, is_admin=False,
                                    definition=changed)
    assert row["version"] == 2
    with pytest.raises(RuleError) as exc:
        scenarios.activate(db, row["id"], user_id=owner, is_admin=False, now=NOW)
    assert "another version" in exc.value.message
    active = activate(db, row)
    assert active["status"] == "active" and active["activated_dry_run_id"]


def test_editing_an_active_scenario_returns_it_to_draft(db, world):
    from services.monitoring import scenarios
    from services.monitoring.rules import RuleError

    owner = user(db)
    row = activate(db, make(db, world, owner))
    renamed = scenarios.update_scenario(db, row["id"], user_id=owner, is_admin=False,
                                        name="renamed " + _U + uuid.uuid4().hex[:4])
    assert renamed["status"] == "active" and renamed["version"] == 1   # not semantic
    changed = definition(world, strategy="all_matching")
    row = scenarios.update_scenario(db, row["id"], user_id=owner, is_admin=False,
                                    definition=changed)
    assert row["status"] == "draft" and row["activated_dry_run_id"] is None
    with pytest.raises(RuleError):
        scenarios.set_status(db, row["id"], "resume", user_id=owner, is_admin=False)
    assert [v["version"] for v in scenarios.list_versions(db, row["id"])] == [1, 2]


def test_a_draft_is_never_evaluated(db, world):
    row = make(db, world, user(db))
    assert evaluate(db, row)["status"] == "not_active"
    assert outcome_rows(db, row) == []


# ---------------------------------------------------------------------------
# Evaluation, notifications, history
# ---------------------------------------------------------------------------

def test_baseline_then_notification_with_provenance(db, world):
    owner = user(db)
    d = definition(world, key="c")
    from services.monitoring import scenarios

    row = scenarios.create_scenario(db, owner_id=owner, name=f"sc {uuid.uuid4().hex[:6]}",
                                    definition=d)
    first = store(world, IMMINENT, key="c")
    row = activate(db, row)
    r1 = evaluate(db, row)
    assert r1["counts"]["baseline"] is True and r1["counts"]["notifications"] == 0
    assert [x["delivery"] for x in outcome_rows(db, row)] == ["baseline"]
    assert scenario_alerts(db, row) == []

    second = store(world, IMMINENT, key="c")
    r2 = evaluate(db, row, now=NOW + dt.timedelta(hours=1))
    assert r2["counts"]["notifications"] == 1
    new = outcome_rows(db, row, r2["evaluation_id"])
    assert [(x["hash_id"], x["delivery"], x["priority"]) for x in new] == \
        [(second, "notified", "high")]
    [alert] = scenario_alerts(db, row)
    assert alert["recipient_user_id"] == owner and alert["scenario_id"] == row["id"]
    assert alert["scenario_evaluation_id"] == r2["evaluation_id"]
    assert alert["rule_id"] is None and alert["rule_evaluation_id"] is None
    assert alert["priority"] == "high" and alert["file_id"] is not None
    m = alert["metadata"]
    assert m["entered_outcomes"] == ["urgent"] and m["matched_cases"] == ["c_soon", "c_dated"]
    assert m["outcome_record_id"] == new[0]["id"] and m["hash_id"] == second
    assert m["definition_fingerprint"] == row["definition_fingerprint"]
    assert m["priority_basis"]["high_and_imminent"] is True
    assert "soon" in m["conditions_held"] and m["sample_signal_ids"]["soon"]
    # Nothing changed: nothing recorded, nothing notified.
    r3 = evaluate(db, row, now=NOW + dt.timedelta(hours=2))
    assert r3["counts"]["recorded"] == {} and r3["counts"]["notifications"] == 0
    assert first != second


def test_outcome_changes_are_appended_with_the_previous_outcome(db, world):
    owner = user(db)
    row = activate(db, make(db, world, owner))
    evaluate(db, row)
    # Two weeks later the 5 October date is past the 0..10 day window: A
    # moves from urgent to dated. Recorded (dated has no action), appended.
    later = evaluate(db, row, now=NOW + dt.timedelta(days=14))
    rows = outcome_rows(db, row, later["evaluation_id"])
    moved = [x for x in rows if x["hash_id"] == world["A"]]
    assert [(x["outcomes"], x["previous_outcomes"], x["delivery"]) for x in moved] == \
        [(["dated"], ["urgent"], "recorded")]
    history = [x["outcomes"] for x in outcome_rows(db, row) if x["hash_id"] == world["A"]]
    assert history == [["urgent"], ["dated"]]          # both kept


def test_contents_leaving_the_population_return_to_the_default(db, world):
    from services.monitoring import scenarios

    owner = user(db)
    d = definition(world)
    d["criteria"] = {"sources": [world["source_a"], world["source_b"]]}
    row = activate(db, scenarios.create_scenario(
        db, owner_id=owner, name=f"sc {uuid.uuid4().hex[:6]}", definition=d))
    evaluate(db, row)
    assert current(db, row)[world["D"]] == ["urgent"]
    d["criteria"] = {"sources": [world["source_a"]]}
    row = scenarios.update_scenario(db, row["id"], user_id=owner, is_admin=False, definition=d)
    row = activate(db, row)
    result = evaluate(db, row)
    assert result["counts"]["left_population"] == 1
    [left] = [x for x in outcome_rows(db, row, result["evaluation_id"])
              if x["hash_id"] == world["D"]]
    assert left["outcomes"] == ["none"] and left["previous_outcomes"] == ["urgent"]
    assert left["evidence"] == {"left_population": True}


def test_outcome_history_is_append_only(db, world):
    row = activate(db, make(db, world, user(db)))
    evaluate(db, row)
    rid = outcome_rows(db, row)[0]["id"]
    for statement in ("UPDATE scenario_outcomes SET outcomes = ARRAY['x'] WHERE id = %s",
                      "DELETE FROM scenario_outcomes WHERE id = %s"):
        with pytest.raises(psycopg2.errors.IntegrityConstraintViolation,
                           match="append-only"):
            with db.cursor() as cur:
                cur.execute(statement, (rid,))
        db.rollback()


def test_deleting_the_owner_cascades_through_the_history(db, world):
    owner = user(db)
    row = activate(db, make(db, world, owner))
    evaluate(db, row)
    assert outcome_rows(db, row)
    with db.cursor() as cur:
        cur.execute("DELETE FROM alerts WHERE recipient_user_id = %s", (owner,))
        cur.execute("DELETE FROM users WHERE id = %s", (owner,))
    db.commit()
    assert outcome_rows(db, row) == []


def test_an_owner_who_lost_the_privilege_disables_the_scenario(db, world):
    owner = user(db)
    row = activate(db, make(db, world, owner, notify_existing=True))
    with db.cursor() as cur:
        cur.execute("UPDATE users SET role = 'viewer' WHERE id = %s", (owner,))
    db.commit()
    result = evaluate(db, row)
    assert result["status"] == "owner_revoked"
    assert outcome_rows(db, row) == [] and scenario_alerts(db, row) == []
    [status] = scenario_alerts(db, row, "scenario_status")
    assert status["recipient_user_id"] == owner and status["metadata"]["disabled_reason"] == \
        "owner_role"
    with db.cursor() as cur:
        cur.execute("SELECT status, disabled_reason FROM scenarios WHERE id = %s", (row["id"],))
        assert cur.fetchone() == ("disabled", "owner_role")
    db.rollback()


def test_notifications_are_capped_with_an_explicit_overflow_summary(db, world):
    from services.monitoring import scenarios
    from services.monitoring.scenario_engine import MAX_ALERTS_PER_EVALUATION

    for _ in range(MAX_ALERTS_PER_EVALUATION + 2):
        store(world, IMMINENT, key="d")
    owner = user(db)
    row = activate(db, scenarios.create_scenario(
        db, owner_id=owner, name=f"sc {uuid.uuid4().hex[:6]}",
        definition=definition(world, key="d", notify_existing=True)))
    result = evaluate(db, row)
    recorded = result["counts"]["recorded"]
    assert recorded == {"notified": MAX_ALERTS_PER_EVALUATION - 1, "overflow": 3}
    alerts = scenario_alerts(db, row)
    assert len(alerts) == MAX_ALERTS_PER_EVALUATION == result["counts"]["notifications"]
    summary = alerts[-1]["metadata"]
    assert summary["delivery"] == "overflow" and summary["content_count"] == 3
    assert summary["outcome_counts"] == {"urgent": 3}


def test_sql_priority_agrees_with_the_python_rule(db, world):
    from core.monitoring.priority import priority_from_summary

    row = activate(db, make(db, world, user(db)))
    evaluate(db, row)
    for r in outcome_rows(db, row):
        b = r["priority_basis"]
        summary = {"high_and_imminent": b["high_and_imminent"],
                   "highest_confidence": b["highest_confidence"],
                   "unrecorded_confidence": b["unrecorded_confidence"],
                   "earliest_event_date": (dt.date.fromisoformat(b["earliest_event_date"])
                                           if b["earliest_event_date"] else None),
                   "matches": b["matches"]}
        prio, basis = priority_from_summary(summary, NOW.date())
        assert prio == r["priority"] and basis == b


def test_a_failed_delivery_rolls_the_whole_evaluation_back(db, world, monkeypatch):
    from services.monitoring import scenario_engine

    row = activate(db, make(db, world, user(db), notify_existing=True))

    def boom(*a, **k):
        raise RuntimeError("delivery failed")

    monkeypatch.setattr(scenario_engine, "_deliver", boom)
    result = evaluate(db, row)
    assert result["status"] == "failed" and "delivery failed" in result["error"]
    assert outcome_rows(db, row) == []
    with db.cursor() as cur:
        cur.execute("SELECT baselined_version FROM scenarios WHERE id = %s", (row["id"],))
        assert cur.fetchone()[0] == 0
    db.rollback()


def test_a_failed_dry_run_is_recorded(db, world, monkeypatch):
    from services.monitoring import scenario_engine

    row = make(db, world, user(db))

    def broken(*a, **k):
        q = scenario_engine._Q()
        return q.add("SELECT 1 / 0 AS report")

    monkeypatch.setattr(scenario_engine, "_report_sql", broken)
    result = scenario_engine.dry_run(db, row["id"], now=NOW)
    assert result["status"] == "failed" and "division by zero" in result["error"]
    with db.cursor() as cur:
        cur.execute("SELECT status, error IS NOT NULL FROM scenario_evaluations WHERE id = %s",
                    (result["evaluation_id"],))
        assert cur.fetchone() == ("failed", True)
    db.rollback()


def test_job_body_evaluates_only_active_scenarios(db, world):
    from services.monitoring.scenario_engine import run_scenario_evaluation

    owner = user(db)
    active = activate(db, make(db, world, owner))
    draft = make(db, world, owner)

    class _Conn:
        def __enter__(self):
            self.c = connect(world["pg_db"])
            return self.c

        def __exit__(self, *a):
            self.c.close()

    result = run_scenario_evaluation(_Conn, now=NOW)
    ids = {e["scenario_id"] for e in result.evaluations}
    assert active["id"] in ids and draft["id"] not in ids
    assert not result.errors


def test_deleting_a_content_cascades_through_its_history(db, world):
    # The dedup service deletes orphaned contents (cleanup_orphaned_hashes);
    # the append-only trigger must not make that fail.
    from services.monitoring import scenarios

    with db.cursor() as cur:
        cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                    " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id",
                    (f"sc_src_del_{uuid.uuid4().hex[:6]}",))
        source = cur.fetchone()[0]
    db.commit()
    local = dict(world, source_x=source, side_x=world["side_a"])
    doomed = store(local, IMMINENT, key="x")
    d = definition(world)
    d["criteria"] = {"sources": [source]}
    row = activate(db, scenarios.create_scenario(
        db, owner_id=user(db), name=f"sc {uuid.uuid4().hex[:6]}", definition=d))
    evaluate(db, row)
    assert [r["hash_id"] for r in outcome_rows(db, row)] == [doomed]
    with db.cursor() as cur:
        cur.execute("DELETE FROM paths WHERE context_id IN (SELECT id FROM hash_contexts"
                    " WHERE hash_id = %s)", (doomed,))
        cur.execute("DELETE FROM hash_contexts WHERE hash_id = %s", (doomed,))
        cur.execute("DELETE FROM hashs WHERE id = %s", (doomed,))
    db.commit()
    assert outcome_rows(db, row) == []
