#!/usr/bin/env python
"""Build the audit inventories from the code, not from memory (audit Phase A).

Every row is produced by reading ``app.url_map``, the filesystem, the AST of the
source tree or ``pg_catalog`` - never typed by hand. Output is one CSV per
inventory in ``audit_report/inventory/`` with a ``status`` column initialised to
``UNTOUCHED``; later audit phases rewrite that column.

Usage::

    python tools/audit/build_inventories.py                 # write the CSVs
    python tools/audit/build_inventories.py --only routes   # one inventory
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import pathlib
import re
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "audit_report" / "inventory"
STATUS = "UNTOUCHED"

SOURCE_DIRS = ("Api", "apps", "core", "database", "services", "pipeline",
               "settings", "classification", "concurrency", "tools")


def _rel(path) -> str:
    return str(pathlib.Path(path).relative_to(ROOT))


def _write(name: str, header, rows) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(list(header) + ["status"])
        n = 0
        for row in rows:
            w.writerow(list(row) + [STATUS])
            n += 1
    print(f"{name:24s} {n:5d} rows -> {_rel(path)}")
    return n


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------

def python_files():
    for d in SOURCE_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts or "build" in p.relative_to(ROOT).parts:
                continue
            yield p


def _parse(path):
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def _app():
    """The real application object (imported once, never dispatched)."""
    from apps.web.app import app

    return app


# --------------------------------------------------------------------------
# 1. routes
# --------------------------------------------------------------------------

def route_policy(app):
    """``{endpoint: (auth, roles)}`` inferred from the decorators on each view.

    The application registers a default-deny middleware, so the declared
    policy is what the view itself says: an explicit ``@login_required``-style
    decorator, a role decorator, or nothing (covered by the middleware).
    """
    import inspect
    import textwrap

    policies = {}
    for rule in app.url_map.iter_rules():
        view = app.view_functions.get(rule.endpoint)
        auth, roles = "middleware-default-deny", ""
        if view is None:
            policies[rule.endpoint] = (auth, roles)
            continue
        target = inspect.unwrap(view)
        try:
            src = textwrap.dedent(inspect.getsource(target))
        except (OSError, TypeError):
            src = ""
        found_auth, found_roles = [], []
        for name in ("login_required", "admin_required", "role_required",
                     "requires_role", "analyst_required", "auth_required"):
            if re.search(rf"@\w*\.?{name}\b", src):
                found_auth.append(name)
        for m in re.finditer(r"role_required\(\s*['\"]([\w, ]+)['\"]", src):
            found_roles.append(m.group(1))
        if found_auth:
            auth = "+".join(sorted(set(found_auth)))
        if found_roles:
            roles = ";".join(sorted(set(found_roles)))
        elif found_auth and "admin" in auth:
            roles = "admin"
        policies[rule.endpoint] = (auth, roles)
    return policies


def inv_routes(app):
    policy = route_policy(app)
    rows = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, r.endpoint)):
        methods = ",".join(sorted(m for m in rule.methods
                                  if m not in ("HEAD", "OPTIONS")))
        view = app.view_functions.get(rule.endpoint)
        auth, roles = policy.get(rule.endpoint, ("", ""))
        view_mod = getattr(view, "__module__", "") if view else ""
        rows.append((rule.rule, methods, rule.endpoint, view_mod,
                     ",".join(sorted(rule.arguments)), auth, roles))
    return _write("routes", ("rule", "methods", "endpoint", "view_module",
                             "arguments", "auth_decorator", "roles"), rows)


# --------------------------------------------------------------------------
# 2. templates (+ the route that renders them)
# --------------------------------------------------------------------------

def _render_map():
    """``{template_name: [endpoint...]}`` from ``render_template`` calls."""
    mapping = defaultdict(list)
    for path in python_files():
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", getattr(fn, "id", ""))
                if name not in ("render_template", "render_template_string"):
                    continue
                if name == "render_template_string" or not node.args:
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    mapping[arg.value].append(f"{_rel(path)}")
    return mapping


def inv_templates(app, render_map):
    rows = []
    for path in sorted((ROOT / "templates").rglob("*.html")):
        rel = _rel(path)
        name = str(path.relative_to(ROOT / "templates"))
        rendered_by = render_map.get(name, [])
        rows.append((name, rel, ",".join(sorted(set(rendered_by))) or "-",
                     "yes" if rendered_by else "no",
                     path.stat().st_size))
    return _write("templates", ("template", "path", "rendered_by",
                                "has_route", "bytes"), rows)


# --------------------------------------------------------------------------
# 3. JS modules (+ the templates that load them)
# --------------------------------------------------------------------------

SCRIPT_RE = re.compile(
    r"<script\b[^>]*?(?<![\w-])src=([\"'])(.*?)\1", re.I)
#: Attribute values are quoted with the same character they open with; a
#: Jinja expression inside the attribute contains its own quotes, so the
#: value is taken up to the *matching* quote (a plain ``[^\"']+`` stopped
#: at the first quote inside ``{{ url_for(...) }}``).


URL_FOR_STATIC_RE = re.compile(
    r"url_for\(\s*['\"]static['\"]\s*,\s*filename\s*=\s*['\"]([^'\"]+)['\"]")


def static_reference(ref: str):
    """Normalise a ``src``/``href`` value to ``/static/<file>``.

    Templates write ``{{ url_for('static', filename='js/x.js') }}`` rather than
    a literal path, so matching raw attribute text against ``/static/js/x.js``
    credited nothing: every JavaScript file was reported as loaded by zero
    templates. Jinja expressions that are not ``url_for('static', ...)`` are
    genuinely unresolvable statically and are skipped.
    """
    m = URL_FOR_STATIC_RE.search(ref)
    if m:
        return "/static/" + m.group(1).lstrip("/")
    ref = ref.split("?")[0].split("#")[0]
    if not ref or ref.startswith(("{{", "{%", "http://", "https://", "//")):
        return None
    if ref.startswith("/static/"):
        return ref
    return ref


ES_IMPORT_RE = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(?\s*)['"](\.[^'"]+)['"]""")


def _module_import_refs(refs):
    """Credit a JavaScript file to every module that ``import``s it.

    Page modules are pulled in by other modules (``import { api } from
    './api/api-client.js'``), not by a ``<script src>``, so counting template
    references alone reported half the frontend as never loaded.
    """
    js_root = (ROOT / "static" / "js").resolve()
    for path in js_root.rglob("*.js"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in ES_IMPORT_RE.finditer(text):
            target = (path.parent / m.group(1)).resolve()
            try:
                rel = target.relative_to(js_root)
            except ValueError:
                continue
            refs["/static/js/" + str(rel)].add(_rel(path))


def _template_static_refs():
    refs = defaultdict(set)
    for path in (ROOT / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in SCRIPT_RE.finditer(text):
            url = static_reference(m.group(2))
            if url:
                refs[url.split("?")[0]].add(_rel(path))
    _module_import_refs(refs)
    return refs


def inv_js(refs):
    rows = []
    for path in sorted((ROOT / "static" / "js").rglob("*.js")):
        url = "/static/js/" + str(path.relative_to(ROOT / "static" / "js"))
        loaded_by = sorted(refs.get(url, set()))
        # also credit a page module loaded dynamically by name
        if not loaded_by:
            loaded_by = sorted(
                t for ref, ts in refs.items() if path.name in ref for t in ts)
        rows.append((str(path.relative_to(ROOT / "static" / "js")),
                     path.stat().st_size, len(loaded_by),
                     ",".join(loaded_by) if loaded_by else "-"))
    return _write("js_modules", ("module", "bytes", "loaded_by_count",
                                 "loaded_by_templates"), rows)


# --------------------------------------------------------------------------
# 4. CSS
# --------------------------------------------------------------------------

LINK_RE = re.compile(
    r"<link\b[^>]*?(?<![\w-])href=([\"'])(.*?)\1", re.I)
IMPORT_RE = re.compile(r"@import\s+(?:url\()?[\"']([^\"']+)[\"']", re.I)


def inv_css():
    refs = defaultdict(set)
    for path in (ROOT / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in LINK_RE.finditer(text):
            url = static_reference(m.group(2))
            if not url or not url.split("?")[0].endswith(".css"):
                continue
            if url:
                refs[url.split("?")[0]].add(_rel(path))
    for path in (ROOT / "static" / "js").rglob("*.js"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"[\"'](/static/css/[^\"']+\.css)[\"']", text):
            refs[m.group(1)].add(_rel(path))
    rows = []
    for path in sorted((ROOT / "static" / "css").rglob("*.css")):
        url = "/static/css/" + str(path.relative_to(ROOT / "static" / "css"))
        used = sorted(refs.get(url, set()))
        imports = [m.group(1) for m in
                   IMPORT_RE.finditer(path.read_text(encoding="utf-8",
                                                     errors="replace"))]
        rows.append((str(path.relative_to(ROOT / "static" / "css")),
                     path.stat().st_size, ",".join(used) or "-",
                     ";".join(imports) or "-"))
    return _write("css", ("file", "bytes", "referenced_by", "imports"), rows)


# --------------------------------------------------------------------------
# 5. tables and constraints (frozen migrations + live catalogue)
# --------------------------------------------------------------------------

def _migration_files():
    """Every frozen migration, in version order."""
    return sorted((ROOT / "database" / "migrations").glob("m000*.py"))


def _frozen_schema():
    """``{table: {column: type}}`` and ``{table: [constraint ddl]}``.

    The frozen truth is the whole migration chain, not just the baseline:
    ``m0002``/``m0003`` add columns with ``ALTER TABLE ... ADD COLUMN``. Both
    shapes are parsed here, and a ``CREATE TABLE`` body is closed by counting
    parentheses rather than by a line ending in ``)`` - a multi-line ``CHECK``
    whose last continuation line ends with ``)`` used to close the table early
    and silently drop the columns that followed it (``geo_place_names
    .name_type``).
    """
    tables: dict[str, dict[str, str]] = {}
    constraints: dict[str, list[str]] = {}
    cur = None
    depth = 0
    for path in _migration_files():
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            m = re.search(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)\s*\(", line)
            if m:
                cur = m.group(1)
                tables.setdefault(cur, {})
                depth = line.count("(") - line.count(")")
                continue
            if cur:
                m = re.match(r"\s{4,12}(\w+)\s+([A-Za-z]+[^(,]*)", line)
                if m and m.group(1).upper() not in (
                        "CONSTRAINT", "CHECK", "PRIMARY", "FOREIGN", "UNIQUE"):
                    tables[cur][m.group(1)] = m.group(2).strip().rstrip(",")
                depth += line.count("(") - line.count(")")
                if depth <= 0:
                    cur = None
        # Columns added after the baseline live in ALTER TABLE statements.
        for m in re.finditer(
                r"ALTER TABLE\s+(\w+)\s+(?:.*?)\bADD COLUMN(?:\s+IF NOT EXISTS)?"
                r"\s+(\w+)\s+([A-Za-z]+[^\n,\"]*)", text, re.IGNORECASE | re.DOTALL):
            table, column, ctype = m.group(1), m.group(2), m.group(3)
            tables.setdefault(table, {}).setdefault(column, ctype.strip())
        for m in re.finditer(
                r"CREATE (?:UNIQUE )?INDEX(?: IF NOT EXISTS)?\s+(\w+)"
                r"\s+ON\s+(\w+)", text):
            constraints.setdefault(m.group(2), []).append(f"INDEX {m.group(1)}")
    return tables, constraints


def _live_schema(cfg=None):
    """Live ``pg_catalog``: tables, columns, constraints - the executed truth."""
    sys.path.insert(0, str(ROOT))
    try:
        import psycopg2
    except Exception:
        return None
    host = os.environ.get("DB_HOST")
    if not host:
        return None
    try:
        conn = psycopg2.connect(
            host=host, port=int(os.environ.get("DB_PORT") or 5432),
            user=os.environ.get("DB_USER") or "postgres",
            password=os.environ.get("DB_PASSWORD") or "",
            dbname=os.environ.get("DB_NAME") or "file_analysis_test",
            connect_timeout=5)
    except Exception:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod)"
                " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0"
                " AND NOT a.attisdropped"
                " WHERE n.nspname = 'public' AND c.relkind = 'r'"
                " ORDER BY 1, a.attnum")
            cols = cur.fetchall()
            cur.execute(
                "SELECT c.relname, con.conname, con.contype,"
                " pg_get_constraintdef(con.oid)"
                " FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'public' ORDER BY 1, 2")
            cons = cur.fetchall()
            cur.execute(
                "SELECT tablename, indexname FROM pg_indexes"
                " WHERE schemaname = 'public' ORDER BY 1, 2")
            idx = cur.fetchall()
    finally:
        conn.close()
    return cols, cons, idx


def inv_schema():
    tables, cons = _frozen_schema()
    live = _live_schema()
    live_cols = defaultdict(dict)
    live_cons = defaultdict(list)
    live_idx = defaultdict(list)
    if live:
        for t, c, ty in live[0]:
            live_cols[t][c] = ty
        for t, name, typ, ddl in live[1]:
            live_cons[t].append(f"{name}:{typ}")
        for t, name in live[2]:
            live_idx[t].append(name)
    rows = []
    for table in sorted(set(tables) | set(live_cols)):
        frozen_cols = tables.get(table, {})
        cols = live_cols.get(table, {})
        missing_in_live = sorted(set(frozen_cols) - set(cols))
        extra_in_live = sorted(set(cols) - set(frozen_cols)) if frozen_cols else []
        rows.append((table, len(frozen_cols), len(cols),
                     ",".join(missing_in_live) or "-",
                     ",".join(extra_in_live) or "-",
                     len(live_cons.get(table, [])),
                     ";".join(sorted(live_cons.get(table, [])))[:400] or "-",
                     ";".join(sorted(live_idx.get(table, []))) or "-"))
    return _write("schema", ("table", "frozen_columns", "live_columns",
                             "missing_in_live", "extra_in_live",
                             "live_constraints", "constraints", "indexes"), rows)


# --------------------------------------------------------------------------
# 6. services / repositories / core modules
# --------------------------------------------------------------------------

def inv_modules():
    rows = []
    for path in sorted(python_files()):
        rel = _rel(path)
        tree = _parse(path)
        classes = funcs = imports = 0
        if tree is not None:
            classes = sum(isinstance(n, ast.ClassDef) for n in ast.walk(tree))
            funcs = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        for n in ast.walk(tree))
            imports = sum(isinstance(n, (ast.Import, ast.ImportFrom))
                          for n in ast.walk(tree))
        # who imports it?
        mod = rel[:-3].replace("/", ".")
        stem = re.escape(mod.split(".")[-1])
        # ``from . import import_export`` (a relative import of the module
        # *name*, not its dotted path) used to count as "nobody imports it".
        import_re = re.compile(
            rf"(?m)^\s*(?:from|import)\s+[^\n#]*\b{stem}\b")
        importers = []
        for other in python_files():
            if other == path:
                continue
            text = other.read_text(encoding="utf-8", errors="replace")
            if import_re.search(text):
                importers.append(_rel(other))
        rows.append((rel, path.stat().st_size, classes, funcs, imports,
                     len(importers), ",".join(sorted(importers)[:6]) or "-"))
    return _write("modules", ("module", "bytes", "classes", "functions",
                              "imports", "imported_by", "importers"), rows)


# --------------------------------------------------------------------------
# 7. SQL statements, with their tables
# --------------------------------------------------------------------------

SQL_KW = re.compile(r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|WITH)\b", re.I)
TABLE_RE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_][a-z0-9_]*)", re.I)
SQL_NOISE = {"select", "where", "set", "values", "on", "as", "and", "or", "not",
             "null", "true", "false", "distinct", "case", "when", "then", "else",
             "end", "lateral", "unnest", "generate_series", "exists"}


def _sql_strings(path):
    """Every string literal that looks like SQL, with its line number."""
    tree = _parse(path)
    if tree is None:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if len(s) < 20 or not SQL_KW.search(s):
                continue
            if "CREATE TABLE" in s.upper() and "FROM" not in s.upper():
                continue
            yield node.lineno, s
        elif isinstance(node, ast.JoinedStr):
            parts = [v.value for v in node.values
                     if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            s = "".join(parts)
            if len(s) >= 20 and SQL_KW.search(s):
                yield node.lineno, s


def inv_sql():
    rows = []
    for path in sorted(python_files()):
        for lineno, sql in _sql_strings(path):
            flat = re.sub(r"\s+", " ", sql).strip()
            tables = sorted({t.lower() for t in TABLE_RE.findall(flat)
                             if t.lower() not in SQL_NOISE})
            verb = (re.match(r"\s*(?:#.*\n\s*)*(\w+)", flat) or [None, "?"])[1]
            rows.append((_rel(path), lineno, verb.upper(),
                         ",".join(tables) or "-", flat[:200]))
    return _write("sql", ("file", "line", "verb", "tables", "statement"), rows)


# --------------------------------------------------------------------------
# 8. jobs and schedulers
# --------------------------------------------------------------------------

def inv_jobs():
    from services.jobs import job_state

    tables, _ = _frozen_schema()
    job_cols = sorted(tables.get("jobs", {}))
    ev_cols = sorted(tables.get("job_events", {}))
    rows = []
    for kind in sorted(job_state.EVENT_TYPES):
        rows.append(("event", kind, ",".join(job_cols), "job_events.event_type",
                     "code vocabulary"))
    for status in sorted(getattr(job_state, "STATUSES", []) or []):
        rows.append(("status", str(status), "jobs.status", "jobs.status",
                     "code vocabulary"))
    # job types actually dispatched by the manager
    mgr = (ROOT / "services" / "jobs" / "manager.py").read_text(encoding="utf-8")
    for m in sorted(set(re.findall(r"job_type\s*==\s*[\"'](\w+)[\"']", mgr)) |
                    set(re.findall(r"[\"'](\w+)[\"']\s*:\s*_?(?:run|handle)", mgr))):
        rows.append(("job_type", m, "", "services/jobs/manager.py", "dispatch"))
    for path in sorted((ROOT / "services").rglob("*scheduler*.py")):
        rows.append(("scheduler", _rel(path), "", "", "scheduler module"))
    if not any(r[0] == "scheduler" for r in rows):
        for path in sorted(ROOT.rglob("*schedul*")):
            if path.is_file() and "__pycache__" not in path.parts:
                rows.append(("scheduler", _rel(path), "", "", "scheduler file"))
    return _write("jobs", ("kind", "name", "columns", "target", "source"), rows)


# --------------------------------------------------------------------------
# 9. configuration and environment variables
# --------------------------------------------------------------------------

ENV_RE = re.compile(r"os\.environ(?:\.get)?(?:\[|\()\s*[\"']([A-Z][A-Z0-9_]+)[\"']")
ENV_RE2 = re.compile(r"os\.getenv\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']")


def inv_env():
    used = defaultdict(list)
    for path in python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in list(ENV_RE.finditer(text)) + list(ENV_RE2.finditer(text)):
            used[m.group(1)].append(_rel(path))
    docs = ""
    for name in ("CONFIGURATION.md", "README.md", "DEVELOPMENT.md", "SECURITY.md"):
        for cand in (ROOT / "docs" / name, ROOT / name):
            if cand.exists():
                docs += cand.read_text(encoding="utf-8", errors="replace")
    doc_names = {n for n in used if n in docs}
    rows = []
    for var in sorted(used):
        rows.append((var, len(used[var]), ",".join(sorted(set(used[var]))[:4]),
                     "documented" if var in doc_names else "undocumented"))
    return _write("env_vars", ("variable", "read_by", "files", "documentation"),
                  rows)


# --------------------------------------------------------------------------
# 10. i18n msgids
# --------------------------------------------------------------------------

MSGID_CALLS = frozenset({"_", "gettext", "ngettext"})

#: Last resort for a Python file the parser cannot read: one literal only.
PY_MSGID_RE = re.compile(r"""\b(?:_|gettext|ngettext)\(\s*["']((?:[^"'\\]|\\.)*)["']""")
CALL_START_RE = re.compile(r"""(?<![\w$.])(?:_|gettext|ngettext)\s*\(""")
LITERAL_RE = re.compile(r""""((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'""", re.S)
ESCAPE_RE = re.compile(r"\\(.)")
ESCAPE_CHARS = {"n": "\n", "t": "\t", "r": "\r", "0": "\0"}


def _unescape(literal: str) -> str:
    """The characters a template or JS string literal stands for.

    The catalogue stores the runtime string, so ``doesn\\'t`` in a template has
    to be compared as ``doesn't`` - otherwise every apostrophe in the UI looks
    like a missing translation.
    """
    return ESCAPE_RE.sub(lambda m: ESCAPE_CHARS.get(m.group(1), m.group(1)), literal)


def python_msgids(text: str) -> set:
    """Msgids in one Python file, with adjacent literals already joined.

    ``ast`` folds implicit concatenation, so ``_("a " "b")`` yields one msgid
    rather than two fragments. The regex fallback exists only for a file that
    will not parse - and it cannot join anything.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {_unescape(m.group(1)) for m in PY_MSGID_RE.finditer(text)}
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in MSGID_CALLS:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.add(first.value)
        elif (isinstance(first, ast.BinOp) and isinstance(first.op, ast.Add)
              and isinstance(first.left, ast.Constant) and isinstance(first.right, ast.Constant)
              and isinstance(first.left.value, str) and isinstance(first.right.value, str)):
            found.add(first.left.value + first.right.value)
    return found


def _balanced_argument(text: str, start: int) -> str:
    """The argument text of a call whose opening ``(`` ends at ``start``."""
    depth, quote, i = 1, None, start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return text[start:]


def _joined_literals(argument: str):
    """The msgid when the argument is literals joined by ``+`` (JS) or ``~``.

    Returns ``None`` when the argument is not a literal expression, so a call
    whose first argument is a variable is not mistaken for a msgid.
    """
    pieces, pos, want_literal = [], 0, True
    while True:
        while pos < len(argument) and argument[pos].isspace():
            pos += 1
        if pos >= len(argument):
            break
        if want_literal:
            match = LITERAL_RE.match(argument, pos)
            if not match:
                return None
            value = match.group(1) if match.group(1) is not None else match.group(2)
            pieces.append(_unescape(value))
            pos = match.end()
        else:
            if argument[pos] not in "+~":
                break
            pos += 1
        want_literal = not want_literal
    return "".join(pieces) if pieces else None


def markup_msgids(text: str) -> set:
    """Msgids in a template or JS file, joining literals across line breaks."""
    found = set()
    for match in CALL_START_RE.finditer(text):
        value = _joined_literals(_balanced_argument(text, match.end()))
        if value:
            found.add(value)
    return found


def _catalog(lang):
    try:
        import polib
    except Exception:
        polib = None
    path = ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po"
    ids = set()
    if not path.exists():
        return ids
    if polib is not None:
        return {e.msgid for e in polib.pofile(str(path)) if e.msgid}
    for m in re.finditer(r'^msgid "(.*)"$', path.read_text(encoding="utf-8"),
                         re.M):
        if m.group(1):
            ids.add(m.group(1))
    return ids


def inv_i18n():
    """Every msgid with the catalogues that hold it, spelled in full.

    The msgid is written whole - truncating it makes the worksheet useless to
    a translator, and a truncated key cannot be looked up in a catalogue.
    """
    langs = ["en", "ar", "he", "fa", "hr"]
    catalogs = {lang: _catalog(lang) for lang in langs}
    usage = defaultdict(set)
    for path in (ROOT / "templates").rglob("*.html"):
        for msgid in markup_msgids(path.read_text(encoding="utf-8", errors="replace")):
            usage[msgid].add(_rel(path))
    for path in (ROOT / "static" / "js").rglob("*.js"):
        if ".min." in path.name:
            continue
        for msgid in markup_msgids(path.read_text(encoding="utf-8", errors="replace")):
            usage[msgid].add(_rel(path))
    for path in python_files():
        for msgid in python_msgids(path.read_text(encoding="utf-8", errors="replace")):
            usage[msgid].add(_rel(path))
    rows = []
    for msgid in sorted(usage):
        presence = [lang for lang in langs if msgid in catalogs[lang]]
        rows.append((msgid, len(usage[msgid]),
                     ",".join(sorted(usage[msgid])[:3]),
                     ",".join(presence) or "-",
                     "complete" if len(presence) == len(langs) else "gap"))
    for lang in langs:
        for msgid in sorted(catalogs[lang] - set(usage)):
            if len(msgid) < 3:
                continue
            rows.append((msgid, 0, "(catalog only)",
                         ",".join(l for l in langs if msgid in catalogs[l]),
                         "unused"))
    return _write("i18n_msgids", ("msgid", "used_by", "first_uses",
                                  "catalogs", "coverage"), rows)


# --------------------------------------------------------------------------
# 11. interface registry
# --------------------------------------------------------------------------

def inv_interfaces(app):
    """One row per registered interface, resolved against the running app.

    The registry stores an endpoint name in ``route`` (``index``,
    ``files.files_list``), not a URL. Resolving it through ``app.url_map`` is
    the only way to learn whether the interface has a page behind it - and
    whether that page has a template and a test.
    """
    from core.interfaces import registry

    entries = list(getattr(registry, "REGISTRY", ()))
    by_endpoint = {r.endpoint: r for r in app.url_map.iter_rules()}
    tpls = {str(p.relative_to(ROOT / "templates"))
            for p in (ROOT / "templates").rglob("*.html")}
    rendered = _render_map()
    tests_text = ""
    for tests_dir in (ROOT / "tests",):
        for path in tests_dir.rglob("*.py"):
            tests_text += path.read_text(encoding="utf-8", errors="replace")
    rows = []
    for entry in entries:
        key = getattr(entry, "interface_id", "")
        endpoint = getattr(entry, "route", "") or ""
        rule = by_endpoint.get(endpoint)
        templates = rendered.get(endpoint, [])
        # A page's template is the one its view renders; fall back to the
        # template whose name matches the interface id.
        template = templates[0] if templates else ""
        if not template:
            guess = f"{key}.html"
            template = guess if guess in tpls else ""
        tests = "yes" if key in tests_text else "no"
        rows.append((key, getattr(entry, "name", ""),
                     str(getattr(entry, "kind", "")).split(".")[-1]
                     or str(getattr(entry, "kind", "")),
                     rule.rule if rule else "-", endpoint,
                     template or "-",
                     "yes" if template and template in tpls else "-",
                     "yes" if rule else "NO-ROUTE",
                     getattr(entry, "required_role", None) or "-",
                     tests))
    return _write("interfaces", ("key", "title", "kind", "rule", "endpoint",
                                 "template", "template_exists", "route_exists",
                                 "required_role", "has_test"), rows)


# --------------------------------------------------------------------------

BUILDERS = {
    "routes": lambda app: inv_routes(app),
    "templates": lambda app: inv_templates(app, _render_map()),
    "js": lambda app: inv_js(_template_static_refs()),
    "css": lambda app: inv_css(),
    "schema": lambda app: inv_schema(),
    "modules": lambda app: inv_modules(),
    "sql": lambda app: inv_sql(),
    "jobs": lambda app: inv_jobs(),
    "env": lambda app: inv_env(),
    "i18n": lambda app: inv_i18n(),
    "interfaces": lambda app: inv_interfaces(app),
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", action="append", choices=sorted(BUILDERS))
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    app = _app()
    wanted = args.only or list(BUILDERS)
    total = 0
    for name in wanted:
        total += BUILDERS[name](app)
    print(f"TOTAL {total} rows across {len(wanted)} inventories")


if __name__ == "__main__":
    main()
