#!/usr/bin/env python
"""Turn the JUnit results into the audit's failing-test table.

Nothing here is typed by hand: the file, the test name and the message come
from the XML the suite wrote. Only the *category*, the *decision id* and the
*proposed action* come from the rule table below, which is keyed by test file
and (where one file mixes causes) by test-name prefix.

Categories (the vocabulary the audit report uses)
-------------------------------------------------
``PROJECT-DEFECT``          the product is wrong; fix it plus a regression test
``TEST-ENCODES-OLD-SCHEMA`` the test asserts a migration, constraint name or
                            column the frozen chain (m0001-m0003) does not have
``DOCS-NOT-COMMITTED``      generated documentation the checkout does not carry
``NEEDS-DECISION``          a decision the operator owns; the test is not edited
``ENV``                     caused by the sandbox/environment, not the product
``COLLATERAL``              caused by another failure or by test order

Usage::

    python tools/audit/triage_failures.py \\
        audit_report/evidence_final/junit_unit.xml \\
        audit_report/evidence_final/junit_rest.xml
"""

from __future__ import annotations

import argparse
import collections
import csv
import pathlib
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "audit_report" / "findings"

#: (test file, test-name prefix or None) -> (category, decision, cause, action)
RULES = [
    # ---------------- decisions the operator owns -------------------------
    ("tests/integration/test_scenario_engine_pg.py", "test_sql_priority", "PROJECT-DEFECT",
     "D-1", "the SQL priority and the Python rule disagree (NotificationPriority enum vs "
     "the stored string)", "OPEN: the inconsistency is real; fix the rule or the query, "
     "the test is not edited"),
    ("tests/integration/test_scenario_engine_pg.py", None, "PROJECT-DEFECT",
     "D-1", "the helper now reads alerts.scenario_id / scenario_evaluation_id (round 5); "
     "it passed 22/22 at 987edc4, so a failure here is a new defect",
     "fix the product, not the test"),
    ("tests/integration/test_places_crud_pg.py", None, "NEEDS-DECISION",
     "D-4", "place_key naming and a 500 in the admin curation path", "do-not-edit test"),
    ("tests/integration/test_signal_explorer_pg.py", None, "NEEDS-DECISION",
     "D-4", "the explorer returns no rows for the seeded fixture", "do-not-edit test"),
    ("tests/unit/test_identifiers_intel.py", None, "NEEDS-DECISION",
     "D-4", "email identifiers are reported under a different key", "do-not-edit test"),
    ("tests/integration/test_title_parent_linkage.py", None, "NEEDS-DECISION",
     "D-4", "the test inserts 5 positional args into a repository that takes 4",
     "do-not-edit test"),
    ("tests/unit/test_notification_volume_guards.py", None, "NEEDS-DECISION",
     "D-10", "demands a m0014 alerts-index migration the chain does not have",
     "reverted to 2670f3b as instructed; failure stays visible"),
    ("tests/unit/test_component_library.py", "test_the_audit_counts", "NEEDS-DECISION",
     "—", "hand-written table count is 10 at 2670f3b (expected 9) and 11 now that "
     "reports.html carries a raw table", "migrate the two tables onto record_table, "
     "or change the documented count"),
    # ---------------- tests that encode a schema this checkout lacks ------
    ("tests/integration/test_signal_ingestion.py", None, "PROJECT-DEFECT",
     "D-2", "these now assert the constraint *rule* via pg_get_constraintdef and pass "
     "on m0001-m0003 (8/8); a failure is a regression in the rule or the assertion",
     "fix the product or the assertion, never the schema"),
    ("tests/integration/test_saved_searches_pg.py", None, "PROJECT-DEFECT",
     "D-2", "these now assert the constraint rule, not the baseline's names (4/4 pass)",
     "fix the product or the assertion, never the schema"),
    ("tests/integration/test_notification_accuracy.py", None, "NEEDS-DECISION",
     "D-10", "test_alert_indexes_exist expects idx_alerts_* names from a migration this "
     "chain does not have; the test is on the do-not-edit list",
     "needs your word: either the index set is a requirement (schema change) or the "
     "expectation is dropped"),
    ("tests/integration/test_db_integrity_audit.py", "test_no_duplicate", "PROJECT-DEFECT",
     "D-8", "the five duplicate index shapes are registered and this passes; a failure "
     "means an unregistered duplicate appeared", "register it or drop it by decision"),
    ("tests/integration/test_db_integrity_audit.py", None, "PROJECT-DEFECT",
     "D-3", "rewritten against m0001-m0003 and passing; a failure is a regression",
     "fix the product or the assertion"),
    ("tests/integration/test_structured_content_display.py", None, "PROJECT-DEFECT",
     "D-3", "rewritten against the frozen chain (m0001-m0003) and passing",
     "fix the product or the assertion"),
    ("tests/integration/test_places_pg.py", None, "COLLATERAL",
     "D-4", "passes 14/14 in isolation on both 04bb817 and HEAD, but fails inside the "
     "full run (seed rows and table presence depend on another module's state)",
     "fix by test isolation (per-module database or fixture reset); never by the schema"),
    # ---------------- generated documentation the checkout lacks ---------
    ("tests/unit/test_interface_docs.py", None, "DOCS-NOT-COMMITTED", "D-7",
     "docs/INTERFACE_REGISTRY.md, REGISTRY_EVIDENCE.md, endpoint_inventory.json are "
     "generated and uncommitted", "commit the generated set after your review"),
    ("tests/unit/test_component_library.py", None, "DOCS-NOT-COMMITTED", "D-7",
     "docs/COMPONENT_LIBRARY.md is generated and uncommitted", "commit after review"),
    ("tests/unit/test_action_lifecycle_components.py", None, "DOCS-NOT-COMMITTED", "D-7",
     "docs/COMPONENT_LIBRARY.md", "commit after review"),
    ("tests/unit/test_action_surface_audit.py", None, "DOCS-NOT-COMMITTED", "D-7",
     "docs/ACTION_SURFACE_AUDIT.md", "commit after review"),
    ("tests/unit/test_architecture_diagrams.py", None, "DOCS-NOT-COMMITTED", "D-7",
     "docs/ARCHITECTURE.md", "commit after review"),
    ("tests/unit/test_experience_contract.py", None, "DOCS-NOT-COMMITTED", "D-7",
     "docs/EXPERIENCE_CONTRACT.md", "commit after review"),
    ("tests/unit/test_interface_lifecycle.py", None, "DOCS-NOT-COMMITTED", "D-7",
     "docs/INTERFACE_REGISTRY.md", "commit after review"),
    ("tests/unit/test_screen_inspector.py", None, "DOCS-NOT-COMMITTED", "D-7",
     "docs/SCREEN_INSPECTOR_EVIDENCE.md", "commit after review"),
    ("tests/unit/test_ocr_provenance_docs.py", None, "DOCS-NOT-COMMITTED", "D-7",
     "docs/DOMAIN_MODEL.md", "commit after review"),
    ("tests/unit/test_windows_offline_kit.py", None, "DOCS-NOT-COMMITTED", "D-7",
     "docs/windows/DEPENDENCY_INVENTORY.md", "commit after review"),
    ("tests/unit/test_reference_docs.py", None, "DOCS-NOT-COMMITTED", "D-7",
     "the generated reference pages under docs/reference are stale", 
     "run tools/docs/generate_reference.py and commit"),
    ("tests/integration/test_interface_coverage.py", None, "DOCS-NOT-COMMITTED", "D-7",
     "docs/REGISTRY_EVIDENCE.md and docs/endpoint_inventory.json",
     "commit after review"),
    # ---------------- licensing / missing module / translation -----------
    ("tests/unit/test_licensing.py", None, "NEEDS-DECISION", "D-6",
     "pyproject declares license = AGPL-3.0-or-later and the README states it (round 5), "
     "but THIRD_PARTY_NOTICES.md - which pyproject lists in license-files - is still "
     "absent, and pymupdf-layout / pymupdf4llm declare no usable licence metadata",
     "write THIRD_PARTY_NOTICES.md and decide the two PDF packages; both need your "
     "review (licensing text) and are listed OPEN in audit_report/R5_verification_report.md"),
    ("tests/unit/test_analytics_measures.py", None, "NEEDS-DECISION", "D-5",
     "core.reporting.datasets must expose TERM_KEYNESS_TOTALS_V1.sql carrying "
     ">= 15.13; no keyness dataset SQL exists anywhere in this repository and never "
     "has (git log --all -- core/reporting is empty), so a facade would have to invent "
     "the statement", "D-5 BLOCKED: supplying the keyness dataset SQL is new product "
     "code, not a facade - needs your decision"),
    ("tests/unit/test_bandit_triage_fixes.py", None, "NEEDS-DECISION", "D-5",
     "imports core.reporting.model._ID / _HELP_TOPIC; the same module would also need "
     "the registry constants and dataset ids",
     "D-5 BLOCKED with test_analytics_measures (one module, one decision)"),
    ("tests/unit/test_gazetteer_packaging.py", None, "PROJECT-DEFECT", "D-4",
     "data/gazetteer/SOURCES.md ships since 86c8d27 and the test passes; a failure "
     "means the shipped provenance lost a field", "fix the product"),
    ("tests/integration/test_translation_coverage.py", None, "NEEDS-DECISION", "—",
     "284-359 untranslated/fuzzy entries per locale",
     "needs a translator; filling them is a decision, not a code change"),
    # ---------------- environment, disproved by re-running ---------------
    ("tests/unit/test_archive_formats.py", None, "ENV", "—",
     "pyzipper is a declared test-only dependency (pyproject [project.optional-dependencies] "
     "dev) but is not installed in this rebuilt venv, so the AES-ZIP fixture cannot be made",
     "install pyzipper in the audit venv and re-run; not a product defect"),
    ("tests/security/test_rate_limiting.py", None, "ENV", "—",
     "the suite inherited RATE_LIMIT_* from the browser-walk launch; the limiter reads "
     "them at import", "re-run at product defaults: 16 passed (see R3 report §c)"),
    ("tests/integration/test_startup_upgrade_path.py", None, "ENV", "—",
     "CREATE DATABASE ... TEMPLATE was refused because another module's pool still held "
     "the source database (ObjectInUse)", "fixed by building the private database from "
     "the migrations instead of cloning it (test isolation, no schema change)"),
]


def classify(path, name):
    best = None
    for rule_path, prefix, category, decision, cause, action in RULES:
        if rule_path != path or (prefix and not name.startswith(prefix)):
            continue
        score = len(prefix or "")
        if best is None or score > best[0]:
            best = (score, category, decision, cause, action)
    if best is None:
        return ("UNCLASSIFIED", "—", "no rule matched this file",
                "classify before claiming the suite is triaged")
    return best[1:]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("junit", nargs="+",
                    help="the JUnit XMLs of one clean run")
    ap.add_argument("--out", default="audit_report/findings/failure_triage.csv")
    args = ap.parse_args(argv)

    rows = []
    totals = collections.Counter()
    for source in args.junit:
        root = ET.parse(ROOT / source).getroot()
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        totals["tests"] += int(suite.get("tests", 0))
        totals["failures"] += int(suite.get("failures", 0))
        totals["errors"] += int(suite.get("errors", 0))
        totals["skipped"] += int(suite.get("skipped", 0))
        totals["suites"] += 1
        for tc in suite.iter("testcase"):
            bad = [c for c in tc if c.tag in ("failure", "error")]
            if not bad:
                continue
            cls = tc.get("classname", "")
            parts = [p for p in cls.split(".") if not p[:1].isupper()]
            path = "/".join(parts) + ".py"
            message = (bad[0].get("message") or bad[0].text or "").strip()
            first = message.split("\n")[0][:200]
            category, decision, cause, action = classify(path, tc.get("name", ""))
            rows.append((path, tc.get("name", ""), bad[0].tag, first, category,
                         decision, cause, action))

    rows.sort(key=lambda r: (r[4], r[0], r[1]))
    OUT.mkdir(parents=True, exist_ok=True)
    with (ROOT / args.out).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "test", "outcome", "message", "category", "decision",
                    "cause", "proposed_action"])
        w.writerows(rows)

    counts = collections.Counter(r[4] for r in rows)
    print(f"XMLs: {args.junit}")
    print(f"tests: {totals['tests']}  failures: {totals['failures']}  "
          f"errors: {totals['errors']}  skipped: {totals['skipped']}")
    print(f"failing/erroring test cases: {len(rows)}")
    for category, count in counts.most_common():
        print(f"  {category:26s} {count}")
    print(f"wrote {args.out}")
    return 1 if counts.get("UNCLASSIFIED") else 0


if __name__ == "__main__":
    sys.exit(main())
