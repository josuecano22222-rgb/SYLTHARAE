"""Integration: Previous/Next between files (File Detail and Reader).

Reported need: while reviewing one file, move to the neighbouring file without
going back to the library list and finding your place again.

These tests run the real routes against a real database, because the whole
point of the feature is that the neighbour it offers is the next row *of the
query the list page ran*, not of the rows that happened to be rendered. What
is proven here:

* stepping through files visits exactly the library list's order, tie-break
  included (two files sharing a creation date);
* the boundaries (first/last of the browsed set) render a disabled control
  instead of an offer to leave the set;
* a filtered list is carried into the detail page and honoured there: with
  ``nav_source=<id>`` the neighbours come only from that source, and the links
  keep carrying the filter, so a whole review stays inside the view it
  started in;
* the position counter reports where the file sits in that view;
* the two surfaces of a file (Detail and Reader) share one control and link to
  each other with the view intact.

Not covered here: the undated-file branch of the keyset predicate. The
``paths.date_creation`` column is NOT NULL in the schema (m0001), so no row can
reach it; the branch is pinned by unit tests in tests/unit/test_file_navigation.py.
"""

import re
import sys
import uuid
from datetime import date
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]

# Insertion order matters for the tie-break test: ``newer`` and ``tied`` share
# a date, and the higher id (``tied``) must come first in the list.
_FILES = (
    ("newer", date(2026, 3, 2)),
    ("tied", date(2026, 3, 2)),
    ("mid", date(2026, 3, 1)),
    ("older", date(2026, 2, 28)),
    ("oldest", date(2026, 2, 27)),
)
# The order the library list shows (ORDER BY date_creation DESC, id DESC): the
# later insertion of the shared date first.
_EXPECTED_ORDER = ("tied", "newer", "mid", "older", "oldest")

_NAV_TAG = re.compile(r'<(a|span)\b[^>]*data-nav-dir="(?P<dir>prev|next)"[^>]*>')
_HREF = re.compile(r'href="([^"]+)"')
_TITLE = re.compile(r'title="([^"]*)"')
_FILE_LINK = re.compile(r'href="/file/(\d+)(?:\?[^"]*)?"')


def steps(html):
    """The Previous/Next control as rendered: ``{dir: {href, disabled, title}}``."""
    found = {}
    for match in _NAV_TAG.finditer(html):
        tag = match.group(0)
        href = _HREF.search(tag)
        title = _TITLE.search(tag)
        direction = "previous" if match.group("dir") == "prev" else "next"
        found[direction] = {
            "href": href.group(1) if href else None,
            "disabled": "is-disabled" in tag,
            "title": title.group(1) if title else "",
        }
    return found


def file_id(href):
    return int(re.match(r"/file/(\d+)", href).group(1))


def neighbour(library, label, direction):
    """The file the button for ``label`` must lead to, per _EXPECTED_ORDER."""
    order = list(_EXPECTED_ORDER)
    step = -1 if direction == "previous" else 1
    index = order.index(label) + step
    if index < 0 or index >= len(order):
        return None
    return library["ids"][order[index]]


@pytest.fixture(scope="module")
def library(pg_db):
    """Five files in one source, plus one file in a second source."""
    cfg = pg_db
    conn = psycopg2.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], dbname=cfg["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation)"
                " VALUES (%s, 1.0, CURRENT_DATE) RETURNING id",
                (f"nav-side-{_UNIQUE}",),
            )
            side_id = cur.fetchone()[0]

            def source(label):
                cur.execute(
                    "INSERT INTO sources (name, job, importance, country, date_creation)"
                    " VALUES (%s, 'test', 1.0, 'NL', CURRENT_DATE) RETURNING id",
                    (f"nav-source-{label}-{_UNIQUE}",),
                )
                source_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                    (f"nav-hash-{label}-{_UNIQUE}",),
                )
                _hash_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                    " VALUES (%s, %s, %s) RETURNING id",
                    (_hash_id, source_id, side_id),
                )
                return source_id, _hash_id, cur.fetchone()[0]

            main_source, main_hash, main_ctx = source("main")
            other_source, other_hash, other_ctx = source("other")

            ids = {}
            for label, created in _FILES:
                cur.execute(
                    """
                    INSERT INTO paths (file_name, file_path, file_size, file_type,
                                       file_status, file_date, date_creation, context_id)
                    VALUES (%s, %s, 2048, '.txt', 'Read', %s, %s, %s)
                    RETURNING id
                    """,
                    (f"nav-{label}-{_UNIQUE}.txt", f"/nav-test/{label}.txt",
                     created, created, main_ctx),
                )
                ids[label] = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO paths (file_name, file_path, file_size, file_type,
                                   file_status, file_date, date_creation, context_id)
                VALUES (%s, %s, 2048, '.txt', 'Read', %s, %s, %s)
                RETURNING id
                """,
                (f"nav-outsider-{_UNIQUE}.txt", "/nav-test/outsider.txt",
                 date(2026, 3, 2), date(2026, 3, 2), other_ctx),
            )
            ids["outsider"] = cur.fetchone()[0]

            cur.executemany(
                "INSERT INTO words (word) VALUES (%s) ON CONFLICT (word) DO NOTHING",
                [(f"navword{i}",) for i in range(4)],
            )
            cur.execute(
                "SELECT id FROM words WHERE word = ANY(%s) ORDER BY word",
                ([f"navword{i}" for i in range(4)],),
            )
            word_ids = [row[0] for row in cur.fetchall()]
        conn.commit()
    finally:
        conn.close()

    # Content for the reader page (it redirects when a file has none).
    from database.services.contents_db_service import ContentDBService

    ContentDBService().contents_repo.store_text_content(
        word_ids, date.today(), main_hash)

    return {
        "ids": ids,
        "main_source": main_source,
        "other_source": other_source,
        "context": f"nav_source={main_source}",
    }


def detail(client, file_id_, context):
    suffix = f"?{context}" if context else ""
    return client.get(f"/file/{file_id_}{suffix}")


# ---------------------------------------------------------------------------
# The list and the control agree
# ---------------------------------------------------------------------------

def test_list_links_carry_the_view_so_navigation_can_start_there(admin_client, library):
    """Opening a file from a filtered list passes the filter on."""
    response = admin_client.get(f"/files?source={library['main_source']}&limit=100")
    body = response.get_data(as_text=True)
    for label in _EXPECTED_ORDER:
        expected = f'href="/file/{library["ids"][label]}?'
        assert expected in body, f"link for {label} does not carry the view"
    assert f"nav_source={library['main_source']}" in body


def test_stepping_visits_the_list_order(admin_client, library):
    """Walk the whole set with Next and compare it with the list's order."""
    listed = re.findall(
        r'href="/file/(\d+)\?[^"]*nav_source=' + str(library["main_source"]),
        admin_client.get(
            f"/files?source={library['main_source']}&limit=100"
        ).get_data(as_text=True),
    )
    # Both the table and the grid render a link per file; walk the order once.
    unique = []
    for value in listed:
        if int(value) not in unique:
            unique.append(int(value))
    listed = unique
    assert len(listed) == len(_FILES)

    walked = []
    current = listed[0]
    for _ in range(len(_FILES) + 1):
        body = detail(admin_client, current, library["context"]).get_data(as_text=True)
        walked.append(current)
        nav = steps(body)
        if nav["next"]["disabled"]:
            break
        current = file_id(nav["next"]["href"])

    assert walked == listed
    assert walked == [library["ids"][label] for label in _EXPECTED_ORDER]


# ---------------------------------------------------------------------------
# Neighbours, boundaries and position
# ---------------------------------------------------------------------------

def test_middle_file_offers_both_neighbours(admin_client, library):
    ids = library["ids"]
    body = detail(admin_client, ids["mid"], library["context"]).get_data(as_text=True)
    nav = steps(body)
    assert file_id(nav["previous"]["href"]) == neighbour(library, "mid", "previous")
    assert file_id(nav["next"]["href"]) == neighbour(library, "mid", "next")
    # The buttons name the file they lead to.
    older, newer = _EXPECTED_ORDER[3], _EXPECTED_ORDER[1]
    assert f"nav-{newer}-{_UNIQUE}.txt" in nav["previous"]["title"]
    assert f"nav-{older}-{_UNIQUE}.txt" in nav["next"]["title"]


def test_first_file_of_the_view_disables_previous(admin_client, library):
    ids = library["ids"]
    body = detail(admin_client, ids["tied"], library["context"]).get_data(as_text=True)
    nav = steps(body)
    assert nav["previous"]["disabled"] is True
    assert nav["previous"]["href"] is None
    assert file_id(nav["next"]["href"]) == neighbour(library, "tied", "next")


def test_last_file_of_the_view_disables_next(admin_client, library):
    ids = library["ids"]
    body = detail(admin_client, ids["oldest"], library["context"]).get_data(as_text=True)
    nav = steps(body)
    assert nav["next"]["disabled"] is True
    assert nav["next"]["href"] is None
    assert file_id(nav["previous"]["href"]) == neighbour(library, "oldest", "previous")


def test_position_counter_reports_the_place_in_the_view(admin_client, library):
    ids = library["ids"]
    body = detail(admin_client, ids["mid"], library["context"]).get_data(as_text=True)
    assert f"File 3 of {len(_FILES)}" in body

    first = detail(admin_client, ids["tied"], library["context"]).get_data(as_text=True)
    assert f"File 1 of {len(_FILES)}" in first


def test_ties_are_broken_deterministically(admin_client, library):
    """Two files on the same date never swap places between two requests."""
    ids = library["ids"]
    seen = []
    for _ in range(3):
        body = detail(admin_client, ids["tied"], library["context"]).get_data(as_text=True)
        seen.append(file_id(steps(body)["next"]["href"]))
    assert seen == [ids["newer"]] * 3


# ---------------------------------------------------------------------------
# The view travels with the links
# ---------------------------------------------------------------------------

def test_neighbours_stay_inside_the_filtered_view(admin_client, library):
    """The source filter excludes the file that is newest in the library."""
    ids = library["ids"]
    body = detail(admin_client, ids["newer"], library["context"]).get_data(as_text=True)
    nav = steps(body)
    # ``outsider`` is in another source and shares the newest date: a whole
    # library walk would offer it as Previous.
    assert file_id(nav["previous"]["href"]) == ids["tied"]
    assert ids["outsider"] not in [file_id(nav["previous"]["href"])]
    for direction in ("previous", "next"):
        assert f"nav_source={library['main_source']}" in nav[direction]["href"]


def test_a_file_outside_the_view_still_renders_and_keeps_its_neighbours(
        admin_client, library):
    """Opening an unrelated file with a stale filter must not break the page."""
    body = detail(
        admin_client, library["ids"]["outsider"], library["context"]
    ).get_data(as_text=True)
    assert "File navigation" in body
    assert "Not in the current view" in body
    nav = steps(body)
    # The set is still browsable around that file's place in the order.
    assert file_id(nav["next"]["href"]) == library["ids"]["tied"]


def test_the_in_document_search_term_survives_a_step(admin_client, library):
    """``q`` is page state, not a library filter: it must not be lost."""
    response = admin_client.get(
        f"/file/{library['ids']['mid']}?q=navword0&{library['context']}"
    )
    nav = steps(response.get_data(as_text=True))
    assert "q=navword0" in nav["next"]["href"]
    assert f"nav_source={library['main_source']}" in nav["next"]["href"]


def test_without_a_context_the_whole_library_is_browsed(admin_client, library):
    """Opened from a bookmark or a search result: no filter is invented."""
    body = detail(admin_client, library["ids"]["mid"], "").get_data(as_text=True)
    nav = steps(body)
    assert "nav_source" not in nav["next"]["href"]
    assert f"/file/{library['ids']['older']}" in nav["next"]["href"]


# ---------------------------------------------------------------------------
# The Reader is the same browsing session
# ---------------------------------------------------------------------------

def test_reader_shows_the_same_control(admin_client, library):
    response = admin_client.get(
        f"/file/{library['ids']['mid']}/full-content?{library['context']}"
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    nav = steps(response.get_data(as_text=True))
    assert file_id(nav["previous"]["href"]) == neighbour(library, "mid", "previous")
    assert file_id(nav["next"]["href"]) == neighbour(library, "mid", "next")


def test_reader_steps_stay_on_the_reader(admin_client, library):
    """Stepping from the reader must not bounce the operator to the detail page."""
    response = admin_client.get(
        f"/file/{library['ids']['mid']}/full-content?{library['context']}"
    )
    nav = steps(response.get_data(as_text=True))
    assert f"/file/{library['ids']['older']}/full-content?" in nav["next"]["href"]


def test_switching_between_the_two_views_keeps_the_view(admin_client, library):
    ids = library["ids"]
    context = library["context"]

    detail_body = admin_client.get(f"/file/{ids['mid']}?{context}").get_data(as_text=True)
    reader_link = re.search(r'href="(/file/%d/full-content\?[^"]*)"' % ids["mid"],
                            detail_body)
    assert reader_link, "the detail page must offer the reader"
    assert f"nav_source={library['main_source']}" in reader_link.group(1)

    reader_body = admin_client.get(
        f"/file/{ids['mid']}/full-content?{context}"
    ).get_data(as_text=True)
    details_link = re.search(r'href="(/file/%d\?[^"]*)"' % ids["mid"], reader_body)
    assert details_link, "the reader must offer a way back to the details"
    assert f"nav_source={library['main_source']}" in details_link.group(1)



def test_the_control_ships_the_assets_it_needs(admin_client, library):
    """A rename of the stylesheet or script must not silently break the control."""
    body = detail(admin_client, library["ids"]["mid"], library["context"]).get_data(as_text=True)
    assert "/static/css/file-nav.css" in body
    assert "/static/js/file-nav.js" in body

    script = admin_client.get("/static/js/file-nav.js")
    assert script.status_code == 200
    assert "data-nav-dir" in script.get_data(as_text=True)


def test_the_reader_also_loads_the_keyboard_handler(admin_client, library):
    response = admin_client.get(
        f"/file/{library['ids']['mid']}/full-content?{library['context']}"
    )
    body = response.get_data(as_text=True)
    assert "/static/js/file-nav.js" in body
    assert "/static/css/file-nav.css" in body


def test_invalid_filters_do_not_break_the_list_or_leak_into_links(admin_client):
    """A filter the list ignores must not become a navigation filter either."""
    response = admin_client.get(
        "/files?source=not-a-number&date_from=yesterday&status=Bogus"
        "&size_min=abc&limit=5"
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "nav_source" not in body
    assert "nav_date_from" not in body


def test_a_name_search_is_carried_into_the_file_links(admin_client, library):
    """The list's text search filters file names; it must travel as nav_search."""
    response = admin_client.get(f"/files?search=nav-mid-{_UNIQUE}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert f"nav_search=nav-mid-{_UNIQUE}" in body
    assert f'href="/file/{library["ids"]["mid"]}?' in body


def test_an_empty_filtered_list_is_not_an_error(admin_client, library):
    response = admin_client.get(
        f"/files?source={library['main_source']}&search=no-such-file-{_UNIQUE}"
    )
    assert response.status_code == 200
    assert "No files found" in response.get_data(as_text=True)
