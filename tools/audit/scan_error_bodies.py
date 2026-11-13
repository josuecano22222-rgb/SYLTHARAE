#!/usr/bin/env python
"""Inventory of error responses that interpolate exception text.

A response body is read by whoever is holding the session cookie; the server
log is not. This walks every route body in ``Api/`` and lists the statements
that build a *response* from an exception (``jsonify``/``_error``/``return``
with ``str(exc)`` and friends), then separately lists the ones that only reach
a log call. The first list is the review surface for round 20's error-message
workstream; the second is not a finding by itself.

    python tools/audit/scan_error_bodies.py --csv audit_report/r20/error_bodies.csv
"""
from __future__ import annotations

import argparse
import ast
import csv
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

LOG_CALLS = {"warning", "error", "exception", "debug", "info", "critical"}
EXC_NAMES = {"e", "exc", "error", "err", "exception", "ex"}


def _mentions_exception(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in EXC_NAMES:
            return True
        if isinstance(sub, ast.Call) and getattr(sub.func, "id", "") == "str" \
                and sub.args and isinstance(sub.args[0], ast.Name) \
                and sub.args[0].id in EXC_NAMES:
            return True
    return False


def _is_log(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in LOG_CALLS:
            return True
        if isinstance(func, ast.Name) and func.id in LOG_CALLS:
            return True
    return False


def scan() -> list[dict]:
    rows = []
    for path in sorted((ROOT / "Api").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Return, ast.Expr)) or node.value is None:
                continue
            if _is_log(node.value) or not _mentions_exception(node.value):
                continue
            # A body may interpolate the exception anywhere inside it.
            source = ast.get_source_segment(path.read_text(encoding="utf-8",
                                                           errors="ignore"), node) or ""
            source = " ".join(source.split())
            if "logger." in source and "return" not in source:
                continue
            if not ("jsonify" in source or "_error(" in source or "abort(" in source
                    or "make_response" in source or "success" in source):
                continue
            # SEC-08: these helpers log the exception and return a fixed
            # sentence plus an ERR- reference, so they are not leaks.
            sanitised = ("client_error(" in source or "client_safe_message(" in source)
            rows.append({"file": str(path.relative_to(ROOT)), "line": node.lineno,
                         "kind": "sanitised" if sanitised else "raw",
                         "snippet": source[:180]})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=pathlib.Path,
                        default=ROOT / "audit_report" / "r20" / "error_bodies.csv")
    options = parser.parse_args()
    rows = scan()
    options.csv.parent.mkdir(parents=True, exist_ok=True)
    with options.csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file", "line", "kind", "snippet"])
        writer.writeheader()
        writer.writerows(rows)
    raw = [row for row in rows if row["kind"] == "raw"]
    print(f"{len(rows)} response bodies interpolate exception text "
          f"({len(raw)} unsanitised) -> {options.csv}")
    for row in raw[:60]:
        print(f"  {row['file']}:{row['line']} {row['snippet'][:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
