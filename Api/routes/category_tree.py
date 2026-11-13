"""JSON routes for the category hierarchy (depth, move, adopt, delete).

Kept out of ``Api/routes/categories.py`` so the existing page routes (which are
several hundred lines of list/search/merge logic) stay untouched: this module
adds a surface, it does not rewrite one.

Surface
=======
``GET  /api/categories/tree``                keyset-paged, path-ordered tree
``GET  /api/categories/tree/<id>``           one node with depth/path/counts
``POST /api/categories/tree``                create root | child (+ batch children)
``POST /api/categories/tree/parent-above``   new node adopts existing categories
``POST /api/categories/tree/<id>/move``      re-parent (cycle refused)
``POST /api/categories/tree/<id>/delete``    block-or-reparent

Authorization is the global policy in ``core.security.flask_ext``: any
authenticated user may read, analysts and admins may write, and CSRF is enforced
by the app-wide ``CSRFProtect``. Nothing here loosens that.

Every mutation writes an audit entry through the same auth service the places
routes use; a failed audit write is logged, never allowed to undo the work.
"""
from __future__ import annotations

import logging

from flask import jsonify, render_template, request

from Api.utils.utils import get_connection
from core.security.flask_ext import current_user
from services.categories.tree import (
    DEFAULT_PAGE,
    MAX_PAGE,
    CategoryTreeError,
    create_node,
    create_parent_above,
    delete_node,
    fetch_level,
    fetch_node,
    fetch_tree,
    move_node,
    node_stats,
    parse_node_id,
)

logger = logging.getLogger(__name__)


def _audit(action, node_id, detail=None):
    user = current_user()
    try:
        from core.security.service import get_auth_service

        payload = dict(detail or {})
        payload.setdefault("target_type", "category")
        payload.setdefault("target_id", str(node_id))
        get_auth_service().audit(
            action,
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", None),
            resource=f"category:{node_id}",
            detail=payload,
        )
    except Exception:  # pragma: no cover - audit must not fail the request
        logger.warning("category audit log failed: %s %s", action, node_id, exc_info=True)


def _fail(exc: CategoryTreeError):
    body = {"error": exc.message, "code": exc.code}
    if exc.details:
        body["details"] = exc.details
    return jsonify(body), exc.status


def _json_body():
    """A JSON object, or a readable 400.

    The app-wide hook already rejects an array or scalar body, but a missing or
    empty body and a body sent with the wrong content type are still possible
    here, and both should answer 400 rather than raising.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise CategoryTreeError("invalid_body", "a JSON object body is required", 400)
    return body


def _read(fn):
    with get_connection() as conn:
        try:
            with conn.cursor() as cur:
                return fn(cur)
        finally:
            conn.rollback()


def _write(fn):
    with get_connection() as conn:
        try:
            with conn.cursor() as cur:
                result = fn(cur)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def register_category_tree_routes(app):
    """Register the hierarchy endpoints and the tree page."""

    @app.route("/categories/tree", methods=["GET"])
    def categories_tree_page():
        """The hierarchy view: indented list and hierarchy map.

        The page loads the tree from ``/api/categories/tree`` rather than
        rendering it server-side: expand/collapse, re-parenting and search all
        change the shape of what is on screen, and none of them should cost a
        round trip through a template.
        """
        return render_template("Category/categories_tree.html")

    @app.route("/api/categories/tree", methods=["GET"])
    def category_tree_list():
        try:
            limit = request.args.get("limit", DEFAULT_PAGE)
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                raise CategoryTreeError("invalid_limit", "limit must be an integer", 400)
            if not 1 <= limit <= MAX_PAGE:
                raise CategoryTreeError(
                    "invalid_limit", f"limit must be between 1 and {MAX_PAGE}", 400)
            search = request.args.get("search") or None
            if search and len(search) > 200:
                raise CategoryTreeError(
                    "invalid_search", "search must be at most 200 characters", 400)
            cursor = request.args.get("cursor") or None
            with_total = (request.args.get("total", "1") not in ("0", "false", "no"))

            payload = _read(lambda cur: fetch_tree(
                cur, search=search, cursor=cursor, limit=limit, with_total=with_total))
            return jsonify(payload)
        except CategoryTreeError as exc:
            return _fail(exc)

    def _level(parent_id, endpoint_name):
        try:
            limit = request.args.get("limit", 200)
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                raise CategoryTreeError("invalid_limit", "limit must be an integer", 400)
            cursor = request.args.get("cursor") or None
            payload = _read(lambda cur: fetch_level(
                cur, parent_id=parent_id, limit=limit, cursor=cursor))
            return jsonify(payload)
        except CategoryTreeError as exc:
            return _fail(exc)

    @app.route("/api/categories/tree/roots", methods=["GET"])
    def category_tree_roots():
        """The top level only: what the page paints before anything is opened."""
        return _level(None, "category_tree_roots")

    @app.route("/api/categories/tree/<node_id>/children", methods=["GET"])
    def category_tree_children(node_id):
        """One node's direct children, fetched when the node is expanded."""
        try:
            parent_id = parse_node_id(node_id, "parent_id")
        except CategoryTreeError as exc:
            return _fail(exc)
        return _level(parent_id, "category_tree_children")

    @app.route("/api/categories/tree/<node_id>/stats", methods=["GET"])
    def category_tree_stats(node_id):
        """descendant_count and total_terms for one node, on request.

        Deliberately separate from the level endpoints: this is the only read
        whose cost is the size of a subtree, so the page asks for it when the
        operator asks to see it rather than on every paint.
        """
        try:
            node_id = parse_node_id(node_id)
            if _read(lambda cur: fetch_node(cur, node_id)) is None:
                raise CategoryTreeError("not_found", f"no category with id {node_id}", 404)
            return jsonify({"stats": _read(lambda cur: node_stats(cur, node_id))})
        except CategoryTreeError as exc:
            return _fail(exc)

    @app.route("/api/categories/tree/<node_id>", methods=["GET"])
    def category_tree_node(node_id):
        try:
            node_id = parse_node_id(node_id)
            node = _read(lambda cur: fetch_node(cur, node_id))
            if node is None:
                raise CategoryTreeError("not_found", f"no category with id {node_id}", 404)
            return jsonify({"node": node})
        except CategoryTreeError as exc:
            return _fail(exc)

    @app.route("/api/categories/tree", methods=["POST"])
    def category_tree_create():
        try:
            body = _json_body()
            mode = body.get("mode", "root")
            result = _write(lambda cur: create_node(
                cur,
                name=body.get("name"),
                mode=mode,
                parent_id=body.get("parent_id"),
                children=body.get("children"),
            ))
            _audit("category_create", result["node"]["id"],
                   {"mode": mode, "name": result["node"]["name"],
                    "parent_id": result["node"]["parent_id"],
                    "children": [child["id"] for child in result["children"]]})
            return jsonify(result), 201
        except CategoryTreeError as exc:
            return _fail(exc)

    @app.route("/api/categories/tree/parent-above", methods=["POST"])
    def category_tree_parent_above():
        try:
            body = _json_body()
            result = _write(lambda cur: create_parent_above(
                cur,
                name=body.get("name"),
                child_ids=body.get("child_ids"),
                parent_id=body.get("parent_id"),
            ))
            _audit("category_parent_above", result["node"]["id"],
                   {"name": result["node"]["name"], "moved": result["moved"]})
            return jsonify(result), 201
        except CategoryTreeError as exc:
            return _fail(exc)

    def _require_existing(node_id):
        """400 for a non-integer id, 404 for one that names nothing.

        Checked before the body is judged: a request for a category that does
        not exist should say so, not complain about which key the body lacked.
        """
        node_id = parse_node_id(node_id)
        if _read(lambda cur: fetch_node(cur, node_id)) is None:
            raise CategoryTreeError("not_found", f"no category with id {node_id}", 404)
        return node_id

    @app.route("/api/categories/tree/<node_id>/move", methods=["POST"])
    def category_tree_move(node_id):
        try:
            node_id = _require_existing(node_id)
            body = _json_body()
            if "parent_id" not in body:
                raise CategoryTreeError(
                    "missing_parent", "parent_id is required (use null to promote "
                                       "the category to the root)", 400)
            result = _write(lambda cur: move_node(
                cur, node_id=node_id, parent_id=body.get("parent_id")))
            _audit("category_move", result["node"]["id"],
                   {"from": result["previous_parent_id"],
                    "to": result["node"]["parent_id"]})
            return jsonify(result)
        except CategoryTreeError as exc:
            return _fail(exc)

    @app.route("/api/categories/tree/<node_id>/delete", methods=["POST"])
    def category_tree_delete(node_id):
        try:
            node_id = _require_existing(node_id)
            body = _json_body()
            result = _write(lambda cur: delete_node(
                cur,
                node_id=node_id,
                mode=body.get("mode", "block"),
                reparent_to=body.get("reparent_to"),
            ))
            _audit("category_delete", result["deleted"],
                   {"mode": body.get("mode", "block"),
                    "children_moved": result["children_moved"],
                    "children_moved_to": result["children_moved_to"]})
            return jsonify(result)
        except CategoryTreeError as exc:
            return _fail(exc)
