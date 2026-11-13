"""The pagination model, as pure arithmetic: size, page, clamping, batches.

These hold the rules in core/table_paging.py. The route-level tests in
tests/integration/test_table_paging_routes.py check the same rules end to end
against a real database.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from core.table_paging import (
    BATCH_HEADER,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    PageRequest,
    clamp_page,
    clamp_size,
    fetch_page,
    read_page_request,
    total_pages_for,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BATCH = {BATCH_HEADER: "fetch"}


def _dataset(total: int):
    """A fake list of ``total`` rows, and the fetch function a route would pass."""
    rows = list(range(1, total + 1))
    calls = []

    def fetch(page: int, size: int):
        calls.append((page, size))
        start = (page - 1) * size
        return rows[start:start + size], total

    return fetch, calls


class TestSize:
    def test_the_default_is_the_one_the_selector_starts_on(self):
        assert DEFAULT_PAGE_SIZE == 10
        assert clamp_size(None) == 10
        assert clamp_size("not a number") == 10

    @pytest.mark.parametrize("raw,expected", [
        ("1", 1), ("25", 25), ("200", 200),
        ("0", 1), ("-4", 1),                     # below the floor: the floor
        ("201", MAX_PAGE_SIZE), ("9999999", MAX_PAGE_SIZE),  # above the cap: the cap
    ])
    def test_a_size_is_bounded_to_one_and_the_maximum(self, raw, expected):
        assert clamp_size(raw) == expected

    def test_the_size_key_is_configurable(self):
        req = read_page_request({"limit": "25"}, size_key="limit")
        assert req.size == 25


class TestPage:
    @pytest.mark.parametrize("raw,expected", [("1", 1), ("7", 7), ("0", 1), ("-3", 1), ("x", 1), (None, 1)])
    def test_a_page_is_at_least_one(self, raw, expected):
        assert clamp_page(raw) == expected

    def test_total_pages_is_at_least_one_even_when_empty(self):
        assert total_pages_for(0, 10) == 1
        assert total_pages_for(-5, 10) == 1

    @pytest.mark.parametrize("total,size", [(1, 1), (10, 10), (11, 10), (23, 5), (1_000_000, 10), (1_000_000, 200)])
    def test_total_pages_is_the_ceiling(self, total, size):
        assert total_pages_for(total, size) == math.ceil(total / size)


class TestTheFullPageRule:
    @pytest.mark.parametrize("size", [10, 25, 50, 100, 200])
    def test_every_page_of_every_size_covers_the_list_exactly_once(self, size):
        total = 1000 + 7
        fetch, _ = _dataset(total)
        seen = []
        for page in range(1, total_pages_for(total, size) + 1):
            result = fetch_page(fetch, PageRequest(page=page, size=size, is_batch=False))
            assert result.page == page
            seen.extend(result.rows)
        assert seen == list(range(1, total + 1)), "no duplicates, no loss"

    def test_a_page_past_the_end_shows_the_last_page(self):
        fetch, _ = _dataset(23)
        result = fetch_page(fetch, PageRequest(page=99, size=5, is_batch=False))
        assert result.page == 5
        assert result.total_pages == 5
        assert result.rows == [21, 22, 23]
        assert result.past_end is False

    def test_a_list_that_shrank_clamps_to_its_new_last_page(self):
        # Page 9 was asked for when the list had 18 rows at 2 a page; the
        # filters have since cut it to 7 rows, which is 4 pages.
        fetch, _ = _dataset(7)
        result = fetch_page(fetch, PageRequest(page=9, size=2, is_batch=False))
        assert (result.page, result.total_pages) == (4, 4)
        assert result.rows == [7]

    def test_an_empty_list_shows_page_one_with_no_rows(self):
        fetch, _ = _dataset(0)
        result = fetch_page(fetch, PageRequest(page=6, size=10, is_batch=False))
        assert (result.page, result.total_pages, result.rows, result.total) == (1, 1, [], 0)


class TestTheBatchRule:
    def test_a_batch_inside_the_list_returns_its_page(self):
        fetch, _ = _dataset(23)
        result = fetch_page(fetch, PageRequest(page=2, size=5, is_batch=True))
        assert result.rows == [6, 7, 8, 9, 10]
        assert result.past_end is False

    def test_a_batch_past_the_end_returns_nothing_so_nothing_is_duplicated(self):
        # Clamping a batch would append the last page a second time.
        fetch, _ = _dataset(23)
        result = fetch_page(fetch, PageRequest(page=6, size=5, is_batch=True))
        assert result.rows == []
        assert result.past_end is True
        assert result.total == 23

    def test_a_batch_request_is_read_from_the_engine_header(self):
        req = read_page_request({"page": "3"}, BATCH)
        assert req.is_batch is True
        assert read_page_request({"page": "3"}, {}).is_batch is False
        assert read_page_request({"page": "3"}, None).is_batch is False


class TestLargeCounts:
    def test_a_million_rows_is_a_bounded_number_of_fetches(self):
        # Pages are computed, not walked: a far page costs one fetch.
        fetch, calls = _dataset(1_000_000)
        result = fetch_page(fetch, PageRequest(page=99_999, size=10, is_batch=False))
        assert result.total_pages == 100_000
        assert result.rows == list(range(999_981, 999_991))
        assert len(calls) == 1

    def test_a_far_page_past_a_shrink_clamps_in_one_retry(self):
        fetch, calls = _dataset(10)
        result = fetch_page(fetch, PageRequest(page=900_000, size=10, is_batch=False))
        assert result.page == 1
        assert len(calls) == 2  # the first ask, then the clamped page


class TestTheRoutesUseTheModel:
    """Every list route reads its page and size through the one model. A route
    with its own clamp or its own default is how the limits drift apart. The
    check is per route function, so unrelated routes in the same file (an
    email-words page, say) are not held to it."""

    ROUTES = {
        "Api/routes/categories.py": ["categories_list", "category_words"],
        "Api/routes/keywords.py": ["keywords_list", "api_keywords"],
        "Api/routes/sides.py": ["sides_list"],
        "Api/routes/sources.py": ["sources_list"],
        "Api/routes/words.py": ["words_list"],
        "Api/routes/search.py": ["search_page"],
    }

    @staticmethod
    def _function_sources(path: str):
        import ast
        text = (PROJECT_ROOT / path).read_text(encoding="utf-8")
        tree = ast.parse(text)
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.setdefault(node.name, ast.get_source_segment(text, node) or "")
        return found

    @pytest.mark.parametrize("path,func", [(p, f) for p, fs in ROUTES.items() for f in fs])
    def test_the_route_reads_page_and_size_through_the_model(self, path, func):
        body = self._function_sources(path)[func]
        assert "read_page_request(" in body, f"{path}:{func} does not use the shared request reader"
        assert "fetch_page(" in body, f"{path}:{func} does not use the shared page fetch"

    @pytest.mark.parametrize("path,func", [(p, f) for p, fs in ROUTES.items() for f in fs])
    def test_no_route_keeps_its_own_clamp(self, path, func):
        body = self._function_sources(path)[func]
        assert not re.search(r"per_page\s*>\s*(100|200)\s*:", body), f"{path}:{func} has its own size cap"
        assert not re.search(r"if page < 1:", body), f"{path}:{func} has its own page floor"
        assert "per_page', 20" not in body and "per_page', 50" not in body, f"{path}:{func} has its own default"
