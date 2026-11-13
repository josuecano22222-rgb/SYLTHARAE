"""The archives page's exports must be usable after the download.

Three gaps were measured on the File Management and Analysis page:

* the details export could produce CSV and XLSX only, so a script had to parse
  a spreadsheet to get at the same allowlisted columns;
* the bulk ZIP (extracted text or original files) carried no record of *what*
  it contained - which file each archive name came from, from which source, on
  which date, and why something was left out;
* the export dialog chain (a name prompt, then a save-location picker) stood
  between the button and the file.

The first two are pinned here against the real schema. The third is a client
concern and is verified in the browser, not here.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from tests.integration._seed import connect, document, side, source

pytestmark = pytest.mark.integration


@pytest.fixture()
def two_files(pg_db, tmp_path):
    """Two file rows whose original bytes really exist on disk."""
    conn = connect(pg_db)
    created = {"paths": []}
    try:
        with conn, conn.cursor() as cur:
            source_id = source(cur, name="export-src")
            side_id = side(cur, name="export-side")
            first = tmp_path / "report.txt"
            first.write_text("exported report body", encoding="utf-8")
            second = tmp_path / "ledger.txt"
            second.write_bytes(b"ledger bytes")
            for path, name in ((first, "report.txt"), (second, "ledger.txt")):
                path_id, _hash, _ctx = document(
                    cur, source_id=source_id, side_id=side_id,
                    text=f"{name} body", file_name=name, file_type="txt")
                cur.execute("UPDATE paths SET file_path = %s, file_size = %s WHERE id = %s",
                            (str(path), path.stat().st_size, path_id))
                created["paths"].append(path_id)
    finally:
        conn.close()
    yield created
    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM paths WHERE id = ANY(%s)", (created["paths"],))
    finally:
        conn.close()


def test_details_export_can_be_json(admin_client, two_files):
    response = admin_client.post("/api/files/details/export", json={
        "scope": "selected", "file_ids": two_files["paths"], "format": "json",
        "columns": ["id", "name", "type", "source", "side", "date"],
    })
    assert response.status_code == 200, response.get_data(as_text=True)[:200]
    assert response.mimetype == "application/json"
    assert response.headers["Content-Disposition"].split(".")[-1].startswith("json")

    document = json.loads(response.get_data(as_text=True))
    assert document["row_count"] == 2
    assert [column["key"] for column in document["columns"]] == \
        ["id", "name", "type", "source", "side", "date"]
    names = {row["name"] for row in document["rows"]}
    assert names == {"report.txt", "ledger.txt"}
    assert document["rows"][0]["source"] == "export-src"


def test_details_export_refuses_an_unknown_format(admin_client, two_files):
    response = admin_client.post("/api/files/details/export", json={
        "scope": "selected", "file_ids": two_files["paths"], "format": "parquet"})
    assert response.status_code == 400
    assert "csv, excel or json" in response.get_json()["error"]


@pytest.mark.parametrize("mode", ("originals", "text"))
def test_the_bulk_zip_describes_itself(admin_client, two_files, mode):
    response = admin_client.post("/files/export", data={
        "mode": mode, "file_ids": [str(path_id) for path_id in two_files["paths"]]})
    assert response.status_code == 200, response.get_data(as_text=True)[:300]

    with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
        names = archive.namelist()
        assert "_manifest.tsv" in names
        manifest = archive.read("_manifest.tsv").decode("utf-8")
        lines = [line for line in manifest.splitlines() if line and not line.startswith("#")]
        header = lines[0].split("\t")
        assert header == ["file_id", "file_name", "source", "date", "archive_name",
                          "status", "reason"]
        assert len(lines) == 3, manifest       # header + two files
        rows = [dict(zip(header, line.split("\t"))) for line in lines[1:]]
        assert {row["file_name"] for row in rows} == {"report.txt", "ledger.txt"}
        assert all(row["status"] == "included" for row in rows)
        assert all(row["source"] == "export-src" for row in rows)
        # Every manifest entry names a member that is actually in the archive.
        for row in rows:
            assert row["archive_name"] in names
        assert "mode\t%s" % mode in manifest
        assert "requested\t2" in manifest


def test_the_bulk_zip_names_a_file_it_could_not_include(admin_client, two_files, pg_db,
                                                      tmp_path):
    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE paths SET file_path = %s WHERE id = %s",
                        (str(tmp_path / "gone.txt"), two_files["paths"][1]))
    finally:
        conn.close()

    response = admin_client.post("/files/export", data={
        "mode": "originals", "file_ids": [str(path_id) for path_id in two_files["paths"]]})
    assert response.status_code == 200
    assert response.headers["X-Export-Included"] == "1"
    assert response.headers["X-Export-Skipped"] == "1"

    with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
        manifest = archive.read("_manifest.tsv").decode("utf-8")
        # The record keeps its stored name; what must be reported is that this
        # file was not included, and why.
        assert "not included" in manifest
        assert "missing-on-disk" in manifest
        assert str(two_files["paths"][1]) in manifest
        assert "_not_included.txt" in archive.namelist()
