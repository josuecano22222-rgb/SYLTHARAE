"""Unit tests for StoragePipeline._resolve_processing_status.

The pre-existing model stored only file_status in ('Read','Unread'), which says
whether text exists, not what happened. A corrupt file, a deliberately skipped
icon and an unrecognised type all landed as 'Unread' and were indistinguishable
afterwards. These tests pin the distinction.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.storage_pipeline import StoragePipeline  # noqa: E402


def _resolve(content, file_status="Unread"):
    return StoragePipeline._resolve_processing_status(None, content, file_status)


def test_every_returned_state_is_permitted_by_the_schema():
    """Migration 0007 constrains the column; a typo would be a runtime error."""
    allowed = {
        "discovered", "queued", "processing", "processed", "partially_processed",
        "failed", "unsupported", "skipped", "retrying",
    }
    assert set(StoragePipeline.TERMINAL_PROCESSING_STATES) <= allowed


def test_no_in_flight_states_are_claimed():
    """This is a synchronous write path: it cannot truthfully know these."""
    for state in ("queued", "processing", "retrying"):
        assert state not in StoragePipeline.TERMINAL_PROCESSING_STATES


def test_clean_text_is_processed():
    assert _resolve({"text": "hello"}, "Read") == ("processed", None)


def test_unsupported_type_beats_failed():
    """Order matters: an unsupported type also carries an error string."""
    status, detail = _resolve({"error": "Unsupported file type: .xyz"})
    assert status == "unsupported"
    assert ".xyz" in detail


def test_corrupt_input_is_failed_with_the_real_reason():
    status, detail = _resolve({"error": "Failed to open file 'x.pdf' as type pdf"})
    assert status == "failed"
    assert "Failed to open file" in detail


def test_skipped_carries_its_reason():
    status, detail = _resolve({"extraction_info": {"skipped": True, "skip_reason": "too_small"}})
    assert status == "skipped"
    assert detail == "too_small"


def test_skipped_without_a_reason_is_still_skipped():
    assert _resolve({"extraction_info": {"skipped": True}})[0] == "skipped"


def test_ocr_attempted_without_text_is_partial_not_clean():
    status, detail = _resolve({"ocr_attempted": True, "ocr_successful": False, "text": ""})
    assert status == "partially_processed"
    assert "ocr" in detail


def test_successful_ocr_is_not_partial():
    status, _ = _resolve(
        {"ocr_attempted": True, "ocr_successful": True, "text": "MARKER"}, "Read"
    )
    assert status == "processed"


def test_engine_error_makes_the_result_partial():
    status, detail = _resolve(
        {"text": "some", "extraction_info": {"engine_error": "engine crashed"}}, "Read"
    )
    assert status == "partially_processed"
    assert "engine crashed" in detail


def test_truncated_input_is_partial():
    status, _ = _resolve(
        {"text": "partial", "extraction_info": {"truncated": True}}, "Read"
    )
    assert status == "partially_processed"


def test_some_pages_failed_is_partial():
    pages = [
        {"page_number": 1, "method": "direct_extraction", "text": "a"},
        {"page_number": 2, "method": "ocr_failed", "error": "no engine"},
    ]
    status, detail = _resolve({"pages": pages})
    assert status == "partially_processed"
    assert "1 of 2" in detail


def test_every_page_failing_is_failed_not_partial():
    pages = [
        {"page_number": 1, "error": "corrupt"},
        {"page_number": 2, "method": "conversion_failed"},
    ]
    assert _resolve({"pages": pages})[0] == "failed"


def test_all_pages_direct_extraction_is_processed():
    pages = [{"page_number": 1, "method": "direct_extraction", "text": "a"}]
    assert _resolve({"pages": pages})[0] == "processed"


def test_empty_but_readable_is_processed_not_failed():
    """file_status 'Read' means content exists, so nothing needs qualifying."""
    assert _resolve({"text": "", "file_path": "/x/empty.txt"}, "Read") == ("processed", None)


def test_unread_with_no_text_explains_itself():
    """'Unread' alone cannot say why; status_detail must."""
    assert _resolve({"text": ""}, "Unread") == ("processed", "no extractable text")


def test_no_content_at_all_reports_no_extractable_text():
    assert _resolve({}, "Unread") == ("processed", "no extractable text")


def test_status_detail_is_length_capped():
    status, detail = _resolve({"error": "x" * 5000})
    assert status == "failed"
    assert len(detail) <= 2000


def test_non_dict_content_does_not_raise():
    for bad in (None, 42, "text", []):
        assert _resolve(bad)[0] == "processed"


def test_non_dict_extraction_info_does_not_raise():
    assert _resolve({"extraction_info": "not a dict"})[0] == "processed"

def test_a_held_file_is_failed_but_says_it_was_locked():
    """Windows/POSIX lock: retryable, not a defect of the artifact.

    The stored vocabulary (migration 0007's CHECK constraint) has no 'locked'
    value, so the distinction has to survive in status_detail - an operator
    triaging 'failed' rows must be able to tell a retry from a real failure.
    """
    state, detail = _resolve(
        {"error": "[WinError 32] The process cannot access the file because it"
                  " is being used by another process"}
    )

    assert state == "failed"
    assert detail.startswith("locked:"), detail
    assert "another process" in detail


def test_a_normal_error_is_not_labelled_locked():
    state, detail = _resolve({"error": "invalid PDF structure"})

    assert state == "failed"
    assert not detail.startswith("locked:"), detail


def test_the_row_and_the_run_agree_on_what_locked_means():
    """The resolver (row) and classify_result (accounting) must not diverge."""
    from pipeline.progress_ledger import OUTCOME_LOCKED, classify_result

    contents = [
        {"error": "[WinError 32] being used by another process"},
        {"error": "Permission denied: '/x/y.pdf'"},
        {"error": "resource temporarily unavailable"},
        {"error": "invalid PDF structure"},
    ]
    for content in contents:
        state, detail = _resolve(content)
        outcome = classify_result(content)
        assert (detail.startswith("locked:") is (outcome == OUTCOME_LOCKED)), (
            f"row says {detail!r} while the ledger says {outcome!r}: {content}"
        )


# ----------------------------------------------------------------------
# Containers that were only partly read (RAR without a decoder)
# ----------------------------------------------------------------------
def _partial_archive(**overrides):
    info = {
        "warning": "archive_needs_external_decoder",
        "decoder_missing": True,
        "members_total": 405,
        "members_read": 64,
        "members_unreadable": {"decoder_required": 341},
    }
    info.update(overrides)
    return {"text": "RAR archive: Deleted Items.rar\nMembers: 405", "extraction_info": info}


def test_partly_read_archive_is_partially_processed():
    """'processed' would claim the whole archive was read."""
    state, detail = _resolve(_partial_archive(), "Read")
    assert state == "partially_processed", state
    assert "341 of 405" in detail and "decoder required" in detail


def test_partly_read_archive_detail_names_the_read_portion():
    _, detail = _resolve(_partial_archive(), "Read")
    assert detail.endswith("64 read"), detail


def test_fully_read_archive_is_processed():
    state, detail = _resolve(
        _partial_archive(members_total=3, members_read=3, members_unreadable={}),
        "Read",
    )
    assert (state, detail) == ("processed", None)


def test_archive_member_counts_without_a_total_still_reported():
    state, detail = _resolve(
        _partial_archive(members_total=None, members_read=None), "Read"
    )
    assert state == "partially_processed"
    assert "341 archive member(s) could not be read" in detail


def test_an_explicit_error_still_wins_over_a_partial_member_read():
    state, detail = _resolve({**_partial_archive(), "error": "read failed"}, "Unread")
    assert (state, detail) == ("failed", "read failed")


def test_reader_reason_replaces_the_generic_no_text_message():
    """An SVG with no text elements must not say only 'no extractable text'."""
    state, detail = _resolve(
        {"text": "", "extraction_info": {"reason": "svg_has_no_text_elements"}},
        "Unread",
    )
    assert state == "processed"
    assert detail == "no extractable text: svg_has_no_text_elements"


def test_reason_detail_is_length_capped_too():
    from pipeline.storage_pipeline import STATUS_DETAIL_MAX_LENGTH

    _, detail = _resolve(
        {"text": "", "extraction_info": {"reason": "x" * 5000}}, "Unread"
    )
    assert len(detail) <= STATUS_DETAIL_MAX_LENGTH


def test_archive_and_html_diagnostics_are_persisted():
    """The states above must be queryable after the run, not just logged."""
    from pipeline.storage_pipeline import StoragePipeline

    provenance = StoragePipeline._build_extraction_provenance(
        None, _partial_archive()
    )
    diagnostics = provenance["diagnostics"]
    assert diagnostics["decoder_missing"] is True
    assert diagnostics["members_total"] == 405
    assert diagnostics["members_unreadable"] == {"decoder_required": 341}

    html = StoragePipeline._build_extraction_provenance(None, {
        "text_content": "hi",
        "extraction_info": {"visible_text_chars": 539, "script_chars": 41200},
    })
    assert html["diagnostics"]["visible_text_chars"] == 539
    assert html["diagnostics"]["script_chars"] == 41200


class TestNoContentLogSeverity:
    """A stored-but-empty document is only a warning when nothing says why.

    The production log carried one ``No indexable content ...`` warning per
    small icon (dozens per run). Those rows are deliberate skips and carry
    their reason in ``status_detail``; logging them at WARNING buried the
    documents that genuinely had nothing to index and no explanation.
    """

    def test_recorded_outcomes_are_not_warnings(self):
        from database.services.contents_db_service import _empty_content_is_recorded

        assert _empty_content_is_recorded("skipped", "too_small") is True
        assert _empty_content_is_recorded("processed", "no extractable text") is True
        assert _empty_content_is_recorded(
            "processed", "no extractable text: svg_has_no_text_elements"
        ) is True
        assert _empty_content_is_recorded("unsupported", "Unsupported file type") is True

    def test_unexplained_and_failed_rows_stay_warnings(self):
        from database.services.contents_db_service import _empty_content_is_recorded

        assert _empty_content_is_recorded("failed", "boom") is False
        assert _empty_content_is_recorded("partially_processed", "1 of 2 pages") is False
        assert _empty_content_is_recorded("processed", None) is False
        assert _empty_content_is_recorded("discovered", None) is False
