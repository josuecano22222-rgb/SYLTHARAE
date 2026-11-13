"""Every list with a pager has ONE Load More control, owned by the table engine.

These are the source-level contracts for each surface that was migrated:
no second Show More implementation, every bound pager points at a real table
that hands its control over, and every page that renders a unified table loads
the engine that makes it work.
"""
from __future__ import annotations

import re

import pytest

from core.frontend.component_audit import PROJECT_ROOT

TEMPLATES = PROJECT_ROOT / "templates"
STATIC_JS = PROJECT_ROOT / "static" / "js"

# Retired controls: their presence means a second Load More is back.
RETIRED = (
    "unified-pagination-show-more-btn",   # the legacy pager Show More
    "signals-load-more",                  # the signals hand-written control
    "fmasShowMoreFiles",                  # the file list's own Show More
    "renderShowMoreControl",              # its renderer
    "fmas-show-more-wrap",
)

# Pages that render a record_table (or the shared file library) themselves.
# Components and fragments are rendered inside those pages and are not listed.
PAGES_WITH_UNIFIED_TABLES = [
    "Category/categories_list.html", "Category/category_words.html",
    "Keyword/keywords_list.html", "Word/Word_list.html", "Side/sides_list.html",
    "Sources/sources_list.html", "Search/search.html", "Reports/dashboard.html",
    "Retention/retention.html", "Scheduling/schedules.html", "file/file_types.html",
    "file/files_list.html", "Settings/settings.html",
]

# Every bound pager, with the table it hands its Load More control to.
BOUND_PAGERS = {
    "Category/categories_list.html": "categoriesTable",
    "Category/category_words.html": "categoryWordsTable",
    "Keyword/keywords_list.html": "keywordsTable",
    "Word/Word_list.html": "wordsTable",
    "Side/sides_list.html": "sidesTable",
    "Sources/sources_list.html": "sourcesTable",
    "email_words/email_words.html": "emailWordsTable",
    "Search/search.html": "searchResultsTable",
}


def _read(relative: str, root=TEMPLATES) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _js_sources():
    for path in STATIC_JS.rglob("*.js"):
        if "dist" in path.parts or "i18n" in path.parts:
            continue
        yield path


@pytest.mark.parametrize("token", RETIRED)
def test_no_retired_control_is_left_in_templates(token):
    hits = [p.relative_to(PROJECT_ROOT).as_posix() for p in TEMPLATES.rglob("*.html")
            if token in p.read_text(encoding="utf-8")]
    assert not hits, f"{token} is back in: {hits}"


@pytest.mark.parametrize("token", RETIRED)
def test_no_retired_control_is_left_in_scripts(token):
    hits = [p.relative_to(PROJECT_ROOT).as_posix() for p in _js_sources()
            if token in p.read_text(encoding="utf-8")]
    assert not hits, f"{token} is back in: {hits}"


@pytest.mark.parametrize("relative,table_id", sorted(BOUND_PAGERS.items()))
def test_a_bound_pager_hands_its_control_to_the_pager(relative, table_id):
    text = _read(relative)
    assert f"table_id='{table_id}'" in text or f'table_id="{table_id}"' in text, (
        f"{relative}: the pager is not bound to {table_id}")
    if relative != "email_words/email_words.html":
        # A record_table hands its control over only when the pager renders it.
        assert "pager=True" in text, f"{relative}: the table still renders its own Load More"


def test_the_email_words_table_has_the_id_its_pager_names():
    text = _read("email_words/email_words.html")
    assert 'id="emailWordsTable"' in text


@pytest.mark.parametrize("relative", PAGES_WITH_UNIFIED_TABLES)
def test_a_page_with_a_unified_table_loads_the_engine(relative):
    text = _read(relative)
    assert "unified-table.js" in text, (
        f"{relative} renders a unified table but never loads the engine that "
        "sorts, filters and appends its rows")


def test_the_engine_has_one_load_more_listener():
    text = (STATIC_JS / "modules/ui/unified-table.js").read_text(encoding="utf-8")
    assert text.count("document.addEventListener('click', (event) => {") == 1, (
        "one delegated click listener for every placement of the control")


def test_the_renderers_emit_the_same_control_as_the_server():
    server = _read("components/unified_pagination.html")
    client = (STATIC_JS / "modules/rendering/unified-pagination.js").read_text(encoding="utf-8")
    for attr in ("data-ut-load-more", "data-table-id", "data-page", "data-total-pages",
                 "data-total", "data-shown", "data-ut-first", "data-ut-last", "data-ut-total",
                 "data-ut-load-error", "ut-load-more-info", "ut-load-more-error"):
        assert attr in server, f"server control lost {attr}"
        assert attr in client, f"client renderer lost {attr}"


def test_paged_text_has_no_load_more_because_it_has_no_table():
    # file_detail pages document text; its numbered pages and page size are the
    # navigation, and a Load More would have no table to append to.
    text = _read("file/file_detail.html")
    assert "unified_pagination(current_page, total_pages, url_for," in text
    assert "table_id=" not in text.split("unified_pagination(current_page", 1)[1][:400]
