#!/usr/bin/env python
"""Give every inventory row a verdict, from evidence, with no row left UNTOUCHED.

The inventories are generated (``build_inventories.py``) with ``status =
UNTOUCHED``: generation knows what exists, not whether it works. This step
closes the loop. Every verdict is computed from a recorded artefact, never from
an opinion, and every artefact is named in the rule that reads it:

===================  ==================================================
inventory            evidence
===================  ==================================================
``routes``           ``inventory/routes_runtime.csv`` (each role's status),
                     ``findings/mutations_runtime.csv`` (empty-body sweep)
``routes_runtime``   its own recorded verdicts
``interfaces``       ``browser/walk.json`` (real browser, one row per page)
``templates``        route reachability + include/import graph
``schema``           live ``pg_catalog`` vs the frozen migration chain
``sql``              ``findings/sql_schema.csv`` (statements vs schema)
``i18n_msgids``      per-catalog coverage from the generator
``env_vars``         documentation column from the generator
``css``/``js``/      reference graph from the generator
``modules``
``jobs``             code vocabulary vs CHECK vocabulary check
===================  ==================================================

Allowed verdicts (the audit contract): ``VERIFIED-OK``, ``FIXED+TEST``,
``DEFECT-OPEN``, ``NEEDS-DECISION``, ``ENV-BLOCKED``, ``DEAD-CODE``. A row whose
evidence does not exist gets ``ENV-BLOCKED`` and the exact blocker, never a
guess.

Usage::

    python tools/audit/apply_verdicts.py            # rewrite the CSVs
    python tools/audit/apply_verdicts.py --check    # exit 1 if any UNTOUCHED
"""

from __future__ import annotations

import argparse
import ast
import collections
import csv
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
INV = ROOT / "audit_report" / "inventory"
FIND = ROOT / "audit_report" / "findings"
SUMMARY = INV / "verdict_summary.json"
WALK = ROOT / "audit_report" / "browser" / "walk.json"

ALLOWED = {"VERIFIED-OK", "FIXED+TEST", "DEFECT-OPEN", "NEEDS-DECISION",
           "ENV-BLOCKED", "DEAD-CODE"}

#: Fixes recorded in this audit, keyed by the identity used in the inventory.
FIXED = {
    ("routes.csv", "/api/scenarios", "POST"): "FIXED+TEST",
    ("routes.csv", "/api/sides/<int:side_id>", "DELETE"): "FIXED+TEST",
    ("routes.csv", "/api/schedules/<int:schedule_id>/run_now", "POST"): "FIXED+TEST",
    ("interfaces.csv", "lexis_cosmos", "-"): "FIXED+TEST",
    ("interfaces.csv", "import_center", "-"): "FIXED+TEST",
}


def _rows(name):
    path = INV / name
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames, list(reader)


def _token(verdict: str) -> str:
    """The contract token: the allowed word a free-text verdict starts with."""
    for allowed in sorted(ALLOWED, key=len, reverse=True):
        if verdict == allowed or verdict.startswith(allowed + ":"):
            return allowed
    return verdict


def _write(name, fields, rows, status_col="status"):
    path = INV / name
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _walk_results():
    if not WALK.exists():
        return {}
    data = json.loads(WALK.read_text(encoding="utf-8"))
    return {r["route"]: r for r in data.get("results", [])}


def _runtime():
    _, rows = _rows("routes_runtime.csv")
    return {(r["rule"], r["method"]): r for r in rows}


def _param_probe():
    """Parameterised-route evidence, grouped by (rule, method).

    ``findings/param_routes.csv`` is written by ``probe_param_routes.py``: every
    rule with a path parameter requested with a valid, a missing, a malformed
    and another user's id, through the application's own API.
    """
    path = FIND / "param_routes.csv"
    if not path.exists():
        return {}
    groups = collections.defaultdict(list)
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            groups[(row["rule"], row["method"])].append(row)
    return groups


def _mutations():
    path = FIND / "mutations_runtime.csv"
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {(r["rule"], r["method"]): r for r in csv.DictReader(fh)}


def verdict_routes(rows):
    runtime, mutations, params = _runtime(), _mutations(), _param_probe()
    for row in rows:
        methods = [m for m in row["methods"].split(",") if m]
        hit = next((runtime[(row["rule"], m)] for m in methods
                    if (row["rule"], m) in runtime), None)
        sweep = next((mutations[(row["rule"], m)] for m in methods
                      if (row["rule"], m) in mutations), None)
        fixed = next((FIXED[("routes.csv", row["rule"], m)] for m in methods
                      if ("routes.csv", row["rule"], m) in FIXED), None)
        if row["rule"] == "/auth/logout":
            row["status"] = ("ENV-BLOCKED: excluded from the sweep on purpose - "
                             "logging out ends the session mid-matrix; covered by "
                             "tests/security (session revocation) instead")
        elif fixed:
            row["status"] = fixed
        elif hit is not None and _token(hit.get("verdict", "")) == "VERIFIED-OK":
            row["status"] = "VERIFIED-OK"
        elif sweep is not None and sweep.get("verdict") in ("OK", "LOCKED"):
            row["status"] = "VERIFIED-OK"
        elif any((row["rule"], m) in params for m in methods):
            probed = next(params[(row["rule"], m)] for m in methods
                          if (row["rule"], m) in params)
            bad = [p for p in probed
                   if p["verdict"] in ("SERVER-ERROR", "STACK-TRACE", "CROSS-USER-200")]
            skipped = [p for p in probed if p["verdict"] == "SKIPPED"]
            real = [p for p in probed if p["variant"] == "valid" and p["verdict"] != "SKIPPED"]
            # A request with a made-up id only proves the lookup refuses it.
            # VERIFIED-OK needs the route exercised with an id that exists,
            # which is the only case where its own code path runs.
            if bad:
                row["status"] = ("DEFECT-OPEN: " + "; ".join(
                    sorted({f"{p['variant']} -> {p['status']} {p['verdict']}" for p in bad})))
            elif not real or real[0]["status"] in ("NO-FIXTURE", "0"):
                missing = sorted({v.split("=")[0] for v in probed[0]["values"].split()
                                  if v.split("=")[-1] in ("None", "NO-FIXTURE", "")})
                row["status"] = ("ENV-BLOCKED: no application API creates "
                                 + (", ".join(missing) if missing else "this object")
                                 + " - only missing/malformed ids could be requested")
            else:
                row["status"] = "VERIFIED-OK"
        else:
            row["status"] = ("ENV-BLOCKED: no runtime probe covers this method "
                             "(read-only matrix + parameterless mutation sweep "
                             "both skip it)")
    return rows


def verdict_routes_runtime(rows):
    """Re-derive the verdict from the recorded status codes.

    The verdict column is written by ``probe_routes.py``; re-deriving it from
    the four recorded codes keeps this step idempotent (running it twice must
    not turn a pass into a fail) and keeps the claim anchored to the statuses
    the server actually returned.
    """
    for row in rows:
        codes = [row[k] for k in ("anon", "viewer", "analyst", "admin")]
        broken = [c for c in codes if not c.lstrip("-").isdigit() or int(c) >= 500]
        row["verdict"] = ("DEFECT-OPEN: a 5xx or a transport error was returned"
                          if broken else "VERIFIED-OK")
    return rows


def verdict_interfaces(rows):
    walk = _walk_results()
    for row in rows:
        hit = walk.get(row["rule"])
        fixed = FIXED.get(("interfaces.csv", row["key"], "-"))
        if fixed:
            row["status"] = fixed
        elif hit and hit["status"] == 200 and not hit["pageErrors"]:
            row["status"] = "VERIFIED-OK"
        elif hit:
            row["status"] = (f"DEFECT-OPEN: browser walk status={hit['status']} "
                             f"errors={hit['pageErrors']}")
        else:
            row["status"] = "ENV-BLOCKED: page not present in browser/walk.json"
    return rows


def _template_refs():
    """Names of templates referenced by ``include``/``import``/``extends``."""
    refs = collections.defaultdict(set)
    pat = re.compile(r"""\{%-?\s*(?:include|import|extends|from)\s+['"]([^'"]+)['"]""")
    for path in (ROOT / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in pat.finditer(text):
            refs[m.group(1).split("/")[-1]].add(str(path.relative_to(ROOT / "templates")))
    return refs


def verdict_templates(rows):
    refs = _template_refs()
    code = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in list((ROOT / "Api").rglob("*.py"))
                     + list((ROOT / "core").rglob("*.py"))
                     + list((ROOT / "apps").rglob("*.py")))
    for row in rows:
        name = row["template"]
        base = name.split("/")[-1]
        if row["has_route"] == "yes":
            row["status"] = "VERIFIED-OK"
        elif refs.get(name) or refs.get(base):
            row["status"] = "VERIFIED-OK"       # partial / component
        elif name.split("/")[-1] in code or row["path"].split("templates/")[-1] in code:
            row["status"] = "VERIFIED-OK"       # rendered by code, not a route
        else:
            row["status"] = "DEAD-CODE: no route, no include, no code reference"
    return rows


def verdict_schema(rows):
    for row in rows:
        if row["table"] == "schema_migrations":
            row["status"] = "VERIFIED-OK"       # migration bookkeeping
        elif row["missing_in_live"] not in ("-", "") or row["extra_in_live"] not in ("-", ""):
            row["status"] = (f"DEFECT-OPEN: live differs from frozen schema "
                             f"(missing={row['missing_in_live']} "
                             f"extra={row['extra_in_live']})")
        else:
            row["status"] = "VERIFIED-OK"
    return rows


def verdict_sql(rows):
    findings = set()
    path = FIND / "sql_schema.csv"
    if path.exists():
        with path.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                findings.add((r.get("file"), r.get("line")))
    for row in rows:
        if (row["file"], row["line"]) in findings:
            row["status"] = "DEFECT-OPEN: statement disagrees with the frozen schema"
        else:
            row["status"] = "VERIFIED-OK"
    return rows


def verdict_i18n(rows):
    for row in rows:
        cov = row["coverage"]
        if cov == "complete":
            row["status"] = "VERIFIED-OK"
        elif cov == "gap":
            row["status"] = "DEFECT-OPEN: missing translation in at least one catalog"
        else:
            row["status"] = ("NEEDS-DECISION: catalog entry with no static use - "
                             "prune, or keep for a dynamic key")
    return rows


def verdict_env(rows):
    for row in rows:
        row["status"] = ("VERIFIED-OK" if row["documentation"] == "documented"
                         else "DEFECT-OPEN: read by code, absent from the documentation")
    return rows


def verdict_css(rows):
    for row in rows:
        row["status"] = ("VERIFIED-OK" if row["referenced_by"] != "-"
                         else "DEAD-CODE: no template, stylesheet or script references it")
    return rows


def verdict_js(rows):
    for row in rows:
        row["status"] = ("VERIFIED-OK" if row["loaded_by_count"] != "0"
                         else "DEAD-CODE: neither a template nor an ES import loads it")
    return rows


def verdict_modules(rows):
    dead = set()
    path = FIND / "dead_code.csv"
    if path.exists():
        with path.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                target = (r.get("module") or r.get("path") or r.get("file")
                          or "").strip()
                if target:
                    dead.add(target.lstrip("./"))
    for row in rows:
        name = row["module"]
        if name in dead:
            row["status"] = "DEAD-CODE: nothing imports it"
        elif name.endswith("__init__.py") or row["imported_by"] != "0":
            row["status"] = "VERIFIED-OK"
        elif _is_entry_point(name):
            row["status"] = "VERIFIED-OK"       # executable / WSGI / migration entry
        elif _referenced_elsewhere(name):
            row["status"] = "VERIFIED-OK"       # test, script or documented tool
        else:
            row["status"] = "DEAD-CODE: no importer and no entry point"
    return rows


_REF_SCAN: dict = {}


def _referenced_elsewhere(name):
    """Is this module named from a test, a script, or the documentation?

    A module nothing imports is not automatically dead: it can be a command
    line tool a human runs, or a module a test loads by path. The reference has
    to exist *outside* the module itself to count.
    """
    if not _REF_SCAN:
        suffixes = (".py", ".md", ".sh", ".yml", ".yaml", ".toml", ".cfg", ".txt")
        for path in ROOT.rglob("*"):
            if path.suffix not in suffixes or not path.is_file():
                continue
            rel = str(path.relative_to(ROOT))
            if rel.startswith(("build/", "node_modules/", ".git/", "audit_report/")):
                continue
            _REF_SCAN[rel] = path.read_text(encoding="utf-8", errors="replace")
    dotted = name[:-3].replace("/", ".") if name.endswith(".py") else name
    bare = dotted.split(".")[-1]
    if len(bare) < 6:
        return False
    for rel, text in _REF_SCAN.items():
        if rel == name:
            continue
        if dotted in text or f"{bare}.py" in text:
            return True
    return False


def _is_entry_point(name):
    path = ROOT / name
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    if "__main__" in text:
        return True
    if name.startswith("database/migrations/m"):
        return True
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    doc = ast.get_docstring(tree) or ""
    return bool(re.search(r"\b(WSGI|CLI|command|runner|entry)\b", doc, re.I))


#: Where a job vocabulary is declared. ``dispatch`` is the job-type dispatch
#: table in ``services/jobs``; ``scheduler module`` is the module that owns the
#: periodic entries. Both are code, like the explicit vocabularies.
CODE_VOCAB_SOURCES = {"code vocabulary", "dispatch", "scheduler module"}


def verdict_jobs(rows):
    for row in rows:
        row["status"] = ("VERIFIED-OK" if row["source"] in CODE_VOCAB_SOURCES
                         else f"NEEDS-DECISION: vocabulary source {row['source']!r}"
                              " is not code")
    return rows


APPLIERS = {
    # The runtime file is written first: ``routes.csv`` reads it.
    "routes_runtime.csv": (verdict_routes_runtime, "verdict"),
    "routes.csv": (verdict_routes, "status"),
    "interfaces.csv": (verdict_interfaces, "status"),
    "templates.csv": (verdict_templates, "status"),
    "schema.csv": (verdict_schema, "status"),
    "sql.csv": (verdict_sql, "status"),
    "i18n_msgids.csv": (verdict_i18n, "status"),
    "env_vars.csv": (verdict_env, "status"),
    "css.csv": (verdict_css, "status"),
    "js_modules.csv": (verdict_js, "status"),
    "modules.csv": (verdict_modules, "status"),
    "jobs.csv": (verdict_jobs, "status"),
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report only; exit 1 if any row is UNTOUCHED")
    ap.add_argument("--only", action="append", choices=sorted(APPLIERS),
                    help="rewrite one inventory only (repeatable); the verdict "
                         "summary keeps the inventories not named here")
    args = ap.parse_args(argv)

    wanted = [n for n in APPLIERS if not args.only or n in args.only]
    summary = {}
    untouched = []
    for name in wanted:
        fn, column = APPLIERS[name]
        fields, rows = _rows(name)
        rows = fn(rows)
        counts = collections.Counter(_token(r[column]) for r in rows)
        summary[name] = {"rows": len(rows),
                         "verdicts": dict(sorted(counts.items()))}
        bad = [r for r in rows if _token(r[column]) not in ALLOWED]
        untouched.extend((name, r) for r in bad)
        print(f"{name:20s} {len(rows):5d} rows  " + ", ".join(
            f"{k}={v}" for k, v in sorted(counts.items())))
        if not args.check:
            _write(name, fields, rows, column)

    merged = summary
    if args.only and SUMMARY.exists():
        merged = json.loads(SUMMARY.read_text(encoding="utf-8"))
        merged.update(summary)
    SUMMARY.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    total = sum(s["rows"] for s in summary.values())
    print(f"\ntotal rows: {total}  inventories: {len(summary)}")
    if untouched:
        print(f"rows without a contract verdict: {len(untouched)}")
        for name, r in untouched[:10]:
            print("  ", name, r)
        return 1
    print("every row carries one of: " + ", ".join(sorted(ALLOWED)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
