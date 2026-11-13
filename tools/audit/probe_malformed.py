#!/usr/bin/env python
"""Throw malformed requests at every mutating route and record what comes back.

The parameterised-route probe (``probe_param_routes.py``) answers "what does a
*valid shape* with a bad id do". This one answers the other half: what happens
when the body is not what the view expects at all - no body, no content type,
broken JSON, a list where a mapping was assumed, a 1 MB string, a null byte, a
hundred-level nest, a number where a name belongs.

A 4xx is a handled request and is not a finding. A 5xx, a stack trace or a
database error message in the response is a finding: it means the view trusted
the shape of its input. The sweep runs as an administrator, so an *authorization*
refusal cannot mask an input-handling defect.

Usage::

    python tools/audit/probe_malformed.py

Writes ``audit_report/findings/malformed_bodies.csv``; exits 1 when anything
answered 5xx or leaked.
"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.audit.probe_param_routes import (
    LEAK_MARKERS,
    bootstrap_audit_db,
    make_app,
    sign_in,
)

OUT = "audit_report/findings/malformed_bodies.csv"
COLUMNS = ("rule", "method", "endpoint", "payload", "status", "body_shape",
           "leaked", "snippet", "verdict")

#: Bodies that are wrong in a different way each time. ``None`` means "send no
#: body at all"; the ``raw`` entries are sent as-is with the given content type.
BODIES = [
    ("no-body", None, None),
    ("empty-object", b"{}", "application/json"),
    ("json-list", b"[]", "application/json"),
    ("json-scalar", b"7", "application/json"),
    ("json-string", b'"text"', "application/json"),
    ("json-null", b"null", "application/json"),
    ("broken-json", b'{"a": ', "application/json"),
    ("not-json", b"a=1&b=2", "application/x-www-form-urlencoded"),
    ("plain-text", b"hello", "text/plain"),
    ("wrong-types", None, "generated"),
    ("unknown-keys", None, "generated"),
    ("huge-string", None, "generated"),
    ("null-byte", None, "generated"),
    ("deep-nest", None, "generated"),
    ("array-of-arrays", None, "generated"),
]

#: Field names the views read; used to build the "wrong type" bodies.
COMMON_FIELDS = ("name", "id", "title", "description", "query", "username",
                 "password", "role", "file_id", "source_id", "category_id",
                 "path", "file_path", "content", "value", "settings")


def shape_of(resp):
    """Body shape for a response that may not be text at all.

    A traversal payload can reach a binary handler (a tile, an original file),
    and decoding that as UTF-8 raises before the verdict is recorded.
    """
    raw = resp.get_data()
    if not raw:
        return "empty", [], ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"binary:{len(raw)}B", [], ""
    try:
        data = json.loads(text)
    except Exception:
        shape = "html" if text.lstrip().startswith("<") else "text"
    else:
        if isinstance(data, dict):
            shape = "json:" + ",".join(sorted(data)[:6])
        else:
            shape = f"json:{type(data).__name__}"
    leaked = [marker for marker in LEAK_MARKERS if marker in text]
    return shape, leaked, text[:160].replace("\n", " ")


def _nested(depth: int):
    node: dict = {"x": 1}
    for _ in range(depth):
        node = {"x": node}
    return node


def generated(kind: str, rule: str):
    """A body for the ``generated`` variants, aimed at the route it goes to."""
    fields = list(COMMON_FIELDS)
    words = [w for w in rule.replace("/", " ").replace("<", " ").replace(">", " ")
             .replace(":", " ").replace("_", " ").split() if w.isalpha()]
    fields = [w for w in words if w not in ("api", "int", "string", "id")][:4] + fields
    if kind == "wrong-types":
        return {f: ([], {}, True) for f in fields[:8]}
    if kind == "unknown-keys":
        return {f"unknown_{i}": i for i in range(20)}
    if kind == "huge-string":
        return {f: "A" * 1_000_000 for f in fields[:2]}
    if kind == "null-byte":
        return {f: "before\x00after" for f in fields[:4]}
    if kind == "deep-nest":
        return {"name": _nested(100), "title": _nested(60)}
    if kind == "array-of-arrays":
        return {f: [[[index]]] for index, f in enumerate(fields[:4])}
    raise SystemExit(f"unknown generated body {kind}")


def main() -> int:
    cfg, report = bootstrap_audit_db("file_analysis_malformed")
    print("audit database:", cfg["database"], report["applied_migrations"])
    app = make_app()
    client = sign_in(app, "malformed_admin", "admin")

    rows = []
    rules = sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, sorted(r.methods)))
    for rule in rules:
        methods = sorted(m for m in rule.methods
                         if m in ("POST", "PUT", "PATCH", "DELETE"))
        if not methods or rule.endpoint in ("auth.logout",):
            continue
        url = rule.rule
        converters = {name: type(conv).__name__
                      for name, conv in (rule._converters or {}).items()}  # noqa: SLF001
        for name in rule.arguments:
            kind = converters.get(name, "UnicodeConverter")
            if kind == "IntegerConverter":
                url = re.sub(r"<(?:int:)?%s>" % re.escape(name), "1", url)
            elif kind == "FloatConverter":
                url = re.sub(r"<(?:float:)?%s>" % re.escape(name), "1.0", url)
            elif kind == "PathConverter":
                url = re.sub(r"<path:%s>" % re.escape(name), "../" * 12 + "etc/passwd", url)
            else:
                url = re.sub(r"<(?:[^<>:]+:)?%s>" % re.escape(name), "probe", url)
        for method in methods:
            for label, raw, content_type in BODIES:
                if label == "no-body":
                    body = None
                elif raw is not None:
                    body = raw
                else:
                    body = json.dumps(generated(label, rule.rule)).encode()
                headers = {"Content-Type": content_type} if content_type and content_type != "generated" \
                    else {"Content-Type": "application/json"}
                try:
                    resp = client.open(url, method=method, data=body,
                                       headers=headers, follow_redirects=False)
                    status = resp.status_code
                except Exception as exc:                       # noqa: BLE001 - recorded
                    status, resp = 0, None
                    print(f"EXCEPTION {method} {url} {label}: {exc!r}")
                shape, leaked, snippet = ("?", [], "") if resp is None else shape_of(resp)
                if resp is not None and not leaked:
                    leaked = [m for m in LEAK_MARKERS if m in snippet]
                if status >= 500:
                    verdict = "SERVER-ERROR"
                elif leaked:
                    verdict = "STACK-TRACE"
                elif status == 0:
                    verdict = "EXCEPTION"
                elif 400 <= status < 500 or 200 <= status < 300:
                    # 202 included: a queued job is an accepted request, not an
                    # unexpected answer.
                    verdict = "OK"
                elif 300 <= status < 400:
                    verdict = "REDIRECT"
                else:
                    verdict = f"UNEXPECTED-{status}"
                rows.append((rule.rule, method, rule.endpoint, label, status, shape,
                             "|".join(leaked), snippet, verdict))
                if verdict != "OK" and verdict != "REDIRECT":
                    print(f"{verdict:14s} {method:6s} {url:60s} {label:14s} {status} "
                          f"{shape[:30]} {snippet[:70]}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)

    bad = [r for r in rows if r[-1] in ("SERVER-ERROR", "STACK-TRACE", "EXCEPTION")]
    print(f"\nrows: {len(rows)}  findings: {len(bad)}  written: {OUT}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
