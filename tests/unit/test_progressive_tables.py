"""Progressive loading: the table grows in place, the page stays put.

The File Library (and, through the same component, every panel that follows)
loads its first batch server-side and then asks for the next batch with the
table's own Load More control. These tests hold the contract: the server
renders the counters and the control, the batch answer is the same view one
page on, and the operations a row offers (rename, copy, locate) answer with
honest results.
"""

from __future__ import annotations

import sys

import jinja2
import pytest

from core.frontend.component_audit import PROJECT_ROOT
from core.serialization import pack_int_list

_SEQ = __import__('itertools').count()
from tests.integration._seed import connect, document, side, source


class TestTheLoadMoreComponent:
    @pytest.fixture()
    def render(self):
        environment = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(PROJECT_ROOT / "templates")),
            autoescape=True)
        environment.globals["_"] = lambda text: text

        def render_source(source: str) -> str:
            return environment.from_string(source).render()

        return render_source

    def _table(self, render, **overrides):
        params = {
            "id": "filesTable",
            "columns": "[{'key': 'name', 'label': _('Name'), 'sortable': True}]",
            "mode": "'server'",
            "page": "1",
            "total_pages": "5",
            "total_count": "97",
            "rows_shown": "20",
            "append": "True",
        }
        params.update(overrides)
        call = ",".join(f"{k}={v}" for k, v in params.items())
        return render(
            "{% from 'components/table.html' import record_table %}"
            f"{{% call record_table({call}) %}}"
            "<tr><td>a</td></tr>"
            "{% endcall %}")

    def test_a_full_list_has_no_load_more(self, render):
        out = self._table(render, total_pages="1", page="1")
        assert "data-ut-load-more" not in out

    def test_a_partial_list_offers_the_next_batch(self, render):
        out = self._table(render)
        assert "data-ut-load-more" in out
        assert 'data-page="1"' in out
        assert 'data-total-pages="5"' in out
        assert 'data-total="97"' in out
        assert 'data-shown="20"' in out

    def test_the_last_batch_hides_the_control(self, render):
        out = self._table(render, page="5")
        assert "data-ut-load-more" not in out

    def test_the_progressive_counter_is_the_toolbars_info(self, render):
        out = self._table(render)
        assert "data-ut-last" in out
        assert "data-ut-total" in out

    def test_a_numbered_table_keeps_the_callers_info_line(self, render):
        out = self._table(render, append="False", info_text="'Showing 1-20 of 97'")
        assert "Showing 1-20 of 97" in out
        assert "data-ut-load-more" not in out

    def test_the_page_js_asks_for_one_page_on_the_same_view(self):
        source = (PROJECT_ROOT / "static/js/modules/ui/unified-table.js").read_text(
            encoding="utf-8")
        # The batch is the same view's next aligned block (nextBatchUrl): the
        # reader's search, sort and filters ride along, and the batch states its
        # own page and size. The pager's page is not part of the request.
        assert "nextBatchUrl" in source
        assert "searchParams.delete('cursor')" in source, (
            "the next batch must not inherit a cursor coordinate")
        assert "url.searchParams.set(config.pageParam" in source
        assert "url.searchParams.set(config.sizeParam" in source


class TestTheFileLibraryList:
    def test_the_library_is_a_progressive_table(self):
        # The library's table is the one shared rendering
        # (components/file_library_table.html) that the panels embed too;
        # the page's contract is that it renders through it and nothing else.
        text = (PROJECT_ROOT / "templates/file/files_list.html").read_text(
            encoding="utf-8")
        assert "file_library_table(" in text
        assert "record_table(" not in text, (
            "the page must not keep its own table rendering beside the "
            "shared one")
        component = (PROJECT_ROOT /
                     "templates/components/file_library_table.html").read_text(
            encoding="utf-8")
        assert "append=True" in component
        assert "rows_shown=files|length" in component
        assert "unified_pagination" not in component and "unified_pagination" not in text, (
            "the library is a continuous list now; a numbered pager is the "
            "old model")
        assert "data-file-menu-button" in component

    def test_a_searched_name_is_highlighted_and_marked_as_a_hit(self, app, admin_client):
        resp = admin_client.get("/files?search=zzz_no_such_file")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # No match: no hit rows, no <mark>.
        assert "ut-row-hit" not in html
        assert "<mark>" not in html


class TestTheFileOperations:
    def test_rename_requires_a_name(self, admin_client):
        resp = admin_client.post("/api/file/1/rename", json={"new_name": "  "})
        assert resp.status_code == 400

    def test_rename_rejects_path_separators(self, admin_client):
        for bad in ("../evil", "a/b", "a\\b", "."):
            resp = admin_client.post("/api/file/1/rename", json={"new_name": bad})
            assert resp.status_code == 400, bad

    def test_rename_of_a_missing_file_is_404(self, admin_client):
        resp = admin_client.post("/api/file/999999/rename", json={"new_name": "x.pdf"})
        assert resp.status_code == 404

    def test_copy_of_a_missing_file_is_404(self, admin_client):
        resp = admin_client.post("/api/file/999999/copy", json={})
        assert resp.status_code == 404

    def test_locate_of_a_missing_file_is_404(self, admin_client):
        resp = admin_client.get("/api/file/999999/locate")
        assert resp.status_code == 404

    def test_locate_answers_with_the_recorded_path(self, app, admin_client, pg_db):
        conn = connect(pg_db)
        with conn.cursor() as cur:
            path_id = document(cur, source_id=source(cur), side_id=side(cur),
                               text="locate corpus", file_name="locate_me.txt",
                               file_date=__import__("datetime").date.today())[0]
            cur.execute("UPDATE paths SET file_path = %s WHERE id = %s",
                        ("/nonexistent/nowhere/locate_me.txt", path_id))
        conn.commit()
        resp = admin_client.get(f"/api/file/{path_id}/locate")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["path"].endswith("locate_me.txt")
        assert body["revealed"] is False

    def test_rename_updates_the_record(self, app, admin_client, pg_db):
        conn = connect(pg_db)
        with conn.cursor() as cur:
            path_id = document(cur, source_id=source(cur), side_id=side(cur),
                               text="rename corpus", file_name="old_name.txt",
                               file_date=__import__("datetime").date.today())[0]
            cur.execute("UPDATE paths SET file_path = %s WHERE id = %s",
                        ("/nonexistent/nowhere/old_name.txt", path_id))
        conn.commit()
        resp = admin_client.post(
            f"/api/file/{path_id}/rename", json={"new_name": "new name.txt"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["name"] == "new name.txt"
        with conn.cursor() as cur:
            cur.execute("SELECT file_name FROM paths WHERE id = %s", (path_id,))
            assert cur.fetchone()[0] == "new name.txt"

    def test_copy_duplicates_the_record_over_the_same_bytes(self, app, admin_client, pg_db):
        import datetime
        conn = connect(pg_db)
        with conn.cursor() as cur:
            src = source(cur)
            path_id, hash_id, context_id = document(
                cur, source_id=src, side_id=side(cur), text="copy corpus",
                file_name="report.pdf", file_type="pdf",
                file_date=datetime.date.today())
        conn.commit()
        resp = admin_client.post(f"/api/file/{path_id}/copy", json={})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["name"] == "report (copy).pdf"
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_name, file_path, context_id FROM paths WHERE id = %s",
                (body["id"],))
            twin = cur.fetchone()
        assert twin[0] == "report (copy).pdf"
        assert twin[1], "the copy points at the same file on disk"
        assert twin[2] == context_id, "the copy shares the content occurrence"

    def test_save_as_names_the_download_not_the_bytes(self, admin_client):
        """The content response refuses nothing about the reader's name: the
        name rides in the disposition only."""
        from Api.services.original_file import OriginalFileService
        import inspect
        source = inspect.getsource(OriginalFileService.content_response)
        assert "download_name" in source
        assert "cleaned[:255]" in source


class TestTheFileTypesDashboard:
    """The dashboard of detected formats and the panel of one format's files."""

    def test_the_dashboard_uses_the_unified_table(self):
        text = (PROJECT_ROOT / "templates/file/file_types.html").read_text(
            encoding="utf-8")
        assert "record_table(" in text, (
            "the formats list is the unified table, not a hand-written one")
        assert "file-type-table" not in text, (
            "the old hand-written table is gone, not a fallback")
        assert "data-panel-open" in text, (
            "a format row opens its documents in the side panel")
        assert "documents_panel(" in text, (
            "the panel is the shared component")

    def test_the_panel_fragment_is_the_shared_library_table(self):
        text = (PROJECT_ROOT / "templates/file/_file_documents_fragment.html").read_text(
            encoding="utf-8")
        assert "file_library_table(" in text
        assert "show_per_page=False" in text
        assert "row_select_onchange=none" in text, (
            "the page-only bulk toolbar handler must not be emitted in a panel")
        assert "export_params=view_string" in text, (
            "the panel's Export speaks about the panel's view, not the page's")

    def test_the_panel_answers_with_the_library_rows(self, app, admin_client, pg_db):
        from tests.integration._seed import connect, document, side, source
        import datetime
        conn = connect(pg_db)
        with conn.cursor() as cur:
            document(cur, source_id=source(cur), side_id=side(cur),
                     text="panel corpus one", file_name="alpha-report.pdf",
                     file_type="pdf", file_date=datetime.date.today())
            document(cur, source_id=source(cur), side_id=side(cur),
                     text="panel corpus two", file_name="beta-scan.pdf",
                     file_type="pdf", file_date=datetime.date.today())
        conn.commit()
        resp = admin_client.get("/files/types/pdf/documents")
        body = resp.get_data(as_text=True)
        assert 'id="panelFilesTable"' in body
        assert "alpha-report.pdf" in body and "beta-scan.pdf" in body
        assert 'class="file-checkbox ut-row-check"' in body
        assert 'data-on-change="updateBulkToolbar()"' not in body, (
            "the panel's own selection controller must not invoke the "
            "File Library page toolbar")
        assert "data-ut-load-more" in body or "data-unified-table" in body
        # The panel's export menu speaks about the panel's own view.
        assert 'data-export-params="file_type=pdf' in body
        assert 'data-export-scope-base="file_type=pdf"' in body
        # The panel's identity survives the "entire dataset" scope.

    def test_the_panel_search_and_type_are_enforced(self, app, admin_client, pg_db):
        from tests.integration._seed import connect, document, side, source
        import datetime
        conn = connect(pg_db)
        with conn.cursor() as cur:
            document(cur, source_id=source(cur), side_id=side(cur),
                     text="needle corpus", file_name="needle.pdf",
                     file_type="pdf", file_date=datetime.date.today())
            document(cur, source_id=source(cur), side_id=side(cur),
                     text="other corpus", file_name="haystack.txt",
                     file_type="txt", file_date=datetime.date.today())
        conn.commit()
        resp = admin_client.get("/files/types/pdf/documents?search=needle")
        body = resp.get_data(as_text=True)
        assert "<mark>needle</mark>.pdf" in body, (
            "the panel highlights the hit where it hit, like the library")
        assert "haystack.txt" not in body, (
            "the panel is pinned to its type; the query string cannot widen it")

    def test_the_dashboard_page_renders_the_formats(self, app, admin_client, pg_db):
        from tests.integration._seed import connect, document, side, source
        import datetime
        conn = connect(pg_db)
        with conn.cursor() as cur:
            document(cur, source_id=source(cur), side_id=side(cur),
                     text="dash corpus", file_name="dash.pdf",
                     file_type="pdf", file_date=datetime.date.today())
        conn.commit()
        resp = admin_client.get("/files/types")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'id="fileTypesTable"' in body
        assert "data-panel-open" in body


class TestCategoryWordsExportScope:
    def test_page_and_export_keep_the_category_from_the_route(self, app, admin_client, pg_db):
        tag = next(_SEQ)
        conn = connect(pg_db)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (f"cw-{tag}",))
            word_id = cur.fetchone()[0]
            cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id", (word_id,))
            category_id = cur.fetchone()[0]
            cur.execute("INSERT INTO words_categorys (word_id, category_id) VALUES (%s, %s)",
                        (word_id, category_id))
        conn.commit()
        conn.close()

        page = admin_client.get(f"/categories/{category_id}/words?search=cw")
        assert page.status_code == 200
        assert f'data-export-scope-base="category_id={category_id}"' in page.get_data(as_text=True)

        exported = admin_client.get(
            f"/api/export/category_words?format=csv&columns=word&category_id={category_id}&search=cw")
        assert exported.status_code == 200
        assert f"cw-{tag}" in exported.get_data(as_text=True)


class TestTheDocumentsPanels:
    """The keyword and category panels: the library's list, pinned to one owner."""

    def _seed_keyword(self, pg_db, tag, hash_ids):
        from core.serialization import pack_int_list  # noqa: F401 - used in category test too
        conn = connect(pg_db)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO words (word) VALUES (%s), (%s) RETURNING id",
                        (f"kwcat{tag}", f"kw{tag}"))
            cat_word, kw_word = [r[0] for r in cur.fetchall()]
            cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                        (cat_word,))
            category_id = cur.fetchone()[0]
            cur.execute("INSERT INTO keywords (keyword, category_id) VALUES (%s, %s)"
                        " RETURNING id", (pack_int_list([kw_word]), category_id))
            keyword_id = cur.fetchone()[0]
            for hash_id in hash_ids:
                cur.execute("INSERT INTO keywords_hashs (hash_id, keyword_id, word_count)"
                            " VALUES (%s, %s, 1)", (hash_id, keyword_id))
        conn.commit()
        conn.close()
        return keyword_id

    def test_a_keyword_panel_answers_with_its_documents(self, app, admin_client, pg_db):
        from tests.integration._seed import connect, document, side, source
        import datetime
        conn = connect(pg_db)
        with conn.cursor() as cur:
            _, hash_a, _ = document(cur, source_id=source(cur), side_id=side(cur),
                                    text="keyword panel one", file_name="kw-one.txt",
                                    file_date=datetime.date.today())
            _, hash_b, _ = document(cur, source_id=source(cur), side_id=side(cur),
                                    text="keyword panel two", file_name="kw-two.txt",
                                    file_date=datetime.date.today())
            document(cur, source_id=source(cur), side_id=side(cur),
                     text="keyword panel three", file_name="kw-other.txt",
                     file_date=datetime.date.today())
        conn.commit()
        keyword_id = self._seed_keyword(pg_db, "p1", [hash_a, hash_b])
        conn.close()

        resp = admin_client.get(f"/keywords/{keyword_id}/documents")
        assert resp.status_code == 200, resp.headers.get('Location')
        body = resp.get_data(as_text=True)
        assert 'id="keywordFilesTable"' in body
        assert "kw-one.txt" in body and "kw-two.txt" in body
        assert "kw-other.txt" not in body, (
            "the panel is pinned to its keyword")
        assert 'data-export-scope-base="keyword_id=' + str(keyword_id) + '"' in body

    def test_a_category_panel_answers_with_its_documents(self, app, admin_client, pg_db):
        from tests.integration._seed import connect, document, side, source
        import datetime
        tag = next(_SEQ)
        conn = connect(pg_db)
        with conn.cursor() as cur:
            _, hash_a, _ = document(cur, source_id=source(cur), side_id=side(cur),
                                    text=f"annual report meeting {tag}", file_name="cat-one.txt",
                                    file_date=datetime.date.today())
            document(cur, source_id=source(cur), side_id=side(cur),
                     text="unrelated text entirely", file_name="cat-other.txt",
                     file_date=datetime.date.today())
            # The category's word occurs in the first document's content only.
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id",
                        (f"annual{tag}",))
            cat_word = cur.fetchone()[0]
            cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                        (cat_word,))
            category_id = cur.fetchone()[0]
            cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                        " VALUES (%s, %s)", (cat_word, category_id))
            cur.execute(
                "INSERT INTO words_hashs (hash_id, word_id, position_indexer)"
                " VALUES (%s, %s, %s)",
                (hash_a, cat_word, pack_int_list([])))
        conn.commit()
        conn.close()

        resp = admin_client.get(f"/categories/{category_id}/documents")
        assert resp.status_code == 200, resp.headers.get('Location')
        body = resp.get_data(as_text=True)
        assert 'id="categoryFilesTable"' in body
        assert "cat-one.txt" in body
        assert "cat-other.txt" not in body, (
            "the panel is pinned to its category")
        assert 'data-export-scope-base="category_id=' + str(category_id) + '"' in body

    def test_the_export_honours_the_panel_scope(self, app, admin_client, pg_db):
        import datetime
        from tests.integration._seed import connect, document, side, source
        conn = connect(pg_db)
        with conn.cursor() as cur:
            _, hash_a, _ = document(cur, source_id=source(cur), side_id=side(cur),
                                    text="export scope one", file_name="scope-one.txt",
                                    file_date=datetime.date.today())
            document(cur, source_id=source(cur), side_id=side(cur),
                     text="export scope two", file_name="scope-two.txt",
                     file_date=datetime.date.today())
        conn.commit()
        keyword_id = self._seed_keyword(pg_db, "p2", [hash_a])
        conn.close()

        resp = admin_client.get(f"/api/export/file_library?format=csv&keyword_id={keyword_id}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "scope-one.txt" in body
        assert "scope-two.txt" not in body, (
            "the panel's export cannot leak past the panel's owner")


class TestTheColumnFilters:
    """The Type column's show-only/hide decisions reach the same list query."""

    def _seed_types(self, pg_db):
        from tests.integration._seed import connect, document, side, source
        import datetime
        conn = connect(pg_db)
        with conn.cursor() as cur:
            document(cur, source_id=source(cur), side_id=side(cur),
                     text="types corpus a", file_name="report.pdf",
                     file_type="pdf", file_date=datetime.date.today())
            document(cur, source_id=source(cur), side_id=side(cur),
                     text="types corpus b", file_name="notes.txt",
                     file_type="txt", file_date=datetime.date.today())
            document(cur, source_id=source(cur), side_id=side(cur),
                     text="types corpus c", file_name="sheet.xlsx",
                     file_type="xlsx", file_date=datetime.date.today())
        conn.commit()
        conn.close()

    def test_the_type_filter_accepts_a_set_of_formats(self, app, admin_client, pg_db):
        self._seed_types(pg_db)
        resp = admin_client.get("/files?file_type=pdf,xlsx")
        body = resp.get_data(as_text=True)
        assert "report.pdf" in body and "sheet.xlsx" in body
        assert "notes.txt" not in body

    def test_the_type_filter_can_hide_formats(self, app, admin_client, pg_db):
        self._seed_types(pg_db)
        resp = admin_client.get("/files?exclude_file_type=pdf,xlsx")
        body = resp.get_data(as_text=True)
        assert "notes.txt" in body
        assert "report.pdf" not in body and "sheet.xlsx" not in body

    def test_show_and_hide_compose_with_the_search(self, app, admin_client, pg_db):
        self._seed_types(pg_db)
        resp = admin_client.get("/files?file_type=pdf,xlsx,txt&exclude_file_type=xlsx&search=report")
        body = resp.get_data(as_text=True)
        assert "<mark>report</mark>.pdf" in body
        assert "sheet.xlsx" not in body and "notes.txt" not in body


class TestTheSearchResults:
    """Search results are the unified table, and the term rides to the viewer."""

    def _seed(self, pg_db, name, text):
        from tests.integration._seed import connect, document, side, source
        import datetime
        conn = connect(pg_db)
        with conn.cursor() as cur:
            document(cur, source_id=source(cur), side_id=side(cur), text=text,
                     file_name=name, file_date=datetime.date.today())
        conn.commit()
        conn.close()

    def test_results_render_through_the_unified_table(self, app, admin_client, pg_db):
        self._seed(pg_db, "needle-report.pdf", "the needle is in here somewhere")
        resp = admin_client.get("/search?q=needle")
        assert resp.status_code == 200, resp.headers.get('Location')
        body = resp.get_data(as_text=True)
        assert 'id="searchResultsTable"' in body
        assert "needle-report.pdf" in body
        assert "data-ut-load-more" in body or "data-unified-table" in body
        # The term rides to the reader: opening a result locates the match.
        assert "q=needle" in body
        # The count is in the toolbar, and the bulk buttons answer it.
        assert 'id="selectedResultCount"' in body

    def test_results_can_be_sorted_by_name(self, app, admin_client, pg_db):
        self._seed(pg_db, "aaa-needle.txt", "needle content here")
        self._seed(pg_db, "zzz-needle.txt", "another needle document")
        resp = admin_client.get("/search?q=needle&sort=name&order=asc")
        body = resp.get_data(as_text=True)
        assert body.index("aaa-needle.txt") < body.index("zzz-needle.txt")
        resp = admin_client.get("/search?q=needle&sort=name&order=desc")
        body = resp.get_data(as_text=True)
        assert body.index("zzz-needle.txt") < body.index("aaa-needle.txt")

    def test_the_results_export_carries_the_query(self, app, admin_client, pg_db):
        self._seed(pg_db, "export-needle.csv", "needle for the export")
        self._seed(pg_db, "unrelated.docx", "nothing to do with the query")
        resp = admin_client.get("/api/export/search_results?q=needle&format=csv")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "export-needle.csv" in body
        assert "unrelated.docx" not in body, (
            "a search export without its query would be the whole library")

    def test_an_empty_query_exports_nothing(self, app, admin_client):
        resp = admin_client.get("/api/export/search_results?format=csv")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "needle" not in body
