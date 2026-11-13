"""The pagination model, end to end, against a real database.

Each test seeds its own uniquely tagged rows and searches for that tag, so the
dataset under test is exact and no other data can leak into a count. The
checks are the ones a reader would see: which records each page shows, the
range the table reports, the page the pager is on, and what a Show More batch
adds.
"""
from __future__ import annotations

import re
import uuid

import pytest

from _seed import connect, side

pytestmark = pytest.mark.integration

BATCH = {"X-Requested-With": "fetch"}
TOTAL = 23

_CATEGORY_ROW = re.compile(r'<tr class="category-item"\s+data-category-id="(\d+)"')
_SIDE_ROW = re.compile(r'<tr class="side-item" data-side-id="(\d+)"')
_TOOLBAR_RANGE = re.compile(
    r'data-ut-first>(\d+)<.*?data-ut-last>(\d+)<.*?data-ut-total>(\d+)<', re.S)
_CURRENT_PAGE = re.compile(r"data-ut-page>(\d+)<")


def _seed_categories(pg_db, tag: str, count: int) -> set[int]:
    conn = connect(pg_db)
    ids = set()
    with conn, conn.cursor() as cur:
        for i in range(count):
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id",
                        (f"tpg_{tag}_{i:02d}",))
            word_id = cur.fetchone()[0]
            cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id", (word_id,))
            ids.add(cur.fetchone()[0])
    conn.close()
    return ids


def _seed_sides(pg_db, tag: str, count: int) -> set[int]:
    conn = connect(pg_db)
    ids = set()
    with conn, conn.cursor() as cur:
        for i in range(count):
            ids.add(side(cur, name=f"tpgside_{tag}_{i:02d}"))
    conn.close()
    return ids


def _range(html: str):
    match = _TOOLBAR_RANGE.search(html)
    assert match, "the table reports no range"
    return tuple(int(x) for x in match.groups())


def _walk(client, url_base: str, total_pages: int, per_page: int, *, batch_only_after_first=True):
    """Show More the whole list: a full first page, then batches. Returns the
    record ids in the order they arrived, one list per response."""
    responses = []
    first = client.get(f"{url_base}&page=1&per_page={per_page}")
    assert first.status_code == 200
    responses.append(first.get_data(as_text=True))
    for page in range(2, total_pages + 1):
        resp = client.get(f"{url_base}&page={page}&per_page={per_page}", headers=BATCH)
        assert resp.status_code == 200
        responses.append(resp.get_data(as_text=True))
    return responses


class TestCategoriesShowMore:
    def test_a_show_more_walk_covers_every_record_exactly_once(self, admin_client, pg_db):
        tag = uuid.uuid4().hex[:8]
        seeded = _seed_categories(pg_db, tag, TOTAL)
        base = f"/categories?search=tpg_{tag}&sort=name&order=asc"
        per_page = 5
        pages = (TOTAL + per_page - 1) // per_page

        seen: list[int] = []
        for html in _walk(admin_client, base, pages, per_page):
            seen.extend(int(x) for x in _CATEGORY_ROW.findall(html))

        assert len(seen) == TOTAL, "a record was lost or repeated"
        assert len(set(seen)) == TOTAL, "a record was repeated"
        assert set(seen) == seeded

    def test_the_range_and_the_pager_follow_each_page(self, admin_client, pg_db):
        tag = uuid.uuid4().hex[:8]
        _seed_categories(pg_db, tag, TOTAL)
        base = f"/categories?search=tpg_{tag}&sort=name&order=asc&per_page=5"

        first = admin_client.get(base + "&page=1").get_data(as_text=True)
        assert _range(first) == (1, 5, TOTAL)
        assert _CURRENT_PAGE.search(first).group(1) == "1"

        last = admin_client.get(base + "&page=5").get_data(as_text=True)
        assert _range(last) == (21, 23, TOTAL)
        assert _CURRENT_PAGE.search(last).group(1) == "5"

    def test_a_page_past_the_end_shows_the_last_page(self, admin_client, pg_db):
        tag = uuid.uuid4().hex[:8]
        _seed_categories(pg_db, tag, TOTAL)
        html = admin_client.get(
            f"/categories?search=tpg_{tag}&sort=name&order=asc&per_page=5&page=99"
        ).get_data(as_text=True)
        assert len(_CATEGORY_ROW.findall(html)) == 3
        assert _range(html) == (21, 23, TOTAL)
        assert _CURRENT_PAGE.search(html).group(1) == "5"

    def test_a_batch_past_the_end_adds_no_rows(self, admin_client, pg_db):
        # Clamping a batch would append the last page a second time.
        tag = uuid.uuid4().hex[:8]
        _seed_categories(pg_db, tag, TOTAL)
        html = admin_client.get(
            f"/categories?search=tpg_{tag}&sort=name&order=asc&per_page=5&page=6",
            headers=BATCH,
        ).get_data(as_text=True)
        assert _CATEGORY_ROW.findall(html) == []
        assert "data-ut-load-more" not in html

    def test_a_filter_that_shrinks_the_list_clamps_the_page(self, admin_client, pg_db):
        tag = uuid.uuid4().hex[:8]
        _seed_categories(pg_db, tag, TOTAL)
        # Page 5 was reachable with the whole list; this search leaves one record.
        html = admin_client.get(
            f"/categories?search=tpg_{tag}_07&sort=name&order=asc&per_page=5&page=5"
        ).get_data(as_text=True)
        assert len(_CATEGORY_ROW.findall(html)) == 1
        assert _range(html) == (1, 1, 1)
        # One page: the pager is not rendered, so no page above 1 can show.
        current = _CURRENT_PAGE.search(html)
        assert current is None or current.group(1) == "1"

    def test_the_size_control_reports_the_size_in_use(self, admin_client, pg_db):
        tag = uuid.uuid4().hex[:8]
        _seed_categories(pg_db, tag, TOTAL)
        html = admin_client.get(
            f"/categories?search=tpg_{tag}&sort=name&order=asc&per_page=7&page=1"
        ).get_data(as_text=True)
        # 7 is not one of the offered sizes; the control offers it so it does
        # not show a value the rows do not have.
        assert re.search(r'<option value="7" selected>7</option>', html)
        assert len(_CATEGORY_ROW.findall(html)) == 7
        assert _range(html) == (1, 7, TOTAL)

    @pytest.mark.parametrize("raw,expected_rows", [
        ("abc", 10),       # not a number: the default size
        ("0", 1),          # below the floor: one row
        ("99999", TOTAL),  # above the cap (200): every record fits on one page
    ])
    def test_a_bad_size_is_bounded_not_trusted(self, admin_client, pg_db, raw, expected_rows):
        tag = uuid.uuid4().hex[:8]
        _seed_categories(pg_db, tag, TOTAL)
        html = admin_client.get(
            f"/categories?search=tpg_{tag}&sort=name&order=asc&per_page={raw}&page=1"
        ).get_data(as_text=True)
        assert len(_CATEGORY_ROW.findall(html)) == expected_rows

    def test_the_sort_order_changes_the_order_not_the_dataset(self, admin_client, pg_db):
        tag = uuid.uuid4().hex[:8]
        seeded = _seed_categories(pg_db, tag, TOTAL)
        per_page = 4
        pages = (TOTAL + per_page - 1) // per_page
        asc, desc = [], []
        for html in _walk(admin_client, f"/categories?search=tpg_{tag}&sort=name&order=asc", pages, per_page):
            asc.extend(int(x) for x in _CATEGORY_ROW.findall(html))
        for html in _walk(admin_client, f"/categories?search=tpg_{tag}&sort=name&order=desc", pages, per_page):
            desc.extend(int(x) for x in _CATEGORY_ROW.findall(html))
        assert set(asc) == set(desc) == seeded
        assert len(asc) == len(desc) == TOTAL
        assert asc == list(reversed(desc)), "descending is ascending reversed"


class TestSidesShowMore:
    def test_a_show_more_walk_covers_every_record_exactly_once(self, admin_client, pg_db):
        tag = uuid.uuid4().hex[:8]
        seeded = _seed_sides(pg_db, tag, TOTAL)
        per_page = 6
        pages = (TOTAL + per_page - 1) // per_page
        seen: list[int] = []
        for html in _walk(admin_client, f"/sides?search=tpgside_{tag}&sort=name&order=asc", pages, per_page):
            seen.extend(int(x) for x in _SIDE_ROW.findall(html))
        assert len(seen) == TOTAL
        assert len(set(seen)) == TOTAL
        assert set(seen) == seeded

    def test_a_page_past_the_end_is_clamped(self, admin_client, pg_db):
        tag = uuid.uuid4().hex[:8]
        _seed_sides(pg_db, tag, TOTAL)
        html = admin_client.get(
            f"/sides?search=tpgside_{tag}&sort=name&order=asc&per_page=6&page=50"
        ).get_data(as_text=True)
        assert len(_SIDE_ROW.findall(html)) == TOTAL - 6 * 3
        assert _CURRENT_PAGE.search(html).group(1) == "4"


class TestPerPageOffers200:
    def test_the_per_page_control_offers_200_and_selects_it(self, admin_client, pg_db):
        tag = uuid.uuid4().hex[:8]
        _seed_categories(pg_db, tag, TOTAL)
        html = admin_client.get(
            f"/categories?search=tpg_{tag}&sort=name&order=asc&per_page=200&page=1"
        ).get_data(as_text=True)
        assert re.search(r'<option value="200" selected>200</option>', html)
        assert len(_CATEGORY_ROW.findall(html)) == TOTAL
        assert _range(html) == (1, TOTAL, TOTAL)
