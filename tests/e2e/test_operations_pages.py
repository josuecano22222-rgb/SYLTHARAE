"""Frontend pages render for authenticated users (Operations UI)."""


def test_operations_pages_render(app, admin_client):
    for url, marker in (
        ("/operations/input", "Start Analysis"),
        ("/operations/import", "Import Center"),
        ("/operations/jobs", "Jobs"),
    ):
        resp = admin_client.get(url)
        assert resp.status_code == 200, url
        assert marker.encode() in resp.data, url

    # job detail page renders for any id (loads data via API)
    resp = admin_client.get("/operations/jobs/XYZ123")
    assert resp.status_code == 200


def test_dashboard_has_operations_widget(app, admin_client):
    resp = admin_client.get("/")
    assert resp.status_code == 200
    assert b"operations" in resp.data.lower() or b"Operations" in resp.data


def test_input_options_info_endpoint(app, admin_client, monkeypatch):
    """Regression: the Input page fetches /api/input/options-info on load.

    The route was missing in production (404 on page open). It must return
    real capability facts the page renders.
    """
    monkeypatch.delenv("INGESTION_ROOTS", raising=False)
    resp = admin_client.get("/api/input/options-info")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True
    assert body["ingestion_roots_configured"] is False
    assert body["server_path_import_available"] is False
    assert body["upload_available"] is True

    monkeypatch.setenv("INGESTION_ROOTS", "/tmp/anything")
    body = admin_client.get("/api/input/options-info").get_json()
    assert body["ingestion_roots_configured"] is True
    assert body["server_path_import_available"] is True


def test_upload_url_redirects_to_the_single_ingestion_page(app, admin_client):
    """One interface: the old /upload URL still resolves, to the same page.

    The interface toggle that used to gate /upload still applies - the alias
    either lands on the ingestion page or (when the interface is switched off)
    on the dashboard, exactly as before the merge.
    """
    from settings import get_interface_manager

    response = admin_client.get("/upload", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["Location"]

    followed = admin_client.get("/upload", follow_redirects=True)
    assert followed.status_code == 200

    if get_interface_manager().is_interface_enabled("input_ingestion"):
        assert location.endswith("/operations/input")
        assert b"Start Analysis" in followed.data
    else:
        # Switched off: the gate sends the alias to the dashboard, as before.
        assert "/upload" not in location
        assert b"Start Analysis" not in followed.data


def test_the_ingestion_page_ships_the_single_interface(app, admin_client):
    """The page loads its own controller, stylesheet and endpoint map."""
    html = admin_client.get("/operations/input").get_data(as_text=True)
    assert "js/pages/ingestion-studio-page.js" in html
    assert "css/ingestion.css" in html
    assert "ingestionPageData" in html
    # Both input modes are named on the page, not hidden behind tabs.
    assert "This computer" in html
    assert "Server path" in html
    # The pipeline the engine runs is stated, not offered as dead checkboxes.
    assert "What this pipeline always does" in html


def test_all_three_input_modes_are_wired_in_the_markup(app, admin_client):
    """Single file, whole folder and server path each have a real control.

    The browser-side behaviour (a Windows file dialog, a folder dragged from
    Explorer) cannot be exercised here; what can be proven headlessly is that
    the page ships the controls and the attributes browsers key off, and that
    the same selection reaches the API (tests/integration/
    test_input_upload_pipeline.py feeds it exactly what a Windows browser
    sends).
    """
    html = admin_client.get("/operations/input").get_data(as_text=True)

    # single file: a multi-select file input, plus the modal-less picker button
    assert 'id="fileInput"' in html and "multiple" in html
    # whole folder: webkitdirectory is what makes a picker return a tree, and
    # `directory` is the standards-name some engines look at
    assert 'id="folderInput"' in html
    assert "webkitdirectory" in html and "directory" in html
    # server path: a free-text field, plus the recursive walk toggle
    assert 'id="serverPath"' in html and 'id="recursive"' in html
    # drag and drop is wired for both files and folders
    assert 'id="dropZone"' in html


def test_the_removed_interface_is_gone_and_the_task_api_is_not(app, admin_client):
    """The duplicated page went; the task-manager API it shared a prefix with did not.

    ``/upload/process-path`` was the deleted page's own ingestion entry point
    (a second, differently-shaped pipeline). ``/upload/active-tasks`` belongs to
    Api/task_manager.py, whose tasks the import flows create and the dashboard
    polls - removing it would break live progress, so it must keep answering.
    """
    assert admin_client.post("/upload/process-path", json={}).status_code == 404

    active = admin_client.get("/upload/active-tasks")
    assert active.status_code == 200
    assert active.get_json()["success"] is True


def test_the_sidebar_shows_one_shortcut_per_destination(app, admin_client):
    """One page, one icon: the retired upload shortcut must not come back.

    Consolidating the upload page into /operations/input left its sidebar
    entry behind, so the navigation showed two icons - "Input / Ingestion" and
    the old "Upload Files" - that went to the same page. The duplicate is
    removed and the switch that used to gate it (upload_files) now gates the
    surviving entry, so it still means "does the operator see a way in".

    Counted from the rendered navigation: every data-endpoint the sidebar
    declares must be unique, and ingestion must be declared exactly once.
    """
    import re

    html = admin_client.get("/operations/input").get_data(as_text=True)
    navigation = html.split('class="sidebar-nav"', 1)[1].split("</ul>", 1)[0]

    endpoints = re.findall(r'data-endpoint="([^"]+)"', navigation)
    duplicates = sorted({e for e in endpoints if endpoints.count(e) > 1})
    assert not duplicates, f"the sidebar lists the same destination twice: {duplicates}"
    assert endpoints.count("operations_input_page") == 1

    # The retired entry's icon and label are gone from the navigation, while
    # the retired page's URL still lands on the surviving page.
    assert "bi-cloud-upload" not in navigation
    assert ">Upload Files<" not in navigation
    assert admin_client.get("/upload", follow_redirects=False).headers["Location"].endswith(
        "/operations/input")


def test_the_settings_offer_one_ingestion_switch(app, admin_client):
    """One ingestion interface, and only one.

    The retired core twin ``file_upload`` and the old ``upload_files`` key both
    named this capability, so Settings listed two switches for one page - one of
    which gated nothing at all. The listing now comes from the interface
    registry, which declares exactly one ingestion interface
    (``input_ingestion``); the earlier keys survive only as stored settings
    whose values are folded onto it.
    """
    from core.interfaces import LEGACY_INTERFACE_IDS
    from settings import get_interface_manager

    manager = get_interface_manager()
    listed = manager.get_interfaces_by_domain()
    flat = {name: entry for group in listed.values() for name, entry in group.items()}

    assert "file_upload" not in flat, "the retired duplicate switch is listed again"
    assert "upload_files" not in flat, "the renamed key is not an interface any more"

    entry = flat["input_ingestion"]
    assert entry["name"] == "Input / Ingestion"
    assert entry["endpoint"] == "operations_input_page"
    assert entry["domain"] == "INGEST"

    # The switch still controls the navigation entry and the old /upload alias.
    assert manager.is_interface_enabled_by_endpoint("operations_input_page") == \
        manager.is_interface_enabled("input_ingestion")
    assert manager.is_interface_enabled_by_endpoint("files.upload_page") == \
        manager.is_interface_enabled("input_ingestion")

    # Both earlier keys are declared, so a stored choice reaches the interface
    # that replaced them rather than being silently dropped.
    assert LEGACY_INTERFACE_IDS["upload_files"] == "input_ingestion"
    assert LEGACY_INTERFACE_IDS["file_upload"] == "input_ingestion"


def test_ingestion_batch_print_route_renders_the_dedicated_report(
        app, admin_client, monkeypatch):
    """GET /operations/input/reports/<id> is the live batch dossier."""
    from services.jobs.manager import JobManager

    fake = {
        "job_id": "BATCHPRINT1",
        "job_type": "ingestion",
        "options": {"source": "Field Office", "side": "North", "path": "/data/in"},
        "stats": {
            "files_total": 4,
            "files_processed": 3,
            "files_failed": 1,
            "files_skipped": 0,
            "files_stored": 3,
        },
        "result_summary": {
            "batch_report": {
                "source": "Field Office",
                "side": "North",
                "path_ids": [],
                "type_outcomes": {
                    "pdf": {"total": 3, "completed": 2, "failed": 1},
                    "txt": {"total": 1, "completed": 1},
                },
                "html_path": "already-written.html",
            }
        },
        "errors": [],
        "warnings": [],
    }

    class FakeManager:
        def get(self, job_id):
            return fake if job_id == "BATCHPRINT1" else None

    monkeypatch.setattr(JobManager, "get_instance", classmethod(lambda cls: FakeManager()))
    missing = admin_client.get("/operations/input/reports/no-such-job")
    assert missing.status_code == 404
    resp = admin_client.get("/operations/input/reports/BATCHPRINT1")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    body = resp.get_data(as_text=True)
    assert 'class="ingest-batch"' in body
    assert "Field Office" in body
    assert "North" in body
    assert "Outcome counts" in body
    assert "Files by type" in body
    assert "Categories assigned" in body
    assert "Keywords assigned" in body
    assert "@media print" in body
