"""The shared list export: one endpoint, the view's rows, chosen columns.

The table toolbar offers "Export"; these tests hold the server side of that
promise - the allowlist (a column name is never trusted from the request),
the formats, and the fact that the export answers to the same search and
sort the table on screen shows.
"""

from __future__ import annotations

import io

import jinja2
import pytest

from core.frontend.component_audit import PROJECT_ROOT


# ---------------------------------------------------------------------------
# The toolbar component
# ---------------------------------------------------------------------------

class TestTheExportMenu:
    @pytest.fixture()
    def render(self):
        environment = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(PROJECT_ROOT / "templates")),
            autoescape=True)
        environment.globals["_"] = lambda text: text

        def render_source(source: str) -> str:
            return environment.from_string(source).render()

        return render_source

    def test_the_menu_offers_the_table_columns_and_the_formats(self, render):
        out = render(
            "{% from 'components/table.html' import export_menu %}"
            "{{ export_menu('words', [{'key': 'word', 'label': _('Word')},"
            " {'kind': 'actions'}],"
            " extra_columns=[{'key': 'id', 'label': _('Id')}]) }}")
        assert 'data-export-interface="words"' in out
        assert 'data-export-column="word"' in out
        assert 'data-export-column="id"' in out
        assert 'data-export-download="csv"' in out
        assert 'data-export-download="xlsx"' in out
        assert 'data-export-print' in out
        assert 'data-export-columns-all' in out
        assert 'data-export-columns-none' in out
        # A column with no key (the Actions column) is not a database column
        # and is not offered.
        assert 'data-export-column="None"' not in out

    def test_the_menu_is_the_only_place_the_download_is_built(self):
        """The page templates never build an export URL; the menu and the
        shared script do."""
        text = (PROJECT_ROOT / "static/js/modules/ui/table-export.js").read_text(
            encoding="utf-8")
        assert "/api/export/" in text
        assert "start_position" in text, "a page coordinate leaked into the export"
        assert "chooseExportDestination" in text and "saveExportBlob" in text
        assert "baseParams.forEach((value, key) => params.set(key, value))" in text
        assert "window.location.href = exportUrl" not in text

    def test_document_panel_offers_shared_batch_content_and_original_exports(self, render):
        out = render(
            "{% from 'components/documents_panel.html' import documents_panel %}"
            "{{ documents_panel() }}")
        assert 'data-panel-export-toolbar' in out
        assert 'data-bulk-file-export="text"' in out
        assert 'data-bulk-file-export="originals"' in out
        script = (PROJECT_ROOT / "static/js/modules/ui/documents-panel-exports.js").read_text(
            encoding="utf-8")
        assert "exportSelectedFiles" in script
        assert ".file-checkbox:checked" in script


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.usefixtures("app")


class TestTheExportEndpoint:
    def test_unknown_interface_is_refused(self, admin_client):
        resp = admin_client.get("/api/export/nobody_knows_this")
        assert resp.status_code == 404

    def test_unknown_column_is_refused(self, admin_client):
        resp = admin_client.get("/api/export/words?columns=word,secret_column")
        assert resp.status_code == 400
        assert b"secret_column" in resp.data

    def test_unknown_format_is_refused(self, admin_client):
        resp = admin_client.get("/api/export/words?format=parquet")
        assert resp.status_code == 400

    def test_csv_export_answers_with_the_chosen_columns(self, admin_client):
        resp = admin_client.get("/api/export/words?format=csv&columns=id,word")
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        body = resp.get_data(as_text=True)
        header = body.lstrip("\ufeff").splitlines()[0]
        assert header == "Id,Word"
        assert body.count("\n") >= 1

    def test_default_columns_are_the_table_view(self, admin_client):
        resp = admin_client.get("/api/export/words?format=csv")
        assert resp.status_code == 200
        header = resp.get_data(as_text=True).lstrip("\ufeff").splitlines()[0]
        assert header == "Id,Word,Usage Count,Status"

    def test_xlsx_export_is_a_workbook(self, admin_client):
        resp = admin_client.get("/api/export/words?format=xlsx&columns=word,usage_count")
        assert resp.status_code == 200
        assert "spreadsheetml" in (resp.mimetype or "")
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(resp.get_data()))
        sheet = workbook.active
        assert [cell.value for cell in sheet[1]] == ["Word", "Usage Count"]

    def test_geolocation_export_unknown_column_is_refused(self, admin_client):
        resp = admin_client.get(
            "/api/export/geolocation?format=xlsx&columns=coordinates,not_a_column")
        assert resp.status_code == 400
        assert b"not_a_column" in resp.data

    def test_geolocation_export_xlsx_honors_chosen_columns(self, admin_client, monkeypatch):
        from Api.routes import exports

        # The export handler reads its rows from the registry entry, which holds
        # the function itself; patching the module attribute would leave the
        # spec pointing at the original (and query the database). Same
        # convention as the words/categories exports in this file.
        monkeypatch.setitem(exports.EXPORT_SPECS["geolocation"], "rows", lambda: [{
            "id": 1,
            "coordinates": "40.712800°N, 74.006000°W",
            "latitude": 40.7128,
            "longitude": -74.006,
            "name": "photo.jpg",
            "type": ".jpg",
            "path": r"C:\data\photo.jpg",
        }])
        resp = admin_client.get(
            "/api/export/geolocation?format=xlsx&columns=coordinates,name,type,path")
        assert resp.status_code == 200
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(resp.get_data()))
        sheet = workbook.active
        assert [cell.value for cell in sheet[1]] == [
            "Coordinates", "File Name", "Type", "Path"]
        assert sheet[2][0].value == "40.712800°N, 74.006000°W"
        assert sheet[2][1].value == "photo.jpg"

    def test_categories_export_carries_the_tables_own_keys(self, admin_client):
        resp = admin_client.get("/api/export/categories?format=csv&columns=name,files,words")
        assert resp.status_code == 200
        header = resp.get_data(as_text=True).lstrip("\ufeff").splitlines()[0]
        assert header == "Category Name,Files,Words"

    def test_category_export_reads_beyond_the_interactive_200_row_page(
            self, admin_client, monkeypatch):
        from Api.routes import exports

        def paged_categories(*, page, per_page, known_total=None, **_kwargs):
            assert per_page == 200
            assert known_total == (None if page == 1 else 250)
            start = (page - 1) * per_page
            stop = min(start + per_page, 250)
            return ([{"id": i + 1, "name": f"cat-{i + 1}",
                      "file_count": 0, "word_count": 0}
                     for i in range(start, stop)], 250)

        monkeypatch.setattr(exports, "get_categories_paged", paged_categories)
        resp = admin_client.get("/api/export/categories?format=csv&columns=name")
        assert resp.status_code == 200
        rows = resp.get_data(as_text=True).lstrip("\ufeff").splitlines()
        assert len(rows) == 251
        assert rows[-1] == "cat-250"

    def test_missing_category_export_page_is_refused_not_silently_truncated(
            self, admin_client, monkeypatch):
        from Api.routes import exports

        def incomplete_categories(*, page, **_kwargs):
            if page == 1:
                return ([{"id": i, "name": f"cat-{i}",
                          "file_count": 0, "word_count": 0}
                         for i in range(200)], 250)
            return [], 0  # the underlying paged reader reports a failed batch as empty

        monkeypatch.setattr(exports, "get_categories_paged", incomplete_categories)
        response = admin_client.get("/api/export/categories?format=csv&columns=name")
        assert response.status_code == 409
        assert response.get_json()["code"] == "export_incomplete"
        assert "Content-Disposition" not in response.headers

    def test_category_words_export_reads_all_pages(self, admin_client, monkeypatch):
        from Api.routes import exports

        def paged_words(category_id, *, page, per_page, known_total=None, **_kwargs):
            assert category_id == 7
            assert per_page == 200
            assert known_total == (None if page == 1 else 225)
            start = (page - 1) * per_page
            stop = min(start + per_page, 225)
            return ([{"id": i + 1, "word": f"word-{i + 1}", "usage_count": 1}
                     for i in range(start, stop)], 225)

        monkeypatch.setattr(exports, "get_words_by_category_paged", paged_words)
        resp = admin_client.get(
            "/api/export/category_words?format=csv&category_id=7&columns=word")
        assert resp.status_code == 200
        rows = resp.get_data(as_text=True).lstrip("\ufeff").splitlines()
        assert len(rows) == 226
        assert rows[-1] == "word-225"

    def test_sources_export_searches_like_the_list(self, admin_client):
        resp = admin_client.get("/api/export/sources?format=csv&columns=name,documents&search=zzz_no_such_source")
        assert resp.status_code == 200
        rows = resp.get_data(as_text=True).lstrip("\ufeff").splitlines()
        assert rows[0] == "Name,Documents"
        assert len(rows) == 1, "a search the list would not match leaked into the export"

    def test_category_words_export_needs_its_category(self, admin_client):
        resp = admin_client.get("/api/export/category_words?format=csv")
        assert resp.status_code == 400
        assert b"category_id" in resp.data

    def test_an_explicit_empty_column_selection_is_refused(self, admin_client):
        for selection in ("", ",,", " , "):
            resp = admin_client.get(f"/api/export/words?format=csv&columns={selection}")
            assert resp.status_code == 400
            assert b"at least one" in resp.data

    def test_over_limit_exports_are_refused_not_truncated(self, admin_client, monkeypatch):
        from Api.routes import exports

        monkeypatch.setattr(exports, "MAX_EXPORT_ROWS", 1)
        monkeypatch.setitem(exports.EXPORT_SPECS["words"], "rows", lambda: [
            {"id": 1, "word": "one"}, {"id": 2, "word": "two"},
        ])
        resp = admin_client.get("/api/export/words?format=csv")
        assert resp.status_code == 413
        assert resp.get_json()["code"] == "export_limit_exceeded"
        assert resp.get_json()["max_rows"] == 1
        assert resp.get_json()["observed_at_least"] == 2
        assert "Content-Disposition" not in resp.headers

    def test_spreadsheet_exports_neutralize_formula_cells(self, admin_client, monkeypatch):
        from Api.routes import exports
        from openpyxl import load_workbook

        monkeypatch.setitem(exports.EXPORT_SPECS["words"], "rows", lambda: [
            {"id": 1, "word": '=HYPERLINK("https://invalid")'},
        ])
        csv_response = admin_client.get(
            "/api/export/words?format=csv&columns=word&filename=safe.csv")
        assert csv_response.status_code == 200
        assert "'=HYPERLINK" in csv_response.get_data(as_text=True)

        xlsx_response = admin_client.get(
            "/api/export/words?format=xlsx&columns=word&filename=safe.xlsx")
        workbook = load_workbook(io.BytesIO(xlsx_response.get_data()), data_only=False)
        cell = workbook.active["A2"]
        assert cell.value.startswith("'=HYPERLINK")
        assert cell.data_type == "s"

    def test_export_filename_is_sanitized_and_matches_the_format(self, admin_client):
        resp = admin_client.get(
            "/api/export/words?format=csv&filename=..%2F..%2Fsensitive.xlsx&columns=word")
        assert resp.status_code == 200
        assert resp.headers["Content-Disposition"].endswith('filename="sensitive.csv"')

    def test_invalid_panel_identity_does_not_broaden_the_file_export(self, admin_client):
        resp = admin_client.get("/api/export/file_library?category_id=not-an-id&format=csv")
        assert resp.status_code == 400
        assert b"category_id" in resp.data

    def test_sides_export_answers_the_sort_the_headers_use(self, admin_client):
        resp = admin_client.get("/api/export/sides?format=csv&columns=name,created&sort=name&order=asc")
        assert resp.status_code == 200
        assert resp.get_data(as_text=True).lstrip("\ufeff").splitlines()[0] == "Name,Created"
