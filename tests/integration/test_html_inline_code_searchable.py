"""Integration: inline HTML code reaches the word index.

BeautifulSoup's ``get_text()`` omits ``<script>``/``<style>``, and the reader
used to pass that omission straight through: a standalone ``.js`` or ``.css``
file is an indexed text format in this pipeline (READER-02), while the same
bytes inside an ``.html`` file were dropped - silently, and without a trace in
the log. For a single-page application that is most of the document (the
reported case: an ``index.html`` of ~77 KB that produced 539 characters of
content, with nothing explaining the difference).

The reader now returns the inline code and publishes it through
``forensic_text``, the channel FORENSIC-01 added for evidence outside the
visible body. This test proves the wiring end to end: reader -> storage ->
database -> search, for a marker that exists *only* inside a script.
"""

import datetime
import itertools
import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_SEQ = itertools.count()

PAGE_NAME = "index.html"
VISIBLE_MARKER = "HTMLVISIBLE 5521"
SCRIPT_MARKER = "HTMLSCRIPT 3311"
STYLE_MARKER = "htmlstyle3311"

PAGE = f"""<!doctype html>
<html><head><title>Dashboard</title>
<style>.{STYLE_MARKER}{{color:red}}</style>
<script>var payload = {{label: "{SCRIPT_MARKER}"}};</script>
</head><body><h1>{VISIBLE_MARKER}</h1><p>Short page.</p></body></html>
"""


@pytest.fixture(scope="module")
def corpus(pg_db, tmp_path_factory):
    from pipeline.integrated_reader import IntegratedFileReader

    tag = f"_html_{next(_SEQ)}"
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    today = datetime.date.today()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5, %s)"
            " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name",
            (f"{tag}_side", today),
        )
        cur.execute(
            "INSERT INTO sources (name, job, importance, country, date_creation)"
            " VALUES (%s, 't', 0.5, 't', %s) ON CONFLICT (name)"
            " DO UPDATE SET name = EXCLUDED.name",
            (f"{tag}_src", today),
        )
    conn.commit()

    root = tmp_path_factory.mktemp("html_inline")
    (root / PAGE_NAME).write_text(PAGE, encoding="utf-8")

    IntegratedFileReader(
        max_workers=1, enable_storage=True,
        storage_source=f"{tag}_src", storage_side=f"{tag}_side",
    ).process_folder(str(root))

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.id, p.file_name, p.processing_status,"
                " p.extraction_provenance"
                " FROM paths p JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN sides s ON s.id = hc.side_id WHERE s.name = %s",
                (f"{tag}_side",),
            )
            rows = {name: {"id": pid, "processing_status": status,
                           "provenance": prov or {}}
                    for pid, name, status, prov in cur.fetchall()}
    finally:
        conn.close()
    return rows


@pytest.fixture(scope="module")
def page(corpus):
    assert PAGE_NAME in corpus, sorted(corpus)
    return corpus[PAGE_NAME]


def test_page_was_read(page):
    assert page["processing_status"] == "processed", page


def test_visible_text_is_searchable(page):
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(query=VISIBLE_MARKER, limit=10)
    assert total >= 1
    assert PAGE_NAME in [r.get("file_name") for r in results]


def test_inline_script_text_is_searchable(page):
    """The reported gap: script content used to be dropped without a trace."""
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(query=SCRIPT_MARKER, limit=10)
    assert total >= 1, "inline script content was not indexed"
    assert PAGE_NAME in [r.get("file_name") for r in results]


def test_inline_style_text_is_searchable(page):
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(query=STYLE_MARKER, limit=10)
    assert total >= 1, "inline style content was not indexed"
    assert PAGE_NAME in [r.get("file_name") for r in results]


def test_the_character_split_is_recorded(page):
    """So 'N characters from a much larger file' is answerable later."""
    diagnostics = page["provenance"].get("diagnostics") or {}
    assert diagnostics.get("visible_text_chars") is not None, diagnostics
    assert diagnostics.get("script_chars"), diagnostics
    assert diagnostics.get("style_chars"), diagnostics
