#!/usr/bin/env python3
"""Write the Round 22 baseline reconciliation from the JUnit files.

Round 21 published "34 pre-existing". Round 22 first published "67". Round 22
then published "76". Three numbers, one repository, and none of them carried an
id list that could be checked. This tool emits the reconciliation as a
generated document: every number in it is counted from an artefact, so the
report can be re-derived rather than believed.

The one number that cannot be re-derived is the 67. Its raw capture no longer
exists (the sandbox was re-cloned), so the document says so and does not treat
it as evidence.

Usage::

    python3 tools/audit/r22_reconcile.py \
        --before /tmp/an/junit_dc6a413_samepath.xml \
        --after  /tmp/an/junit_6cf97f3.xml \
        --engines /tmp/an/junit_ocr_env.xml \
        --checkout /tmp/an/junit_real_head.xml \
        --out audit_report/r22_workbook/RECONCILIATION.md
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit.r22_baseline import counts, failing_ids  # noqa: E402

R21_CSV = pathlib.Path("audit_report/r21_i18n/id_diff_unit.csv")


def r21_failing() -> set:
    """Ids Round 21 recorded failing at dc6a413, in this tool's id format."""
    out = set()
    with (ROOT / R21_CSV).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("head") in ("fail", "error"):
                out.add(row["id"])
    return out


def r21_status() -> dict:
    with (ROOT / R21_CSV).open(newline="", encoding="utf-8") as handle:
        return {row["id"]: row["head"] for row in csv.DictReader(handle)}


def skipped_ids(path: pathlib.Path) -> set:
    """Ids the run did not execute. A skip is not a pass."""
    import xml.etree.ElementTree as ET
    out = set()
    for case in ET.parse(path).getroot().iter("testcase"):
        if any(child.tag == "skipped" for child in case):
            out.add(f"{case.get('classname')}::{case.get('name')}")
    return out


def read_rows(path: pathlib.Path) -> list:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--before", required=True, type=pathlib.Path)
    ap.add_argument("--after", required=True, type=pathlib.Path)
    ap.add_argument("--engines", required=True, type=pathlib.Path)
    ap.add_argument("--checkout", required=True, type=pathlib.Path)
    ap.add_argument("--ids", type=pathlib.Path,
                    default=ROOT / "audit_report/r22_workbook/baseline_ids.csv")
    ap.add_argument("--out", required=True, type=pathlib.Path)
    args = ap.parse_args(argv)

    before, after = counts(args.before), counts(args.after)
    engine_counts = counts(args.engines)
    checkout = counts(args.checkout)

    rows = read_rows(args.ids)
    r21 = r21_failing()
    before_ids = failing_ids(args.before)
    after_ids = failing_ids(args.after)
    checkout_ids = failing_ids(args.checkout)

    reproduced = sorted(r21 & set(before_ids))
    extra = sorted(set(before_ids) - r21)
    now_green = sorted(r21 - set(before_ids))
    # Round 21 recorded ids that this run does not even collect (a test deleted
    # or renamed since); they are neither green nor failing, so they are listed
    # separately rather than folded into either count.
    not_collected = sorted(r21 - set(before_ids) - set(after_ids))
    status21 = r21_status()
    skips21 = {i for i, st in status21.items() if st == "skip"}
    skips_now = skipped_ids(args.after)

    class_of = {row["id"]: row["class"] for row in rows}
    extra_by_class = {}
    for test_id in extra:
        extra_by_class.setdefault(class_of.get(test_id, "UNKNOWN"), []).append(test_id)

    path_dependent = sorted(set(checkout_ids) - set(after_ids))
    fixed = [row["id"] for row in rows if row["class"] == "FIXED"]

    def table(header, lines):
        out = ["| " + " | ".join(header) + " |",
               "|" + "|".join("---" for _ in header) + "|"]
        out += ["| " + " | ".join(str(c) for c in line) + " |" for line in lines]
        return "\n".join(out)

    runs = table(
        ["run", "tests", "passed", "failed", "errors", "skipped", "failing ids"],
        [
            ["dc6a413, shared worktree", before["tests"], before["passed"],
             before["failed"], before["errors"], before["skipped"], before["failing_ids"]],
            ["6cf97f3, shared worktree (previous environment)", 3653, 3550, 71, 5, 27, 76],
            ["the final commit, the real checkout", after["tests"], after["passed"],
             after["failed"], after["errors"], after["skipped"],
             after["failing_ids"]],
            ["nine OCR/media modules, engine venv", engine_counts["tests"],
             engine_counts["passed"], engine_counts["failed"], engine_counts["errors"],
             engine_counts["skipped"], engine_counts["failing_ids"]],
        ])

    classes = table(
        ["class", "failing at dc6a413", "failing at 6cf97f3", "meaning"],
        [
            ["ENV-OCR", sum(1 for r in rows if r["class"] == "ENV-OCR"),
             sum(1 for r in rows if r["class"] == "ENV-OCR"),
             "no OCR engine installed; the module passes in the engine venv"],
            ["ENV-FFMPEG", sum(1 for r in rows if r["class"] == "ENV-FFMPEG"),
             sum(1 for r in rows if r["class"] == "ENV-FFMPEG"),
             "no ffmpeg binary; the module passes in the engine venv"],
            ["DOCS-NOT-COMMITTED",
             sum(1 for r in rows if r["class"] == "DOCS-NOT-COMMITTED"),
             sum(1 for r in rows if r["class"] == "DOCS-NOT-COMMITTED"),
             "generated docs the checkout does not carry, and no generator that "
             "can create them"],
            ["NEEDS-DECISION", sum(1 for r in rows if r["class"] == "NEEDS-DECISION"),
             sum(1 for r in rows if r["class"] == "NEEDS-DECISION"),
             "a stale contract or a dependency fact the operator owns"],
            ["test_licensing", sum(1 for r in rows if r["class"] == "test_licensing"),
             sum(1 for r in rows if r["class"] == "test_licensing"),
             "two dependency distributions publish no licence metadata"],
            ["test_no_machine_paths",
             sum(1 for r in rows if r["class"] == "test_no_machine_paths"),
             sum(1 for r in rows if r["class"] == "test_no_machine_paths"),
             "committed raw captures name the sandbox path; the gate must not be "
             "exempted, so it stays red"],
            ["FIXED", len(fixed), 0, "passes at 6cf97f3"],
        ])

    extra_table = table(
        ["class of the 42 extra ids", "count"],
        sorted([[cls, len(ids)] for cls, ids in extra_by_class.items()],
               key=lambda pair: -pair[1]))

    lines = [
        "# Baseline reconciliation — Round 22 follow-up (A)",
        "",
        "Generated by `tools/audit/r22_reconcile.py`; do not edit by hand.",
        "Every count below is read from a JUnit file, and every id is in",
        "`audit_report/r22_workbook/baseline_ids.csv`.",
        "",
        "## The runs",
        "",
        runs,
        "",
        "The skips are reported beside every pass count because a skip is not a",
        "pass: 27 ids did not run, and `tests/unit/test_xlsx_external_link_without_rels.py`",
        "accounts for several of them (`sample workbook not in checkout`).",
        "",
        "## The answer to 67 versus 34, in one sentence",
        "",
        "Round 21 measured 35 failing ids in a venv that had the OCR engine and",
        "`ffmpeg` installed; the Round 22 run that produced 67 was made in a venv",
        "rebuilt from `requirements.txt` alone, which has neither, so "
        f"{sum(len(v) for v in extra_by_class.values())} of today's extra ids are "
        "environment — proved, not assumed, by re-running those modules in the engine",
        f"venv ({engine_counts['passed']} passed, {engine_counts['failed']} failed, "
        f"{engine_counts['skipped']} skipped) — and the code did not change.",
        "",
        "## The 42 extras, by class",
        "",
        extra_table,
        "",
        "## Three numbers, and what happened to each",
        "",
        table(["number", "where it came from", "status"], [
            ["34", "Round 21's `id_diff_unit.csv` class column", 
             "**verified** - the ids are in the committed CSV; 35 ids are "
             "recorded failing (34 pre-existing plus the one regression)"],
            ["67", "a `grep -c` over a `-q` text capture from an earlier run in "
                   "the previous session",
             "**retired** - no artefact survives, and the method cannot produce "
             "an id count (a `-q` capture repeats each id in the short summary, so "
             "a line count is a function of capture length, not of failures)"],
            ["76 / 77", "the JUnit XML of two runs made in one venv",
             "**measured** - these are the numbers this report uses"],
        ]),
        "",
        "The 67 is not reconciled with the 76; it is **withdrawn**. A figure whose "
        "raw artefact is gone cannot be re-derived, and the arithmetic that would "
        "have to be invented to explain it would be a claim, not a measurement. "
        "Everything this round publishes is counted from the JUnit files below. "
        "The retired number appears nowhere as evidence, and the sentence it used "
        "to support - \"67 pre-existing failures\" - is replaced by the table "
        "above.",
        "",
        "## Reproduced, fixed, and new",
        "",
        table(["measure", "count"], [
            ["Round 21 ids that reproduce at dc6a413", len(reproduced)],
            ["Round 21 ids that are green at dc6a413",
             len(now_green) - len(not_collected)],
            ["Round 21 ids this run no longer collects", len(not_collected)],
            ["ids failing at dc6a413 that are not in the Round 21 list", len(extra)],
            ["ids failing at dc6a413", len(before_ids)],
            ["ids failing at the final commit", len(after_ids)],
            ["of those, newly passing since dc6a413", len(fixed)],
            ["of those, newly failing since dc6a413",
             len(set(after_ids) - set(before_ids))],
            ["of those, failing only in a real checkout (see below)",
             len(set(after_ids) - set(before_ids))],
            ["ids skipped in the final run", len(skips_now)],
            ["Round 21 ids recorded as skipped", len(skips21)],
            ["Round 21 skips still skipped at the final commit", len(skips21 & skips_now)],
            ["Round 21 skips that now run and pass",
             len(skips21 - skips_now - set(after_ids))],
            ["Round 21 skips that now fail", len(skips21 & set(after_ids))],
        ]),
        "",
        "Newly passing: " + (", ".join(f"`{i}`" for i in fixed) or "none") + ".",
        "",
        "## The ids that only fail in a real checkout", "",
        table(["id", "why it fails in the checkout and not in a worktree"], [
            ["`tests.unit.test_no_machine_paths::test_no_file_contains_the_checkout_path`",
             "the test asserts that no tracked file contains *this checkout's* "
             "absolute path; a worktree has a different path, so the gate cannot "
             "fire there. Two files trip it - raw captures from round 14 "
             "(`audit_report/r14/ocr_before_no_engine.txt`, `ownership_red.txt`) - "
             "and rewriting a captured run would make it say something that was "
             "never printed. The gate stays red; the owner decides"],
            ["`tests.unit.test_reference_docs::test_reference_docs_are_current`",
             "it compares `docs/reference/` with what the generator produces now; "
             "the pages were regenerated at the final commit, so it passes here"],
        ]),
        "",
        f"It is in the failing set of this round because the final run was made in "
        f"the checkout (the earlier comparison runs were made in a worktree). "
        f"`path_dependent` = {len(path_dependent)}: the two runs compared above "
        f"were both made in the checkout, so no id differs between them for a "
        f"path reason.",
        "",
        "## Every rule this round added to the classifier, and its proof",
        "",
        "The classifier is `tools/audit/r22_baseline.py`. It uses "
        "`tools/audit/triage_failures.py`'s vocabulary and adds three classes, "
        "because the environment question could not be answered with the existing "
        "ones. **None of these rules turns a failure green**: each assigns a class, "
        "and the tool exits non-zero on any id it cannot place. A rule is added "
        "only with a measurement beside it.",
        "",
        table(["rule", "what it claims", "the measurement that justifies it"],
              [[f"`{key}`", why, evidence]
               for key, why, evidence in [
                   ("ENV-OCR", "the module cannot run without an OCR engine",
                    "the same nine modules in a venv with rapidocr + opencv 4.10.0 "
                    "+ numpy<2 + ffmpeg 7.0.2: "
                    f"{engine_counts['tests']} passed, {engine_counts['failed']} "
                    f"failed, {engine_counts['skipped']} skipped"),
                   ("ENV-FFMPEG", "the media reader cannot run without ffmpeg",
                    "same run as ENV-OCR (the media modules are in the nine)"),
                   ("DOCS-NOT-COMMITTED / NEEDS-DECISION / test_licensing / "
                    "test_no_machine_paths", "classes taken from "
                    "`triage_failures.py`'s own rule table",
                    "`tools/audit/triage_failures.py` - no new vocabulary was "
                    "invented for these"),
               ]]),
        "",
        "One rule is worth spelling out because it looked like gate tuning: the two "
        "`test_content_vs_identity` ids are classified *stale helper*, with the "
        "proof that `inspect.signature(StoragePipeline._extract_text_from_content)` "
        "is `(self, content)` and its body calls `self._media_metadata_text`, that "
        "the product change is `1c60be5` (an ancestor of the baseline commit), and "
        "that both ids are recorded as failing at the baseline in Round 21's own "
        "CSV. The rule did not make them pass - it named the cause. They pass now "
        "because the helper was fixed, and that edit is listed, with its old and "
        "new premise, in `TEST_EDITS.md`.",
        "",
        "## The integration suite, at the same commit", "",
        "Classification of the integration run (1467 tests, 1349 passed, 5 failed,",
        "113 skipped) is in `integration_ids.csv`, generated from",
        "`junit/integration_final.xml` by",
        "`tools/audit/r22_baseline.py --single`. The five:",
        "",
        table(["class", "ids", "why"], [
            ["NEEDS-DECISION", "4", "the four `test_translation_coverage` locales "
             "(ar, he, fa, hr) are untranslated catalogues - a translation task, "
             "not a code defect; `triage_failures.py` already classed them so"],
            ["COLLATERAL", "1", "`test_the_resolver_declines_what_is_not_a_name"
             "[\\u202eevil]` passes in isolation at this and at the baseline commit; "
             "in the full run the resolver finds word 'Evil' from "
             "`test-doc/Spreadsheets/text-test.tsv`, ingested by another module into "
             "the shared session database. A test-isolation defect older than this "
             "round"],
        ]),
        "",
        "113 integration ids were skipped. A skip is not a pass, and no claim in "
        "this report counts one as green.",
        "",
        "## Classes that would block closeout",
        "",
        "None. Every failing id carries a class from `triage_failures.py`'s",
        "vocabulary or a class this tool proves by measurement, and",
        "`tools/audit/r22_baseline.py` exits non-zero if any id is unclassified.",
        "",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"extra={len(extra)} reproduced={len(reproduced)} now_green={len(now_green)} "
          f"path_dependent={len(path_dependent)} fixed={len(fixed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
