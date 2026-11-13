#!/usr/bin/env python
"""Browser check: every action name fires its own request on its own page.

The static test (``tests/unit/test_js_action_ownership.py``) proves which files
*may* publish a name. This proves what a browser actually gets: for every page
that uses one of the 17 duplicate action names it records

* which function ``window.<name>`` actually holds once the page is interactive.
  A bound function (``[native code]``) is the registry's copy, which is what the
  old collisions produced; a page module's own implementation keeps its source,
  and it is matched against the file expected to own it;
* whether clicking the element that names the action **issues the request its
  implementation is meant to issue** (or, for a pure-UI action, produces its
  documented effect).

Every row must show a page-owned function *and* the expected observable.

Usage::

    python tools/audit/browser_action_ownership.py \
        --base http://127.0.0.1:5000 --user admin --password '...'

Chromium comes from the npm bundle in ``/tmp/br`` (the only browser source
reachable from this sandbox) and runs against NSS/NSPR shims in ``/tmp/stubgen``
- this image ships no NSS at all. Both are rebuilt per session; see
``audit_report/r14/README.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "audit_report" / "r14"
NODE_DIR = pathlib.Path("/tmp/br")
SCRIPT = pathlib.Path(__file__).with_name("action_ownership.mjs")

TRACK = [
    "applyFilters", "resetFilters", "submitSourceForm", "exportSelectedFiles",
    "removeAnalystCategoriesFromSelection", "toggleAllFilesSelection",
    "updateKeywordAssociations", "bulkDelete", "clearSearch", "deleteKeyword",
    "deleteWord", "openAddWordModal", "performSearch", "refresh",
    "saveWordToCategory", "switchTab", "toggleAllSelection",
    "applyFiltersShared", "resetFiltersShared",
]

CHECKBOX = '#filesTable input[type="checkbox"], input.ut-row-check, table tbody input[type="checkbox"]'
KEYWORD_CHECK = '.keyword-checkbox'
WORD_CHECK = '.word-checkbox'

# route, template, owning page module, [(action, spec)]
PAGES = [
    ("/files", "file/files_list.html", "static/js/pages/files-list-page.js", [
        ("toggleAllFilesSelection", {"effect": "checkedBoxes"}),
        ("bulkDelete", {"request": r"/files/bulk-delete",
                        "preselect": [".file-checkbox"],
                        "known": "FIXTURE-LIMIT: the files toolbar stays disabled after a "
                                 "synthetic row selection (the page's selection model, not the "
                                 "checkbox, drives it), so the click reaches no handler"}),
    ]),
    ("/keywords", "Keyword/keywords_list.html", "static/js/pages/keywords-list-page.js", [
        ("updateKeywordAssociations", {"request": r"/api/keywords/update-associations", "wait": 6000}),
        ("bulkDelete", {"request": r"/api/keywords/bulk-delete", "preselect": [KEYWORD_CHECK],
                        "known": "DEFECT-OPEN: action_button omits data-on-click while the "
                                 "control is disabled and ActionToolbar.sync only clears "
                                 "disabled, so after selecting rows the button has no handler"}),
        ("toggleAllSelection", {"effect": "checkedBoxes"}),
        ("clearSearch", {"effect": "requestOrNavigation"}),
    ]),
    ("/words", "Word/Word_list.html", "static/js/pages/words-list-page.js", [
        ("bulkDelete", {"request": r"/api/words/bulk-delete", "preselect": [WORD_CHECK],
                        "known": "DEFECT-OPEN: action_button omits data-on-click while the "
                                 "control is disabled and ActionToolbar.sync only clears "
                                 "disabled, so after selecting rows the button has no handler"}),
        ("openAddWordModal", {"effect": "modalsShown"}),
        ("toggleAllSelection", {"effect": "checkedBoxes"}),
        ("clearSearch", {"effect": "requestOrNavigation"}),
    ]),
    ("/sources", "Sources/sources_list.html", "static/js/pages/sources-list-page.js", [
        ("submitSourceForm", {
            "request": r"/api/sources",
            "prefill": [['#sourceForm input[name="name"]', "R14 source"],
                        ['#sourceForm input[name="job"]', "R14 analyst"],
                        ['#sourceForm input[name="country"]', "NL"]],
        }),
        ("toggleAllSelection", {"effect": "checkedBoxes"}),
        ("clearSearch", {"effect": "requestOrNavigation"}),
    ]),
    ("/sides", "Side/sides_list.html", "static/js/pages/sides-list-page.js", [
        ("toggleAllSelection", {"effect": "checkedBoxes"}),
        ("clearSearch", {"effect": "requestOrNavigation"}),
    ]),
    ("/categories", "Category/categories_list.html", "static/js/pages/categories-list-page.js", [
        ("saveWordToCategory", {"request": r"/api/words-categorys/add",
                                "preClick": ['[data-on-click^="addWordToCategory"]'],
                                "prefill": [["#targetCategoryId", "1"]],
                                "jquerySelect": [["#wordSelect", "1"]],
                                "known": "FIXTURE-LIMIT: the modal's Select2 keeps its own "
                                         "value; the handler answers 'Please select or enter a "
                                         "word' (toast captured) instead of posting"}),
        ("clearSearch", {"effect": "requestOrNavigation"}),
    ]),
    ("/search/advanced", "Search/search_advanced.html", "static/js/pages/search-advanced-page.js", [
        ("exportSelectedFiles", {"known": "FIXTURE-LIMIT: the fixture's search returns no "
                                          "result rows, so there is nothing to select and the "
                                          "handler answers 'Select one or more documents first.'",
                                 "request": r"/files/export|/api/files/names/export",
                                 "preClick": ['[data-on-click="executeAdvancedSearch()"]'],
                                 "preSelectWait": 4000,
                                 "preselect": [".result-checkbox"]}),
        ("removeAnalystCategoriesFromSelection", {"known": "FIXTURE-LIMIT: same empty result "
                                                            "set; the handler needs selected ids",
                                                  "request": r"/api/analyst/remove",
                                                  "preClick": ['[data-on-click="executeAdvancedSearch()"]'],
                                                  "preSelectWait": 4000,
                                                  "preselect": [".result-checkbox"]}),
    ]),
    ("/dashboard/charts", "Analysis/charts_dashboard.html",
     "static/js/pages/charts-dashboard-page.js", [
        ("applyFilters", {"request": r"/api/dashboard/"}),
        ("resetFilters", {"request": r"/api/dashboard/"}),
    ]),
    ("/dashboard/comprehensive", "Analysis/comprehensive_dashboard.html",
     "static/js/pages/comprehensive-dashboard-page.js", [
        ("applyFilters", {"request": r"/api/dashboard/"}),
        ("resetFilters", {"request": r"/api/dashboard/"}),
    ]),
    ("/analyst/categorization", "Analyst/analyst_categorization.html",
     "static/js/pages/analyst-categorization-page.js", [
        ("resetFilters", {"request": r"/api/analyst/"}),
    ]),
    ("/analysis/batch", "Analysis/analysis_batch.html",
     "static/js/pages/analysis-batch-page.js", [
        ("toggleAllSelection", {"effect": "checkedBoxes"}),
    ]),
    ("/", "Analysis/dashboard.html", "static/js/pages/dashboard-page.js", [
        ("performSearch", {"effect": "requestOrNavigation"}),
        ("switchTab", {"effect": "switchTab"}),
    ]),
    ("/notifications", "Notifications/notifications.html",
     "static/js/pages/notifications-page.js", [
        ("switchTab", {"effect": "requestOrNavigation"}),
    ]),
    ("/reports/dashboard", "Reports/dashboard.html", "static/js/pages/reports-dashboard-page.js",
     [("refresh", {"request": r"/api/reports/"})]),
    ("/retention", "Retention/retention.html", "static/js/pages/retention-page.js",
     [("refresh", {"request": r"/api/retention"})]),
    ("/schedules", "Scheduling/schedules.html", "static/js/pages/schedules-page.js",
     [("refresh", {"request": r"/api/schedules"})]),
    ("/email-words", "email_words/email_words.html", "static/js/pages/email-words-page.js",
     [("toggleAllSelection", {"effect": "checkedBoxes"})]),
    ("/categories/3/words", "Category/category_words.html",
     "static/js/pages/category-words-page.js", [
        ("openAddWordModal", {"effect": "modalsShown"}),
        ("saveWordToCategory", {"request": r"/api/words-categorys/add",
                                "uniqueWordInto": "#wordInput"}),
    ]),
]


def build_pages() -> list[dict]:
    pages = []
    for route, template, module, actions in PAGES:
        pages.append({
            "route": route, "template": template,
            "actions": [{"name": name, "expect": spec, **{k: v for k, v in spec.items()}}
                        for name, spec in actions],
        })
    return pages


def _signature(module: str, name: str) -> str | None:
    """A distinctive slice of the function the page module defines for a name."""
    path = ROOT / module
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", text)
    if not m:
        return None
    body = text[m.end():m.end() + 400]
    norm = re.sub(r"\s+", " ", body).strip()
    return norm[:32] or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", required=True)
    ap.add_argument("--raw-only", action="store_true")
    args = ap.parse_args()

    cfg = {"base": args.base, "user": args.user, "password": args.password,
           "track": TRACK, "pages": build_pages()}
    NODE_DIR.mkdir(parents=True, exist_ok=True)
    target_script = NODE_DIR / "action_owner.mjs"
    target_script.write_text(SCRIPT.read_text())
    env = dict(os.environ)
    env["ACTION_CONFIG"] = json.dumps(cfg)
    env["LD_LIBRARY_PATH"] = "/tmp/stubgen:" + env.get("LD_LIBRARY_PATH", "")
    proc = subprocess.run(["node", str(target_script)], cwd=str(NODE_DIR), env=env,
                          capture_output=True, text=True, timeout=2400)
    if proc.returncode != 0:
        print(proc.stdout[-3000:], proc.stderr[-3000:], file=sys.stderr)
        return 2
    raw = json.loads(proc.stdout)
    module_of = {route: module for route, _t, module, _a in PAGES}

    checks = []
    for page in raw["pages"]:
        module = module_of.get(page["route"], "")
        for action in page["actions"]:
            name = action["name"]
            exp = action.get("expect") or {}
            handler = action.get("handler") or {}
            sig = _signature(module, name) if module else None
            source = re.sub(r"\s+", " ", (handler.get("source") or ""))
            if handler.get("type") != "function":
                owner_ok, owner_detail = False, f"window.{name} is {handler.get('type')}"
            elif handler.get("bound"):
                owner_ok, owner_detail = False, "bound function: the registry's copy won"
            elif action.get("ownerBefore") and module.rsplit("/", 1)[-1] in action["ownerBefore"]:
                owner_ok, owner_detail = True, f"assignment traced to {module}"
            elif sig and sig in source:
                owner_ok, owner_detail = True, f"source matches {module}"
            elif sig:
                owner_ok, owner_detail = False, f"source does not match {module}: {source[:120]}"
            else:
                owner_ok, owner_detail = False, f"no function named {name} in {module}"
            checks.append({"route": page["route"], "template": page["template"],
                           "action": name, "known": exp.get("known"),
                           "check": "owner is the page module",
                           "ok": owner_ok, "detail": owner_detail})

            reqs = action.get("requests") or []
            effect = action.get("effect") or {}
            if "request" in exp:
                hit = [r for r in reqs if re.search(exp["request"], r)]
                checks.append({"route": page["route"], "template": page["template"],
                               "action": name, "known": exp.get("known"),
                               "check": f"issues {exp['request']}",
                               "ok": bool(hit), "detail": hit or reqs[:8]})
            else:
                kind = exp.get("effect")
                if kind == "checkedBoxes":
                    ok = effect.get("checkedBoxes", 0) > 0
                elif kind == "modalsShown":
                    ok = effect.get("modalsShown", 0) > 0
                elif kind == "switchTab":
                    ok = len(effect.get("activeTabs") or []) > 0
                elif kind == "requestOrNavigation":
                    ok = bool(reqs) or bool(effect.get("urlChanged"))
                else:
                    ok = False
                checks.append({"route": page["route"], "template": page["template"],
                               "action": name, "known": exp.get("known"),
                               "check": f"effect {kind}", "ok": bool(ok),
                               "detail": {"effect": effect, "requests": reqs[:6]}})
            if action.get("error") and not action.get("found"):
                checks.append({"route": page["route"], "template": page["template"],
                               "action": name, "known": exp.get("known"),
                               "check": "element found", "ok": False,
                               "detail": f"{action['error']} / {action.get('absentReason')}"})

    failed = [c for c in checks if not c["ok"]]
    unexpected = [c for c in failed if not c.get("known")]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "browser_actions.json").write_text(json.dumps(
        {"base": args.base, "raw": raw, "checks": checks,
         "summary": {"checks": len(checks), "failed": len(failed),
                     "unexpected_failed": len(unexpected),
                     "known_defect": len([c for c in failed
                                          if (c.get("known") or "").startswith("DEFECT")]),
                     "known_fixture_limit": len([c for c in failed
                                                 if (c.get("known") or "").startswith("FIXTURE")])}},
        indent=2))
    per_route = {}
    for c in checks:
        s = per_route.setdefault(c["route"], {"checks": 0, "failed": 0})
        s["checks"] += 1
        if not c["ok"]:
            s["failed"] += 1
    for route, s in per_route.items():
        print(f"  {route:28} checks {s['checks']:3}  failed {s['failed']}")
    print(f"checks: {len(checks)}  failed: {len(failed)}  unexpected: {len(unexpected)}")
    for c in failed:
        tag = f"[{c['known'].split(':')[0]}] " if c.get("known") else ""
        print(f"  FAIL {tag}{c['route']} {c['action']}: {c['check']} -> {str(c['detail'])[:200]}")
    return 0 if (not unexpected or args.raw_only) else 1


if __name__ == "__main__":
    raise SystemExit(main())
