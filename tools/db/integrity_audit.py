#!/usr/bin/env python3
"""Read-only database integrity audit.

Every check below is a SELECT. Nothing here writes, and nothing here changes a
constraint: this is the reproducible query set behind
``docs/database/RELATIONSHIP_INTEGRITY.md``, meant to be run against a real
database (the audit that produced those documents had only a freshly migrated,
empty archive available, so the data-level findings still have to be confirmed
on a deployed one).

Usage::

    python3 tools/db/integrity_audit.py              # human-readable report
    python3 tools/db/integrity_audit.py --json       # machine-readable
    python3 tools/db/integrity_audit.py --counts     # row counts only

Exit status is 1 when a check reports findings, so it can gate a deployment;
0 when every check is clean. A check that cannot run (missing table) is
reported as an error, never silently skipped. One group - ``redundant_indexes``
- is reported but excluded from the exit status on purpose: those shapes are
the already-registered redundancies of ``docs/database/DUPLICATION_REGISTER.md``
(DB-1, DB-2), not data-integrity violations. The group is a tripwire for a
*new* duplicate shape and must come back empty after the registered removals.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

#: Every foreign key in the migrated schema is checked automatically; these are
#: the checks a constraint cannot express.
VOCABULARY_CHECKS: List[Tuple[str, str, str]] = [
    ("alerts", "type", "notifications.type"),
    ("alerts", "priority", "notifications.priority"),
    ("jobs", "status", "jobs.job_state.ALL_STATES"),
    ("job_events", "event_type", "jobs.job_state.EVENT_TYPES"),
]

#: Columns the application requires but the schema leaves nullable, with the
#: reason it matters. NULL here means a record the application cannot render or
#: attribute. (``paths.context_id`` and ``content_signals.evidence`` used to be
#: listed here; both are NOT NULL in the migrated schema, so the database
#: already enforces them and a check would be decoration.)
REQUIRED_IN_PRACTICE: List[Tuple[str, str, str]] = [
    ("analyst_file_categories", "assigned_by",
     "an assignment with no author: only the denormalised username would remain"),
    ("audit_log", "user_id", "an audit row with neither id nor username is unattributable"),
    ("content_signals", "confidence", "a signal whose confidence cannot be judged"),
]


def _connect():
    import psycopg2

    from database import get_db_config

    cfg = get_db_config()
    return psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                            password=cfg.get("password", ""), dbname=cfg["database"])


def _scalar(cur, sql, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _tables(cur) -> set:
    cur.execute("SELECT table_name FROM information_schema.tables"
                " WHERE table_schema='public' AND table_type='BASE TABLE'")
    return {r[0] for r in cur.fetchall()}


def orphan_checks(cur, tables: set) -> List[Dict[str, object]]:
    """Child rows whose parent does not exist, for every foreign key.

    Also reports constraints that are *not validated*: a ``NOT VALID`` foreign
    key or check is enforced for new writes but says nothing about rows already
    stored, which is where a dangling reference survives.
    """
    cur.execute("""
        SELECT r.relname, c.conname, a.attname, conf.relname, af.attname, c.convalidated
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        JOIN pg_class conf ON conf.oid = c.confrelid
        JOIN pg_attribute a ON a.attrelid = r.oid AND a.attnum = c.conkey[1]
        JOIN pg_attribute af ON af.attrelid = conf.oid AND af.attnum = c.confkey[1]
        WHERE n.nspname = 'public' AND c.contype = 'f' AND cardinality(c.conkey) = 1
        ORDER BY 1, 2""")
    findings = []
    for child, conname, child_col, parent, parent_col, validated in cur.fetchall():
        if child not in tables or parent not in tables:
            continue
        sql = (f'SELECT COUNT(*) FROM "{child}" c WHERE c."{child_col}" IS NOT NULL'
               f' AND NOT EXISTS (SELECT 1 FROM "{parent}" p WHERE p."{parent_col}" = c."{child_col}")')
        count = _scalar(cur, sql)
        findings.append({"check": "orphan", "child": child, "constraint": conname,
                         "column": child_col, "parent": parent, "count": int(count or 0),
                         "validated": bool(validated)})
    return findings


def duplicate_key_checks(cur, tables: set) -> List[Dict[str, object]]:
    """Duplicate rows where the schema declares uniqueness but not as a key.

    The composite association tables use a UNIQUE constraint rather than a
    primary key, and tables without any unique constraint still have a business
    key the application relies on. A duplicate here is how the same fact ends
    up in two rows.
    """
    declared = [
        # Composite-key association tables (their uniqueness is the key).
        ("words_hashs", "hash_id, word_id"),
        ("keywords_hashs", "hash_id, keyword_id"),
        ("words_categorys", "word_id, category_id"),
        ("content_signal_places", "signal_id, place_id"),
        ("content_signal_runs", "hash_id, detector"),
        # Rows with a declared business key.
        ("hash_contexts", "hash_id, source_id, side_id"),
        ("scenarios", "name"),
        ("analyst_categories", "name"),
        ("sources", "name"),
        ("sides", "name"),
        ("words", "word"),
        ("keywords", "keyword"),
        ("hashs", "hash"),
        # geo_places has no external-key column; its business key is the
        # natural one the unique constraint declares.
        ("geo_places", "label, category_id, country_codes"),
        ("content_signals", "dedup_key"),
    ]
    findings = []
    for table, columns in declared:
        if table not in tables:
            continue
        sql = (f'SELECT COUNT(*) FROM (SELECT 1 FROM "{table}"'
               f' GROUP BY {columns} HAVING COUNT(*) > 1) dup')
        count = _scalar(cur, sql)
        findings.append({"check": "duplicate_key", "table": table, "columns": columns,
                         "count": int(count or 0)})
    return findings


def vocabulary_checks(cur, tables: set) -> List[Dict[str, object]]:
    """Values that are not in the vocabulary the code defines for the column."""
    findings = []
    for table, column, source in VOCABULARY_CHECKS:
        if table not in tables:
            continue
        values = _allowed_values(source)
        if not values:
            findings.append({"check": "vocabulary", "table": table, "column": column,
                             "source": source, "error": "vocabulary not found in code"})
            continue
        placeholders = ", ".join(["%s"] * len(values))
        count = _scalar(cur, f'SELECT COUNT(*) FROM "{table}"'
                             f' WHERE "{column}" IS NOT NULL AND "{column}" NOT IN ({placeholders})',
                        tuple(values))
        findings.append({"check": "vocabulary", "table": table, "column": column,
                         "source": source, "allowed": len(values), "count": int(count or 0)})
    return findings


def _allowed_values(source: str) -> List[str]:
    """Read the vocabulary out of the module that defines it."""
    if source.startswith("notifications."):
        from core.monitoring import notification_service as module

        enum_name = {"type": "NotificationType", "priority": "NotificationPriority"}[source.split(".")[1]]
        return [member.value for member in getattr(module, enum_name)]
    if source.startswith("jobs.job_state.ALL_STATES"):
        from services.jobs import job_state

        return list(job_state.ALL_STATES)
    if source.startswith("jobs.job_state.EVENT_TYPES"):
        from services.jobs import job_state

        return list(job_state.EVENT_TYPES)
    return []


def required_checks(cur, tables: set) -> List[Dict[str, object]]:
    findings = []
    for table, column, why in REQUIRED_IN_PRACTICE:
        if table not in tables:
            continue
        if table == "audit_log" and column == "user_id":
            # Both the id and the snapshot name are nullable; the row is only
            # unattributable when they are missing together.
            count = _scalar(cur, 'SELECT COUNT(*) FROM audit_log'
                                 ' WHERE user_id IS NULL AND username IS NULL')
        else:
            count = _scalar(cur, f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NULL')
        findings.append({"check": "required_in_practice", "table": table, "column": column,
                         "count": int(count or 0), "why": why})
    return findings


def redundant_index_checks(cur, tables: set) -> List[Dict[str, object]]:
    """Index shapes that exist twice on the same table.

    Informational, and deliberately excluded from the exit status: the nine
    shapes in ``docs/database/DUPLICATION_REGISTER.md`` (DB-1, DB-2) are known,
    registered redundancies awaiting a decision, not data-integrity violations,
    and a tool that fails on a clean database is a tool nobody runs. It is here
    as a tripwire: after the registered removals land this list must be empty,
    and a new duplicate shape shows up immediately.

    The shape is the ``indexdef`` with the index *name*, its table qualifier and
    the ``UNIQUE`` keyword removed - PostgreSQL stores the name inside
    ``indexdef``, so grouping on the raw text finds nothing (a
    constraint-backed index and a plain index over the same columns differ only
    in those tokens).
    """
    cur.execute("""
        SELECT tablename,
               regexp_replace(indexdef,
                   '^(CREATE( UNIQUE)? INDEX )\\S+ ON public\\.\\S+ ', '') AS shape,
               string_agg(indexname, ', ' ORDER BY indexname) AS names,
               count(*) AS n
        FROM pg_indexes
        WHERE schemaname = 'public'
        GROUP BY tablename,
                 regexp_replace(indexdef, '^(CREATE( UNIQUE)? INDEX )\\S+ ON public\\.\\S+ ', '')
        HAVING count(*) > 1
        ORDER BY tablename, shape""")
    findings = []
    for table, shape, names, n in cur.fetchall():
        if table not in tables:
            continue
        findings.append({"check": "redundant_index", "table": table, "shape": shape,
                         "indexes": names, "count": int(n)})
    return findings


def row_counts(cur, tables: set) -> Dict[str, int]:
    counts = {}
    for table in sorted(tables):
        counts[table] = int(_scalar(cur, f'SELECT COUNT(*) FROM "{table}"') or 0)
    return counts


def run() -> Dict[str, object]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            tables = _tables(cur)
            report = {
                "tables": len(tables),
                "orphans": orphan_checks(cur, tables),
                "duplicate_keys": duplicate_key_checks(cur, tables),
                "vocabularies": vocabulary_checks(cur, tables),
                "required_in_practice": required_checks(cur, tables),
                # Informational only - see redundant_index_checks() for why it
                # does not affect the exit status.
                "redundant_indexes": redundant_index_checks(cur, tables),
                "row_counts": row_counts(cur, tables),
            }
    finally:
        conn.close()
    return report


def _problems(report: Dict[str, object]) -> List[str]:
    problems = []
    for group in ("orphans", "duplicate_keys", "vocabularies", "required_in_practice"):
        for item in report[group]:  # type: ignore[index]
            if item.get("error") or item.get("count") or item.get("validated") is False:
                problems.append(f"{group}: {item}")
    return problems


def main(argv: List[str]) -> int:
    report = run()
    # With --json the stdout stream is the document, so the closing status line
    # goes to stderr: otherwise `--json > integrity.json` writes a file that is
    # not valid JSON (the trailing "no findings" line is data to any parser).
    machine_readable = "--json" in argv
    status = sys.stderr if machine_readable else sys.stdout
    if machine_readable:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    elif "--counts" in argv:
        for table, count in report["row_counts"].items():  # type: ignore[union-attr]
            print(f"{count:8}  {table}")
    else:
        print(f"tables: {report['tables']}")
        redundant = report["redundant_indexes"]  # type: ignore[index]
        print(f"\nredundant_indexes: {len(redundant)} registered shape(s)"
              " (informational, does not affect the exit status)")
        for item in redundant:
            print(f"  x{item['count']}  {item['table']}: {item['shape']} -> {item['indexes']}")
        for group in ("orphans", "duplicate_keys", "vocabularies", "required_in_practice"):
            items = report[group]  # type: ignore[index]
            print(f"\n{group}: {len(items)} checks")
            for item in items:
                if item.get("error"):
                    print(f"  ERROR {item}")
                elif item.get("validated") is False:
                    print(f"  UNVALIDATED {item}")
                elif item.get("count"):
                    print(f"  {item['count']:>8}  {item}")
                else:
                    print(f"  {0:>8}  {item.get('table', item.get('child'))}"
                          f"{'.' + item['column'] if 'column' in item else ''}")
    problems = _problems(report)
    if problems:
        print(f"\n{len(problems)} finding(s)", file=sys.stderr)
        return 1
    print("\nno findings", file=status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
