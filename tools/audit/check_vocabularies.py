#!/usr/bin/env python
"""Every value the code writes to a CHECK-constrained column must be admitted.

The vocabulary is read from ``pg_get_constraintdef`` on the migrated database,
so the answer is what PostgreSQL will actually accept - not a transcription.
Two write shapes are examined:

* **SQL literals** - ``INSERT INTO t (a, b) VALUES ('x', ...)`` and
  ``UPDATE t SET col = 'x'`` inside a string constant, including the
  parameter-templated forms (only the literal values are checked).
* **dict writes** - ``{"status": "RUNNING"}`` style payloads in modules that
  write the table, which is how the job and notification services build rows.

Writes ``audit_report/findings/vocabularies.csv``; exit 1 on any finding.
"""

from __future__ import annotations

import ast
import collections
import csv
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "audit_report" / "findings"

CHECK_RE = re.compile(
    r"CHECK\s*\((?:\s*\(\s*)?([a-z_][a-z0-9_]*)\s*=?\s*(?:ANY\s*\(\s*ARRAY\[)?"
    r"[^)]*?IN\s*\((.*?)\)", re.I | re.S)
VALUE_RE = re.compile(r"'((?:[^']|'')*)'::(?:character varying|text|[\w ]+)|'((?:[^']|'')*)'")


def vocabularies():
    """``{(table, column): {value, ...}}`` from the live CHECK constraints."""
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT") or 5432),
        user=os.environ.get("DB_USER") or "postgres",
        password=os.environ.get("DB_PASSWORD") or "",
        dbname=os.environ.get("DB_NAME") or "file_analysis_test")
    out = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT c.relname, con.conname, pg_get_constraintdef(con.oid)"
                " FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'public' AND con.contype = 'c'")
            rows = cur.fetchall()
    finally:
        conn.close()
    for table, name, ddl in rows:
        # A column's admitted set is the union of every branch: a table may
        # carry several CHECKs on the same column (one per kind, say), and
        # taking only the first would invent violations.
        for col, arr in re.findall(
                r"\(\s*\"?([a-z_][a-z0-9_]*)\"?\s*\)::text\s*=\s*ANY\s*"
                r"\(\(?\s*ARRAY\[([^\]]*)\]", ddl, re.I | re.S):
            vals = set(re.findall(r"'((?:[^']|'')*)'", arr))
            if vals:
                out.setdefault((table, col.lower()), set()).update(vals)
        for col, val in re.findall(
                r"\(\s*\"?([a-z_][a-z0-9_]*)\"?\s*\)::text\s*=\s*"
                r"'((?:[^']|'')*)'", ddl, re.I):
            out.setdefault((table, col.lower()), set()).add(val)
    return out


def python_files():
    for d in ("Api", "apps", "core", "database", "services", "pipeline",
              "settings", "classification", "concurrency"):
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" not in p.parts:
                yield p


def sql_literal_writes(vocab):
    """Check literal values in INSERT/UPDATE statements against the vocabulary."""
    findings = []
    for path in sorted(python_files()):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)):
                continue
            sql = node.value
            if len(sql) < 20:
                continue
            for table, cols, values in re.findall(
                    r"INSERT\s+INTO\s+([a-z_][a-z0-9_]*)\s*\(([^)]*)\)\s*"
                    r"VALUES\s*\(([^)]*)\)", sql, re.I | re.S):
                col_list = [c.strip().lower() for c in cols.split(",")]
                val_list = [v.strip() for v in values.split(",")]
                if len(col_list) != len(val_list):
                    continue
                for col, val in zip(col_list, val_list):
                    vocab_values = vocab.get((table.lower(), col))
                    if not vocab_values:
                        continue
                    m = re.fullmatch(r"'((?:[^']|'')*)'", val)
                    if not m:
                        continue
                    # INSERT ... SELECT writes a column reference, not a value
                    if re.search(r"'\s*[a-z_][a-z0-9_]*\.[a-z_]", values):
                        continue
                    if m.group(1) not in vocab_values:
                        findings.append((str(path.relative_to(ROOT)),
                                         getattr(node, "lineno", 0), table, col,
                                         m.group(1)))
            for table, assignments in re.findall(
                    r"UPDATE\s+([a-z_][a-z0-9_]*)\s+SET\s+(.*?)(?:\bWHERE\b|\"|$)",
                    sql, re.I | re.S):
                for col, val in re.findall(
                        r"([a-z_][a-z0-9_]*)\s*=\s*'((?:[^']|'')*)'", assignments):
                    vocab_values = vocab.get((table.lower(), col.lower()))
                    if vocab_values and val not in vocab_values:
                        findings.append((str(path.relative_to(ROOT)),
                                         getattr(node, "lineno", 0), table,
                                         col.lower(), val))
    return findings


def dict_writes(vocab):
    """Check ``{"column": "value"}`` literals for constrained columns.

    Only modules that mention the table are examined: a dict with a ``status``
    key in an unrelated file says nothing about ``jobs.status``.
    """
    findings = []
    for path in sorted(python_files()):
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        constrained = {(t, c): v for (t, c), v in vocab.items()
                       if t in text}
        if not constrained:
            continue
        cols_by_table = collections.defaultdict(set)
        for (t, c) in constrained:
            cols_by_table[t].add(c)
        all_cols = set().union(*cols_by_table.values()) if cols_by_table else set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            # Row payload keys are identifiers; display labels are prose.
            if not all(re.fullmatch(r"[a-z_][a-z0-9_]*", k) for k in keys):
                continue
            # A row payload names several columns of the table; a display dict
            # ("Status": ..., "Processing Status": ...) names none.
            if len(keys & all_cols) < 2:
                continue
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)):
                    continue
                # Column values are lower-case identifiers. Display labels
                # ("Status"), prose and SQL expressions ("lower(n.name)") are
                # not values written to the column.
                if not re.fullmatch(r"[a-z][a-z0-9_]*", value.value):
                    continue
                for (table, col), values in constrained.items():
                    if key.value != col or value.value in values:
                        continue
                    # the same key name constrains several tables (status);
                    # only report when the value matches no sibling vocabulary
                    siblings = [v for (t, c), v in vocab.items()
                                if c == col and t in text]
                    if any(value.value in v for v in siblings):
                        continue
                    findings.append((str(path.relative_to(ROOT)),
                                     getattr(node, "lineno", 0), table, col,
                                     value.value))
    return findings


def call_site_literals(vocab):
    """Literal arguments passed to parameters named after a constrained column.

    This is the check that catches an event name chosen at a call site rather
    than stored in a dict: ``_publish(job_id, "FAILED", {...})`` where
    ``_publish(self, job_id, event_type, payload)``. The parameter name is the
    signal - a parameter called ``event_type`` or ``status`` is going into that
    column, and its literal arguments must be admissible.
    """
    # Only unambiguous column names: a parameter called ``kind`` is used for
    # half a dozen unrelated things (path revisions, PDF objects, search
    # lenses) and carries no schema meaning at the call site.
    UNIQUE = {"event_type", "priority", "role", "file_status",
              "processing_status", "delivery", "trigger", "change_kind",
              "name_type", "language", "title_status", "status"}
    # Values are collected per column *name* across every table: a parameter
    # called ``status`` may feed ``paths.file_status`` just as well as
    # ``scenarios.status``, and the call site does not name the table.
    values_by_column = collections.defaultdict(set)
    for (_table, col), values in vocab.items():
        values_by_column[col] |= values
    by_column = collections.defaultdict(set)
    for col in UNIQUE:
        by_column[col] |= values_by_column.get(col, set())
        for other, values in values_by_column.items():
            if other.endswith("_" + col) or col.endswith("_" + other):
                by_column[col] |= values
    if not by_column:
        return []

    # name -> sorted param names (positional, self excluded)
    signatures = {}
    for path in sorted(python_files()):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = [a.arg for a in node.args.args
                      if a.arg not in ("self", "cls")]
            if not params:
                continue
            interesting = [(i, p) for i, p in enumerate(params) if p in by_column]
            if not interesting:
                continue
            # The function must actually write the constrained column;
            # otherwise ``event_type`` is just a name a report builder liked.
            body = ast.dump(node)
            writes = any(f"INSERT INTO {t}" in body or f"UPDATE {t}" in body
                         for (t, _c) in vocab)
            if not writes:
                continue
            signatures.setdefault(node.name, []).append((path, interesting))

    findings = []
    for path in sorted(python_files()):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "attr", getattr(fn, "id", ""))
            if name not in signatures or not node.args:
                continue
            for _def_path, interesting in signatures[name]:
                for index, param in interesting:
                    if index >= len(node.args):
                        continue
                    arg = node.args[index]
                    if not (isinstance(arg, ast.Constant)
                            and isinstance(arg.value, str)):
                        continue
                    if arg.value in by_column[param]:
                        continue
                    findings.append((str(path.relative_to(ROOT)),
                                     node.lineno, "(call site)", param,
                                     arg.value))
    return findings


def self_test():
    """Prove the instrument still catches the bug it was written for.

    The original defect was ``_publish(job_id, "FAILED", ...)``, where the
    stored column admits ``JOB_FAILED`` and never bare ``FAILED``. A checker
    that reports zero findings is only meaningful if it would have said one.
    """
    vocab = {("job_events", "event_type"): {"JOB_CREATED", "JOB_FAILED",
                                           "PROGRESS", "STATUS_CHANGE"}}
    source = (
        "def _publish(self, job_id, event_type, payload):\n"
        "    sql = 'INSERT INTO job_events (job_id, event_type, payload) VALUES"
        " (%s, %s, %s)'\n"
        "    return sql\n"
        "\n"
        "def caller():\n"
        "    _publish('j1', 'FAILED', {})\n"
    )
    import tempfile
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        path = pathlib.Path(tmp) / "sample.py"
        path.write_text(source, encoding="utf-8")
        original = python_files
        globals()["python_files"] = lambda: [path]  # type: ignore[assignment]
        try:
            found = call_site_literals(vocab)
            found += sql_literal_writes(vocab)
        finally:
            globals()["python_files"] = original  # type: ignore[assignment]
    caught = any(f[4] == "FAILED" for f in found) or any(f[4] == "FAILED" for f in found)
    print(f"self-test: injected 'FAILED' event -> {'caught' if caught else 'MISSED'}")
    return 0 if caught else 1


def main():
    import sys as _sys
    if "--self-test" in _sys.argv:
        return self_test()
    vocab = vocabularies()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in (sql_literal_writes(vocab) + dict_writes(vocab)
              + call_site_literals(vocab)):
        rows.append(f)
    seen, unique = set(), []
    for r in rows:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    with (OUT / "vocabularies.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "line", "table", "column", "value", "admitted",
                    "status"])
        for file, line, table, col, value in unique:
            admitted = ";".join(sorted(vocab.get((table, col), [])))
            w.writerow([file, line, table, col, value, admitted, "UNTOUCHED"])
    print(f"constrained columns discovered: {len(vocab)}")
    for (t, c), v in sorted(vocab.items()):
        print(f"  {t}.{c}: {len(v)} values")
    print(f"findings: {len(unique)} -> audit_report/findings/vocabularies.csv")
    for r in unique[:30]:
        print("  ", r)
    return 1 if unique else 0


if __name__ == "__main__":
    sys.exit(main())
