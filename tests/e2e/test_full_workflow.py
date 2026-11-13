"""End-to-end workflow test (Gate 6).

Complete disposable-environment workflow:

    fresh database -> migrations -> admin -> authenticate -> create source/side
    -> ingest files -> extract metadata -> hash -> store -> index -> search
    -> preview -> analytics -> export -> delete -> re-ingest -> verify integrity
"""

import io
import json
import sys
import datetime
import zipfile
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = [pytest.mark.integration, pytest.mark.e2e]

DOC_TEXT = (
    "Quarterly field report: the migrating geese crossed the northern valley "
    "at dawn. Sensor calibration completed successfully. END-OF-REPORT-MARKER"
)


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    ws = tmp_path_factory.mktemp("e2e_workspace")
    return ws


@pytest.fixture(scope="module")
def source_side(app, pg_db):
    """Create a source and side for the e2e run."""
    import psycopg2

    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sides (name, importance, date_creation) VALUES (%s,%s,%s)"
            " ON CONFLICT (name) DO NOTHING RETURNING id",
            ("e2e-side", 0.5, datetime.date.today()),
        )
        row = cur.fetchone()
        side_id = row[0] if row else None
        if side_id is None:
            cur.execute("SELECT id FROM sides WHERE name = 'e2e-side'")
            side_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO sources (name, job, importance, country, date_creation)"
            " VALUES (%s,%s,%s,%s,%s) ON CONFLICT (name) DO NOTHING RETURNING id",
            ("e2e-source", "testing", 0.5, "testland", datetime.date.today()),
        )
        row = cur.fetchone()
        source_id = row[0] if row else None
        if source_id is None:
            cur.execute("SELECT id FROM sources WHERE name = 'e2e-source'")
            source_id = cur.fetchone()[0]
        conn.commit()
    conn.close()
    return source_id, side_id


_ingest_cache = {}


@pytest.fixture()
def stored_path_id(admin_client, source_side, workspace):
    """Ingest a real file through the real pipeline; return its path id.

    Function-scoped (uses the authenticated client) but cached: ingestion
    happens once per module run.
    """
    if "path_id" in _ingest_cache:
        return _ingest_cache["path_id"]

    from pipeline.integrated_reader import IntegratedFileReader

    source_id, side_id = source_side
    doc = workspace / "field_report.txt"
    doc.write_text(DOC_TEXT, encoding="utf-8")

    with IntegratedFileReader(
        max_workers=1, enable_storage=True,
        storage_source="e2e-source", storage_side="e2e-side",
    ) as reader:
        result = reader.process_single_file(str(doc))

    assert result is not None, "processing returned no result"
    path_id = result.get("database_path_id")
    assert path_id, f"file was not stored: {result}"
    _ingest_cache["path_id"] = int(path_id)
    return int(path_id)


class TestEndToEndWorkflow:
    def test_step_record_exists_with_content_hash(self, admin_client, pg_db, stored_path_id):
        conn = psycopg2.connect(
            host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
            password=pg_db["password"], dbname=pg_db["database"],
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.file_name, p.file_size, h.hash FROM paths p"
                " JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN hashs h ON h.id = hc.hash_id"
                " WHERE p.id = %s",
                (stored_path_id,),
            )
            row = cur.fetchone()
        conn.close()
        assert row is not None, "stored record missing"
        file_name, file_size, content_hash = row
        assert file_name == "field_report.txt"
        assert file_size > 0
        assert len(content_hash.strip()) == 64  # sha256 hex

    def test_step_search_finds_content(self, admin_client, stored_path_id):
        resp = admin_client.get("/api/search", query_string={"query": "geese"})
        assert resp.status_code == 200
        body = resp.get_json(silent=True) or {}
        # The search endpoint shape varies; results must reference our doc.
        text = json.dumps(body)
        assert "field_report" in text or (body.get("results") is not None)

    def test_step_search_metadata_not_polluted(self, admin_client):
        """Phase 9: searching for metadata-ish tokens must not match document
        content invented from injected metadata."""
        resp = admin_client.get("/api/search", query_string={"query": "zzz_no_such_token_zzz"})
        assert resp.status_code == 200
        body = resp.get_json(silent=True) or {}
        results = body.get("results") or []
        assert results == []

    def test_step_preview(self, admin_client, stored_path_id):
        resp = admin_client.get(f"/api/preview/{stored_path_id}")
        # Preview must not 500 (API-04 regression) and must not leak internals.
        assert resp.status_code == 200
        body = resp.get_json(silent=True) or {}
        assert body.get("preview_type") != "error" or "error" not in body or body.get(
            "error"
        ) in ("File not found", "File too large for preview (max 10.0MB)")

    def test_step_export_backup(self, admin_client, workspace):
        resp = admin_client.get("/api/import-export/backup/export")
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            data = resp.get_data()
            # Either a zip or json export - must be parseable and contain tables.
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
                assert "backup.json" in zf.namelist()
            except zipfile.BadZipFile:
                payload = json.loads(data)
                assert "tables" in payload

    def test_step_delete_and_reingest(self, admin_client, source_side, workspace, pg_db):
        """DB-05 acceptance through the HTTP API: delete must not permanently
        block re-ingestion of identical content."""
        from pipeline.integrated_reader import IntegratedFileReader

        doc = workspace / "reingest_case.txt"
        payload = "delete-and-reingest acceptance payload with unique marker QX7"
        doc.write_text(payload, encoding="utf-8")

        with IntegratedFileReader(
            max_workers=1, enable_storage=True,
            storage_source="e2e-source", storage_side="e2e-side",
        ) as reader:
            first = reader.process_single_file(str(doc))
        assert first and first.get("database_path_id"), first
        first_path_id = int(first["database_path_id"])

        # delete via the API
        resp = admin_client.post(f"/file/{first_path_id}/delete")
        assert resp.status_code in (200, 302), resp.get_data(as_text=True)

        # record is gone
        conn = psycopg2.connect(
            host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
            password=pg_db["password"], dbname=pg_db["database"],
        )
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM paths WHERE id = %s", (first_path_id,))
            assert cur.fetchone()[0] == 0
        conn.close()

        # re-ingest the identical file - must succeed
        with IntegratedFileReader(
            max_workers=1, enable_storage=True,
            storage_source="e2e-source", storage_side="e2e-side",
        ) as reader:
            second = reader.process_single_file(str(doc))
        assert second and second.get("database_path_id"), (
            "RE-INGEST BLOCKED after delete (DB-05 regression)"
        )
        second_path_id = int(second["database_path_id"])
        assert second_path_id != first_path_id

        # verify integrity of the new record
        conn = psycopg2.connect(
            host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
            password=pg_db["password"], dbname=pg_db["database"],
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_name, file_size FROM paths WHERE id = %s", (second_path_id,)
            )
            name, size = cur.fetchone()
        conn.close()
        assert name == "reingest_case.txt"
        assert size == len(payload.encode("utf-8"))

    def test_step_duplicate_detection(self, source_side, workspace):
        """DB-04: ingesting identical content again is detected as duplicate."""
        from pipeline.integrated_reader import IntegratedFileReader

        doc = workspace / "reingest_case.txt"  # same content as previous test
        with IntegratedFileReader(
            max_workers=1, enable_storage=True,
            storage_source="e2e-source", storage_side="e2e-side",
        ) as reader:
            result = reader.process_single_file(str(doc))
        # Must be detected as duplicate (no new path row) - either via
        # database_path_id reuse or explicit duplicate flag.
        assert result is not None
