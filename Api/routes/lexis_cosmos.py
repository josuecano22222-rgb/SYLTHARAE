"""LEXIS Cosmos routes - the page and its JSON API.

The page (``/lexis``) is the galaxy browser: root -> lens -> category ->
subcategory -> term -> file, with the sidebar search, the viewer and the
Manage modal. Every number it shows comes from the statements in
:mod:`Api.services.lexis_queries`, through :class:`LexisCosmosService`.

Conventions
-----------
* Reads are ``GET``/``POST`` JSON endpoints under ``/api/lexis/...``; the
  page script is the only client. Nothing here renders a second taxonomy.
* Mutations carry ``@write_access_required`` (analyst or administrator) and are
  recorded through Q11.2 in the same request that changed the data, so the
  Activity tab and ``/admin/audit`` show the same events.
* Errors are answered through ``client_error`` (correlation id, safe message),
  never with ``str(exc)``: a psycopg2 message can carry a table name and a
  query fragment.
* The JSON filter envelope is accepted from the query string (repeated keys or
  one ``filters`` JSON document) and from a JSON body, so the page, an export
  and a saved view all speak the same shape.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import g, jsonify, render_template, request, url_for

from core.errors import conflict, is_unique_violation
from core.validation import PayloadError, int_field, text_field
from Api.services.lexis_cosmos import (
    LexisCosmosService,
    LexisError,
    MAX_PAGE_SIZE,
    SORT_KEYS,
    SORT_DIRECTIONS,
)
from core.errors import client_error
from core.security.flask_ext import current_user, write_access_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)

#: Query-string keys that carry a repeatable value.
LIST_KEYS = ("file_types", "source_ids", "side_ids", "sources", "sides", "basket", "path_ids")
#: Query-string keys that carry one value.
SCALAR_KEYS = ("q", "date_from", "date_to", "node", "focus", "hide_empty",
               "sort_key", "sort_dir", "page", "size", "file_type", "mfq",
               "limit", "offset", "label", "name", "type")


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------

def _service() -> LexisCosmosService:
    return LexisCosmosService()


def _actor() -> Dict[str, Any]:
    """The signed-in user's id and username (None when not authenticated)."""
    user = current_user()
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "authenticated": bool(getattr(user, "is_authenticated", False)),
    }


def _payload() -> Dict[str, Any]:
    """Merge the JSON body and the query string into one request payload.

    Order matters: query-string values win over body values, and a ``filters``
    JSON document is merged first so explicit keys refine it. The page sends a
    body; an export link or a saved view can send everything in the URL.
    """
    data: Dict[str, Any] = {}
    raw_filters = request.args.get("filters")
    if raw_filters:
        try:
            parsed = json.loads(raw_filters)
        except (TypeError, ValueError):
            raise LexisError("filters must be a JSON document")
        if isinstance(parsed, dict):
            data.update(parsed)
    if request.is_json:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            data.update(body)
            nested = body.get("filters")
            if isinstance(nested, dict):
                data.update(nested)
    for key in SCALAR_KEYS:
        if key in request.args:
            data[key] = request.args.get(key)
    for key in LIST_KEYS:
        values = request.args.getlist(key)
        if values:
            data[key] = values
    return data


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int(value: Any, default: int, label: str = "value") -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise LexisError("%s must be a whole number" % label)


def _saved_search_state(data: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a request that defines a view (stored verbatim in filters).

    LEXIS-specific keys live here, not in the search criteria: ``saved_searches``
    keeps ``filters`` as JSONB, so the focus node, the basket, the sort order
    and the pagination size survive a round trip without teaching the shared
    criteria model about a second interface.
    """
    return {
        "focus": data.get("focus") or data.get("node") or "root",
        "basket": data.get("basket") or [],
        "hide_empty": _bool(data.get("hide_empty"), True),
        "sort_key": data.get("sort_key") if data.get("sort_key") in SORT_KEYS else "hits",
        "sort_dir": data.get("sort_dir") if data.get("sort_dir") in SORT_DIRECTIONS else "desc",
        "size": min(max(_int(data.get("size"), 50, "size"), 1), MAX_PAGE_SIZE),
        "file_types": data.get("file_types") or None,
        "sources": data.get("sources") or None,
        "sides": data.get("sides") or None,
        "date_from": data.get("date_from") or None,
        "date_to": data.get("date_to") or None,
        "q": data.get("q") or None,
        "side_dim": data.get("side_dim") or None,
    }


def _error(exc: Exception, message: str, status: int = 500,
           duplicate: str = "That name is already in use"):
    if isinstance(exc, LexisError):
        return jsonify({"success": False, "error": str(exc)}), 400
    if is_unique_violation(exc):
        # A duplicate category / view name is the caller's mistake: 409, not a
        # server fault with a correlation id.
        body, conflict_status = conflict(duplicate, field="name",
                                         success_key="success")
        return jsonify(body), conflict_status
    return client_error(exc, subsystem="lexis", public_message=message,
                        success_key="success", status=status)


def _log_activity(action: str, resource: Optional[str], message: str) -> None:
    """Record a LEXIS mutation (Q11.2). Never fails the request itself."""
    actor = _actor()
    try:
        _service().record_activity(
            action=action,
            resource=resource,
            message=message,
            user_id=actor["id"],
            username=actor["username"],
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
        )
    except Exception:
        logger.warning("LEXIS: activity log write failed (%s %s)", action, resource,
                       exc_info=True)


def _ok(payload: Optional[Dict[str, Any]] = None, **extra):
    body: Dict[str, Any] = {"success": True}
    if payload:
        body.update(payload)
    if extra:
        body.update(extra)
    return jsonify(body)


def _scope(data: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the search scope from the request (basket wins over focus)."""
    basket = data.get("basket")
    if isinstance(basket, str):
        try:
            basket = json.loads(basket)
        except ValueError:
            basket = [basket]
    return _service().resolve_scope(focus=data.get("focus") or data.get("node"),
                                    basket=basket)


def _export_rows(data: Dict[str, Any], builder):
    """Run one export builder over the target ids, tolerating an empty scope.

    ``LexisCosmosService._target_ids`` (the service's own guard) raises on an
    empty list because a *file action* with no files is a caller mistake. A data
    export is different: "everything in this view" on an installation whose
    taxonomy is still empty is a legitimate request, and its correct answer is a
    file with a header row. So the empty case is answered here, before the
    builder is reached, and the builder never has to know.
    """
    ids = _target_ids(data, required=False)
    if not ids:
        return []
    return builder(ids)


def _target_ids(data: Dict[str, Any], *, required: bool = True) -> List[int]:
    """The files an action applies to: the selection, or the current view.

    ``required=False`` is for the data exports (CSV/JSON), where an empty scope
    is a legitimate answer - a taxonomy with no terms yet still has to produce a
    file with a header row rather than an error. The ZIP of original files keeps
    ``required=True``, because a ZIP with nothing in it is not an export.
    """
    explicit = data.get("path_ids") or data.get("selected_path_ids")
    if explicit:
        return list(explicit)
    # No selection: fall back to the filtered view, paged to the export ceiling.
    scope = _scope(data)
    result = _service().search(scope, data, size=MAX_PAGE_SIZE, page=1)
    ids = [row["id"] for row in result["files"]]
    if required and not ids:
        raise LexisError(
            "the current view has no files to export; select files or widen the"
            " filters (a taxonomy with no terms yet scopes to no files)")
    return ids



def _page_payload(actor: Dict[str, Any]) -> Dict[str, Any]:
    """What the page script needs before its first request.

    ``urls`` maps a logical name to a concrete URL; entries ending in ``/0`` are
    templates the script fills with an id (``fileUrl("12")``). ``limits``
    echoes the server's own bounds so the page cannot ask for more than the
    service accepts, and ``capabilities`` hides the write affordances from a
    viewer account instead of letting it discover them by a 403.
    """
    user = current_user()
    can_write = bool(user and (user.has_role("admin") or user.has_role("analyst")))
    return {
        "user": {"name": actor["username"], "can_write": can_write},
        "limits": {"page_size": MAX_PAGE_SIZE},
        "sort": {"keys": list(SORT_KEYS), "directions": list(SORT_DIRECTIONS)},
        "urls": {
            "page": url_for("lexis_cosmos_page"),
            "health": url_for("lexis_health"),
            "tree": url_for("lexis_tree"),
            "lenses": url_for("lexis_lenses"),
            "outline": url_for("lexis_outline"),
            "find": url_for("lexis_find"),
            "scope": url_for("lexis_scope"),
            "search": url_for("lexis_search"),
            "facets": url_for("lexis_facets"),
            "filterOptions": url_for("lexis_filter_options"),
            "file": url_for("lexis_file", path_id=0),
            "fileText": url_for("lexis_file_text", path_id=0),
            "fileMatches": url_for("lexis_file_matches", path_id=0),
            "exportReview": url_for("lexis_export_review"),
            "exportText": url_for("lexis_export_text"),
            "exportOriginals": url_for("lexis_export_originals"),
            "exportOriginalsZip": url_for("lexis_export_originals_zip"),
            "exportStructured": url_for("lexis_export_structured"),
            "exportColumns": url_for("lexis_export_columns"),
            "rename": url_for("lexis_files_rename", path_id=0),
            "duplicate": url_for("lexis_file_duplicate", path_id=0),
            "bulkSide": url_for("lexis_files_bulk_side"),
            "bulkDelete": url_for("lexis_files_bulk_delete"),
            "dashboard": url_for("lexis_dashboard"),
            "manageFiles": url_for("lexis_manage_files"),
            "ingest": url_for("lexis_file_ingest"),
            "categoryCreate": url_for("lexis_category_create"),
            "categoryUpdate": url_for("lexis_category_update", category_id=0),
            "categoryDelete": url_for("lexis_category_delete", category_id=0),
            "categoryColor": url_for("lexis_category_color", category_id=0),
            "termDuplicate": url_for("lexis_term_duplicate"),
            "termCreate": url_for("lexis_term_create"),
            "termUpdate": url_for("lexis_term_update"),
            "termDelete": url_for("lexis_term_delete"),
            "keywordCreate": url_for("lexis_keyword_create"),
            "keywordUpdate": url_for("lexis_keyword_update", keyword_id=0),
            "keywordDelete": url_for("lexis_keyword_delete", keyword_id=0),
            "views": url_for("lexis_views_list"),
            "viewRename": url_for("lexis_views_rename", view_id=0),
            "viewSave": url_for("lexis_views_list"),
            "viewTouch": url_for("lexis_views_touch", view_id=0),
            "viewDelete": url_for("lexis_views_delete", view_id=0),
            "activity": url_for("lexis_activity_list"),
            "activityRecord": url_for("lexis_activity_list"),
            "ingestionPage": url_for("operations_input") if _endpoint_exists("operations_input") else "",
        },
    }


def _endpoint_exists(endpoint: str) -> bool:
    from flask import current_app

    return endpoint in {rule.endpoint for rule in current_app.url_map.iter_rules()}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register_lexis_cosmos_routes(app):
    """Register the LEXIS Cosmos page and its API."""

    # ==================================================================
    # Page
    # ==================================================================

    @app.route("/lexis")
    def lexis_cosmos_page():
        """The LEXIS Cosmos interface (registry ``lexis_cosmos``).

        Every URL the page script uses is rendered here rather than built in
        JavaScript: one place to change a rule, and no string that silently
        points at an endpoint that was renamed.
        """
        actor = _actor()
        return render_template(
            "Lexis/cosmos.html",
            current_user_name=actor["username"],
            is_rtl=getattr(g, "is_rtl", False) or request.args.get("rtl") == "1",
            page_data=_page_payload(actor),
        )

    # ==================================================================
    # 1. Health
    # ==================================================================

    @app.route("/api/lexis/health")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_health():
        """Q1.1 - counts for the root node and the status bar."""
        try:
            return _ok({"health": _service().health()})
        except Exception as exc:
            logger.error("LEXIS health failed", exc_info=True)
            return _error(exc, "Could not read the library totals.")

    # ==================================================================
    # 2. Tree
    # ==================================================================

    @app.route("/api/lexis/tree")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_tree():
        """Lazy children of one node (Q2.1-Q2.10).

        ``node`` is the node id (see the service module); without it the root's
        children - the two lenses - are returned.
        """
        data = _payload()
        try:
            result = _service().children(
                node=data.get("node") or "root",
                filters=data,
                hide_empty=_bool(data.get("hide_empty"), True),
            )
            return _ok(result)
        except LexisError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        except Exception as exc:
            logger.error("LEXIS tree failed", exc_info=True)
            return _error(exc, "Could not open that part of the structure.")

    @app.route("/api/lexis/lenses")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_lenses():
        """Q2.1 - per-lens counts (the galaxy summary)."""
        data = _payload()
        try:
            return _ok({"lenses": _service().lens_summary(data)})
        except LexisError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        except Exception as exc:
            logger.error("LEXIS lens summary failed", exc_info=True)
            return _error(exc, "Could not read the galaxy counts.")

    @app.route("/api/lexis/outline")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_outline():
        """Q2.11 - the whole taxonomy with per-term file counts."""
        try:
            return _ok({"outline": _service().outline()})
        except Exception as exc:
            logger.error("LEXIS outline failed", exc_info=True)
            return _error(exc, "Could not read the taxonomy outline.")

    # ==================================================================
    # 3. Term finder
    # ==================================================================

    @app.route("/api/lexis/find")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_find():
        """Q3.1 + Q3.2 - the header typeahead."""
        data = _payload()
        try:
            return _ok({"result": _service().find(str(data.get("q") or ""))})
        except Exception as exc:
            logger.error("LEXIS find failed", exc_info=True)
            return _error(exc, "Could not search the terms.")

    # ==================================================================
    # 4. Scope, search, facets
    # ==================================================================

    @app.route("/api/lexis/scope", methods=["POST", "GET"])
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_scope():
        """Q4.1 - expand the focus (or basket) into word and keyword ids."""
        data = _payload()
        try:
            return _ok({"scope": _scope(data)})
        except Exception as exc:
            logger.error("LEXIS scope resolution failed", exc_info=True)
            return _error(exc, "Could not resolve that search scope.")

    @app.route("/api/lexis/search", methods=["POST", "GET"])
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_search():
        """Q4.2 / Q4.3 - files in scope, offset or keyset paged."""
        data = _payload()
        try:
            scope = _scope(data)
            cursor = data.get("cursor")
            if isinstance(cursor, str):
                cursor = json.loads(cursor)
            result = _service().search(
                scope,
                data,
                sort_key=str(data.get("sort_key") or "hits"),
                sort_dir=str(data.get("sort_dir") or "desc"),
                page=_int(data.get("page"), 1, "page"),
                size=_int(data.get("size"), 50, "size"),
                cursor=cursor if isinstance(cursor, dict) else None,
            )
            return _ok(result)
        except LexisError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        except Exception as exc:
            logger.error("LEXIS search failed", exc_info=True)
            return _error(exc, "Could not run that search.")

    @app.route("/api/lexis/facets", methods=["POST", "GET"])
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_facets():
        """Q4.4 - facet counts inside the current scope."""
        data = _payload()
        try:
            return _ok({"facets": _service().facets(_scope(data))})
        except Exception as exc:
            logger.error("LEXIS facets failed", exc_info=True)
            return _error(exc, "Could not count the filters.")

    @app.route("/api/lexis/filter-options")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_filter_options():
        """Q4.5 - the values the sidebar dropdowns offer."""
        try:
            return _ok({"options": _service().filter_options()})
        except Exception as exc:
            logger.error("LEXIS filter options failed", exc_info=True)
            return _error(exc, "Could not read the filter values.")

    # ==================================================================
    # 5. Viewer
    # ==================================================================

    @app.route("/api/lexis/file/<int:path_id>")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_file(path_id: int):
        """Q5.1 - metadata and content identity."""
        try:
            service = _service()
            return _ok({"file": service.file_metadata(path_id),
                        "matches": service.file_matches(path_id)})
        except LexisError as exc:
            return jsonify({"success": False, "error": str(exc)}), 404
        except Exception as exc:
            logger.error("LEXIS file metadata failed", exc_info=True)
            return _error(exc, "Could not open that file.")

    @app.route("/api/lexis/file/<int:path_id>/text")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_file_text(path_id: int):
        """Q5.2 (+ Q5.3 fallback) - the display text."""
        try:
            return _ok(_service().file_text(path_id))
        except Exception as exc:
            logger.error("LEXIS file text failed", exc_info=True)
            return _error(exc, "Could not read that file's text.")

    @app.route("/api/lexis/file/<int:path_id>/matches")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_file_matches(path_id: int):
        """Q5.4 - why the file matched, term by term."""
        try:
            return _ok({"matches": _service().file_matches(path_id)})
        except Exception as exc:
            logger.error("LEXIS file matches failed", exc_info=True)
            return _error(exc, "Could not read the term matches.")

    # ==================================================================
    # 6. Actions & exports
    # ==================================================================

    @app.route("/api/lexis/export/review", methods=["POST", "GET"])
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_export_review():
        """Q6.1 - Action 2 (xCats): categories, terms and keywords per file."""
        data = _payload()
        try:
            selected = data.get("selected_path_ids") or data.get("path_ids")
            if not selected:
                # Same rule as the other exports: no selection means the view,
                # and the view is resolved through the normal target path so an
                # empty scope answers with an empty row set, not a 400.
                selected = _target_ids(data, required=False) or None
            rows = _service().review_rows(_scope(data), data, selected)
            return _ok({"rows": rows, "row_count": len(rows)})
        except Exception as exc:
            logger.error("LEXIS review export failed", exc_info=True)
            return _error(exc, "Could not build the category review.")

    @app.route("/api/lexis/export/text", methods=["POST", "GET"])
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_export_text():
        """Q6.2 - Actions 3 & 5: extracted text bundle."""
        data = _payload()
        try:
            rows = _export_rows(data, _service().extracted_text)
            return _ok({"rows": rows, "row_count": len(rows)})
        except Exception as exc:
            logger.error("LEXIS text export failed", exc_info=True)
            return _error(exc, "Could not build the text bundle.")

    @app.route("/api/lexis/export/originals", methods=["POST", "GET"])
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_export_originals():
        """Q6.3 - Actions 4, 6, 9, 10: original paths and metadata."""
        data = _payload()
        try:
            rows = _export_rows(data, _service().original_paths)
            return _ok({"rows": rows, "row_count": len(rows)})
        except Exception as exc:
            logger.error("LEXIS originals export failed", exc_info=True)
            return _error(exc, "Could not read the original paths.")

    @app.route("/api/lexis/export/originals.zip", methods=["POST", "GET"])
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_export_originals_zip():
        """The original files themselves, as one ZIP, for the current scope.

        The other "originals" export answers *where* the files are; this one
        answers *give me the files*. Same target rule as every other action
        (selection, else the filtered view), the same 5,000-file ceiling, and a
        ``_not_included.txt`` naming every file whose bytes are gone - a ZIP
        that silently drops half a selection is worse than one that says so.
        """
        import io
        import zipfile
        from pathlib import Path

        from flask import send_file

        from Api.services.original_file import OriginalFileService

        data = _payload()
        try:
            ids = _target_ids(data)
        except LexisError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        names = {row["file_id"]: row.get("name") or ""
                 for row in _service().original_paths(ids)}
        buffer = io.BytesIO()
        included = 0
        skipped = []
        used = set()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path_id in ids:
                info = OriginalFileService.describe(path_id, include_path=True)
                source = info.get("path") or ""
                name = info.get("name") or names.get(path_id) or f"file-{path_id}"
                arcname = Path(name).name or f"file-{path_id}"
                stem, dot, suffix = arcname.rpartition(".")
                if not dot:
                    stem, suffix = arcname, ""
                candidate, counter = arcname, 2
                while candidate in used:
                    candidate = (f"{stem}_{counter}.{suffix}" if suffix
                                 else f"{arcname}_{counter}")
                    counter += 1
                if not info.get("available") or not source or not Path(source).is_file():
                    skipped.append((path_id, name,
                                    info.get("message") or info.get("reason") or "unavailable"))
                    continue
                used.add(candidate)
                archive.write(source, arcname=candidate)
                included += 1

            manifest = ["# LEXIS original files",
                        f"# generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
                        f"# requested {len(ids)} · included {included} · not included {len(skipped)}",
                        "# file_id\tname\treason"]
            manifest += [f"{path_id}\t{name}\t{reason}" for path_id, name, reason in skipped]
            archive.writestr("_manifest.txt", "\n".join(manifest) + "\n")

        if included == 0:
            return jsonify({
                "success": False,
                "error": "None of the files in this scope still have their original bytes on disk.",
                "skipped": [{"file_id": i, "name": n, "reason": r} for i, n, r in skipped],
            }), 422

        buffer.seek(0)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        response = send_file(buffer, mimetype="application/zip", as_attachment=True,
                             download_name=f"lexis-originals_{stamp}.zip")
        response.headers["X-Export-Included"] = str(included)
        response.headers["X-Export-Skipped"] = str(len(skipped))
        _log_activity("lexis.export.originals", "scope:%s" % (data.get("focus") or "root"),
                      "ZIP with %s file(s)" % included)
        return response

    @app.route("/api/lexis/export/structured", methods=["POST", "GET"])
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_export_structured():
        """Q6.4 - Action 7 (xStruct): structured export rows."""
        data = _payload()
        try:
            rows = _export_rows(
                data, lambda ids: _service().structured_rows(ids, _scope(data), data))
            return _ok({"rows": rows, "row_count": len(rows)})
        except Exception as exc:
            logger.error("LEXIS structured export failed", exc_info=True)
            return _error(exc, "Could not build the structured export.")

    @app.route("/api/lexis/export/columns", methods=["POST", "GET"])
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_export_columns():
        """Q6.6 - Action 11 and the standard exporter."""
        data = _payload()
        try:
            rows = _export_rows(
                data, lambda ids: _service().column_rows(ids, _scope(data), data))
            return _ok({"rows": rows, "row_count": len(rows)})
        except Exception as exc:
            logger.error("LEXIS columns export failed", exc_info=True)
            return _error(exc, "Could not build the export.")

    @app.route("/api/lexis/files/<int:path_id>/rename", methods=["POST"])
    @write_access_required
    def lexis_files_rename(path_id: int):
        """Q6.5 - Action 8 (xRename) with a recorded path revision."""
        data = _payload()
        try:
            actor = _actor()
            result = _service().rename_file(
                path_id,
                str(data.get("new_file_name") or data.get("name") or ""),
                data.get("new_file_path") or data.get("path"),
                user_id=actor["id"],
            )
            _log_activity("lexis.file.rename", "path:%s" % path_id,
                          "Renamed to %s" % result.get("file_name"))
            return _ok({"file": result})
        except Exception as exc:
            logger.error("LEXIS rename failed", exc_info=True)
            return _error(exc, "Could not rename that file.")

    # ==================================================================
    # 7. Dashboard and data health
    # ==================================================================

    @app.route("/api/lexis/manage/dashboard")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_dashboard():
        """Q7.1-Q7.5 - KPIs, charts and the data-health panels."""
        try:
            return _ok({"dashboard": _service().dashboard(user_id=_actor()["id"])})
        except Exception as exc:
            logger.error("LEXIS dashboard failed", exc_info=True)
            return _error(exc, "Could not build the dashboard.")

    # ==================================================================
    # 8. Taxonomy CRUD
    # ==================================================================

    @app.route("/api/lexis/taxonomy/categories", methods=["POST"])
    @write_access_required
    def lexis_category_create():
        """Q8.2 / Q8.3 (+ Q8.5) - create a main category or subcategory."""
        data = _payload()
        try:
            name = text_field(data, "name", required=True, max_length=255, trim=True)
            color = text_field(data, "color", max_length=16) or None
            parent_id = int_field(data, "parent_id", minimum=1)
        except PayloadError as exc:
            return jsonify({"success": False, **exc.to_dict()}), 400
        try:
            result = _service().create_category(
                name,
                parent_id=parent_id,
                color=color,
                user_id=_actor()["id"],
            )
            _log_activity("lexis.category.create", "category:%s" % result["id"],
                          "Created %s" % result["name"])
            return _ok({"category": result})
        except Exception as exc:
            logger.error("LEXIS category create failed", exc_info=True)
            return _error(exc, "Could not create that category.",
                          duplicate="A category with that name already exists")

    @app.route("/api/lexis/taxonomy/categories/<int:category_id>", methods=["PATCH"])
    @write_access_required
    def lexis_category_update(category_id: int):
        """Q8.4 (+ Q8.5) - rename a category or subcategory."""
        data = _payload()
        try:
            name = text_field(data, "name", required=True, max_length=255, trim=True)
            color = text_field(data, "color", max_length=16) or None
        except PayloadError as exc:
            return jsonify({"success": False, **exc.to_dict()}), 400
        try:
            result = _service().rename_category(
                category_id,
                name,
                color=color,
                user_id=_actor()["id"],
            )
            _log_activity("lexis.category.rename", "category:%s" % category_id,
                          "Renamed to %s" % result["name"])
            return _ok({"category": result})
        except Exception as exc:
            logger.error("LEXIS category rename failed", exc_info=True)
            return _error(exc, "Could not rename that category.",
                          duplicate="A category with that name already exists")

    @app.route("/api/lexis/taxonomy/categories/<int:category_id>", methods=["DELETE"])
    @write_access_required
    def lexis_category_delete(category_id: int):
        """Q8.6 / Q8.7 - delete a category (a main category takes its subs)."""
        is_main = _bool(request.args.get("main", request.args.get("is_main")), False)
        try:
            result = _service().delete_category(category_id, is_main)
            _log_activity("lexis.category.delete", "category:%s" % category_id,
                          "Deleted %s" % result["kind"])
            return _ok(result)
        except Exception as exc:
            logger.error("LEXIS category delete failed", exc_info=True)
            return _error(exc, "Could not delete that category.")

    @app.route("/api/lexis/taxonomy/categories/<int:category_id>/color", methods=["POST", "PATCH"])
    @write_access_required
    def lexis_category_color(category_id: int):
        """Q8.5 - set the colour recorded for a category name."""
        data = _payload()
        try:
            name = str(data.get("name") or "")
            if not name:
                raise LexisError("the category name is required")
            result = _service().set_category_color(name, str(data.get("color") or ""),
                                                   user_id=_actor()["id"])
            _log_activity("lexis.category.color", "category:%s" % category_id,
                          "Colour set to %s" % result["color"])
            return _ok({"category": result})
        except LexisError as exc:
            # A malformed colour is the operator's typo, not a server fault:
            # answer 400 with the reason the dialog can show, and keep it out
            # of the error log.
            return jsonify({"success": False, "error": str(exc)}), 400
        except Exception as exc:
            logger.error("LEXIS category colour failed", exc_info=True)
            return _error(exc, "Could not set that colour.")

    @app.route("/api/lexis/taxonomy/duplicate")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_term_duplicate():
        """Q8.8 - is this word already used somewhere in the taxonomy?"""
        data = _payload()
        try:
            return _ok({"duplicate": _service().duplicate_term(str(data.get("label") or ""))})
        except Exception as exc:
            logger.error("LEXIS duplicate check failed", exc_info=True)
            return _error(exc, "Could not check that term.")

    @app.route("/api/lexis/taxonomy/terms", methods=["POST"])
    @write_access_required
    def lexis_term_create():
        """Q8.9 - add a word term to a subcategory."""
        data = _payload()
        try:
            sub_id = _int(data.get("subcategory_id"), 0, "subcategory_id")
            if not sub_id:
                raise LexisError("a subcategory is required")
            result = _service().add_word_term(str(data.get("label") or data.get("word") or ""),
                                              sub_id)
            _log_activity("lexis.term.create", "category:%s" % sub_id,
                          "Added word term %s" % (data.get("label") or data.get("word")))
            return _ok({"term": result})
        except Exception as exc:
            logger.error("LEXIS term create failed", exc_info=True)
            return _error(exc, "Could not add that word term.")

    @app.route("/api/lexis/taxonomy/terms", methods=["PATCH"])
    @write_access_required
    def lexis_term_update():
        """Q8.10 - replace a word term link."""
        data = _payload()
        try:
            sub_id = _int(data.get("subcategory_id"), 0, "subcategory_id")
            word_id = _int(data.get("word_id"), 0, "word_id")
            if not sub_id or not word_id:
                raise LexisError("a word and a subcategory are required")
            result = _service().edit_word_term(word_id,
                                               str(data.get("label") or data.get("word") or ""),
                                               sub_id)
            _log_activity("lexis.term.update", "category:%s" % sub_id,
                          "Edited word term to %s" % (data.get("label") or data.get("word")))
            return _ok({"term": result})
        except Exception as exc:
            logger.error("LEXIS term update failed", exc_info=True)
            return _error(exc, "Could not change that word term.")

    @app.route("/api/lexis/taxonomy/terms", methods=["DELETE"])
    @write_access_required
    def lexis_term_delete():
        """Q8.11 - remove a word term from a subcategory."""
        data = _payload()
        try:
            sub_id = _int(data.get("subcategory_id"), 0, "subcategory_id")
            word_id = _int(data.get("word_id"), 0, "word_id")
            if not sub_id or not word_id:
                raise LexisError("a word and a subcategory are required")
            result = _service().remove_word_term(word_id, sub_id)
            _log_activity("lexis.term.delete", "category:%s" % sub_id,
                          "Removed word term %s" % word_id)
            return _ok({"term": result})
        except Exception as exc:
            logger.error("LEXIS term delete failed", exc_info=True)
            return _error(exc, "Could not remove that word term.")

    @app.route("/api/lexis/taxonomy/keywords", methods=["POST"])
    @write_access_required
    def lexis_keyword_create():
        """Q8.12 - add a keyword expression to a subcategory."""
        data = _payload()
        try:
            sub_id = _int(data.get("subcategory_id"), 0, "subcategory_id")
            if not sub_id:
                raise LexisError("a subcategory is required")
            result = _service().add_keyword(
                str(data.get("keyword") or data.get("label") or ""), sub_id)
            _log_activity("lexis.keyword.create", "category:%s" % sub_id,
                          "Added keyword %s" % result["label"])
            return _ok({"keyword": result})
        except Exception as exc:
            logger.error("LEXIS keyword create failed", exc_info=True)
            return _error(exc, "Could not add that keyword.")

    @app.route("/api/lexis/taxonomy/keywords/<int:keyword_id>", methods=["PATCH"])
    @write_access_required
    def lexis_keyword_update(keyword_id: int):
        """Q8.13 - edit a keyword expression (stale matches are cleared)."""
        data = _payload()
        try:
            sub_id = _int(data.get("subcategory_id"), 0, "subcategory_id")
            if not sub_id:
                raise LexisError("a subcategory is required")
            result = _service().edit_keyword(
                keyword_id, str(data.get("keyword") or data.get("label") or ""), sub_id)
            _log_activity("lexis.keyword.update", "keyword:%s" % keyword_id,
                          "Edited keyword to %s" % result["label"])
            return _ok({"keyword": result})
        except Exception as exc:
            logger.error("LEXIS keyword update failed", exc_info=True)
            return _error(exc, "Could not change that keyword.")

    @app.route("/api/lexis/taxonomy/keywords/<int:keyword_id>", methods=["DELETE"])
    @write_access_required
    def lexis_keyword_delete(keyword_id: int):
        """Q8.14 - delete a keyword expression."""
        try:
            result = _service().delete_keyword(keyword_id)
            _log_activity("lexis.keyword.delete", "keyword:%s" % keyword_id,
                          "Deleted keyword %s" % keyword_id)
            return _ok({"keyword": result})
        except Exception as exc:
            logger.error("LEXIS keyword delete failed", exc_info=True)
            return _error(exc, "Could not delete that keyword.")

    # ==================================================================
    # 9. File manager
    # ==================================================================

    @app.route("/api/lexis/manage/files")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_manage_files():
        """Q9.1 - the file manager list."""
        data = _payload()
        try:
            result = _service().list_files(
                file_type=data.get("file_type"),
                query=data.get("mfq") or data.get("q"),
                limit=_int(data.get("limit"), 50, "limit"),
                offset=_int(data.get("offset"), 0, "offset"),
            )
            return _ok(result)
        except Exception as exc:
            logger.error("LEXIS file manager failed", exc_info=True)
            return _error(exc, "Could not list the files.")

    @app.route("/api/lexis/manage/files", methods=["POST"])
    @write_access_required
    def lexis_file_ingest():
        """Q9.2 - register one file (content, context, path, hits)."""
        data = _payload()
        try:
            result = _service().ingest_file(data)
            _log_activity("lexis.file.create", "path:%s" % result["id"],
                          "Registered %s" % result["name"])
            return _ok({"file": result})
        except Exception as exc:
            logger.error("LEXIS file ingest failed", exc_info=True)
            return _error(exc, "Could not add that file.")

    @app.route("/api/lexis/files/<int:path_id>/duplicate", methods=["POST"])
    @write_access_required
    def lexis_file_duplicate(path_id: int):
        """Q9.3 - duplicate a file record (shared context and content)."""
        data = _payload()
        try:
            result = _service().duplicate_file(path_id, data.get("copy_file_name"))
            _log_activity("lexis.file.duplicate", "path:%s" % result["id"],
                          "Duplicated as %s" % result["name"])
            return _ok({"file": result})
        except Exception as exc:
            logger.error("LEXIS duplicate failed", exc_info=True)
            return _error(exc, "Could not duplicate that file.")

    @app.route("/api/lexis/files/bulk-side", methods=["POST"])
    @write_access_required
    def lexis_files_bulk_side():
        """Q9.4 - move the selection onto another side."""
        data = _payload()
        try:
            ids = _target_ids(data)
            side_id = _int(data.get("side_id"), 0, "side_id")
            if not side_id:
                raise LexisError("a side is required")
            result = _service().bulk_set_side(ids, side_id)
            _log_activity("lexis.file.side", "side:%s" % side_id,
                          "Moved %s file(s) to side %s" % (len(ids), side_id))
            return _ok(result)
        except Exception as exc:
            logger.error("LEXIS bulk side failed", exc_info=True)
            return _error(exc, "Could not move those files.")

    @app.route("/api/lexis/files/bulk-delete", methods=["POST"])
    @write_access_required
    def lexis_files_bulk_delete():
        """Q9.5 - delete the selection."""
        data = _payload()
        try:
            ids = _target_ids(data)
            result = _service().bulk_delete(ids)
            _log_activity("lexis.file.delete", "path",
                          "Deleted %s file(s)" % len(ids))
            return _ok(result)
        except Exception as exc:
            logger.error("LEXIS bulk delete failed", exc_info=True)
            return _error(exc, "Could not delete those files.")

    # ==================================================================
    # 10. Saved views
    # ==================================================================

    @app.route("/api/lexis/views", methods=["GET"])
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_views_list():
        """Q10.1 - saved views visible to the current user."""
        try:
            return _ok({"views": _service().list_views(_actor()["id"])})
        except Exception as exc:
            logger.error("LEXIS views list failed", exc_info=True)
            return _error(exc, "Could not list the saved views.")

    @app.route("/api/lexis/views", methods=["POST"])
    @write_access_required
    def lexis_views_save():
        """Q10.2 - save the current view."""
        data = _payload()
        try:
            name = text_field(data, "name", required=True, max_length=255, trim=True)
            query = text_field(data, "query", max_length=8192)
        except PayloadError as exc:
            return jsonify({"success": False, **exc.to_dict()}), 400
        try:
            state = _saved_search_state(data)
            result = _service().save_view(
                _actor()["id"],
                name,
                query or text_field(data, "q", max_length=8192),
                state,
            )
            _log_activity("lexis.view.save", "view:%s" % result["id"],
                          "Saved view %s" % result["name"])
            return _ok({"view": result})
        except Exception as exc:
            logger.error("LEXIS view save failed", exc_info=True)
            return _error(exc, "Could not save that view.",
                          duplicate="A view with that name already exists")

    @app.route("/api/lexis/views/<int:view_id>", methods=["PATCH"])
    @write_access_required
    def lexis_views_rename(view_id: int):
        """Q10.3 - rename a saved view."""
        data = _payload()
        try:
            name = text_field(data, "name", required=True, max_length=255, trim=True)
        except PayloadError as exc:
            return jsonify({"success": False, **exc.to_dict()}), 400
        try:
            result = _service().rename_view(view_id, name, _actor()["id"])
            _log_activity("lexis.view.rename", "view:%s" % view_id,
                          "Renamed view %s" % result["name"])
            return _ok({"view": result})
        except Exception as exc:
            logger.error("LEXIS view rename failed", exc_info=True)
            return _error(exc, "Could not rename that view.",
                          duplicate="A view with that name already exists")

    @app.route("/api/lexis/views/<int:view_id>/touch", methods=["POST"])
    def lexis_views_touch(view_id: int):
        """Q10.4 - record that a view was applied."""
        try:
            return _ok({"view": _service().touch_view(view_id)})
        except Exception as exc:
            logger.error("LEXIS view touch failed", exc_info=True)
            return _error(exc, "Could not record that view use.")

    @app.route("/api/lexis/views/<int:view_id>", methods=["DELETE"])
    @write_access_required
    def lexis_views_delete(view_id: int):
        """Q10.5 - delete a saved view."""
        try:
            result = _service().delete_view(view_id, _actor()["id"])
            _log_activity("lexis.view.delete", "view:%s" % view_id,
                          "Deleted view %s" % view_id)
            return _ok({"view": result})
        except Exception as exc:
            logger.error("LEXIS view delete failed", exc_info=True)
            return _error(exc, "Could not delete that view.")

    # ==================================================================
    # 11. Activity log
    # ==================================================================

    @app.route("/api/lexis/activity")
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def lexis_activity_list():
        """Q11.1 - recent activity for the current user."""
        try:
            return _ok({"activity": _service().list_activity(_actor()["id"])})
        except Exception as exc:
            logger.error("LEXIS activity list failed", exc_info=True)
            return _error(exc, "Could not read the activity log.")

    @app.route("/api/lexis/activity", methods=["POST"])
    @write_access_required
    def lexis_activity_record():
        """Q11.2 - record an activity entry (undo, redo, backup, restore)."""
        data = _payload()
        try:
            actor = _actor()
            result = _service().record_activity(
                str(data.get("action") or ""),
                data.get("resource"),
                str(data.get("message") or ""),
                user_id=actor["id"],
                username=actor["username"],
                ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
            )
            return _ok({"activity": result})
        except Exception as exc:
            logger.error("LEXIS activity write failed", exc_info=True)
            return _error(exc, "Could not record that activity.")

    return app
