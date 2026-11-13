"""The interface can see the drift, and one call closes it.

The workbook is authoritative, which is only true if somebody or something
notices when the database has fallen behind. These tests drive the two routes
that answer that question and the job that does the work - through the real
Flask app and the real PostgreSQL - rather than the service in isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from services.jobs import job_state
from services.jobs.manager import JobManager

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from apps.importing.validators import workbook_rules as rules  # noqa: E402

pytestmark = pytest.mark.integration

WORKBOOK = ROOT / "classification" / "domain_data.xlsx"


@pytest.fixture(scope="module")
def workbook():
    return rules.read(str(WORKBOOK))


def test_the_status_route_reports_what_the_workbook_holds(admin_client, workbook):
    resp = admin_client.get("/api/taxonomy/status")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    status = resp.get_json()["status"]
    assert status["data_file"] == "domain_data.xlsx"
    assert len(status["fingerprint"]) == 64
    assert status["layout"] == "two-column"
    assert status["workbook"]["categories"] == len(workbook.categories)
    assert status["workbook"]["terms"] == workbook.total_terms()
    assert status["workbook"]["keywords"] == workbook.total_keywords()
    assert isinstance(status["in_sync"], bool)
    assert isinstance(status["planned_changes"], int)


def test_a_path_is_refused_and_not_echoed(admin_client):
    resp = admin_client.get("/api/taxonomy/status?data_file=../etc/passwd")
    assert resp.status_code == 400, resp.get_data(as_text=True)
    message = resp.get_json()["error"]["message"]
    assert "Enter only the file name" in message
    assert "etc/passwd" not in message


def test_a_missing_workbook_is_refused_with_the_place_to_put_it(admin_client):
    resp = admin_client.post("/api/taxonomy/sync",
                             json={"data_file": "definitely-missing.xlsx"})
    assert resp.status_code == 400
    assert "Data file not found" in resp.get_json()["error"]["message"]


def test_applying_through_the_route_brings_the_database_into_sync(admin_client, workbook):
    """The button the interface needs: plan, write, then report in sync.

    Runs the whole committed workbook, because a partial one would prove
    nothing about the file the operator actually edits.
    """
    before = admin_client.get("/api/taxonomy/status").get_json()["status"]

    resp = admin_client.post("/api/taxonomy/sync",
                             json={"sync": True, "apply": True})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()["result"]
    assert result["success"] is True
    assert result["applied"] is True
    assert result["has_snapshot"] is True, "an automatic write must be reversible"
    written = result["stats"]["written"]
    assert written["categories_created"] >= 0

    after = admin_client.get("/api/taxonomy/status").get_json()["status"]
    assert after["planned_changes"] == 0
    assert after["in_sync"] is True
    if before["in_sync"] is False:
        assert written["categories_created"] > 0 or written["terms_linked"] > 0


def test_a_second_apply_reports_nothing_to_do(admin_client):
    admin_client.post("/api/taxonomy/sync", json={"sync": True, "apply": True})
    resp = admin_client.post("/api/taxonomy/sync", json={"sync": True, "apply": True})
    result = resp.get_json()["result"]
    assert result["applied"] is False
    assert result["stats"]["reason"] == "nothing to do"


def test_a_broken_workbook_is_refused_by_the_route_without_writing(admin_client, tmp_path,
                                                                  monkeypatch):
    """The rule check happens before anything is written, and the cell is named."""
    import openpyxl

    from services.importing import taxonomy_sync_service as service_module

    broken_dir = tmp_path / "data"
    broken_dir.mkdir()
    # A name the search paths cannot find anywhere else: the point is that the
    # route reads *this* file and refuses it, not the tracked one.
    copy = broken_dir / "broken_data.xlsx"
    copy.write_bytes(WORKBOOK.read_bytes())
    book = openpyxl.load_workbook(copy)
    book["Networks"].cell(row=2, column=1, value="Load balancing")
    book.save(copy)

    original = service_module.TaxonomySyncService.DEFAULT_SEARCH_PATHS
    monkeypatch.setattr(service_module.TaxonomySyncService, "DEFAULT_SEARCH_PATHS",
                        (str(broken_dir),))
    resp = admin_client.post("/api/taxonomy/sync",
                             json={"sync": True, "apply": True,
                                   "data_file": "broken_data.xlsx"})
    monkeypatch.setattr(service_module.TaxonomySyncService, "DEFAULT_SEARCH_PATHS", original)

    assert resp.status_code == 409, resp.get_data(as_text=True)
    error = resp.get_json()["error"]
    assert error["code"] == "TAXONOMY_INVALID"
    assert "Networks!A2" in error["message"]


def test_the_sync_job_type_runs_and_reports_through_the_job_manager(pg_db):
    """The job the scheduler will fire: same service, same result shape."""
    manager = JobManager(synchronous=True)
    job = manager.create_job("taxonomy_sync", source="t",
                             options={"data_file": "domain_data.xlsx", "apply": False})
    stored = manager.get(job["job_id"])
    assert stored["status"] == job_state.COMPLETED, stored["errors"]
    stats = stored["stats"]
    assert stats["data_file"] == "domain_data.xlsx"
    assert stats["workbook"]["categories"] == 56
    assert stats["plan"] is not None
    assert stats["written"] == {}, "a dry run wrote to the database"
