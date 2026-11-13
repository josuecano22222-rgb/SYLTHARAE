#!/usr/bin/env python
"""Exercise every parameterless mutating route and record the real answer.

``probe_routes.py`` answers "who may read this?"; every route it cannot reach
with at least one role is still unproven. This probe closes the gap the audit
found: 182 of 485 registered routes are POST/PUT/PATCH/DELETE or otherwise
never requested, so nothing was actually executed for them.

Each parameterless route that accepts a mutating method is called, as an
administrator, with an **empty JSON body** and then with a body that has no
recognised fields. What is recorded is the status the application really
returned, and the verdict is narrow on purpose:

* ``OK`` - a 2xx or a deliberate 4xx (validation refused the empty body);
* ``SERVER-ERROR`` - any 5xx: the handler collapsed on empty input;
* ``LOCKED`` - 403 for the administrator (route exists but the admin cannot
  reach it - worth recording, not automatically a defect).

Nothing is asserted about side effects: the point is that the route runs and
answers, on a database of this run's own (``file_analysis_audit``).

Writes ``audit_report/findings/mutations_runtime.csv``; exit 1 on any 5xx.
"""

from __future__ import annotations

import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "audit_report" / "findings"

#: Routes that would change the installation itself or end the session.
SKIP_RULES = {"/auth/logout"}

#: Prefixes whose handlers write files outside the database.
MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")

BODIES = (
    ("empty", {}),
    ("unknown-fields", {"audit_probe": True, "unexpected": ["x"]}),
)


def main():
    from probe_routes import bootstrap_audit_db, make_client  # same directory

    cfg, report = bootstrap_audit_db()
    print("audit database:", cfg["database"], report["applied_migrations"])

    import pathlib as _pl

    from apps.web.app import app
    import core.initialization as init

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    init.INIT_MARKER_FILE = _pl.Path(
        __import__("tempfile").mkdtemp(prefix="audit_marker_")) / ".system_initialized"
    init.mark_system_initialized()
    try:
        from core.security.rate_limit import limiter

        limiter.enabled = False
    except Exception:
        pass

    client = make_client("admin")

    rows = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.arguments or rule.rule in SKIP_RULES:
            continue
        if rule.rule.startswith(("/setup", "/static")):
            continue
        for method in sorted(m for m in rule.methods if m in MUTATING_METHODS):
            statuses = []
            for label, body in BODIES:
                try:
                    resp = client.open(rule.rule, method=method, json=body,
                                       follow_redirects=False)
                    statuses.append((label, resp.status_code))
                except Exception as exc:  # noqa: BLE001 - the point is the record
                    statuses.append((label, f"EXC:{type(exc).__name__}: {exc}"))
            codes = [s for _, s in statuses]
            if any(isinstance(c, str) for c in codes):
                verdict = "SERVER-ERROR"
            elif any(isinstance(c, int) and c >= 500 for c in codes):
                verdict = "SERVER-ERROR"
            elif all(c == 403 for c in codes):
                verdict = "LOCKED"
            else:
                verdict = "OK"
            rows.append((rule.rule, method, rule.endpoint,
                         ";".join(f"{lbl}={st}" for lbl, st in statuses), verdict))
            print(f"{verdict:12s} {method:6s} {rule.rule:52s} "
                  + " ".join(f"{lbl}={st}" for lbl, st in statuses))

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "mutations_runtime.csv").open("w", newline="",
                                              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rule", "method", "endpoint", "responses", "verdict"])
        w.writerows(rows)

    bad = [r for r in rows if r[4] == "SERVER-ERROR"]
    print(f"\nrows: {len(rows)}  server-errors: {len(bad)}")
    for r in bad:
        print("  ", r)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
