"""
Analyst-Driven Manual Categorization routes.

FRS: "Analyst-Driven Manual Categorization with Scoped Search Control".

Provides:

* The dedicated **Analyst Categorization View** (FR-4) at
  ``/analyst/categorization`` - a browser/filter/review surface for every
  manually categorized file, visually and structurally distinct from any
  smart-classification interface.
* A JSON API under ``/api/analyst/...`` for category CRUD, bulk assignment /
  removal from search results, filtered listing, audit log, stats and CSV
  export.

Hard separation guarantees (FR-1.4):

* These endpoints read/write ONLY the ``analyst_categories`` /
  ``analyst_file_categories`` / ``analyst_categorization_log`` tables.
  The system ("smart") taxonomy is never queried or mutated here, and
  analyst categories are never exposed through the smart-category controls
  (``/api/categories``) or vice versa.

Authorization (NFR-5): the global role policy in
``core/security/flask_ext.py`` already enforces "GET for any authenticated
user, mutations for analyst-or-admin". Mutating endpoints additionally
carry an explicit ``@write_access_required`` so the intent is local and
server-side, never just hidden UI.
"""

import logging
from typing import Optional

from flask import g, jsonify, render_template, request, session

from core.validation import PayloadError, query_text, text_field
from Api.services.analyst_categories import (
    DEFAULT_SCOPE,
    SCOPE_SESSION_KEY,
    AnalystCategoryService,
    normalize_scope,
)
from core.errors import client_error
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter
from core.security.flask_ext import write_access_required

logger = logging.getLogger(__name__)


def _current_user():
    """The authenticated user object (or None), set by the auth middleware."""
    user = g.get("user")
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def resolve_request_scope() -> str:
    """Resolve the analyst search scope for the current request (FR-2.1-2.3).

    Precedence: explicit ``scope``/``analyst_scope`` request parameter, then
    the session-persisted last selection (FR-2.3), then the default
    "uncategorized files only" (FR-2.1). A valid explicit parameter is
    persisted into the session for subsequent requests.

    Shared by the search routes so every search surface applies the same
    scope semantics.
    """
    raw = request.args.get("scope") or request.args.get("analyst_scope")
    if raw is None and request.is_json:
        try:
            body = request.get_json(silent=True) or {}
            raw = body.get("scope") or body.get("analyst_scope")
        except Exception:
            raw = None
    if raw is not None:
        scope = normalize_scope(raw)
        session[SCOPE_SESSION_KEY] = scope
        return scope
    return normalize_scope(session.get(SCOPE_SESSION_KEY, DEFAULT_SCOPE))


def register_analyst_category_routes(app):
    """Register analyst categorization page + API routes."""

    # ==================================================================
    # Page (FR-4) - dedicated, visually distinct view
    # ==================================================================

    @app.route("/analyst/categorization")
    def analyst_categorization_page():
        """Analyst Categorization View: browse, filter and review every
        manually categorized file. Deliberately distinct from smart
        classification pages (FR-4.3)."""
        try:
            categories = AnalystCategoryService.list_categories()
            analysts = AnalystCategoryService.list_analysts()
            stats = AnalystCategoryService.get_stats()
            return render_template(
                "Analyst/analyst_categorization.html",
                categories=categories,
                analysts=analysts,
                stats=stats,
                initial_scope=normalize_scope(session.get(SCOPE_SESSION_KEY, DEFAULT_SCOPE)),
            )
        except Exception as e:
            logger.error(f"Error loading analyst categorization view: {e}", exc_info=True)
            return render_template(
                "Analyst/analyst_categorization.html",
                categories=[],
                analysts=[],
                stats=AnalystCategoryService.get_stats(),
                initial_scope=DEFAULT_SCOPE,
            )

    # ==================================================================
    # Category CRUD (analyst-defined namespace only)
    # ==================================================================

    @app.route("/api/analyst/categories")
    def api_analyst_categories():
        """List analyst-defined categories (with file counts).

        This is the ONLY list consumed by the manual-categorization controls;
        it never returns smart categories (FR-1.4).
        """
        try:
            include_counts = request.args.get("counts", "1") not in ("0", "false")
            return jsonify(AnalystCategoryService.list_categories(include_counts=include_counts))
        except Exception as e:
            logger.error(f"Error listing analyst categories: {e}", exc_info=True)
            return client_error(e, subsystem="Api.routes.analyst_categories", status=500)

    @app.route("/api/analyst/categories", methods=["POST"])
    @write_access_required
    def api_analyst_category_create():
        data = request.get_json(silent=True) or request.form.to_dict()
        try:
            name = text_field(data, "name", required=True, max_length=255, trim=True)
            description = text_field(data, "description", max_length=1024) or None
            color = text_field(data, "color", max_length=16) or None
        except PayloadError as exc:
            return jsonify({"success": False, **exc.to_dict()}), 400
        category_id, error = AnalystCategoryService.create_category(
            name=name,
            description=description,
            color=color,
            user=_current_user(),
        )
        if error:
            return jsonify({"success": False, "error": error}), 400
        return jsonify({
            "success": True,
            "category_id": category_id,
            "message": "Analyst category created",
        })

    @app.route("/api/analyst/categories/<int:category_id>", methods=["PATCH"])
    @write_access_required
    def api_analyst_category_update(category_id):
        data = request.get_json(silent=True) or request.form.to_dict()
        try:
            name = text_field(data, "name", max_length=255, trim=True) or None
            description = text_field(data, "description", max_length=1024) or None
            color = text_field(data, "color", max_length=16) or None
        except PayloadError as exc:
            return jsonify({"success": False, **exc.to_dict()}), 400
        ok, error = AnalystCategoryService.update_category(
            category_id,
            name=name,
            description=description,
            color=color,
        )
        if error:
            status = 404 if "not found" in error.lower() else 400
            return jsonify({"success": False, "error": error}), status
        return jsonify({"success": True, "message": "Analyst category updated"})

    @app.route("/api/analyst/categories/<int:category_id>", methods=["DELETE"])
    @write_access_required
    def api_analyst_category_delete(category_id):
        ok, error = AnalystCategoryService.delete_category(category_id)
        if error:
            status = 404 if "not found" in error.lower() else 400
            return jsonify({"success": False, "error": error}), status
        return jsonify({
            "success": True,
            "message": "Analyst category deleted; affected files returned to analyst-uncategorized status",
        })

    # ==================================================================
    # Assignment / removal from search result selections (FR-1.2, FR-1.3)
    # ==================================================================

    @app.route("/api/analyst/assign", methods=["POST"])
    @limiter.limit("60 per minute")
    @write_access_required
    def api_analyst_assign():
        """Assign an analyst category to a set of files surfaced by a search.

        Body: ``path_ids`` (list of file ids), ``category_id`` and/or
        ``category_name`` (with ``create_category`` to create it at the point
        of assignment), and ``source_query`` - the search query that surfaced
        the files, kept for audit traceability (FR-1.5).
        """
        data = request.get_json(silent=True) or request.form.to_dict()
        path_ids = data.get("path_ids") or data.get("file_ids") or []
        if isinstance(path_ids, str):
            path_ids = [p for p in path_ids.replace(",", " ").split() if p]

        category_id = data.get("category_id")
        try:
            category_id = int(category_id) if category_id else None
        except (TypeError, ValueError):
            category_id = None

        result, error = AnalystCategoryService.assign(
            path_ids=path_ids,
            category_id=category_id,
            category_name=data.get("category_name"),
            create_category=str(data.get("create_category", "false")).lower() == "true",
            source_query=data.get("source_query"),
            user=_current_user(),
        )
        if error:
            return jsonify({"success": False, "error": error}), 400
        return jsonify({"success": True, **result})

    @app.route("/api/analyst/remove", methods=["POST"])
    @limiter.limit("60 per minute")
    @write_access_required
    def api_analyst_remove():
        """Remove analyst categories from files (reversible, NFR-3).

        Body: ``path_ids`` and optional ``category_ids`` (omit to clear all
        analyst categories from the files). Smart categories are never
        touched (NFR-2).
        """
        data = request.get_json(silent=True) or request.form.to_dict()
        path_ids = data.get("path_ids") or []
        if isinstance(path_ids, str):
            path_ids = [p for p in path_ids.replace(",", " ").split() if p]

        category_ids = data.get("category_ids") or []
        if isinstance(category_ids, (int, str)):
            category_ids = [category_ids]

        result, error = AnalystCategoryService.remove(
            path_ids=path_ids,
            category_ids=category_ids,
            source_query=data.get("source_query"),
            user=_current_user(),
        )
        if error:
            return jsonify({"success": False, "error": error}), 400
        return jsonify({"success": True, **result})

    # ==================================================================
    # Listing / filters / stats / audit (FR-4.2)
    # ==================================================================

    @limiter.limit(INTERACTIVE_READ_LIMIT)
    @app.route("/api/analyst/assignments")
    def api_analyst_assignments():
        """Filtered assignment list for the Analyst Categorization View.

        Supports filtering by analyst category, analyst, assignment date
        range, and originating search query text (FR-4.2), plus a
        ``file_query`` free-text filter over file names/paths.
        """
        try:
            def _int_arg(name: str) -> Optional[int]:
                value = request.args.get(name)
                try:
                    return int(value) if value else None
                except (TypeError, ValueError):
                    return None

            assignments, total = AnalystCategoryService.list_assignments(
                category_id=_int_arg("category_id"),
                analyst_id=_int_arg("analyst_id"),
                date_from=query_text("date_from") or None,
                date_to=query_text("date_to") or None,
                query=query_text("q") or None,
                file_query=query_text("file_query") or None,
                file_id=_int_arg("file_id"),
                limit=min(int(request.args.get("per_page", 50)), 500),
                offset=max(int(request.args.get("page", 1)) - 1, 0) * min(int(request.args.get("per_page", 50)), 500),
            )
            per_page = min(int(request.args.get("per_page", 50)), 500)
            return jsonify({
                "assignments": assignments,
                "total": total,
                "page": max(int(request.args.get("page", 1)), 1),
                "per_page": per_page,
                "total_pages": max((total + per_page - 1) // per_page, 1) if per_page else 1,
            })
        except ValueError:
            return jsonify({"error": "Invalid pagination parameter"}), 400
        except Exception as e:
            logger.error(f"Error listing analyst assignments: {e}", exc_info=True)
            return client_error(e, subsystem="Api.routes.analyst_categories", status=500)

    @app.route("/api/analyst/analysts")
    def api_analyst_analysts():
        """Distinct analysts who have performed categorizations (FR-4.2)."""
        try:
            return jsonify(AnalystCategoryService.list_analysts())
        except Exception as e:
            logger.error(f"Error listing analysts: {e}", exc_info=True)
            return client_error(e, subsystem="Api.routes.analyst_categories", status=500)

    @app.route("/api/analyst/stats")
    def api_analyst_stats():
        try:
            return jsonify(AnalystCategoryService.get_stats())
        except Exception as e:
            logger.error(f"Error analyst stats: {e}", exc_info=True)
            return client_error(e, subsystem="Api.routes.analyst_categories", status=500)

    @app.route("/api/analyst/log")
    def api_analyst_log():
        """Audit trail of categorization actions (FR-1.5)."""
        try:
            try:
                limit = min(int(request.args.get("per_page", 50)), 500)
                page = max(int(request.args.get("page", 1)), 1)
            except ValueError:
                return jsonify({"error": "Invalid pagination parameter"}), 400

            analyst_id = None
            if request.args.get("analyst_id"):
                try:
                    analyst_id = int(request.args.get("analyst_id"))
                except (TypeError, ValueError):
                    analyst_id = None

            category_id = None
            if request.args.get("category_id"):
                try:
                    category_id = int(request.args.get("category_id"))
                except (TypeError, ValueError):
                    category_id = None

            entries, total = AnalystCategoryService.list_log(
                action=request.args.get("action") or None,
                analyst_id=analyst_id,
                category_id=category_id,
                limit=limit,
                offset=(page - 1) * limit,
            )
            return jsonify({
                "entries": entries,
                "total": total,
                "page": page,
                "per_page": limit,
            })
        except Exception as e:
            logger.error(f"Error listing analyst categorization log: {e}", exc_info=True)
            return client_error(e, subsystem="Api.routes.analyst_categories", status=500)

    @app.route("/api/analyst/export")
    def api_analyst_export():
        """CSV export of analyst categorizations honoring the view filters.

        Exports the ``Analyst Category`` fields as their own columns -
        independent from any smart-category export (FR-1.4).

        SECURITY: every exported cell passes through a CSV-injection guard -
        values beginning with ``=``, ``+``, ``-``, ``@``, a tab or a carriage
        return are prefixed with a single quote so spreadsheet applications
        cannot interpret them as formulas. The output is UTF-8 with a BOM so
        Excel opens Arabic/Hebrew/Persian text correctly.
        """
        try:
            import csv
            from io import StringIO
            from flask import Response

            def _int_arg(name: str) -> Optional[int]:
                value = request.args.get(name)
                try:
                    return int(value) if value else None
                except (TypeError, ValueError):
                    return None

            try:
                columns = AnalystCategoryService.resolve_export_columns(
                    request.args.get("columns"),
                )
            except ValueError as exc:
                return jsonify({"success": False, "error": str(exc)}), 400

            rows = AnalystCategoryService.export_rows(
                category_id=_int_arg("category_id"),
                analyst_id=_int_arg("analyst_id"),
                date_from=query_text("date_from") or None,
                date_to=query_text("date_to") or None,
                query=query_text("q") or None,
                file_query=query_text("file_query") or None,
            )

            def _csv_safe(value) -> str:
                """Neutralize CSV/formula injection in spreadsheet apps."""
                text = "" if value is None else str(value)
                if text.startswith(("=", "+", "-", "@", "\t", "\r")):
                    return "'" + text
                return text

            output = StringIO()
            writer = csv.writer(output)
            writer.writerow([header for header, _field in columns])
            for row in rows:
                writer.writerow([
                    _csv_safe(AnalystCategoryService.export_cell(row, field))
                    for _header, field in columns
                ])

            from core.security.disclosure import note_disclosure
            filters = {k: request.args.get(k) for k in
                       ("category_id", "analyst_id", "date_from", "date_to", "q", "file_query")
                       if request.args.get(k)}
            note_disclosure(kind="analyst_categorizations",
                            scope="filtered" if filters else "all", filters=filters,
                            unit="assignment", row_count=len(rows), format="csv",
                            columns=[header for header, _field in columns])
            # utf-8-sig prepends the BOM expected by Excel for Unicode CSV.
            payload = output.getvalue().encode("utf-8-sig")
            return Response(
                payload,
                mimetype="text/csv",
                headers={"Content-Disposition": "attachment; filename=analyst_categorizations.csv"},
            )
        except Exception as e:
            logger.error(f"Error exporting analyst categorizations: {e}", exc_info=True)
            return client_error(e, subsystem="Api.routes.analyst_categories", status=500)
