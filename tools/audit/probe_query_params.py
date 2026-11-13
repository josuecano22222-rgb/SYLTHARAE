#!/usr/bin/env python
"""Send hostile query strings to every readable route.

The body probes cover what is POSTed; this covers what is in the URL, which
reaches Flask as text and is converted by the view. The interesting cases are
the ones a view converts without looking: a page number, a limit, a sort key,
a filter id, an export format.

A 4xx is handled. A 5xx, a stack trace or a database error in the response is a
finding - the caller sent something the URL bar allows and the view did not
expect. Requests run as an administrator so an authorization refusal cannot
mask an input-handling defect.

Usage::

    python tools/audit/probe_query_params.py

Writes ``audit_report/findings/query_params.csv``; exits 1 when anything 5xx'd
or leaked.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.audit.probe_param_routes import (  # noqa: E402
    LEAK_MARKERS,
    bootstrap_audit_db,
    make_app,
    sign_in,
)

OUT = "audit_report/findings/query_params.csv"
COLUMNS = ("rule", "endpoint", "payload", "query_string", "status", "body_shape",
           "leaked", "snippet", "verdict")

#: One entry per hostile query string. The names are the ones the list
#: endpoints actually read (page/per_page/limit/offset/sort/order/q/format) plus
#: a filter-ish id, so a route that ignores a name is unaffected.
PAYLOADS = {
    "page-abc": {"page": "abc"},
    "page-negative": {"page": "-1"},
    "page-zero": {"page": "0"},
    "page-huge": {"page": "99999999999999999999"},
    "per-page-huge": {"per_page": "1000000"},
    "per-page-negative": {"per_page": "-5"},
    "per-page-zero": {"per_page": "0"},
    "per-page-abc": {"per_page": "abc"},
    "limit-abc": {"limit": "abc"},
    "offset-negative": {"offset": "-1"},
    "sort-sql": {"sort": "id; DROP TABLE users--"},
    "sort-traversal": {"sort": "../../etc/passwd"},
    "order-weird": {"order": "\x00"},
    "q-empty": {"q": ""},
    "q-huge": {"q": "A" * 10000},
    "q-nul": {"q": "a\x00b"},
    "id-array": {"id": "1,2,3"},
    "id-huge": {"id": "99999999999999999999"},
    "format-weird": {"format": "../../etc/passwd"},
    "limit-offset-both": {"limit": "1e9", "offset": "-1"},
}


def shape_of(resp):
    # An endless response (Server-Sent Events) has no body to read: get_data()
    # would block until the stream gives up, and the probe would look hung
    # rather than finished. /api/jobs/stream is such an endpoint, by design.
    if (resp.mimetype or "").startswith("text/event-stream"):
        return "stream:text/event-stream", [], "not read (endless stream)"
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
        shape = ("json:" + ",".join(sorted(data)[:6])) if isinstance(data, dict) \
            else f"json:{type(data).__name__}"
    leaked = [marker for marker in LEAK_MARKERS if marker in text]
    return shape, leaked, text[:160].replace("\n", " ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default=None,
                        help="restrict the sweep to rules whose path contains this")
    parser.add_argument("--out", default=OUT, help=f"CSV path (default {OUT})")
    options = parser.parse_args()
    out = options.out

    cfg, report = bootstrap_audit_db("file_analysis_query")
    print("audit database:", cfg["database"], report["applied_migrations"])
    app = make_app()
    client = sign_in(app, "query_admin", "admin")

    rows = []
    rules = sorted(app.url_map.iter_rules(), key=lambda r: r.rule)
    print(f"sweeping {len(rules)} rules x {len(PAYLOADS)} payloads", flush=True)
    for rule in rules:
        if "GET" not in rule.methods or rule.endpoint in ("static",):
            continue
        if options.only and options.only not in rule.rule:
            continue
        url = rule.rule
        for name in rule.arguments:
            url = url.replace(f"<{name}>", "1")
            for converter in ("int", "float", "path", "string"):
                url = url.replace(f"<{converter}:{name}>", "1")
        if url.count("<"):
            continue                       # an argument we could not fill
        print(f"  {rule.rule}", flush=True)
        for label, params in PAYLOADS.items():
            try:
                resp = client.get(url, query_string=params,
                                  follow_redirects=False)
                status = resp.status_code
            except Exception as exc:       # noqa: BLE001 - recorded
                print(f"EXCEPTION {url} {label}: {exc!r}")
                rows.append((rule.rule, rule.endpoint, label,
                             "&".join(f"{k}={v}" for k, v in params.items()),
                             0, "?", "", "", "EXCEPTION"))
                continue
            shape, leaked, snippet = shape_of(resp)
            if leaked:
                verdict = "STACK-TRACE"
            elif status == 501:
                verdict = "NOT-IMPLEMENTED"     # a stub, not a defect
            elif status >= 500:
                verdict = "SERVER-ERROR"
            elif 200 <= status < 400 or 400 <= status < 500:
                verdict = "OK"
            else:
                verdict = f"UNEXPECTED-{status}"
            rows.append((rule.rule, rule.endpoint, label,
                         "&".join(f"{k}={v[:20]}" for k, v in params.items()),
                         status, shape, "|".join(leaked), snippet, verdict))
            if verdict != "OK":
                print(f"{verdict:14s} {url:60s} {label:16s} {status} "
                      f"{shape[:28]} {snippet[:70]}")

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)

    bad = [r for r in rows if r[-1] in ("SERVER-ERROR", "STACK-TRACE", "EXCEPTION")]
    print(f"\nrows: {len(rows)}  findings: {len(bad)}  written: {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
