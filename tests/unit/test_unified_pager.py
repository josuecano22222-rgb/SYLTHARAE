"""One pager, one Load More control.

A list with a numbered pager renders its Load More inside the pager, and only
there. A table with no pager renders it under itself. Both use the same macro,
so the markup and the engine contract cannot drift apart.
"""
from __future__ import annotations

import jinja2
import pytest

from core.frontend.component_audit import PROJECT_ROOT


@pytest.fixture()
def render():
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(PROJECT_ROOT / "templates")), autoescape=True)
    environment.globals["_"] = lambda text: text
    # Flask's url_for takes keyword values; a Python callable is the same shape.
    environment.globals["url_for"] = lambda endpoint, **values: (
        f"/{endpoint}?" + "&".join(f"{k}={v}" for k, v in sorted(values.items())))

    def render_source(source: str) -> str:
        return environment.from_string(source).render()

    return render_source


PAGER = ("{% from 'components/unified_pagination.html' import unified_pagination %}"
         "{{ unified_pagination(page, total_pages, url_for, 'categories_list', {'per_page': 5}, "
         "table_id='categoriesTable', total_count=97, rows_shown=20) }}")


def _pager(render, page=1, total_pages=5, **extra):
    src = PAGER.replace("page, total_pages", f"{page}, {total_pages}")
    if extra:
        src = src.replace("rows_shown=20)", "rows_shown=20, " + ", ".join(f"{k}={v}" for k, v in extra.items()) + ")")
    return render(src)


def _table(render, pager=False, **overrides):
    params = {
        "id": "'categoriesTable'",
        "columns": "[{'key': 'name', 'label': 'Name', 'sortable': True}]",
        "page": "1", "total_pages": "5", "total_count": "97", "rows_shown": "20",
        "append": "True", "pager": "True" if pager else "False",
    }
    params.update(overrides)
    call = ",".join(f"{k}={v}" for k, v in params.items())
    return render(
        "{% from 'components/table.html' import record_table %}"
        f"{{% call record_table({call}) %}}<tr><td>a</td></tr>{{% endcall %}}")


def test_a_pager_with_a_table_renders_one_load_more_inside_it(render):
    out = _pager(render)
    assert out.count("data-ut-load-more") == 1
    assert 'data-table-id="categoriesTable"' in out
    assert 'data-page="1"' in out and 'data-total-pages="5"' in out
    assert 'data-total="97"' in out and 'data-shown="20"' in out


def test_a_pager_with_a_table_renders_no_second_show_more_button(render):
    out = _pager(render)
    assert "unified-pagination-show-more-btn" not in out, "two Load More controls on one list"


def test_the_numbered_pages_stay_beside_the_load_more(render):
    out = _pager(render)
    assert "unified-pagination-link" in out and "Previous" in out and "Next" in out


def test_the_last_page_has_no_load_more(render):
    assert "data-ut-load-more" not in _pager(render, page=5)


def test_a_single_page_renders_nothing(render):
    assert "unified-pagination-container" not in _pager(render, total_pages=1)


def test_a_pager_without_a_table_has_numbered_pages_and_no_load_more(render):
    # Paged content with no table to append to: the numbered pages are the
    # navigation. There is no second Load More implementation to fall back on.
    src = ("{% from 'components/unified_pagination.html' import unified_pagination %}"
           "{{ unified_pagination(1, 5, url_for, 'email_words', {}) }}")
    out = render(src)
    assert "unified-pagination-link" in out
    assert "unified-pagination-show-more-btn" not in out
    assert "data-ut-load-more" not in out


def test_a_table_with_a_pager_leaves_the_control_to_the_pager(render):
    out = _table(render, pager=True)
    assert "data-ut-load-more" not in out


def test_a_table_without_a_pager_renders_its_own_control(render):
    out = _table(render, pager=False)
    assert out.count("data-ut-load-more") == 1
    assert 'data-table-id="categoriesTable"' in out


def test_the_four_list_pages_hand_their_control_to_the_pager():
    pages = {
        "templates/Category/categories_list.html": "categoriesTable",
        "templates/Category/category_words.html": "categoryWordsTable",
        "templates/Side/sides_list.html": "sidesTable",
        "templates/Sources/sources_list.html": "sourcesTable",
    }
    for path, table_id in pages.items():
        text = (PROJECT_ROOT / path).read_text(encoding="utf-8")
        assert "pager=True" in text, f"{path}: table still renders its own Load More"
        assert f"table_id='{table_id}'" in text, f"{path}: pager is not bound to {table_id}"
