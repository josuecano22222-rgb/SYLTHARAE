"""Unit tests for the dynamic work ledger behind real-time progress.

These cover the accounting contract on its own, without the processing engine:
a dynamically growing denominator, exactly-once settlement, terminal buckets
for failed/skipped/unsupported work, monotonic percentages and the "never 100%
while nested work is outstanding" rule.
"""

import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.progress_ledger import (  # noqa: E402
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_RETRYABLE,
    OUTCOME_SKIPPED,
    OUTCOME_UNSUPPORTED,
    ProgressLedger,
    classify_result,
)


class TestDynamicTotal:
    def test_starts_at_zero_percent_with_no_work(self):
        ledger = ProgressLedger()
        snap = ledger.snapshot()
        assert snap["percent"] == 0
        assert snap["total_files"] == 0
        assert snap["complete"] is False

    def test_zero_percent_means_nothing_finished(self):
        ledger = ProgressLedger()
        ledger.add_discovered(10, initial=True)
        assert ledger.snapshot()["percent"] == 0
        assert ledger.snapshot()["files_done"] == 0

    def test_children_discovered_later_grow_the_total(self):
        """The core defect: the total must not be frozen at the top level."""
        ledger = ProgressLedger()
        ledger.add_discovered(2, initial=True)
        assert ledger.snapshot()["total_files"] == 2

        ledger.add_discovered(5, key="/tmp/x/extracted", parent="/top/a.zip")
        snap = ledger.snapshot()
        assert snap["total_files"] == 7
        assert snap["files_initial"] == 2
        assert snap["files_nested"] == 5
        assert snap["containers_opened"] == 1
        assert snap["children_by_parent"] == {"/top/a.zip": 5}

    def test_recursive_descendants_stay_attributable(self):
        ledger = ProgressLedger()
        ledger.add_discovered(1, initial=True)
        ledger.add_discovered(1, key="/e/l1", parent="/top/level1.zip")
        ledger.add_discovered(3, key="/e/l2", parent="/e/l1/level2.zip")
        ledger.add_discovered(2, key="/e/l3", parent="/e/l2/level3.zip")

        snap = ledger.snapshot()
        assert snap["total_files"] == 7
        assert snap["files_nested"] == 6
        assert snap["containers_opened"] == 3
        assert snap["children_by_parent"] == {
            "/top/level1.zip": 1,
            "/e/l1/level2.zip": 3,
            "/e/l2/level3.zip": 2,
        }

    def test_keyed_discovery_reconciles_instead_of_double_counting(self):
        """Router registers a directory, then the nested reader re-registers it."""
        ledger = ProgressLedger()
        ledger.add_discovered(1, initial=True)
        ledger.add_discovered(12, key="/extract/x")
        assert ledger.snapshot()["total_files"] == 13

        # Same key, smaller count after checkpoint filtering -> reconciles down.
        delta = ledger.add_discovered(10, key="/extract/x")
        assert delta == -2
        assert ledger.snapshot()["total_files"] == 11

        # Repeating the identical registration changes nothing.
        assert ledger.add_discovered(10, key="/extract/x") == 0
        assert ledger.snapshot()["total_files"] == 11


class TestExactlyOnceSettlement:
    def test_unit_settles_once_even_when_settled_twice(self):
        ledger = ProgressLedger()
        ledger.add_discovered(1, initial=True)
        unit = ledger.begin(path="/a.txt")
        assert unit.settle(OUTCOME_COMPLETED) is True
        assert unit.settle(OUTCOME_COMPLETED) is False
        assert unit.settle(OUTCOME_FAILED) is False

        snap = ledger.snapshot()
        assert snap["files_done"] == 1
        assert snap["files_completed"] == 1
        assert snap["files_failed"] == 0
        assert snap["in_progress"] == 0

    def test_abandoned_worker_cannot_be_double_counted(self):
        """A timeout settles the unit; the worker finishing later is a no-op."""
        ledger = ProgressLedger()
        ledger.add_discovered(1, initial=True)
        unit = ledger.begin(path="/slow.zip")

        assert ledger.settle_path("/slow.zip", OUTCOME_RETRYABLE) is True
        # The abandoned worker eventually returns and tries to settle again.
        assert unit.settle(OUTCOME_COMPLETED) is False

        snap = ledger.snapshot()
        assert snap["files_done"] == 1
        assert snap["files_retryable"] == 1
        assert snap["files_completed"] == 0

    def test_settle_path_is_noop_when_nothing_in_flight(self):
        ledger = ProgressLedger()
        assert ledger.settle_path("/never-started.txt", OUTCOME_FAILED) is False

    def test_in_progress_tracks_open_units(self):
        ledger = ProgressLedger()
        ledger.add_discovered(3, initial=True)
        a = ledger.begin(path="/a")
        b = ledger.begin(path="/b")
        assert ledger.snapshot()["in_progress"] == 2
        a.settle(OUTCOME_COMPLETED)
        assert ledger.snapshot()["in_progress"] == 1
        b.settle(OUTCOME_COMPLETED)
        assert ledger.snapshot()["in_progress"] == 0


class TestTerminalBuckets:
    @pytest.mark.parametrize("outcome,key", [
        (OUTCOME_COMPLETED, "files_completed"),
        (OUTCOME_FAILED, "files_failed"),
        (OUTCOME_SKIPPED, "files_skipped"),
        (OUTCOME_UNSUPPORTED, "files_unsupported"),
        (OUTCOME_RETRYABLE, "files_retryable"),
    ])
    def test_every_outcome_advances_the_bar(self, outcome, key):
        """Failed/skipped/unsupported work must not strand progress below 100%."""
        ledger = ProgressLedger()
        ledger.add_discovered(1, initial=True)
        ledger.begin(path="/f").settle(outcome)

        snap = ledger.snapshot()
        assert snap[key] == 1
        assert snap["files_done"] == 1
        assert snap["percent"] == 100
        assert snap["complete"] is True

    def test_record_settles_without_ever_running(self):
        """Checkpoint-skipped and pre-filtered unsupported objects."""
        ledger = ProgressLedger()
        ledger.add_discovered(2, initial=True)
        ledger.record(OUTCOME_SKIPPED, 3, parent="/folder")

        snap = ledger.snapshot()
        assert snap["total_files"] == 5
        assert snap["files_skipped"] == 3
        assert snap["files_done"] == 3
        assert snap["files_pending"] == 2

    def test_mixed_outcomes_all_reach_terminal(self):
        ledger = ProgressLedger()
        # 5 units that run, plus 1 recorded as already-terminal (a checkpoint
        # skip) - record() both discovers and settles, so the total is 6.
        ledger.add_discovered(5, initial=True)
        ledger.begin(path="/ok").settle(OUTCOME_COMPLETED)
        ledger.begin(path="/bad").settle(OUTCOME_FAILED)
        ledger.begin(path="/skip").settle(OUTCOME_SKIPPED)
        ledger.begin(path="/weird.zzz").settle(OUTCOME_UNSUPPORTED)
        ledger.begin(path="/slow").settle(OUTCOME_RETRYABLE)
        ledger.record(OUTCOME_SKIPPED, 1)

        snap = ledger.snapshot()
        assert snap["files_done"] == 6
        assert snap["total_files"] == 6
        assert snap["files_pending"] == 0
        assert snap["percent"] == 100
        assert snap["complete"] is True

    def test_retryable_is_distinguishable_from_terminal_success(self):
        ledger = ProgressLedger()
        ledger.add_discovered(2, initial=True)
        ledger.begin(path="/a").settle(OUTCOME_COMPLETED)
        ledger.begin(path="/b").settle(OUTCOME_RETRYABLE)

        snap = ledger.snapshot()
        assert snap["files_completed"] == 1
        assert snap["files_retryable"] == 1
        assert snap["files_failed"] == 0


class TestCompletionSemantics:
    def test_not_complete_while_a_parent_container_is_in_flight(self):
        """100% must not be reported while nested objects are still queued."""
        ledger = ProgressLedger()
        ledger.add_discovered(2, initial=True)

        # Parent container opens; children are discovered before it settles.
        parent = ledger.begin(path="/box.zip")
        ledger.add_discovered(4, key="/e/box", parent="/box.zip")

        for i in range(4):
            ledger.begin(path=f"/e/box/m{i}").settle(OUTCOME_COMPLETED)
        ledger.begin(path="/other.txt").settle(OUTCOME_COMPLETED)

        snap = ledger.snapshot()
        # 5 of 6 settled, but the parent is still running -> never 100%.
        assert snap["files_done"] == 5
        assert snap["total_files"] == 6
        assert snap["in_progress"] == 1
        assert snap["percent"] < 100
        assert snap["complete"] is False
        assert ledger.is_complete is False

        parent.settle(OUTCOME_COMPLETED)
        assert ledger.snapshot()["percent"] == 100
        assert ledger.is_complete is True

    def test_percent_is_capped_below_100_until_everything_is_terminal(self):
        ledger = ProgressLedger()
        ledger.add_discovered(1, initial=True)
        unit = ledger.begin(path="/a")
        # Even if the arithmetic reached 100, an open unit forbids it.
        ledger.add_discovered(0)
        assert ledger.snapshot()["percent"] < 100
        unit.settle(OUTCOME_COMPLETED)
        assert ledger.snapshot()["percent"] == 100

    def test_percent_never_retreats_when_the_denominator_grows(self):
        """2/6 = 33% must not fall to 2/8 = 25% when a container opens."""
        ledger = ProgressLedger()
        ledger.add_discovered(6, initial=True)
        for i in range(2):
            ledger.begin(path=f"/f{i}").settle(OUTCOME_COMPLETED)
        high = ledger.snapshot()["percent"]
        assert high == 33

        parent = ledger.begin(path="/box.zip")
        ledger.add_discovered(2, key="/e/box", parent="/box.zip")
        grown = ledger.snapshot()["percent"]

        assert grown >= high, "progress bar must not visibly go backwards"
        # ...but the real counters still tell the truth.
        snap = ledger.snapshot()
        assert snap["files_done"] == 2
        assert snap["total_files"] == 8
        parent.settle(OUTCOME_COMPLETED)


class TestConcurrency:
    def test_parallel_workers_produce_consistent_totals(self):
        ledger = ProgressLedger()
        n_threads, per_thread = 8, 50
        ledger.add_discovered(n_threads * per_thread, initial=True)

        def worker(tid):
            for i in range(per_thread):
                ledger.begin(path=f"/t{tid}/f{i}").settle(OUTCOME_COMPLETED)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = ledger.snapshot()
        assert snap["total_files"] == n_threads * per_thread
        assert snap["files_done"] == n_threads * per_thread
        assert snap["in_progress"] == 0
        assert snap["percent"] == 100
        assert snap["complete"] is True

    def test_concurrent_discovery_and_settlement_never_lose_count(self):
        ledger = ProgressLedger()
        ledger.add_discovered(1, initial=True)

        def discover(tid):
            ledger.add_discovered(10, key=f"/e/{tid}", parent=f"/c{tid}.zip")

        def settle(tid):
            for i in range(10):
                ledger.begin(path=f"/e/{tid}/m{i}").settle(OUTCOME_COMPLETED)

        threads = []
        for t in range(6):
            threads.append(threading.Thread(target=discover, args=(t,)))
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        threads = [threading.Thread(target=settle, args=(t,)) for t in range(6)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        snap = ledger.snapshot()
        assert snap["total_files"] == 61
        assert snap["files_nested"] == 60
        assert snap["containers_opened"] == 6


class TestClassification:
    def test_success(self):
        assert classify_result({"Content": {"text": "hi"}}) == OUTCOME_COMPLETED

    def test_none_result_is_skipped(self):
        assert classify_result(None) == OUTCOME_SKIPPED

    def test_unsupported_type_is_not_a_failure(self):
        result = {"Content": {"error": "Unsupported file type: .zzz"}}
        assert classify_result(result) == OUTCOME_UNSUPPORTED

    def test_recursion_depth_guard_is_a_skip(self):
        result = {"Content": {"error": "Maximum recursion depth (5) exceeded"}}
        assert classify_result(result) == OUTCOME_SKIPPED

    def test_timeout_is_retryable(self):
        result = {"Content": {"error": "processing timeout after 1200s"}}
        assert classify_result(result) == OUTCOME_RETRYABLE

    def test_missing_ocr_still_counts_as_completed(self):
        """OCR is optional; the object was still read and stored."""
        result = {"Content": {"error": "OCR unavailable (tesseract not installed)"}}
        assert classify_result(result) == OUTCOME_COMPLETED

    def test_generic_error_is_a_failure(self):
        assert classify_result({"Content": {"error": "boom"}}) == OUTCOME_FAILED
        assert classify_result("not a dict") == OUTCOME_FAILED


class TestClassificationOfRealReaderShapes:
    """Readers do not all report through ``Content.error``.

    These cases come from observing what the pipeline actually produces for an
    ``email -> ZIP -> DOCX -> embedded image`` workload. Classifying only
    ``Content.error`` reported an image whose OCR engine was missing - and which
    therefore extracted nothing - as a successful completion.
    """

    def test_explicit_retryable_flag_is_honoured_at_top_level(self):
        result = {
            "text": "",
            "retryable": True,
            "extraction_info": {"error": "PIL/Image library not available"},
        }
        assert classify_result(result) == OUTCOME_RETRYABLE

    def test_explicit_retryable_flag_is_honoured_inside_content(self):
        result = {"Content": {"retryable": True, "error": "no engine"}}
        assert classify_result(result) == OUTCOME_RETRYABLE

    def test_ocr_required_with_nothing_extracted_is_retryable(self):
        """The shape read_img_fast actually returns for a missing OCR engine."""
        result = {
            "Content": {
                "text": "",
                "ocr_attempted": True,
                "ocr_engine": "unavailable",
                "ocr_successful": False,
                "extraction_info": {
                    "error": "OCR is required but no OCR engine is available",
                    "reason": "ocr_required_engine_unavailable",
                    "extracted": False,
                    "stored": False,
                },
            },
            "Metadata": {},
        }
        assert classify_result(result) == OUTCOME_RETRYABLE

    def test_ocr_required_but_content_survived_is_completed(self):
        """A PDF that kept its native text layer really did produce content."""
        result = {
            "Content": {
                "text": "preserved text layer",
                "extraction_info": {
                    "reason": "ocr_required_engine_unavailable",
                    "extracted": True,
                },
            }
        }
        assert classify_result(result) == OUTCOME_COMPLETED

    def test_error_reported_only_in_extraction_info_is_not_a_success(self):
        result = {
            "Content": {
                "extraction_info": {"error": "Unsupported file type: .zzz"},
            }
        }
        assert classify_result(result) == OUTCOME_UNSUPPORTED

    def test_retryable_still_advances_the_bar_to_completion(self):
        """Retryable is terminal: it must not strand the percentage under 100."""
        ledger = ProgressLedger()
        ledger.add_discovered(2, initial=True)
        ledger.begin(path="/a").settle(OUTCOME_COMPLETED)
        ledger.begin(path="/b").settle(OUTCOME_RETRYABLE)

        snap = ledger.snapshot()
        assert snap["files_retryable"] == 1
        assert snap["files_completed"] == 1
        assert snap["files_done"] == 2
        assert snap["files_pending"] == 0
        assert snap["percent"] == 100
        assert snap["complete"] is True


class TestNotification:
    def test_notifier_receives_snapshots_as_work_settles(self):
        seen = []
        ledger = ProgressLedger(notify=lambda snap: seen.append(snap["percent"]))
        ledger.add_discovered(4, initial=True)
        for i in range(4):
            ledger.begin(path=f"/f{i}").settle(OUTCOME_COMPLETED)

        assert seen, "ledger must push updates, not only be polled"
        assert seen[-1] == 100
        assert len(set(seen)) > 2, "progress must move through intermediates"

    def test_broken_notifier_cannot_break_processing(self):
        def boom(_snap):
            raise RuntimeError("consumer exploded")

        ledger = ProgressLedger(notify=boom)
        ledger.add_discovered(1, initial=True)
        ledger.begin(path="/f").settle(OUTCOME_COMPLETED)
        assert ledger.snapshot()["percent"] == 100


class TestClassificationOfDiagnosticReasons:
    """A ``reason`` explains an outcome; it is not itself the outcome.

    Readers attach one to successful results too - ``read_svg_file`` states that
    it extracted the document's text, or that the document holds none - and
    every SVG therefore counted as a *failed* file in the run summary while the
    database recorded the same object as processed. The two accounts of one run
    must not disagree.
    """

    def test_successful_svg_with_a_reason_is_completed(self):
        result = {
            "Content": {
                "text": "Chart label",
                "extraction_info": {
                    "extracted": True,
                    "stored": True,
                    "reason": "svg_text_extracted",
                    "text_elements": 1,
                },
            }
        }
        assert classify_result(result) == OUTCOME_COMPLETED

    def test_empty_document_that_was_read_is_completed(self):
        """Vector-only artwork: read, and the finding is that it holds no text."""
        result = {
            "Content": {
                "text": "",
                "extraction_info": {
                    "extracted": False,
                    "stored": False,
                    "empty_result": True,
                    "reason": "svg_has_no_text_elements; 12 vector path element(s)",
                },
            }
        }
        assert classify_result(result) == OUTCOME_COMPLETED

    def test_deliberate_skip_is_skipped_not_failed(self):
        """The image below the OCR size floor is stored as 'skipped'."""
        result = {
            "text": "",
            "extraction_info": {
                "skipped": True,
                "skip_reason": "too_small",
                "reason": "too_small",
                "extracted": False,
                "stored": False,
            },
        }
        assert classify_result(result) == OUTCOME_SKIPPED
        assert classify_result({"Content": result}) == OUTCOME_SKIPPED

    def test_reason_without_content_and_without_a_skip_still_fails(self):
        """An unexplained empty result must stay visible as a problem."""
        result = {
            "text": "",
            "extraction_info": {
                "reason": "no_text_extracted",
                "extracted": False,
                "stored": False,
            },
        }
        assert classify_result(result) == OUTCOME_FAILED

    def test_ocr_ran_but_found_no_text_is_completed_not_failed(self):
        """Photos with no OCR text are a completed read, not a failure."""
        result = {
            "text": "",
            "ocr_attempted": True,
            "ocr_successful": False,
            "empty_result": True,
            "extraction_info": {
                "reason": "no_text_extracted",
                "empty_result": True,
                "extracted": False,
                "stored": False,
            },
        }
        assert classify_result(result) == OUTCOME_COMPLETED

    def test_error_outranks_content_and_reason(self):
        result = {
            "Content": {
                "text": "partial",
                "extraction_info": {"error": "boom", "reason": "svg_text_extracted"},
            }
        }
        assert classify_result(result) == OUTCOME_FAILED
