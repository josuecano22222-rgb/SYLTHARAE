"""One state for the table's controls: Per Page, Show More, the registration
range, the page total, the numbered pager and the jump input.

The server renders every control from the same numbers (the page, the page
size, the rows shown, the total). These tests hold that arithmetic and the
contract the table engine relies on, so the controls cannot drift apart.
"""
from __future__ import annotations

import re

import jinja2
import pytest

from core.frontend.component_audit import PROJECT_ROOT

TEMPLATES = PROJECT_ROOT / "templates"
STATIC_JS = PROJECT_ROOT / "static" / "js"


@pytest.fixture()
def render():
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    environment.globals["_"] = lambda text: text
    environment.globals["url_for"] = lambda endpoint, **kwargs: (
        f"/{endpoint}?" + "&".join(f"{k}={v}" for k, v in sorted(kwargs.items())))

    def render_source(source: str) -> str:
        return environment.from_string(source).render()

    return render_source


def _range_text(html: str, scope_attr: str):
    """(first, last, total) read from the range spans inside the element that
    carries ``scope_attr``; the spans are the contract the engine updates."""
    start = html.index(scope_attr)
    chunk = html[start:start + 900]
    first = re.search(r"data-ut-first>(\d+)<", chunk)
    last = re.search(r"data-ut-last>(\d+)<", chunk)
    total = re.search(r"data-ut-total>(\d+)<", chunk)
    assert first and last and total, chunk
    return int(first.group(1)), int(last.group(1)), int(total.group(1))


def _record_table(render, **params) -> str:
    base = {
        "id": "'keywordsTable'",
        "columns": "[{'key': 'name', 'label': 'Name', 'sortable': True}]",
        "mode": "'server'",
        "append": "True",
        "pager": "True",
        "per_page": "25",
        "page": "3",
        "total_pages": "5",
        "total_count": "120",
        "rows_shown": "25",
    }
    base.update(params)
    call = ",".join(f"{k}={v}" for k, v in base.items())
    return render(
        "{% from 'components/table.html' import record_table %}"
        f"{{% call record_table({call}) %}}<tr><td>a</td></tr>{{% endcall %}}")


class TestTheRangeFollowsThePage:
    def test_a_middle_page_reads_its_own_rows(self, render):
        out = _record_table(render, page="3", rows_shown="25")
        assert _range_text(out, 'class="ut-info"') == (51, 75, 120)

    def test_the_last_page_closes_at_the_total(self, render):
        out = _record_table(render, page="5", rows_shown="20")
        assert _range_text(out, 'class="ut-info"') == (101, 120, 120)

    def test_the_first_page_starts_at_one(self, render):
        out = _record_table(render, page="1", rows_shown="25")
        assert _range_text(out, 'class="ut-info"') == (1, 25, 120)

    def test_an_empty_list_reads_zero(self, render):
        out = _record_table(render, page="1", rows_shown="0", total_count="0", total_pages="1")
        assert _range_text(out, 'class="ut-info"') == (0, 0, 0)

    def test_the_load_more_block_carries_the_same_geometry(self, render):
        # Without a pager the table renders its own Load More under itself.
        out = _record_table(render, page="3", rows_shown="25", pager="False")
        block = out[out.index("data-ut-load-more"):]
        assert 'data-page-size="25"' in block
        assert 'data-first-row="51"' in block
        assert 'data-page="3"' in block
        assert _range_text(block, "data-ut-load-more") == (51, 75, 120)

    def test_without_a_page_size_the_range_starts_at_one(self, render):
        # Callers that do not pass a page size keep the old meaning: the first
        # batch starts at row one.
        out = _record_table(render, page="1", rows_shown="20", per_page="None",
                            total_count="97", total_pages="5")
        assert _range_text(out, 'class="ut-info"') == (1, 20, 97)


class TestThePagerAgreesWithTheTable:
    def test_the_pager_carries_the_page_size_and_the_page_it_shows(self, render):
        out = render(
            "{% from 'components/unified_pagination.html' import unified_pagination %}"
            "{{ unified_pagination(3, 7, url_for, 'keywords_list', {'per_page': 25},"
            " table_id='keywordsTable', total_count=170, rows_shown=25, page_size=25) }}")
        assert 'data-page-size="25"' in out
        assert 'data-current-page="3"' in out
        assert re.search(r"<span data-ut-page>3</span>", out)
        assert re.search(r"<span data-ut-pages>7</span>", out)
        # The jump input appears above five pages and shows the current page.
        assert 'value="3"' in out and 'max="7"' in out
        assert _range_text(out, "data-ut-load-more") == (51, 75, 170)

    def test_the_pager_without_a_page_size_still_renders(self, render):
        out = render(
            "{% from 'components/unified_pagination.html' import unified_pagination %}"
            "{{ unified_pagination(2, 4, url_for, 'x', {}) }}")
        assert 'data-current-page="2"' in out
        assert 'data-page-size="0"' in out


class TestTheEngineContract:
    def test_the_renderers_emit_the_range_the_engine_writes(self):
        server = (TEMPLATES / "components" / "unified_pagination.html").read_text(encoding="utf-8")
        client = (STATIC_JS / "modules" / "rendering" / "unified-pagination.js").read_text(encoding="utf-8")
        engine = (STATIC_JS / "modules" / "ui" / "unified-table.js").read_text(encoding="utf-8")
        for marker in ("data-ut-first", "data-ut-last", "data-ut-total", "data-first-row", "data-page-size"):
            assert marker in server, f"server lost {marker}"
            assert marker in engine or marker in client, f"nothing writes {marker}"
        assert "data-ut-first" in client and "data-ut-last" in client

    def test_show_more_never_moves_the_page(self):
        # Show More reveals rows; the pager's page is set only by navigation.
        engine = (STATIC_JS / "modules" / "ui" / "unified-table.js").read_text(encoding="utf-8")
        body = engine[engine.index("function updateLoadMoreBlock"):engine.index("function requestNextBatch")]
        assert "if (appended > 0)" in body
        assert "state.shown = Math.min(state.total - start, state.shown + appended)" in body
        assert "state.page +=" not in body
        assert "state.page =" not in body
        assert "state.page + 1" not in body

    def test_the_batch_request_never_reads_the_pager_page(self):
        # The batch URL is computed from the rows already shown (offset), not from
        # the pager's data-page, so a batch cannot be a page navigation.
        engine = (STATIC_JS / "modules" / "ui" / "unified-table.js").read_text(encoding="utf-8")
        body = engine[engine.index("function nextBatchUrl"):engine.index("function adoptAppendedRows")]
        assert "batchGeometry(block)" in body
        assert "data-page" not in body
        assert "geometry.batchPage" in body

    def test_the_numbered_window_is_drawn_from_the_servers_markup(self):
        # The engine clones the server's prototypes and fills the hooks the
        # server put in place; it does not write pager markup of its own (the
        # one-renderer guard in test_component_library counts JS modules that
        # draw pagination classes).
        server = (TEMPLATES / "components" / "unified_pagination.html").read_text(encoding="utf-8")
        engine = (STATIC_JS / "modules" / "ui" / "unified-table.js").read_text(encoding="utf-8")
        for hook in ("data-ut-nav=\"first\"", "data-ut-nav=\"previous\"", "data-ut-nav=\"next\"",
                     "data-ut-nav=\"last\"", "data-ut-pager-list", "data-ut-numbered",
                     "data-ut-protos", "data-ut-proto=\"page\"", "data-ut-proto=\"current\"",
                     "data-ut-proto=\"gap\"", "data-ut-jump"):
            assert hook in server, f"server lost the hook {hook}"
        for hook in ("[data-ut-nav=\"next\"]", "[data-ut-pager-list]", "[data-ut-numbered]",
                     "[data-ut-proto]", "[data-ut-jump]"):
            assert hook in engine, f"engine does not use the hook {hook}"
        assert "function applyState(" in engine
        assert "function renderNumbers(" in engine
        assert "pagination-item" not in engine and "pagination-link" not in engine

    def test_the_legacy_show_more_wiring_stays_removed(self):
        core = (STATIC_JS / "modules" / "core" / "unified-pagination.js").read_text(encoding="utf-8")
        assert "unified-pagination-show-more-btn" not in core
