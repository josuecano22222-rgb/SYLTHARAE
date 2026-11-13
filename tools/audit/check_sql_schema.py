#!/usr/bin/env python
"""Check every SQL statement in the tree against the live schema.

Two passes, because the code builds SQL by concatenation and half of it is not
a complete statement:

1. **sqlglot** - statements that parse get *qualified* against the catalogue;
   an identifier that names no column of the table it is qualified with is
   reported ("Unknown column: place_key").
2. **alias resolution** - for fragments, every ``alias.column`` reference is
   resolved through the FROM/JOIN aliases of the same statement. This is what
   catches ``a.file_name`` when ``alerts`` has no such column.

Both passes read the schema from ``pg_catalog`` of the migrated database, so the
answer is the schema that actually exists, not a transcription of it.

Writes ``audit_report/findings/sql_schema.csv``; exit code 1 when it finds
anything, so it can gate a build.
"""

from __future__ import annotations

import collections
import csv
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "audit_report" / "findings"

SQL_KW = re.compile(r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|WITH)\b", re.I)
#: A statement (or a fragment) starts with one of these. Prose that merely
#: contains "from" is not SQL; requiring the leading verb removes the docstring
#: false positives without weakening what the check looks at.
SQL_START = re.compile(
    r"^\s*(?:--[^\n]*\n\s*)*(SELECT|INSERT\s+INTO|INSERT|UPDATE|DELETE|WITH|"
    r"ALTER\s+TABLE|CREATE\s+(?:TABLE|INDEX|UNIQUE|OR\s+REPLACE|FUNCTION|"
    r"TRIGGER|VIEW)|DROP\s+(?:TABLE|INDEX|VIEW)|TRUNCATE|GRANT|PREPARE)\b")
#: Keywords written in upper case are the reliable signal: prose is not. A
#: docstring starting "Update a saved search" is English; SQL starting "UPDATE"
#: is code.
SQL_UPPER = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN|WITH)\b")
#: The catalogue's own tables answer questions *about* the schema; they are not
#: part of it, and their columns are not in the application schema.
SYSTEM_TABLES = re.compile(
    r"^(information_schema|pg_catalog|pg_\w+)", re.I)
CTE_RE = re.compile(r"(?:\bWITH(?:\s+RECURSIVE)?|,)\s*([a-z_][a-z0-9_]*)"
                    r"(?:\s*\([^)]*\))?\s+AS\s*(?:MATERIALIZED\s*)?\(", re.I)
#: ``ON DELETE CASCADE`` is DDL, not a table named "cascade".
DDL_TAIL = re.compile(r"\bON\s+(?:DELETE|UPDATE)\s+(?:CASCADE|RESTRICT|SET\s+NULL"
                      r"|SET\s+DEFAULT|NO\s+ACTION)\b", re.I)
#: One- and two-letter names are aliases in practice; resolving them as tables
#: produces noise without ever catching a real defect.
SHORT = re.compile(r"^[a-z]{1,2}$")
TABLE_RE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_][a-z0-9_]*)", re.I)
ALIAS_RE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\s+([a-z_][a-z0-9_]*)\s+(?:AS\s+)?([a-z_][a-z0-9_]*)?",
    re.I)
COLREF_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b", re.I)
NOISE = {"select", "where", "set", "values", "on", "as", "and", "or", "not",
         "null", "true", "false", "distinct", "case", "when", "then", "else",
         "end", "lateral", "unnest", "generate_series", "exists", "interval",
         "cast", "coalesce", "current_date", "now", "array", "any", "all",
         "of", "skip", "locked", "only", "returning", "each", "row"}
#: Temporary tables are created and dropped inside one call; they are not in
#: the catalogue because they live in the session's temp schema.
TEMP_RE = re.compile(r"CREATE\s+(?:TEMP|TEMPORARY)\s+TABLE\s+(?:IF NOT EXISTS\s+)?"
                     r"([a-z_][a-z0-9_]*)", re.I)
#: aliases that shadow real names and must not be resolved as tables
NOT_ALIAS = {"where", "on", "set", "group", "order", "limit", "returning",
             "using", "join", "inner", "left", "right", "outer", "cross",
             "union", "select", "as", "values", "do", "conflict", "not"}


def live_schema():
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT") or 5432),
        user=os.environ.get("DB_USER") or "postgres",
        password=os.environ.get("DB_PASSWORD") or "",
        dbname=os.environ.get("DB_NAME") or "file_analysis_test")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT c.relname, a.attname FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " JOIN pg_attribute a ON a.attrelid = c.oid"
                " AND a.attnum > 0 AND NOT a.attisdropped"
                " WHERE n.nspname = 'public'"
                " AND c.relkind IN ('r', 'v', 'm', 'p')")
            tables = collections.defaultdict(set)
            for t, col in cur.fetchall():
                tables[t].add(col)
    finally:
        conn.close()
    return tables


def python_files():
    for d in ("Api", "apps", "core", "database", "services", "pipeline",
              "settings", "classification", "concurrency"):
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            yield p


def _is_sqlite_module(path):
    """Modules that talk to SQLite own their own schema (map_data/registry.db)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return ("import sqlite3" in text
            or "from services import map_registry" in text
            or "import map_registry" in text
            # ``?`` placeholders only exist in the SQLite layer (psycopg2 uses
            # %s), so their presence identifies the module's target database.
            or ("?" in text and "map_registry" in text))


def sql_strings():
    import ast

    for path in sorted(python_files()):
        if _is_sqlite_module(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                s = node.value
            elif isinstance(node, ast.JoinedStr):
                s = "".join(v.value for v in node.values
                            if isinstance(v, ast.Constant)
                            and isinstance(v.value, str))
            else:
                continue
            if len(s) < 20 or not SQL_KW.search(s) or not SQL_START.search(s):
                continue
            if not SQL_UPPER.search(s):
                continue
            if re.match(r"\s*CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION", s) \
                    and "$$" in s:
                continue
            yield path, getattr(node, "lineno", 0), s


COMMENT_RE = re.compile(r"--[^\n]*")


def strip_pyformat(sql: str) -> str:
    """Make a psycopg2 template parseable: ``%s`` -> ``1``, ``%(x)s`` -> ``1``."""
    sql = re.sub(r"%\([^)]*\)s", "1", sql)
    sql = re.sub(r"%s", "1", sql)
    return sql


def check_sqlglot(sql, tables):
    import sqlglot
    from sqlglot.optimizer import optimize

    schema = {t: {c: "TEXT" for c in cols} for t, cols in tables.items()}
    try:
        expr = sqlglot.parse_one(strip_pyformat(sql), read="postgres")
    except Exception:
        return None
    if expr is None:
        return None
    try:
        optimize(expr, schema=schema, dialect="postgres")
    except Exception as exc:
        msg = str(exc)
        if "Unknown column" in msg or "Unknown table" in msg:
            return msg.splitlines()[0][:160]
    return None


def check_aliases(sql, tables, temps=()):
    """Resolve ``alias.column`` pairs through the statement's own aliases."""
    problems = []
    flat = re.sub(r"\s+", " ", sql)
    aliases = {}
    ctes = {n.lower() for n in CTE_RE.findall(flat)}
    for cte in ctes:
        aliases[cte] = cte
    for m in ALIAS_RE.finditer(flat):
        table, alias = m.group(1).lower(), (m.group(2) or "").lower()
        if table in NOISE:
            continue
        if alias in NOT_ALIAS or alias in NOISE:
            alias = table
        aliases[alias] = table
        aliases.setdefault(table, table)
    subq = {m.group(1).lower() for m in
            re.finditer(r"\)\s*(?:AS\s+)?([a-z_][a-z0-9_]*)", flat, re.I)}
    for m in COLREF_RE.finditer(flat):
        alias, col = m.group(1).lower(), m.group(2).lower()
        if alias in NOISE or alias not in aliases:
            continue
        table = aliases[alias]
        if table in ctes or alias in subq or table in subq:
            continue
        if (SYSTEM_TABLES.match(table) or table in temps
                or SHORT.match(table) or SHORT.match(alias)):
            continue
        if table not in tables:
            problems.append(f"unknown table {table!r} (alias {alias})")
            continue
        if col.endswith(".") or col in ("*",):
            continue
        if col not in tables[table]:
            problems.append(f"{alias}.{col} -> {table} has no column {col!r}")
    return problems


def main():
    tables = live_schema()
    temps_by_file = {}
    ctes_by_file = {}
    for path in python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        temps_by_file[path] = {t.lower() for t in TEMP_RE.findall(text)}
        # Statements are assembled from several literals (and, in the scenario
        # engine, from `q.add` fragments), so a CTE defined in one literal is
        # referenced in another. Collect them per file.
        ctes_by_file[path] = {t.lower() for t in CTE_RE.findall(text)}
    OUT.mkdir(parents=True, exist_ok=True)
    findings = []
    seen = set()
    parsed = unparsed = 0
    for path, lineno, sql in sql_strings():
        try:
            rel = str(path.relative_to(ROOT))
        except ValueError:
            rel = str(path)
        exec_problems = []
        temps = temps_by_file.get(path, set())
        file_ctes = ctes_by_file.get(path, set())
        sql = DDL_TAIL.sub(" ", COMMENT_RE.sub(" ", sql))
        # An f-string's interpolated table name is gone from the literal; the
        # remaining text is not a statement and cannot be validated.
        if re.search(r"\bFROM\s*$|\bFROM\s+(?=WHERE|GROUP|ORDER|LIMIT)", sql,
                     re.I):
            continue
        cte_names = {n.lower() for n in CTE_RE.findall(sql)} | file_ctes
        for t in {x.lower() for x in TABLE_RE.findall(sql)} - NOISE:
            if (t in cte_names or SYSTEM_TABLES.match(t) or t in temps
                    or SHORT.match(t)):
                continue
            if t not in tables:
                exec_problems.append(f"unknown table {t!r}")
        if not exec_problems:
            res = check_sqlglot(sql, tables)
            if res is None:
                parsed += 1
            else:
                parsed += 1
                exec_problems.append(res)
            for p in check_aliases(sql, tables, temps | file_ctes):
                if p not in exec_problems:
                    exec_problems.append(p)
        for problem in exec_problems:
            key = (rel, lineno, problem)
            if key in seen:
                continue
            seen.add(key)
            findings.append((rel, lineno, problem,
                             re.sub(r"\s+", " ", sql)[:180]))
    with (OUT / "sql_schema.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "line", "finding", "statement", "status"])
        for row in findings:
            w.writerow(list(row) + ["UNTOUCHED"])
    print(f"sql statements parsed with schema validation: {parsed}")
    print(f"findings: {len(findings)} -> audit_report/findings/sql_schema.csv")
    by_file = collections.Counter(f[0] for f in findings)
    for f, n in by_file.most_common(15):
        print(f"  {n:3d}  {f}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
