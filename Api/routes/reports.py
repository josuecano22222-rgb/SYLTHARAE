"""Reports: the processing report, its dashboard, and the entry dossier.

===================================  ==========================================
``GET /reports``                      the Reports page (registry ``reports``)
``GET /reports/dashboard``            the Reports Dashboard (registry
                                      ``reports_dashboard``)
``GET /reports/entries/print``        the printable entry dossier (the
                                      ``entry_dossier_print`` alias of
                                      ``reports``)
``GET /api/reports/overview``         the processing report as JSON
``GET /api/reports/dashboard``        the dashboard summary as JSON
===================================  ==========================================

Every number comes from ``services.reporting.processing``: one statement over
``paths`` joined to ``hash_contexts``, ``sources``, ``sides``, the
classification chain (``words_hashs`` -> ``words_categorys`` -> ``categorys``
-> ``words``) and the keyword chain. There is no report table, no cached
counter and no per-row query here.

Filters are the source, the side, a date range and a time range. They are
accepted from the query string, validated strictly, and applied inside the
statement - so the page, the JSON and the dossier always describe the same
population.
"""

from __future__ import annotations

import html
import logging
import uuid
from datetime import date, datetime, time

from flask import jsonify, render_template, request

from core.security.flask_ext import current_user, login_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter
from settings.languages import SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

#: The registered reports this surface offers. Each is a way of reading the
#: same stored material; none of them writes a report row.
REPORTS = (
    {"id": "processing_overview", "title": "Processing report",
     "description": "Every stored occurrence, what happened to it, and what it "
                    "was classified as."},
    {"id": "entry_dossier", "title": "Entry dossier",
     "description": "One page per stored entry, printable."},
)

_DATE_FIELDS = ("date_from", "date_to")
_TIME_FIELDS = ("time_from", "time_to")


def _error(code: str, message: str, status: int):
    return jsonify({"success": False, "error": {
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:12]}}), status


def _parse_date(name: str):
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"{name} must be a date in YYYY-MM-DD form")


def _parse_time(name: str):
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        return time.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"{name} must be a time in HH:MM[:SS] form")


def _parse_int(name: str):
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    if not raw.isascii() or not raw.isdigit():
        raise ValueError(f"{name} must be a positive integer")
    return int(raw)


def report_filters() -> dict:
    """The filters from the query string, or ``ValueError`` explaining one."""
    filters = {"source_id": _parse_int("source_id"),
               "side_id": _parse_int("side_id")}
    for name in _DATE_FIELDS:
        filters[name] = _parse_date(name)
    for name in _TIME_FIELDS:
        filters[name] = _parse_time(name)
    if (filters["date_from"] and filters["date_to"]
            and filters["date_from"] > filters["date_to"]):
        raise ValueError("date_from is after date_to")
    return filters


def _filter_params(filters: dict) -> dict:
    """Filters as the URL query the templates and links carry forward."""
    out = {}
    for key, value in filters.items():
        if value is None:
            continue
        out[key] = value.isoformat() if isinstance(value, (date, time)) else str(value)
    return out


def _query_suffix(filters: dict) -> str:
    """``?source_id=1&date_from=...`` for links that keep the filters."""
    from urllib.parse import urlencode

    params = _filter_params(filters)
    return ("?" + urlencode(params)) if params else ""


def _sources_and_sides():
    """The filter options: the stored sources and sides, read once."""
    from Api.utils import execute_query

    try:
        sources = execute_query(
            "SELECT id, name FROM sources ORDER BY name", fetch="all") or []
        sides = execute_query(
            "SELECT id, name FROM sides ORDER BY name", fetch="all") or []
    except Exception:  # pragma: no cover - an empty filter list, never a 500
        logger.exception("Could not read the source/side options")
        return [], []
    return ([{"id": r[0], "name": r[1]} for r in sources],
            [{"id": r[0], "name": r[1]} for r in sides])


def _totals_with_rates(report: dict) -> dict:
    """Add the percentages the page shows; derived here, never stored."""
    totals = dict(report.get("totals") or {})
    total = int(totals.get("total") or 0)
    for key in ("success", "partial", "failed", "skipped"):
        value = int(totals.get(key) or 0)
        totals[key] = value
        totals[f"{key}_pct"] = round(100.0 * value / total, 1) if total else 0.0
    totals["total"] = total
    for row in report.get("by_file_type") or []:
        row_total = int(row.get("total") or 0)
        for key in ("success", "partial", "failed", "skipped"):
            row[key] = int(row.get(key) or 0)
        row["success_pct"] = (round(100.0 * row["success"] / row_total, 1)
                              if row_total else 0.0)
    return totals


def _dossier_entries(filters: dict, ids=None, limit: int = 500):
    """The per-entry rows of the dossier: one row per stored occurrence.

    A second, deliberately simple statement rather than a wide join: the
    dossier lists entries, so the classification and keyword detail is
    aggregated per hash in the same statement instead of multiplying rows.
    """
    from Api.utils import execute_query

    where = [
        "(%(source_id)s::int IS NULL OR hc.source_id = %(source_id)s::int)",
        "(%(side_id)s::int IS NULL OR hc.side_id = %(side_id)s::int)",
        "(%(date_from)s::date IS NULL OR p.date_creation >= %(date_from)s::date)",
        "(%(date_to)s::date IS NULL OR p.date_creation <= %(date_to)s::date)",
    ]
    params = dict(filters)
    params["limit"] = limit
    if ids:
        where.append("p.id = ANY(%(ids)s)")
        params["ids"] = list(ids)
    sql = f"""
        SELECT p.id, p.file_name, p.file_type, p.file_size, p.file_path,
               p.file_date, p.processing_status, p.status_detail,
               s.name AS source_name, sd.name AS side_name,
               COALESCE((SELECT array_agg(DISTINCT w.word ORDER BY w.word)
                         FROM words_hashs wh
                         JOIN words_categorys wc ON wc.word_id = wh.word_id
                         JOIN categorys c ON c.id = wc.category_id
                         JOIN words w ON w.id = c.word_id
                         WHERE wh.hash_id = hc.hash_id), '{{}}') AS classifications,
               COALESCE((SELECT array_agg(DISTINCT kh.keyword_id)
                         FROM keywords_hashs kh
                         WHERE kh.hash_id = hc.hash_id), '{{}}') AS keyword_ids
        FROM paths p
        LEFT JOIN hash_contexts hc ON hc.id = p.context_id
        LEFT JOIN sources s ON s.id = hc.source_id
        LEFT JOIN sides sd ON sd.id = hc.side_id
        WHERE {' AND '.join(where)}
        ORDER BY p.id
        LIMIT %(limit)s
    """  # nosec B608 - the WHERE list is constant text, every value is bound
    rows = execute_query(sql, params, fetch="all") or []
    from services.reporting.processing import keyword_phrases

    # ``keywords.keyword`` is a packed word-id list; decode the phrases for
    # every keyword on the page in one pass rather than per row.
    phrases = keyword_phrases({kid for r in rows for kid in (r[11] or [])})
    return [
        {"path_id": r[0], "file_name": r[1], "file_type": r[2], "file_size": r[3],
         "file_path": r[4], "file_date": r[5], "processing_status": r[6],
         "status_detail": r[7], "source": r[8], "side": r[9],
         "classifications": list(r[10] or []),
         "keywords": [phrases.get(kid, str(kid)) for kid in (r[11] or [])]}
        for r in rows
    ]


def _dossier_html(entries, totals, filters) -> str:
    """Self-contained printable HTML (no external CSS: it must print alone)."""
    esc = html.escape
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    scope = ", ".join(f"{k}={v}" for k, v in _filter_params(filters).items()) or "all entries"
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>Entry dossier</title><style>",
        "body{font:11pt/1.4 system-ui,sans-serif;margin:24px;color:#111}",
        "h1{font-size:16pt;margin:0 0 4px}",
        "table{border-collapse:collapse;width:100%;margin-top:12px}",
        "th,td{border:1px solid #bbb;padding:4px 6px;text-align:left;vertical-align:top}",
        "th{background:#eee}.muted{color:#555;font-size:9pt}",
        "@media print{body{margin:0}}",
        "</style></head><body>",
        f"<h1>Entry dossier</h1><div class='muted'>Generated {esc(when)} - {esc(scope)}</div>",
        "<p>Total {t} &middot; success {s} &middot; partial {p} &middot; "
        "failed {f} &middot; skipped {k}</p>".format(
            t=totals.get("total", 0), s=totals.get("success", 0),
            p=totals.get("partial", 0), f=totals.get("failed", 0),
            k=totals.get("skipped", 0)),
        "<table><thead><tr><th>#</th><th>File</th><th>Type</th><th>Status</th>",
        "<th>Source</th><th>Side</th><th>Classifications</th><th>Keywords</th>",
        "</tr></thead><tbody>",
    ]
    for entry in entries:
        parts.append(
            "<tr><td>{id}</td><td>{name}<div class='muted'>{path}</div></td>"
            "<td>{type}</td><td>{status}{detail}</td><td>{source}</td><td>{side}</td>"
            "<td>{cls}</td><td>{kw}</td></tr>".format(
                id=entry["path_id"], name=esc(entry["file_name"] or ""),
                path=esc(entry["file_path"] or ""), type=esc(entry["file_type"] or ""),
                status=esc(entry["processing_status"] or ""),
                detail=(f"<div class='muted'>{esc(entry['status_detail'])}</div>"
                        if entry["status_detail"] else ""),
                source=esc(entry["source"] or ""), side=esc(entry["side"] or ""),
                cls=esc(", ".join(entry["classifications"])),
                kw=esc(", ".join(entry["keywords"]))))
    if not entries:
        parts.append("<tr><td colspan='8' class='muted'>No entries match.</td></tr>")
    parts.append("</tbody></table></body></html>")
    return "".join(parts)


def register_report_routes(app):
    """Register the report pages and their JSON endpoints."""

    @app.route("/reports", methods=["GET"])
    @login_required
    def reports_page():
        """The Reports page (registry ``reports``)."""
        from services.reporting.processing import report_for_request

        user = current_user()
        try:
            filters = report_filters()
        except ValueError as exc:
            return render_template(
                "Reports/reports.html",
                page_data={"error": str(exc), "filters": {}, "reports": list(REPORTS)},
                report=None, totals={}, query_suffix="",
                filter_options={"sources": [], "sides": []},
            ), 400
        sources, sides = _sources_and_sides()
        report = report_for_request(filters)
        totals = _totals_with_rates(report)
        return render_template(
            "Reports/reports.html",
            page_data={
                "reports": list(REPORTS),
                "filters": _filter_params(filters),
                "user_id": getattr(user, "id", None),
            },
            query_suffix=_query_suffix(filters),
            report=report, totals=totals,
            filter_options={"sources": sources, "sides": sides},
        )

    @app.route("/reports/dashboard", methods=["GET"])
    @login_required
    def reports_dashboard_page():
        """The Reports Dashboard (registry ``reports_dashboard``)."""
        from services.reporting.processing import report_for_request

        user = current_user()
        try:
            filters = report_filters()
        except ValueError as exc:
            return render_template(
                "Reports/dashboard.html",
                page_data={"error": str(exc), "filters": {}}, report=None,
                totals={}, query_suffix="",
                filter_options={"sources": [], "sides": []},
            ), 400
        sources, sides = _sources_and_sides()
        report = report_for_request(filters)
        totals = _totals_with_rates(report)
        return render_template(
            "Reports/dashboard.html",
            page_data={"filters": _filter_params(filters),
                       "user_id": getattr(user, "id", None)},
            query_suffix=_query_suffix(filters),
            report=report, totals=totals,
            filter_options={"sources": sources, "sides": sides},
        )

    @app.route("/reports/entries/print", methods=["GET"])
    @login_required
    def entry_dossier_print():
        """The printable dossier; ``?ids=1,2,3`` narrows it to those entries."""
        from flask import Response

        from services.reporting.processing import report_for_request

        try:
            filters = report_filters()
            ids = []
            raw_ids = (request.args.get("ids") or "").strip()
            if raw_ids:
                for part in raw_ids.split(","):
                    part = part.strip()
                    if not part or not part.isascii() or not part.isdigit():
                        raise ValueError("ids must be a comma-separated list of "
                                         "positive integers")
                    ids.append(int(part))
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        entries = _dossier_entries(filters, ids=ids or None)
        totals = _totals_with_rates(report_for_request(filters))
        body = _dossier_html(entries, totals, filters)
        response = Response(body, mimetype="text/html; charset=utf-8")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/api/reports/overview", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_reports_overview():
        """The processing report as JSON (same statement as the page)."""
        from services.reporting.processing import report_for_request

        try:
            filters = report_filters()
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        report = report_for_request(filters)
        report["totals"] = _totals_with_rates(report)
        report["filters"] = _filter_params(filters)
        return jsonify({"success": True, **report})

    @app.route("/api/reports/dashboard", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_reports_dashboard():
        """The dashboard summary as JSON: totals, per-type and the facets."""
        from services.reporting.processing import report_for_request

        try:
            filters = report_filters()
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        report = report_for_request(filters)
        totals = _totals_with_rates(report)
        return jsonify({
            "success": True,
            "totals": totals,
            "by_file_type": report.get("by_file_type") or [],
            "classifications": (report.get("classifications") or [])[:20],
            "keywords": (report.get("keywords") or [])[:20],
            "sources": report.get("sources") or [],
            "sides": report.get("sides") or [],
            "filters": _filter_params(filters),
            # The dossier is rendered in the interface language the request
            # carries; this is the same list the language switcher offers.
            "languages": [{"code": code, "name": name} for code, name in
                          SUPPORTED_LANGUAGES.items()],
        })
