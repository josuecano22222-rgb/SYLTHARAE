"""Domain import rejections reach the user as reasons, not "Internal error".

Field report (Windows, after step 12): a path typed into the Import Center's
"data file name" box created a job that failed in the worker; the page showed
`Internal error (ERR-20260928-000002)` and the server logged the rejection as
an unhandled exception.
"""
import pytest

from services.jobs import job_state
from services.jobs.manager import JobManager

PATHS = [
    "classification/domain_data.xlsx",
    "classification\\domain_data.xlsx",                     # Windows, relative
    "C:\\Users\\Solo\\syltharae\\classification\\domain_data.xlsx",  # Windows, absolute
    "/etc/passwd",
    "..",
]


def _job_count(admin_client):
    return len(admin_client.get("/api/import/jobs?limit=200").get_json()["jobs"])


@pytest.mark.parametrize("data_file", PATHS)
@pytest.mark.parametrize("url", ["/api/import/jobs", "/api/import/preview"])
def test_a_path_is_refused_with_a_reason_and_no_job(admin_client, url, data_file):
    before = _job_count(admin_client)
    resp = admin_client.post(url, json={"type": "domain_import", "data_file": data_file})
    assert resp.status_code == 400, resp.get_data(as_text=True)
    message = resp.get_json()["error"]["message"]
    assert message.startswith("Enter only the file name (for example domain_data.xlsx), not a path.")
    assert "classification/" in message                      # where the file must be
    assert data_file not in message                          # input is not echoed
    assert "Internal error" not in message
    assert _job_count(admin_client) == before, "a doomed job row was persisted"


@pytest.mark.parametrize("url", ["/api/import/jobs", "/api/import/preview"])
def test_a_missing_file_is_refused_before_a_job_exists(admin_client, url):
    before = _job_count(admin_client)
    resp = admin_client.post(url, json={"type": "domain_import",
                                        "data_file": "definitely-missing.xlsx"})
    assert resp.status_code == 400
    message = resp.get_json()["error"]["message"]
    assert message.startswith("Data file not found. It must be in one of: ")
    assert "definitely-missing" not in message
    assert _job_count(admin_client) == before


def test_the_tracked_default_file_validates(admin_client):
    resp = admin_client.post("/api/import/validate",
                             json={"type": "domain_import", "data_file": "domain_data.xlsx"})
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_a_rejection_inside_the_worker_keeps_its_reason(pg_db, caplog):
    """The file can vanish between request and run: the job still says why."""
    mgr = JobManager(synchronous=True)
    job = mgr.create_job("domain_import", source="t",
                         options={"data_file": "definitely-missing.xlsx"})
    stored = mgr.get(job["job_id"])
    assert stored["status"] == job_state.FAILED
    assert len(stored["errors"]) == 1
    assert stored["errors"][0].startswith("Data file not found.")
    assert not any("Unhandled exception" in r.getMessage() for r in caplog.records)


def test_an_internal_fault_inside_the_worker_is_still_opaque(pg_db, monkeypatch):
    from services.importing import domain_import_service as dis

    def boom(self, request, **_kw):
        raise RuntimeError("secret detail: password=hunter2")

    monkeypatch.setattr(dis.DomainImportService, "run", boom)
    mgr = JobManager(synchronous=True)
    job = mgr.create_job("domain_import", source="t", options={"data_file": "domain_data.xlsx"})
    errors = mgr.get(job["job_id"])["errors"]
    assert len(errors) == 1 and errors[0].startswith("Internal error (ERR-")
    assert "hunter2" not in errors[0]


def test_the_preview_of_the_committed_workbook_counts_what_the_sheets_hold(pg_db):
    """The Import Center's dry run must see the two columns, not one pile.

    A preview that reported the workbook as one undifferentiated list is how
    the term/keyword distinction would be lost without anybody noticing: the
    import would still "succeed". Run through a synchronous job so the preview
    result is read here rather than skipped.
    """
    from apps.importing.validators import workbook_rules as rules

    workbook = rules.read("classification/domain_data.xlsx")
    mgr = JobManager(synchronous=True)
    job = mgr.create_job("domain_import", source="t",
                         options={"data_file": "domain_data.xlsx", "dry_run": True})
    stored = mgr.get(job["job_id"])
    assert stored["status"] == job_state.COMPLETED, stored["errors"]
    stats = stored["stats"] or {}
    assert stats["categories"] == len(workbook.categories)
    assert stats["words"] == workbook.total_terms()
    assert stats["keywords"] == workbook.total_keywords()
