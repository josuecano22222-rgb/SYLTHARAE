"""The two category pages: the list page's parent column and the tree page.

Red first. Two things are covered here that the API tests cannot see:

* the list page, which rendered **nothing at all** once any category had a
  parent: ``{{ categories|selectattr('id', 'equalto', category.parent_id)
  |first.name }}`` is parsed by Jinja as a filter called ``first.name``, so the
  render raised ``TemplateRuntimeError`` on the first subcategory row and the
  route's own ``except`` turned that into an empty table. The page looked like
  "no categories found" while forty categories existed.
* the tree page: who may open it (any signed-in role, anonymous redirected to
  the sign-in page) and which interface owns it (the request gate refuses a page
  endpoint no interface owns, so an unregistered page is indistinguishable from
  a disabled one).
"""
from __future__ import annotations

import itertools

SEQ = itertools.count()

TREE_API = "/api/categories/tree"


def _name(prefix="page"):
    return f"{prefix}-{next(SEQ)}-{id(SEQ) % 997}"


def _create(client, name, *, mode="root", parent_id=None):
    body = {"name": name, "mode": mode}
    if parent_id is not None:
        body["parent_id"] = parent_id
    resp = client.post(TREE_API, json=body)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["node"]


def test_the_list_page_renders_a_subcategory_row(admin_client):
    """The parent column must not take the whole page down."""
    parent = _create(admin_client, _name("list-root"))
    child = _create(admin_client, _name("list-child"), mode="child",
                    parent_id=parent["id"])

    resp = admin_client.get("/categories", query_string={"per_page": 200})
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert child["name"] in html, "the subcategory row is missing from the list page"
    assert parent["name"] in html, "the parent row is missing from the list page"
    # A rendered row carries the class the table macro styles; the empty state
    # renders none. (Do not assert on the text "No categories found": that
    # string also appears in the page's translation bundle, so the assertion
    # could never fail.)
    assert html.count('class="category-item') >= 2, (
        "no category rows were rendered while categories exist - the page "
        "raised during render and the route swallowed it")


def test_the_list_page_parent_link_points_at_a_search_for_the_parent(admin_client):
    parent = _create(admin_client, _name("link-root"))
    child = _create(admin_client, _name("link-child"), mode="child",
                    parent_id=parent["id"])
    html = admin_client.get("/categories", query_string={"per_page": 200}).get_data(as_text=True)
    assert child["name"] in html
    assert f"?search={parent['name']}" in html, (
        "the parent cell should link to a search for the parent's name")


def test_the_tree_page_is_open_to_any_signed_in_role(viewer_client):
    resp = viewer_client.get("/categories/tree")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="treeRows"' in html
    assert 'id="treeViewMap"' in html
    assert 'data-on-click="openTreeCategoryModal()"' in html


def test_the_tree_page_redirects_an_anonymous_visitor_to_sign_in(client):
    resp = client.get("/categories/tree")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_the_tree_page_is_owned_by_the_categories_interface():
    """An endpoint no interface owns is refused by the request gate."""
    from core.interfaces import REGISTRY, get_interface_for_endpoint

    interface = get_interface_for_endpoint("categories_tree_page")
    assert interface is not None, "categories_tree_page is owned by no interface"
    assert interface.interface_id == "categories"
    assert "categories_tree_page" in interface.aliases
    # ... and it is the same interface the list page belongs to, so switching
    # Categories off hides both pages and switching it on brings both back.
    assert get_interface_for_endpoint("categories_list").interface_id == "categories"
    assert any(entry.interface_id == "categories" for entry in REGISTRY)


def test_the_parent_cell_names_a_parent_that_is_not_on_the_page(admin_client):
    """A parent outside the page still gets a name, not an empty search.

    The cell used to resolve the parent only among the rows on the page, so a
    subcategory shown by a search - its parent filtered out - rendered
    ``href="/categories?search="``: a link to the whole list wearing the
    parent's badge. Found in the browser pass on the populated database, where
    one of four parent cells had an empty search term.
    """
    parent = _create(admin_client, _name("offpage-root"))
    child = _create(admin_client, _name("offpage-child"), mode="child",
                    parent_id=parent["id"])

    html = admin_client.get(
        "/categories", query_string={"search": child["name"], "per_page": 200}
    ).get_data(as_text=True)

    assert child["name"] in html
    # one row only: the search matched the child, so the parent is not on this
    # page and the name cannot come from a rendered parent row.
    assert html.count('class="category-item') == 1, (
        "the parent row is on this page - the test is not testing the lookup")
    assert f"?search={parent['name']}" in html, (
        "the parent cell did not resolve a parent that is not on the page")
    assert '?search=""' not in html and '?search="' not in html
