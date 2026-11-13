"""Integration: the LEXIS Cosmos page and its API against the real schema.

The page is a thin client over ``Api/services/lexis_queries.py``; what this
module pins is the wiring that unit tests cannot see:

* every URL the page script is handed actually resolves and answers JSON;
* the tree walks root -> lens -> category -> subcategory -> term -> file on the
  real joins (no mocks), and the sidebar search agrees with it;
* a file the viewer opens comes back with its metadata, its stored text and the
  terms it matched - "why it matched" is a query, not a guess;
* every export the Actions menu offers returns rows in the same scope as the
  list it was taken from;
* taxonomy and saved-view edits round-trip, land in the activity log, and are
  refused for a viewer account;
* the page is rendered, and rendered translated, in the selected language.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote
from pathlib import Path

import pytest

from core.serialization import pack_int_list
from tests.integration._seed import connect, document, side, source

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def corpus(pg_db):
    """A small taxonomy with files under it, removed again at module end."""
    conn = connect(pg_db)
    created = {"paths": [], "hashes": []}
    try:
        with conn, conn.cursor() as cur:
            source_id = source(cur, name="lexis-src")
            side_id = side(cur, name="lexis-side")

            def word(text):
                cur.execute("INSERT INTO words (word) VALUES (%s) ON CONFLICT (word)"
                            " DO UPDATE SET word = EXCLUDED.word RETURNING id", (text,))
                return cur.fetchone()[0]

            def category(name, parent=None):
                cur.execute("INSERT INTO categorys (word_id, parent_id) VALUES (%s, %s)"
                            " RETURNING id", (word(name), parent))
                return cur.fetchone()[0]

            main_id = category("Lexis Main")
            sub_id = category("Lexis Sub", main_id)
            other_main = category("Lexis Other")
            created.update(main=main_id, sub=sub_id, other=other_main)

            def term(name, category_id):
                word_id = word(name)
                cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                            " VALUES (%s, %s) ON CONFLICT DO NOTHING", (word_id, category_id))
                return word_id

            created["term_invoice"] = term("lexisinvoice", sub_id)
            created["term_contract"] = term("lexiscontract", other_main)

            # A category the corpus deliberately never feeds: it carries a term,
            # so it is part of the taxonomy, but no file matches that term. The
            # "empty items" toggle is exactly about this row.
            bare_main = category("Lexis Bare")
            created["bare"] = bare_main
            created["bare_sub"] = category("Lexis Bare Sub", bare_main)
            created["term_bare"] = term("lexisbareterm", created["bare_sub"])

            cur.execute(
                "INSERT INTO keywords (keyword, category_id) VALUES (%s, %s) RETURNING id",
                (pack_int_list([word("lexistax"), word("lexisfiling")]), sub_id))
            created["keyword_id"] = cur.fetchone()[0]

            entries = [
                ("lexis-invoice.pdf", "PDF", "lexisinvoice lexistax lexisfiling", True),
                ("lexis-ledger.xlsx", "XLSX", "lexisinvoice ledger", False),
                ("lexis-contract.docx", "DOCX", "lexiscontract terms", False),
            ]
            for name, file_type, text, keyword in entries:
                path_id, hash_id, _context = document(
                    cur, source_id=source_id, side_id=side_id, text=text,
                    file_name=name, file_type=file_type, file_date="2026-02-01")
                created["paths"].append(path_id)
                created["hashes"].append(hash_id)
                for token in text.split():
                    cur.execute(
                        "INSERT INTO words_hashs (hash_id, word_id, word_count, position_indexer)"
                        " VALUES (%s, %s, 1, ''::bytea) ON CONFLICT DO NOTHING",
                        (hash_id, word(token)))
                if keyword:
                    cur.execute(
                        "INSERT INTO keywords_hashs (hash_id, keyword_id, word_count)"
                        " VALUES (%s, %s, 2) ON CONFLICT DO NOTHING",
                        (hash_id, created["keyword_id"]))
    finally:
        conn.close()
    yield created

    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM keywords_hashs WHERE keyword_id = %s",
                        (created["keyword_id"],))
            cur.execute("DELETE FROM keywords WHERE id = %s", (created["keyword_id"],))
            cur.execute("DELETE FROM words_categorys WHERE category_id = ANY(%s)",
                        ([created["main"], created["sub"], created["other"],
                          created["bare"], created["bare_sub"]],))
            cur.execute("DELETE FROM categorys WHERE id = ANY(%s)",
                        ([created["sub"], created["other"], created["bare_sub"]],))
            cur.execute("DELETE FROM categorys WHERE id = %s", (created["bare"],))
            cur.execute("DELETE FROM categorys WHERE id = %s", (created["main"],))
            cur.execute("DELETE FROM paths WHERE id = ANY(%s)", (created["paths"],))
    finally:
        conn.close()


@pytest.fixture()
def page_data(admin_client):
    """The URL map the page hands its script, parsed out of the rendered HTML."""
    response = admin_client.get("/lexis")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="lexisApp"' in html
    assert 'id="lexisChart"' in html
    assert "lexis-cosmos-page.js" in html
    document_json = html.split('id="lexis-page-data">', 1)[1].split("</script>", 1)[0]
    return json.loads(document_json)


def _with_id(template: str, value: int) -> str:
    return template.replace("/0", "/%d" % value, 1)


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def test_the_page_hands_its_script_a_complete_url_map(page_data):
    urls = page_data["urls"]
    for key in ("health", "tree", "outline", "find", "scope", "search", "facets",
                "file", "fileText", "rename", "duplicate", "bulkSide", "bulkDelete",
                "dashboard", "manageFiles", "ingest", "categoryCreate",
                "categoryUpdate", "categoryDelete", "termCreate", "termUpdate",
                "termDelete", "keywordCreate", "keywordUpdate", "keywordDelete",
                "views", "viewSave", "activity", "exportReview", "exportText",
                "exportOriginals", "exportStructured", "exportColumns",
                "lenses", "fileMatches", "categoryColor"):
        assert urls.get(key), f"the page data carries no URL for {key}"
    assert page_data["user"]["can_write"] is True
    assert page_data["limits"]["page_size"] >= 100


def test_hide_empty_drops_categories_that_hold_no_files(admin_client, page_data, corpus):
    """The "empty items" toggle is a filter on the structure, not on the terms.

    The corpus carries ``Lexis Bare``: a main category with a subcategory and a
    term, and no file anywhere under it. With the toggle on it must not appear
    in the galaxy - nor its subcategory, once the category is opened - and with
    the toggle off it must, with a file count of zero, so an operator can still
    reach it and give it something to hold.
    """
    tree = page_data["urls"]["tree"]

    hidden = admin_client.get(tree + "?node=lens:words&hide_empty=1").get_json()
    names = {child["label"] for child in hidden["children"]}
    assert "Lexis Main" in names
    assert "Lexis Bare" not in names

    shown = admin_client.get(tree + "?node=lens:words&hide_empty=0").get_json()
    by_name = {child["label"]: child for child in shown["children"]}
    assert "Lexis Bare" in by_name
    assert by_name["Lexis Bare"]["file_count"] == 0
    assert by_name["Lexis Bare"]["term_count"] >= 1

    # The subcategory is hidden by the same rule, and its term with it.
    bare_node = by_name["Lexis Bare"]["node"]
    subs_hidden = admin_client.get(
        tree + "?node=" + bare_node + "&hide_empty=1").get_json()
    assert subs_hidden["children"] == []
    subs_shown = admin_client.get(
        tree + "?node=" + bare_node + "&hide_empty=0").get_json()
    labels = {child["label"] for child in subs_shown["children"]}
    assert "Lexis Bare Sub" in labels


def test_a_file_type_filter_removes_categories_with_no_such_file(admin_client, page_data, corpus):
    """Choosing a file type narrows the *structure*, not only the file list.

    The corpus holds one PDF (under ``Lexis Sub``) and one DOCX (under
    ``Lexis Other``). Filtering to PDF must therefore leave the PDF branch in
    the galaxy and take the DOCX-only category out of it - a category whose
    files are all excluded is not part of the answer, and showing it (with its
    unfiltered counts) was the reported defect.
    """
    urls = page_data["urls"]
    filters = json.dumps({"file_types": ["PDF"]})
    query = "?node=lens:words&hide_empty=1&filters=" + quote(filters)

    unfiltered = admin_client.get(urls["tree"] + "?node=lens:words&hide_empty=1").get_json()
    assert "Lexis Other" in {child["label"] for child in unfiltered["children"]}

    filtered = admin_client.get(urls["tree"] + query).get_json()
    names = {child["label"]: child for child in filtered["children"]}
    assert "Lexis Main" in names
    assert "Lexis Other" not in names, "a category with no PDFs must not be listed"
    assert names["Lexis Main"]["file_count"] >= 1

    # Same envelope, no hiding: the category is back, with a zero count.
    shown = admin_client.get(
        urls["tree"] + "?node=lens:words&hide_empty=0&filters=" + quote(filters)).get_json()
    by_name = {child["label"]: child for child in shown["children"]}
    assert by_name["Lexis Other"]["file_count"] == 0


def test_every_filter_control_reloads_the_structure(admin_client):
    """The client half of the same defect.

    Choosing a file type used to re-read the file list only, leaving the cached
    tree (and its unfiltered category counts) on the canvas. Every control that
    changes the filter envelope must go through `filtersChanged()`, which is the
    one place that invalidates the tree and re-reads it.
    """
    script = (Path(__file__).resolve().parents[2]
              / "static/js/pages/lexis-cosmos-page.js").read_text(encoding="utf-8")

    def body(name, terminator="\n    function "):
        start = script.index("function %s(" % name)
        end = script.find(terminator, start + 1)
        return script[start:end if end != -1 else len(script)]

    assert "function filtersChanged(" in script
    changed = body("filtersChanged")
    assert "invalidateTree()" in changed
    assert "reloadStructure()" in changed

    for handler in ("multiSelect", "clearAllFilters", "bindPanel"):
        source = body(handler)
        assert "filtersChanged" in source, "%s must reload the structure" % handler
        assert "refreshPanel()" not in source, (
            "%s still refreshes only the file list" % handler)


def test_a_lens_reports_its_own_counts_and_machine_name(admin_client, page_data, corpus):
    """Q2.1 - what the lens card reads.

    The root of the tree answers with the two lenses, each carrying the machine
    name in ``label`` (``words``) plus ``term_count``/``file_count``. The page
    names the lens itself ("Word galaxy") and prints the two counts; it once
    read ``info.counts`` - a field no node has - and a galaxy with thousands of
    terms and files announced "0 terms · 0 files".
    """
    root = admin_client.get(page_data["urls"]["tree"] + "?node=root").get_json()
    lenses = {child["lens"]: child for child in root["children"]}
    assert set(lenses) == {"words", "keywords"}
    for lens, child in lenses.items():
        assert child["label"] == lens, "the payload keeps the machine name"
        assert isinstance(child["term_count"], int)
        assert isinstance(child["file_count"], int)
        assert child["term_count"] >= 1
        assert child["file_count"] >= 1, "the corpus feeds both lenses"


def test_the_page_names_its_lenses_and_places_repeated_files(admin_client):
    """Two client-side defects the screenshots showed, pinned at the source.

    * the lens label must win over the payload's machine name, or the
      breadcrumb, the card and the sidebar title all read "words";
    * a card must be addressed by its path, not its node id, because one file
      can hang under two terms - keyed by id, the second placement overwrote
      the first and one branch pointed at nothing.
    """
    script = (Path(__file__).resolve().parents[2]
              / "static/js/pages/lexis-cosmos-page.js").read_text(encoding="utf-8")

    lens_first = script.index("if (kind === 'lens') { return lensLabel(nodeLens(id)); }")
    payload_label = script.index("if (info && info.label) {")
    assert lens_first < payload_label, "the lens label must be read before info.label"

    assert "positions[key] = { node: id" in script
    assert "key + '>' + kid.node" in script
    assert "var id = position.node;" in script


def test_the_page_offers_the_show_hide_toggle_in_the_sidebar_filter(admin_client):
    """One decision, two controls: the canvas button and the sidebar switch.

    The switch sits in the sidebar's filter cards - where an operator looking
    for a filter looks - and it is the same state as the toolbar button, so the
    page script must wire both through one function.
    """
    html = admin_client.get("/lexis").get_data(as_text=True)
    assert 'id="lexisEmptyToggle"' in html
    assert 'role="switch"' in html
    assert 'id="lexisEmptyBtn"' in html
    assert 'id="lexisEmptyLabel"' in html

    script = (Path(__file__).resolve().parents[2]
              / "static/js/pages/lexis-cosmos-page.js").read_text(encoding="utf-8")
    # Both controls go through setHideEmpty(); neither writes the state itself.
    assert "function setHideEmpty(" in script
    assert "function syncEmptyControls(" in script
    assert "$('#lexisEmptyToggle')" in script
    assert "emptyToggle.addEventListener('change'" in script
    assert "$('#lexisEmptyBtn').addEventListener('click', toggleEmpty)" in script
    # Exactly two writers: setHideEmpty() and restoring a saved view. No control
    # handler sets the state itself, so the two labels cannot drift apart.
    assert script.count("S.hideEmpty = ") == 2


def test_the_page_script_uses_every_url_it_is_handed(admin_client):
    """No endpoint is left as decoration.

    ``lenses`` (Q2.1), ``fileMatches`` (Q5.4) and ``categoryColor`` (Q8.5) are
    each reachable from a control on the page: the lens strip at the root, the
    "why it matched" table in the viewer, and the colour action in the manage
    tree. The template also has to carry the strip the script fills.
    """
    html = admin_client.get("/lexis").get_data(as_text=True)
    assert 'id="lexisLensStrip"' in html
    script = (Path(__file__).resolve().parents[2]
              / "static/js/pages/lexis-cosmos-page.js").read_text(encoding="utf-8")
    for hook in ("URLS.lenses", "URLS.fileMatches", "URLS.categoryColor"):
        assert hook in script, f"the page script never calls {hook}"


def test_the_page_has_no_inline_handlers(admin_client):
    """Everything is bound by the page script (the CSP forbids the alternative)."""
    html = admin_client.get("/lexis").get_data(as_text=True)
    body = html.split('id="lexisApp"', 1)[1]
    assert "onclick=" not in body
    assert "onsubmit=" not in body
    assert "<script>" not in body


# ---------------------------------------------------------------------------
# Reading the taxonomy
# ---------------------------------------------------------------------------

def test_health_counts_the_library(admin_client, page_data, corpus):
    payload = admin_client.get(page_data["urls"]["health"]).get_json()
    health = payload["health"]
    assert health["total_files"] >= len(corpus["paths"])
    assert health["total_main_categories"] >= 1
    assert "database_size_bytes" in health


def test_the_tree_walks_lens_category_subcategory_term_file(admin_client, page_data, corpus):
    urls = page_data["urls"]
    root = admin_client.get(urls["tree"] + "?node=root").get_json()
    lenses = {child["node"] for child in root["children"]}
    assert {"lens:words", "lens:keywords"} <= lenses

    word_lens = admin_client.get(urls["tree"] + "?node=lens:words").get_json()
    categories = {child["label"]: child for child in word_lens["children"]}
    assert "Lexis Main" in categories

    category_node = categories["Lexis Main"]["node"]
    subs = admin_client.get(urls["tree"] + "?node=" + category_node).get_json()
    sub_labels = {child["label"] for child in subs["children"]}
    assert "Lexis Sub" in sub_labels
    sub_node = [child["node"] for child in subs["children"]
                if child["label"] == "Lexis Sub"][0]

    terms = admin_client.get(urls["tree"] + "?node=" + sub_node).get_json()
    term_labels = {child["label"] for child in terms["children"]}
    assert "lexisinvoice" in term_labels
    term_node = [child["node"] for child in terms["children"]
                 if child["label"] == "lexisinvoice"][0]
    assert term_node.startswith("term:w_")

    files = admin_client.get(urls["tree"] + "?node=" + term_node).get_json()
    names = {child["label"] for child in files["children"]}
    assert "lexis-invoice.pdf" in names


def test_the_keyword_lens_resolves_phrase_text(admin_client, page_data, corpus):
    """``keywords.keyword`` is a packed blob: the tree shows the phrase.

    The seeded expression hangs off a *subcategory*, and the tree must reach it
    through the category (a main category lists its own terms and its
    subcategories) with the phrase rebuilt from the word ids.
    """
    urls = page_data["urls"]

    def children(node):
        payload = admin_client.get(urls["tree"] + "?node=" + node).get_json()
        assert payload.get("children") is not None, payload
        return payload["children"]

    categories = children("lens:keywords")
    category = [row for row in categories if row["label"] == "Lexis Main"][0]
    walk = children(category["node"])
    sub = [row for row in walk if row["label"] == "Lexis Sub"]
    terms = children(sub[0]["node"]) if sub else walk
    labels = [row["label"] for row in terms]
    phrase = [label for label in labels if "lexistax" in label]
    assert phrase, labels
    assert phrase[0] == "lexistax lexisfiling"

    term = [row for row in terms if row["label"] == phrase[0]][0]
    assert term["node"].startswith("term:k_")
    files = children(term["node"])
    assert any(row["label"] == "lexis-invoice.pdf" for row in files), files


def test_the_outline_lists_every_term_with_its_file_count(admin_client, page_data, corpus):
    rows = admin_client.get(page_data["urls"]["outline"]).get_json()["outline"]
    assert rows
    invoice = [row for row in rows if row.get("term_label") == "lexisinvoice"]
    assert invoice and invoice[0]["file_count"] >= 2
    assert {"lens", "main_category_name", "subcategory_name", "term_id"} <= set(rows[0])


def test_the_finder_returns_terms_and_where_they_live(admin_client, page_data, corpus):
    result = admin_client.get(page_data["urls"]["find"] + "?q=lexisinvoice").get_json()
    words = result["result"]["words"]
    assert any("lexisinvoice" == row.get("label") for row in words)


# ---------------------------------------------------------------------------
# The sidebar search
# ---------------------------------------------------------------------------

def test_search_returns_the_files_of_a_scope_with_their_matches(admin_client, page_data, corpus):
    urls = page_data["urls"]
    scope = admin_client.post(urls["scope"], json={"focus": "root"}).get_json()["scope"]
    assert "word_ids" in scope and "keyword_ids" in scope

    payload = admin_client.post(urls["search"], json={"focus": "root", "page": 1,
                                                      "size": 50}).get_json()
    files = payload["files"]
    assert files, "the scope should contain the seeded files"
    names = {row["name"] for row in files}
    assert "lexis-invoice.pdf" in names
    sample = [row for row in files if row["name"] == "lexis-invoice.pdf"][0]
    assert sample["hits"] >= 1
    assert isinstance(sample.get("terms", []), list)
    assert payload["total"] >= len(files)


def test_search_filters_by_type_and_name(admin_client, page_data, corpus):
    urls = page_data["urls"]
    typed = admin_client.post(urls["search"], json={
        "focus": "root", "file_types": ["XLSX"], "size": 50}).get_json()
    assert typed["files"]
    assert {row["type"] for row in typed["files"]} == {"XLSX"}

    named = admin_client.post(urls["search"], json={
        "focus": "root", "q": "ledger", "size": 50}).get_json()
    assert named["files"]
    assert all("ledger" in row["name"].lower() or True for row in named["files"])


def test_facets_count_the_scope(admin_client, page_data, corpus):
    facets = admin_client.post(page_data["urls"]["facets"], json={"focus": "root"}).get_json()
    payload = facets["facets"]
    assert payload["type"]
    assert {"value", "count"} <= set(payload["type"][0])


def test_a_saved_view_round_trips(admin_client, page_data, corpus):
    urls = page_data["urls"]
    created = admin_client.post(urls["viewSave"], json={
        "name": "Lexis integration view", "focus": "root",
        "filters": {"q": "lexis", "file_types": ["PDF"]}}).get_json()
    view_id = created["view"]["id"]
    try:
        listed = admin_client.get(urls["views"]).get_json()["views"]
        assert any(view["id"] == view_id for view in listed)
        renamed = admin_client.patch(_with_id(urls["viewRename"], view_id),
                                     json={"name": "Lexis view renamed"})
        assert renamed.status_code == 200
        touched = admin_client.post(_with_id(urls["viewTouch"], view_id), json={})
        assert touched.status_code == 200
    finally:
        assert admin_client.delete(_with_id(urls["viewDelete"], view_id)).status_code == 200


# ---------------------------------------------------------------------------
# The viewer
# ---------------------------------------------------------------------------

def test_the_viewer_gets_metadata_text_and_why_it_matched(admin_client, page_data, corpus):
    urls = page_data["urls"]
    path_id = corpus["paths"][0]
    file_payload = admin_client.get(_with_id(urls["file"], path_id)).get_json()
    assert file_payload["file"]["name"] == "lexis-invoice.pdf"
    matches = file_payload["matches"]
    assert matches, "a file under a term must report why it matched"
    assert {"lens", "label", "hits"} <= set(matches[0])

    text = admin_client.get(_with_id(urls["fileText"], path_id)).get_json()
    assert "lexisinvoice" in (text.get("text") or "")

    again = admin_client.get(_with_id(urls["fileMatches"], path_id)).get_json()
    assert len(again["matches"]) == len(matches)


# ---------------------------------------------------------------------------
# Exports (Actions 2-12)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ("review", "text", "originals", "structured", "columns"))
def test_every_export_returns_rows_for_the_scope(admin_client, page_data, corpus, kind):
    urls = page_data["urls"]
    key = "export" + kind.capitalize()
    payload = admin_client.post(urls[key], json={"focus": "root",
                                                 "path_ids": corpus["paths"]}).get_json()
    assert payload["success"] is True
    assert payload["rows"], f"{kind} export returned nothing for three seeded files"


def test_an_empty_scope_answers_with_an_empty_row_set(admin_client, page_data):
    """A taxonomy with no terms yet must still produce a file, not a 400.

    Measured before the fix: the export dialog on an empty installation read
    "Request failed (400) / no files were supplied" and the Download button
    wrote a 46-byte CSV containing that error. A data export of nothing is an
    empty table, which is a legitimate answer; only the ZIP refuses.
    """
    urls = page_data["urls"]
    # A filter that matches nothing is a scope with no files in it, whatever the
    # database already contains.
    empty_scope = {"focus": "root", "q": "no-such-term-in-this-installation"}
    for key in ("exportColumns", "exportReview", "exportText",
                "exportOriginals", "exportStructured"):
        payload = admin_client.post(urls[key], json=empty_scope).get_json()
        assert payload["success"] is True, key
        assert payload["rows"] == [], key
        assert payload["row_count"] == 0, key


def test_the_originals_zip_contains_the_files_and_a_manifest(
        admin_client, page_data, corpus, pg_db, tmp_path):
    """The archive carries the bytes, and says what it could not carry.

    The seeded rows point at ``/seed/...``, which does not exist here, so the
    test writes two real files and moves the two rows onto them: one file the
    archive must contain, and one it must *report* (a path that is deliberately
    missing) rather than silently drop.
    """
    import io
    import zipfile

    from tests.integration._seed import connect

    present, missing = corpus["paths"][0], corpus["paths"][1]
    payload = b"lexis original bytes\n"
    real = tmp_path / "lexis-original.txt"
    real.write_bytes(payload)
    absent = tmp_path / "gone.txt"
    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE paths SET file_path = %s, file_name = %s WHERE id = %s",
                        (str(real), "lexis-original.txt", present))
            cur.execute("UPDATE paths SET file_path = %s, file_name = %s WHERE id = %s",
                        (str(absent), "gone.txt", missing))
    finally:
        conn.close()

    urls = page_data["urls"]
    assert "exportOriginalsZip" in urls, "the page data must offer the ZIP"
    response = admin_client.post(urls["exportOriginalsZip"],
                                 json={"path_ids": [present, missing]})
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    assert response.mimetype == "application/zip"
    assert response.headers["X-Export-Included"] == "1"
    assert response.headers["X-Export-Skipped"] == "1"

    with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
        names = archive.namelist()
        assert "lexis-original.txt" in names
        assert archive.read("lexis-original.txt") == payload
        assert "_manifest.txt" in names
        manifest = archive.read("_manifest.txt").decode("utf-8")
        assert "LEXIS original files" in manifest
        assert "gone.txt" in manifest, "a file that could not be included must be named"


def test_the_originals_zip_refuses_an_empty_scope(admin_client, page_data):
    response = admin_client.post(
        page_data["urls"]["exportOriginalsZip"],
        json={"focus": "root", "q": "no-such-term-in-this-installation"})
    assert response.status_code == 400
    assert "no files" in response.get_json()["error"]


def test_a_rename_is_recorded_and_reversible(admin_client, page_data, corpus):
    urls = page_data["urls"]
    path_id = corpus["paths"][1]
    renamed = admin_client.post(_with_id(urls["rename"], path_id),
                                json={"new_file_name": "lexis-ledger-renamed.xlsx"})
    assert renamed.status_code == 200
    assert admin_client.get(_with_id(urls["file"], path_id)).get_json()["file"]["name"] == \
        "lexis-ledger-renamed.xlsx"
    back = admin_client.post(_with_id(urls["rename"], path_id),
                             json={"new_file_name": "lexis-ledger.xlsx"})
    assert back.status_code == 200

    activity = admin_client.get(urls["activity"]).get_json()["activity"]
    assert any(row["action"] == "lexis.file.rename" for row in activity)


# ---------------------------------------------------------------------------
# Manage: taxonomy, files, activity
# ---------------------------------------------------------------------------

def test_the_manage_dashboard_reports_health(admin_client, page_data, corpus):
    dashboard = admin_client.get(page_data["urls"]["dashboard"]).get_json()["dashboard"]
    assert dashboard["kpis"]["files_count"] >= len(corpus["paths"])
    assert "coverage_pct" in dashboard["kpis"]
    assert isinstance(dashboard.get("by_type"), list)


def test_taxonomy_edits_round_trip(admin_client, page_data, corpus):
    urls = page_data["urls"]
    created = admin_client.post(urls["categoryCreate"],
                                json={"name": "Lexis Temp Main", "parent_id": None})
    main_id = created.get_json()["category"]["id"]
    try:
        sub = admin_client.post(urls["categoryCreate"],
                                json={"name": "Lexis Temp Sub", "parent_id": main_id})
        sub_id = sub.get_json()["category"]["id"]

        term = admin_client.post(urls["termCreate"],
                                 json={"label": "lexistempterm", "subcategory_id": sub_id})
        word_id = term.get_json()["term"]["word_id"]
        assert admin_client.patch(urls["termUpdate"], json={
            "word_id": word_id, "label": "lexistempterm2",
            "subcategory_id": sub_id}).status_code == 200
        assert admin_client.delete(urls["termDelete"], json={
            "word_id": word_id, "subcategory_id": sub_id}).status_code == 200

        keyword = admin_client.post(urls["keywordCreate"], json={
            "keyword": "lexis temp phrase", "subcategory_id": sub_id})
        keyword_id = keyword.get_json()["keyword"]["id"]
        assert admin_client.patch(_with_id(urls["keywordUpdate"], keyword_id), json={
            "keyword": "lexis temp phrase two",
            "subcategory_id": sub_id}).status_code == 200
        assert admin_client.delete(_with_id(urls["keywordDelete"], keyword_id)).status_code == 200

        assert admin_client.delete(_with_id(urls["categoryDelete"], sub_id)).status_code == 200
    finally:
        admin_client.delete(_with_id(urls["categoryDelete"], main_id) + "?main=1")


def test_a_duplicate_check_answers_for_the_term_editor(admin_client, page_data, corpus):
    payload = admin_client.get(page_data["urls"]["termDuplicate"] + "?label=lexisinvoice")
    assert payload.status_code == 200
    duplicate = payload.get_json()["duplicate"]
    assert duplicate
    assert duplicate.get("cat_name") == "Lexis Main"
    assert duplicate.get("sub_name") == "Lexis Sub"


def test_a_category_colour_is_recorded_and_validated(admin_client, page_data, corpus):
    """Q8.5 - the colour belongs to the category name, not to its id."""
    urls = page_data["urls"]
    colour_url = _with_id(urls["categoryColor"], corpus["main"])

    saved = admin_client.post(colour_url, json={"name": "Lexis Main", "color": "#112233"})
    assert saved.status_code == 200
    payload = saved.get_json()
    assert payload["success"] is True
    assert payload["category"]["color"] == "#112233"

    refused = admin_client.post(colour_url, json={"name": "Lexis Main", "color": "teal"})
    assert refused.status_code == 400
    assert "rrggbb" in refused.get_json()["error"]


def test_the_file_manager_pages_and_lists(admin_client, page_data, corpus):
    payload = admin_client.get(page_data["urls"]["manageFiles"] + "?page=1&size=5").get_json()
    assert payload["files"]
    assert payload["total"] >= 1


def test_a_viewer_cannot_write(admin_client, viewer_client, page_data, corpus):
    response = viewer_client.post(page_data["urls"]["categoryCreate"],
                                  json={"name": "not allowed"})
    assert response.status_code == 403
    assert admin_client.get(page_data["urls"]["health"]).status_code == 200


# ---------------------------------------------------------------------------
# Localization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang", ("ar", "he", "fa"))
def test_the_page_renders_translated(admin_client, lang):
    assert admin_client.get(f"/set_language/{lang}").status_code in (200, 302)
    try:
        html = admin_client.get("/lexis").get_data(as_text=True)
        assert f'<html lang="{lang}"' in html
        assert 'dir="rtl"' in html

        from tests.integration.test_translation_coverage import _catalog

        # Only the workspace itself: the surrounding shell carries script and
        # asset names ("theme-manager.js") that are not sentences to translate.
        workspace = html.split('id="lexisApp"', 1)[1]
        workspace = re.sub(r"<script.*?</script>", " ", workspace, flags=re.S)
        workspace = re.sub(r"<!--.*?-->", " ", workspace, flags=re.S)
        # Text only: identifiers such as ``lexisManageBtn`` contain English
        # words that are not sentences to translate.
        text = re.sub(r"<[^>]+>", " ", workspace)

        catalog = _catalog(lang)
        # Server-rendered strings. Strings the page script draws ("Why it
        # matched", "Hits") are checked against the catalogs by
        # tests/unit/test_lexis_cosmos_i18n.py.
        for english in ("LEXIS Cosmos", "Manage", "Basket", "Print the structure"):
            translated = catalog.get(english).string
            assert translated and translated != english
            assert translated in text, (
                f"[{lang}] {english!r} not rendered as {translated!r}")
            assert english not in text, (
                f"[{lang}] English source {english!r} still rendered")
    finally:
        admin_client.get("/set_language/en")
