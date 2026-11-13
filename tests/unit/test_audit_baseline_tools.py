"""Unit: the baseline tools classify every id, or refuse to close the round.

`tools/audit/r22_baseline.py` decides whether a failing test is pre-existing, a
regression or a property of the environment. That decision is the one the audit
report leans on, so it gets a test: the tool must name every id it is given and
must exit non-zero when it cannot, because "unclassified" is what blocks the
round from closing.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

baseline = importlib.import_module("tools.audit.r22_baseline")
reconcile = importlib.import_module("tools.audit.r22_reconcile")


def _junit(path: Path, cases, skipped=()):
    """Write the smallest JUnit file that still has the shape pytest emits."""
    body = []
    for classname, name, status in cases:
        inner = ""
        if status in ("failure", "error"):
            inner = f'<{status} message="boom">trace</{status}>'
        body.append(f'<testcase classname="{classname}" name="{name}">{inner}</testcase>')
    for classname, name in skipped:
        body.append(f'<testcase classname="{classname}" name="{name}">'
                    '<skipped message="no sample"/></testcase>')
    path.write_text(
        '<testsuites><testsuite name="pytest" tests="%d" failures="%d" errors="%d" '
        'skipped="%d" time="0">%s</testsuite></testsuites>' % (
            len(cases) + len(skipped),
            sum(1 for c in cases if c[2] == "failure"),
            sum(1 for c in cases if c[2] == "error"),
            len(skipped),
            "".join(body)),
        encoding="utf-8")
    return path


def test_the_module_is_recovered_from_pytest_classname():
    # pytest puts the class in `classname` and only the test name in `name`;
    # the rule table is keyed by file, so the class must be dropped, not kept.
    assert baseline.module_of_id(
        "tests.unit.test_component_library.TestAdoption::test_x") == \
        "tests/unit/test_component_library.py"
    assert baseline.module_of_id("tests.unit.test_x::test_y") == "tests/unit/test_x.py"


def test_the_counts_come_from_the_suite_totals(tmp_path):
    path = _junit(tmp_path / "run.xml",
                  [("tests.unit.test_a.TestB", "test_f", "failure"),
                   ("tests.unit.test_a.TestB", "test_e", "error"),
                   ("tests.unit.test_a.TestB", "test_ok", None)],
                  skipped=[("tests.unit.test_a.TestB", "test_s")])
    got = baseline.counts(path)
    assert got == {"tests": 4, "passed": 1, "failed": 1, "errors": 1,
                   "skipped": 1, "failing_ids": 2}


def test_a_skip_is_not_counted_as_a_pass(tmp_path):
    path = _junit(tmp_path / "run.xml",
                  [("tests.unit.test_a.TestB", "test_ok", None)],
                  skipped=[("tests.unit.test_a.TestB", "test_s")])
    got = baseline.counts(path)
    assert got["passed"] == 1 and got["skipped"] == 1 and got["tests"] == 2


def test_an_id_in_the_engine_run_is_classed_environment():
    cls, _, evidence = baseline.classify_id(
        "tests.unit.test_ocr_matrix.TestNoText::test_blank", {"tests.unit.test_ocr_matrix.TestNoText::test_blank"})
    assert cls == "ENV"
    assert "passes in the venv with OCR" in evidence


def test_an_ocr_module_without_an_engine_is_environment_not_code():
    cls, _, _ = baseline.classify_id(
        "tests.unit.test_embedded_images.TestX::test_y", set())
    assert cls == "ENV-OCR"


def test_a_known_docs_failure_uses_the_repository_vocabulary():
    cls, verdict, _ = baseline.classify_id(
        "tests.unit.test_interface_docs.TestRegistryEvidence::test_x", set())
    assert cls == "DOCS-NOT-COMMITTED"
    assert "D-7" in verdict


def test_an_unknown_id_is_reported_as_unclassified():
    cls, _, _ = baseline.classify_id("tests/unit/test_nope.py::test_x", set())
    assert cls == "UNCLASSIFIED"


def test_main_exits_non_zero_when_an_id_cannot_be_classified(tmp_path):
    before = _junit(tmp_path / "before.xml",
                    [("tests.unit.test_nope.TestX", "test_y", "failure")])
    after = _junit(tmp_path / "after.xml",
                   [("tests.unit.test_nope.TestX", "test_y", "failure")])
    out = tmp_path / "ids.csv"
    # The classification, not the environment: a run made outside
    # requirements.lock has to say so, and these tests are not measuring the
    # lock. Without the label this assertion would hold only on a machine whose
    # package set happens to match the lock exactly.
    assert baseline.main(["--before", str(before), "--after", str(after),
                          "--out", str(out), "--environment-label", "unit-tests"]) == 1
    assert "test_nope" in out.read_text(encoding="utf-8")


def test_a_fixed_id_is_classed_fixed_and_does_not_block_closeout(tmp_path):
    before = _junit(tmp_path / "before.xml",
                    [("tests.unit.test_nope.TestX", "test_y", "failure")])
    after = _junit(tmp_path / "after.xml",
                   [("tests.unit.test_nope.TestX", "test_y", None)])
    out = tmp_path / "ids.csv"
    assert baseline.main(["--before", str(before), "--after", str(after),
                          "--out", str(out), "--environment-label", "unit-tests"]) == 0
    row = out.read_text(encoding="utf-8")
    assert ",FIXED," in row


def test_the_reconciliation_reads_a_skip_as_a_skip(tmp_path):
    path = _junit(tmp_path / "run.xml",
                  [("tests.unit.test_a.TestB", "test_f", "failure")],
                  skipped=[("tests.unit.test_a.TestB", "test_s")])
    assert reconcile.skipped_ids(path) == {"tests.unit.test_a.TestB::test_s"}


@pytest.mark.parametrize("status", ["failure", "error"])
def test_both_failure_kinds_are_failing_ids(tmp_path, status):
    path = _junit(tmp_path / "run.xml", [("tests.unit.test_a.TestB", "test_x", status)])
    assert baseline.failing_ids(path) == {"tests.unit.test_a.TestB::test_x": status}


# --------------------------------------------------------------------------- #
# The skip-count baseline
# --------------------------------------------------------------------------- #
# Removing 109 of 113 skips is only worth something if a later change cannot
# quietly put them back. `tools/audit/skip_baseline.py` pins the count and fails
# a run whose skips grew without a line in the edit table explaining why.

skip_baseline = importlib.import_module("tools.audit.skip_baseline")


def test_a_skip_is_classified_by_what_produced_it():
    assert skip_baseline.categorise("no OCR engine installed") == "ENGINE-OCR"
    assert skip_baseline.categorise(
        "PostgreSQL not reachable for the crash-recovery tests") == "DATABASE"
    assert skip_baseline.categorise("set CATEGORY_SCALE_100K=1 to build the 100k-node tree") \
        == "OPT-IN-FLAG"
    assert skip_baseline.categorise("nginx or openssl not installed") == "EXTERNAL-BINARY"
    assert skip_baseline.categorise("Requires full ingestion pipeline - Part 2") \
        == "NOT-IMPLEMENTED"
    # An unknown message keeps its own name instead of borrowing a friendly one.
    assert skip_baseline.categorise("something new") == "OTHER"


def test_skips_are_read_with_their_reasons(tmp_path):
    path = _junit(tmp_path / "run.xml", [("tests.unit.test_a.TestB", "test_ok", "pass")],
                  skipped=[("tests.unit.test_a.TestB", "test_s")])
    found = skip_baseline.skips(path)
    assert list(found) == ["tests.unit.test_a.TestB::test_s"]
    assert found["tests.unit.test_a.TestB::test_s"] == "no sample"


def test_a_grown_skip_count_is_unexcused_when_nothing_names_the_module(tmp_path):
    found = {"tests.unit.test_new.TestX::test_y": "no OCR engine installed"}
    delta = skip_baseline.compare(found, {}, "| `tests/unit/test_other.py` | ... |")
    assert delta["unexcused"] == found
    assert delta["excused"] == {}


def test_a_grown_skip_count_is_excused_when_the_edit_table_names_the_module():
    found = {"tests.unit.test_new.TestX::test_y": "no OCR engine installed"}
    delta = skip_baseline.compare(found, {}, "| `tests/unit/test_new.py` | guard added |")
    assert delta["excused"] == found
    assert delta["unexcused"] == {}


def test_main_fails_the_run_when_skips_grow_unexplained(tmp_path):
    current = _junit(tmp_path / "run.xml", [], skipped=[("tests.unit.test_new.TestX", "test_y")])
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"count": 0, "ids": {}}), encoding="utf-8")
    explanations = tmp_path / "edits.md"
    explanations.write_text("| `tests/unit/test_unrelated.py` |", encoding="utf-8")
    assert skip_baseline.main(["--current", str(current), "--baseline", str(baseline),
                               "--explanations", str(explanations)]) == 1


def test_the_first_recording_pins_the_state_and_a_later_increase_is_refused(tmp_path):
    # The suite has skips before this tool exists. Refusing to pin them would
    # make the guard unusable on the day it is added, so the first recording
    # takes the state as a whole and is labelled as a first recording; from
    # then on an increase has to be named in the edit table.
    case = ("tests.unit.test_new.TestX", "test_y")
    current = _junit(tmp_path / "run.xml", [], skipped=[case])
    baseline = tmp_path / "baseline.json"
    explanations = tmp_path / "edits.md"
    explanations.write_text("nothing relevant", encoding="utf-8")

    assert skip_baseline.main(["--current", str(current), "--baseline", str(baseline),
                               "--explanations", str(explanations), "--record"]) == 0
    assert json.loads(baseline.read_text(encoding="utf-8"))["first_recording"] is True

    # Same run, now measured against the pin: it matches, so it passes.
    assert skip_baseline.main(["--current", str(current), "--baseline", str(baseline),
                               "--explanations", str(explanations)]) == 0

    # One more skip, nothing naming its module: refused, and not recorded.
    grown = _junit(tmp_path / "grown.xml", [],
                   skipped=[case, ("tests.unit.test_other.TestZ", "test_w")])
    assert skip_baseline.main(["--current", str(grown), "--baseline", str(baseline),
                               "--explanations", str(explanations), "--record"]) == 1
    assert json.loads(baseline.read_text(encoding="utf-8"))["count"] == 1

    explanations.write_text("| `tests/unit/test_other.py` |", encoding="utf-8")
    assert skip_baseline.main(["--current", str(grown), "--baseline", str(baseline),
                               "--explanations", str(explanations), "--record"]) == 0


def test_a_skip_that_stops_skipping_has_to_be_re_recorded(tmp_path):
    # An improvement is not a failure, but leaving the baseline stale would let
    # the skip return unnoticed, so an equal-count run with a different id set
    # still exits non-zero until the baseline is rewritten.
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"count": 1, "ids": {
        "tests.unit.test_new.TestX::test_old": "no OCR engine installed"}}), encoding="utf-8")
    current = _junit(tmp_path / "run.xml", [], skipped=[("tests.unit.test_new.TestX", "test_y")])
    explanations = tmp_path / "edits.md"
    explanations.write_text("| `tests/unit/test_new.py` |", encoding="utf-8")
    assert skip_baseline.main(["--current", str(current), "--baseline", str(baseline),
                               "--explanations", str(explanations)]) == 1


def test_a_re_pin_is_refused_in_the_same_run_that_increased_the_count(tmp_path):
    """The pin moves only when every new skip is named in the edit table.

    A run that both measures an increase and writes the new baseline would
    launder the increase: the number would be higher, the file would say it is
    expected, and nobody would have had to write down why. The refusal is
    per-id, so a run that names one module and quietly grows by another is
    still refused.
    """
    baseline = tmp_path / "baseline.json"
    explanations = tmp_path / "edits.md"
    explanations.write_text("nothing relevant", encoding="utf-8")
    first = _junit(tmp_path / "first.xml", [], skipped=[("tests.unit.test_a.TestA", "test_one")])
    assert skip_baseline.main(["--current", str(first), "--baseline", str(baseline),
                               "--explanations", str(explanations), "--record"]) == 0

    # Two new skips, one of them named: still an unexplained increase.
    grown = _junit(tmp_path / "grown.xml", [],
                   skipped=[("tests.unit.test_a.TestA", "test_one"),
                            ("tests.unit.test_named.TestN", "test_two"),
                            ("tests.unit.test_unnamed.TestU", "test_three")])
    explanations.write_text("| `tests/unit/test_named.py` | named |", encoding="utf-8")
    assert skip_baseline.main(["--current", str(grown), "--baseline", str(baseline),
                               "--explanations", str(explanations), "--record"]) == 1
    assert json.loads(baseline.read_text(encoding="utf-8"))["count"] == 1, \
        "a refused recording must leave the pin where it was"

    explanations.write_text("| `tests/unit/test_named.py` | named |\n"
                            "| `tests/unit/test_unnamed.py` | named too |", encoding="utf-8")
    assert skip_baseline.main(["--current", str(grown), "--baseline", str(baseline),
                               "--explanations", str(explanations), "--record"]) == 0
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert payload["count"] == 3
    assert payload["previous_count"] == 1
    assert payload["recorded_from"][0]["skips"] == 3
