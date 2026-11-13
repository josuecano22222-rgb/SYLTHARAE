"""The search export is produced from the query, never from the browser.

The old endpoint wrote whatever rows the browser posted. That is what these
tests exist to prevent from coming back: a client that sends a result set is
refused, a query definition is honoured, and the file that leaves the server is
built from the database's own answer.

The unit half checks the request model and the tallying; the served half checks
the endpoint with an authenticated client.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

from Api.services import search_export
from Api.services.search_export import (
    EXPORT_FORMATS,
    EXPORT_SCOPES,
    MAX_ROWS,
    ExportRequestError,
    SearchExportRequest,
)

DEFINITION = {"query": "agreement", "export_scope": "filtered", "format": "csv"}


class TestTheBrowserCannotSupplyTheRows:
    @pytest.mark.parametrize("key", ("results", "rows", "records", "items", "data"))
    def test_a_result_payload_is_refused_and_explained(self, key):
        with pytest.raises(ExportRequestError) as error:
            search_export.parse(dict(DEFINITION, **{key: [{"id": 1}]}))
        message = str(error.value)
        assert "no longer accepts results from the browser" in message
        assert key in message

    def test_an_unknown_field_is_refused_rather_than_ignored(self):
        with pytest.raises(ExportRequestError) as error:
            search_export.parse(dict(DEFINITION, sneaky="payload"))
        assert "Unknown export parameters" in str(error.value)

    def test_a_definition_is_the_only_input(self):
        request = search_export.parse(dict(DEFINITION, source_ids=[3, "4"],
                                           date_from="2026-01-01"))
        assert request.source_ids == (3, 4)
        assert request.date_from == "2026-01-01"
        assert request.definition["query"] == "agreement"


class TestTheRequestModel:
    def test_the_scope_must_be_named(self):
        with pytest.raises(ExportRequestError) as error:
            search_export.parse(dict(DEFINITION, export_scope="everything"))
        message = str(error.value)
        assert "Unknown export_scope" in message
        for scope in EXPORT_SCOPES:
            assert scope in message

    def test_analyst_scope_is_not_overloaded_with_export_scope(self):
        with pytest.raises(ExportRequestError) as error:
            search_export.parse(dict(DEFINITION, scope="filtered"))
        assert "Unknown export parameters" in str(error.value)

        request = search_export.parse(dict(
            DEFINITION,
            analyst_scope="all",
            file_type=["pdf", "docx"],
            status=["Read", "Unread"],
        ))
        assert request.scope == "filtered"
        assert request.analyst_scope == "all"
        assert request.file_types == ("pdf", "docx")
        assert request.file_statuses == ("Read", "Unread")

    def test_empty_status_selection_is_distinct_from_no_status_filter(self):
        assert search_export.parse(DEFINITION).file_statuses is None
        assert search_export.parse(dict(DEFINITION, status=[])).file_statuses == ()
        with pytest.raises(ExportRequestError):
            search_export.parse(dict(DEFINITION, status=["Read", "unknown"]))

    def test_the_format_must_be_known(self):
        with pytest.raises(ExportRequestError):
            search_export.parse(dict(DEFINITION, format="pdf"))
        for export_format in EXPORT_FORMATS:
            assert search_export.parse(dict(DEFINITION, format=export_format))

    def test_dataset_scope_drops_the_query_and_keeps_the_filters(self):
        request = search_export.parse(dict(DEFINITION, export_scope="dataset",
                                           source_ids=[7]))
        assert request.bounded_query == ""
        assert request.source_ids == (7,)
        assert request.definition["query"] == ""

    def test_page_scope_exports_the_page_being_read(self):
        request = search_export.parse(dict(DEFINITION, export_scope="page", page=3,
                                           per_page=25))
        assert request.limit_offset == (25, 50)
        assert request.definition["query"] == "agreement"

    def test_the_whole_result_set_is_fetched_in_rounds(self):
        request = search_export.parse(DEFINITION)
        limit, offset = request.limit_offset
        assert offset == 0
        assert 0 < limit <= MAX_ROWS

    def test_a_bad_id_or_date_is_refused(self):
        with pytest.raises(ExportRequestError):
            search_export.parse(dict(DEFINITION, source_ids=["not-a-number"]))
        with pytest.raises(ExportRequestError):
            search_export.parse(dict(DEFINITION, date_from="01/01/2026"))

    def test_a_bad_sort_order_is_refused(self):
        with pytest.raises(ExportRequestError):
            search_export.parse(dict(DEFINITION, sort_order="sideways"))

    def test_the_filename_is_stable_and_harmless(self):
        request = search_export.parse(dict(DEFINITION, filename="../../etc passwd"))
        name = search_export.suggested_filename(request)
        assert "/" not in name and " " not in name and ".." not in name


class TestTheRowsAreTheServers:
    def test_tuples_and_dictionaries_arrive_in_one_shape(self):
        rows = search_export.normalise([
            {"id": 1, "file_name": "a.pdf", "categories": ["smart"],
             "analyst_categories": ["reviewed"],
             "snippet": "…agreement was signed…"},
            (2, "b.docx", "docx", 12, None, "Read",
             "S", 1, "Side", 2, 0.5, None, "…"),
        ])
        assert len(rows) == 2
        for row in rows:
            assert list(row) == list(search_export.COLUMNS)
        assert rows[0]["id"] == 1
        assert rows[0]["smart_categories"] == "smart"
        assert rows[0]["analyst_categories"] == "reviewed"
        assert rows[1]["id"] == 2

    def test_each_export_format_uses_the_same_published_columns(self):
        rows = search_export.normalise([{
            "id": 7, "file_name": "record.txt", "categories": ["smart"],
            "analyst_categories": ["reviewed"], "snippet": "matched text",
        }])

        for format_name in ("csv", "excel", "json"):
            request = search_export.parse(dict(DEFINITION, format=format_name))
            result = search_export.SearchExportResult(request=request, rows=rows, total=1)
            output, _mimetype, _extension = search_export.export_bytes(result)

            if format_name == "csv":
                header = next(csv.reader(io.StringIO(
                    output.getvalue().decode("utf-8-sig"))))
            elif format_name == "excel":
                from openpyxl import load_workbook

                workbook = load_workbook(io.BytesIO(output.getvalue()), read_only=True)
                sheet = workbook.active
                header = list(next(sheet.iter_rows(values_only=True)))
                workbook.close()
            else:
                header = list(json.loads(output.getvalue().decode("utf-8"))[
                    "results"][0])

            assert header == list(search_export.COLUMNS), format_name

    def test_a_datetime_is_serialised_once(self):
        from datetime import datetime

        row = search_export.normalise([{"id": 1,
                                        "file_date": datetime(2026, 1, 2, 3, 4)}])[0]
        assert row["file_date"].startswith("2026-01-02T03:04")

    def test_the_tally_is_measured_after_the_query(self, monkeypatch):
        """The response reports what happened, not what was asked for."""
        monkeypatch.setattr(search_export, "MAX_ROWS", 3)
        rounds = []

        def fake_search(request, limit, offset, analyst_scope):
            rounds.append((limit, offset))
            total = 10
            start = offset
            return ([{"id": index} for index in range(start, min(start + limit, total))],
                    total)

        monkeypatch.setattr(search_export, "_search_once", fake_search)
        request = SearchExportRequest(query="x", scope="filtered", format="csv")
        result = search_export.resolve(request, "all")

        assert result.exported == 3
        assert result.total == 10
        assert result.truncated is True, "a capped export has to say that it was capped"
        assert result.headers["X-Export-Truncated"] == "true"
        assert result.headers["X-Export-Rows"] == "3"
        assert result.headers["X-Export-Total"] == "10"

    def test_a_complete_export_is_not_marked_truncated(self, monkeypatch):
        def fake_search(request, limit, offset, analyst_scope):
            if offset == 0:
                return [{"id": index} for index in range(2)], 2
            return [], 2

        monkeypatch.setattr(search_export, "_search_once", fake_search)
        result = search_export.resolve(
            SearchExportRequest(query="x", scope="filtered"), "all")
        assert result.exported == 2
        assert result.truncated is False

    def test_page_scope_never_widens_to_the_whole_set(self, monkeypatch):
        seen = {}

        def fake_search(request, limit, offset, analyst_scope):
            seen["limit"] = limit
            seen["offset"] = offset
            return ([{"id": 1}], 5000)

        monkeypatch.setattr(search_export, "_search_once", fake_search)
        result = search_export.resolve(
            SearchExportRequest(query="x", scope="page", page=2, per_page=10), "all")
        assert seen == {"limit": 10, "offset": 10}
        assert result.exported == 1
        assert result.total == 5000


@pytest.fixture()
def exportable_rows(pg_db):
    """One stored file, so the export has something to export.

    These two tests used to skip on "the disposable database has no records":
    they passed before the per-module reset only because another module had left
    rows behind, which is precisely the cross-module dependency the reset exists
    to remove. A test that needs a row seeds the row.

    Rows are inserted through the same tables ingestion writes (side, source,
    hash, hash_context, path) and deleted afterwards, so the module boundary is
    where it was.
    """
    import datetime

    import psycopg2

    conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"],
                            user=pg_db["user"], password=pg_db["password"],
                            dbname=pg_db["database"])
    today = datetime.date.today()
    path_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5, %s)"
                " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                ("export_fixture_side", today))
            side_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES (%s, 'fixture', 0.5, 'NL', %s)"
                " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                ("export_fixture_source", today))
            source_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hashs (hash) VALUES (%s)"
                " ON CONFLICT (hash) DO UPDATE SET hash = EXCLUDED.hash RETURNING id",
                ("export-fixture-hash-0001",))
            hash_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                " VALUES (%s, %s, %s)"
                " ON CONFLICT (hash_id, source_id, side_id) DO UPDATE"
                " SET hash_id = EXCLUDED.hash_id RETURNING id",
                (hash_id, source_id, side_id))
            context_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO paths (file_name, file_path, file_size, file_type,"
                " file_status, file_date, date_creation, context_id, processing_status)"
                " VALUES (%s, %s, 32, 'txt', 'Read', %s, CURRENT_DATE, %s, 'processed')"
                " RETURNING id",
                ("export_fixture.txt", "/fixture/export_fixture.txt", today, context_id))
            path_id = cur.fetchone()[0]
        conn.commit()
        yield path_id
    finally:
        if path_id is not None:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM paths WHERE id = %s", (path_id,))
            conn.commit()
        conn.close()


class TestTheServedEndpoint:
    def _post(self, client, payload):
        return client.post("/api/search/export", json=payload)

    def test_a_client_supplied_result_set_is_refused(self, admin_client):
        response = self._post(admin_client, {"results": [{"id": 1}], "format": "csv"})
        assert response.status_code == 400
        body = response.get_json()
        assert body["code"] == "invalid_export_request"
        assert "no longer accepts" in body["error"]

    def test_an_empty_result_set_is_reported_not_fabricated(self, admin_client):
        response = self._post(admin_client, {"query": "zzz-no-such-term-zzz",
                                             "export_scope": "filtered", "format": "csv"})
        assert response.status_code == 400
        assert "no results" in response.get_json()["error"].lower()

    def test_an_authenticated_export_comes_back_with_its_own_counts(
            self, admin_client, exportable_rows):
        response = self._post(admin_client, {"query": "", "export_scope": "dataset",
                                             "format": "json"})
        assert response.status_code == 200, response.get_data(as_text=True)
        payload = json.loads(response.get_data(as_text=True))
        assert response.headers["X-Export-Scope"] == "dataset"
        assert response.headers["X-Export-Format"] == "json"
        assert int(response.headers["X-Export-Rows"]) >= 0
        assert payload is not None

    def test_the_csv_header_is_the_published_column_set(self, admin_client,
                                                        exportable_rows):
        response = self._post(admin_client, {"query": "", "export_scope": "dataset",
                                             "format": "csv"})
        assert response.status_code == 200, response.get_data(as_text=True)
        text = response.get_data(as_text=True)
        first = next(iter(csv.reader(io.StringIO(text))), [])
        assert first, "the CSV response has no header row"
        # The endpoint emits a UTF-8 BOM so spreadsheet applications recognize
        # the encoding; csv.reader keeps it on the first field by design.
        first[0] = first[0].lstrip("\ufeff")
        assert first == list(search_export.COLUMNS), (
            "CSV must have exactly the published columns, in their declared order")

    def test_an_unauthenticated_client_gets_nothing(self, client):
        response = client.post("/api/search/export",
                               json={"query": "", "export_scope": "dataset"})
        assert response.status_code in (302, 401, 403), response.status_code

    def test_the_route_no_longer_documents_a_client_payload(self):
        source = (__import__("pathlib").Path(__file__).resolve().parents[2]
                  / "Api/routes/search.py").read_text(encoding="utf-8")
        assert "results: List of search result dictionaries" not in source
        assert "- results:" not in source
