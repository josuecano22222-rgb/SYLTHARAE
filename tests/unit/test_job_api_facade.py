"""API facade: job_to_api exposes classification and issue summaries."""
from services.jobs.models import job_to_api


def test_list_shape_includes_issue_counts_and_previews():
    job = {
        "job_id": "ABC",
        "job_type": "ingestion",
        "source": r"C:\data\inbox",
        "options": {"source": "schedules", "side": "primary", "path": r"C:\data\inbox"},
        "errors": ["[failed] a.txt: corrupt"],
        "warnings": ["[retryable] b.pdf: timeout"],
        "stats": {"files_discovered": 2, "files_done": 2},
    }
    payload = job_to_api(job)
    assert payload["classification_source"] == "schedules"
    assert payload["classification_side"] == "primary"
    assert payload["intake_path"] == r"C:\data\inbox"
    assert payload["errors_count"] == 1
    assert payload["warnings_count"] == 1
    assert payload["errors_preview"] == job["errors"]
    assert "errors" not in payload


def test_detail_shape_can_include_full_issue_lists():
    job = {
        "job_id": "ABC",
        "errors": ["e1"],
        "warnings": [],
    }
    payload = job_to_api(job, include_issues=True)
    assert payload["errors"] == ["e1"]
    assert payload["warnings"] == []
