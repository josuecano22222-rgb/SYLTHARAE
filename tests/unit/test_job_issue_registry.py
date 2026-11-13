"""Job issue registry: per-file outcomes become job-visible lines."""
from services.jobs.issue_registry import JobIssueRegistry
from pipeline.progress_ledger import (
    OUTCOME_FAILED,
    OUTCOME_RETRYABLE,
    OUTCOME_UNSUPPORTED,
)


def test_failed_outcome_becomes_error_line():
    reg = JobIssueRegistry(error_cap=10, warning_cap=10)
    reg.record_outcome(
        "/data/bad.pdf",
        {"error": "corrupt header"},
        OUTCOME_FAILED,
    )
    errors, warnings = reg.snapshot()
    assert len(errors) == 1
    assert "bad.pdf" in errors[0]
    assert "corrupt" in errors[0]
    assert not warnings


def test_unsupported_is_warning():
    reg = JobIssueRegistry()
    reg.record_outcome(
        "/data/weird.xyz",
        {"error": "unsupported file type"},
        OUTCOME_UNSUPPORTED,
    )
    errors, warnings = reg.snapshot()
    assert not errors
    assert len(warnings) == 1
    assert "unsupported" in warnings[0].lower()


def test_storage_failed_flag():
    reg = JobIssueRegistry()
    reg.record_outcome("/data/a.txt", {"text": "ok"}, OUTCOME_FAILED, storage_failed=True)
    errors, _ = reg.snapshot()
    assert len(errors) == 1
    assert "storage" in errors[0]


def test_merge_into_result_dedupes():
    reg = JobIssueRegistry()
    reg.record_outcome("/x", {"error": "nope"}, OUTCOME_FAILED)

    class R:
        errors = ["existing"]
        warnings = []

    r = R()
    reg.merge_into_result(r)
    assert len(r.errors) == 2
    reg.merge_into_result(r)
    assert len(r.errors) == 2
