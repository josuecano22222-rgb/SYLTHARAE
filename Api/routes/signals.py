"""Content signals API (content-keyed; detectors ``temporal``, ``places``, ``identifiers``).

* ``GET  /api/content/<hash_id>/signals`` - any authenticated user. Signals
  and the detection run status (per detector, ``runs``) for one content;
  place signals carry their candidate places. ``detector`` (repeatable)
  restricts the detectors returned. Authorization is enforced in
  ``signal_store.signals_for`` against the caller's ``AccessScope`` before
  anything is read; "absent" and "not permitted" are the same 404.
  ``reference_date`` (YYYY-MM-DD) sets the clock used for past/future
  orientation. When omitted, the server's current UTC date is used and the
  response says so (``reference_date_source``) - the clock is never implicit.
* ``GET  /api/signals`` - Signal Explorer: every stored signal, filtered by
  signal dimensions and document criteria (or ``saved_search_id``), exact
  totals and facets, paginated (``services/detection/signal_query.py``).
* ``GET  /api/signals/<signal_id>`` - one signal: evidence, run, detector
  version, visible occurrences. 404 when absent or not visible.
* ``GET  /api/signals/horizon`` - resolved temporal signals grouped into
  overdue / week / month / quarter / later (and ``past`` on request) against
  ``reference_date``; undated references and unmeasured content are counted
  separately.
* ``GET  /signals`` - the Horizon & Signal Explorer page (reads the APIs above).
* ``POST /api/signals/redetect`` - administrators. Queues a
  ``signal_redetection`` job on the existing JobManager (no second job
  framework). Body: ``{"scope": "stale"|"all"|"hash_ids", "hash_ids": [...],
  "detectors": ["temporal", "places", "identifiers"]}`` (``detectors`` optional, default all).
  Audited as ``signals.redetect``.
* ``GET  /signals/detection`` - administrators: the Detection page.
* ``GET  /api/signals/detection/status`` - administrators: per detector, the
  current version, run counts by version and status, never-analysed and
  stale counts (one snapshot, exact).
* ``GET  /api/signals/detection/runs`` - administrators: run rows, newest
  first; ``detector``, ``status``, ``trigger``, ``hash_id``, ``version``
  (``current``/``older``/exact), ``limit`` <= 200, ``offset``.
"""

import datetime
import logging
import uuid

from flask import jsonify, request

from core.criteria.access import scope_for
from core.security.flask_ext import admin_required, current_user, login_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter
from services.detection import detectors as detector_registry
from services.detection import redetection, signal_store

logger = logging.getLogger(__name__)

#: Sources and sides listed in the page's filter menus (the API accepts any id).
FILTER_OPTION_LIMIT = 1000

#: Upper bound for an explicit hash_ids re-detection request.
MAX_REDETECT_IDS = 10_000


def _error(code, message, status):
    return jsonify({"success": False, "error": {
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:12]}}), status


def _parse_reference_date(raw):
    if raw in (None, ""):
        return datetime.datetime.now(datetime.timezone.utc).date(), "server_utc_today"
    try:
        return datetime.date.fromisoformat(raw), "request"
    except (TypeError, ValueError):
        raise ValueError("reference_date must be an ISO date (YYYY-MM-DD)")


def register_signal_routes(app):

    @app.route("/api/content/<int:hash_id>/signals", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_content_signals(hash_id):
        try:
            reference_date, source = _parse_reference_date(request.args.get("reference_date"))
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        wanted = request.args.getlist("detector") or None
        try:
            detector_registry.validate(wanted)
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        from Api.utils.utils import get_connection

        try:
            with get_connection() as conn:
                try:
                    with conn.cursor() as cur:
                        body = signal_store.signals_for(
                            cur, hash_id, reference_date, scope=scope_for(current_user()),
                            detectors=wanted)
                finally:
                    conn.rollback()  # read-only; never leave a transaction open
        except LookupError:
            return _error("NOT_FOUND", "Content not found", 404)
        except Exception:
            logger.exception("signals read failed for hash_id=%s", hash_id)
            return _error("SIGNALS_UNAVAILABLE", "Signals could not be read", 500)
        body["reference_date_source"] = source
        body["success"] = True
        return jsonify(body)

    @app.route("/api/signals/redetect", methods=["POST"])
    @admin_required
    @limiter.limit("10 per minute")
    def api_signals_redetect():
        data = request.get_json(silent=True) or {}
        scope = data.get("scope", "stale")
        if scope not in redetection.SCOPES:
            return _error("VALIDATION_FAILED",
                          f"scope must be one of: {', '.join(redetection.SCOPES)}", 400)
        hash_ids = None
        if scope == "hash_ids":
            raw = data.get("hash_ids")
            if (not isinstance(raw, list) or not raw
                    or not all(isinstance(h, int) and not isinstance(h, bool) and h > 0
                               for h in raw)):
                return _error("VALIDATION_FAILED",
                              "hash_ids must be a non-empty list of positive integers", 400)
            if len(raw) > MAX_REDETECT_IDS:
                return _error("VALIDATION_FAILED",
                              f"at most {MAX_REDETECT_IDS} hash_ids per request", 400)
            hash_ids = sorted(set(raw))
        elif data.get("hash_ids"):
            return _error("VALIDATION_FAILED", "hash_ids is only valid with scope 'hash_ids'", 400)
        try:
            detectors = list(detector_registry.validate(data.get("detectors")))
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)

        from services.jobs.manager import JobManager
        from services.jobs.models import job_to_api

        manager = JobManager.get_instance()
        user = current_user()
        try:
            job = manager.create_job(
                "signal_redetection",
                source=f"signals:{scope}" + (f":{len(hash_ids)}" if hash_ids else "")
                + f":{'+'.join(detectors)}",
                options={"scope": scope, "hash_ids": hash_ids, "detectors": detectors},
                created_by=getattr(user, "username", None) or "system",
            )
        except Exception:
            logger.exception("signal re-detection job creation failed")
            return _error("JOB_CREATE_FAILED", "Re-detection job could not be created", 500)
        # Re-detection replaces stored signals: record who asked for what.
        from core.security.service import get_auth_service

        get_auth_service().audit(
            "signals.redetect", user_id=getattr(user, "id", None),
            username=getattr(user, "username", None),
            resource=f"signal_redetection:{job['job_id']}",
            detail={"scope": scope, "detectors": detectors,
                    "hash_id_count": len(hash_ids) if hash_ids else None},
            ip_address=request.remote_addr)
        if manager.synchronous:
            job = manager.get(job["job_id"])
            return jsonify({"success": True, "job": job_to_api(job)})
        return jsonify({"success": True, "job": job_to_api(job)}), 202

    # ------------------------------------------------------------------
    # Detection coverage and run history (administrators)
    # ------------------------------------------------------------------

    @app.route("/signals/detection", methods=["GET"])
    @admin_required
    def detection_page():
        """Detection coverage, run history and re-detection (registry ``detection``)."""
        from flask import render_template

        from services.detection import detection_admin

        return render_template("Signals/detection.html", page_data={
            "detectors": list(detector_registry.NAMES),
            "statuses": list(detection_admin.STATUSES),
            "page_size": detection_admin.DEFAULT_LIMIT,
            "max_hash_ids": MAX_REDETECT_IDS,
        })

    @app.route("/api/signals/detection/status", methods=["GET"])
    @admin_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_detection_status():
        from Api.utils.utils import get_connection
        from services.detection import detection_admin

        try:
            with get_connection() as conn:
                body = detection_admin.detection_status(conn)
        except detection_admin.DetectionAdminError as exc:
            return _error(exc.code, exc.message, exc.status)
        return jsonify(dict(body, success=True))

    @app.route("/api/signals/detection/runs", methods=["GET"])
    @admin_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_detection_runs():
        from Api.utils.utils import get_connection
        from services.detection import detection_admin

        try:
            filters = detection_admin.parse_run_filters(request.args.to_dict(flat=True))
            with get_connection() as conn:
                body = detection_admin.list_runs(conn, filters)
        except detection_admin.DetectionAdminError as exc:
            return _error(exc.code, exc.message, exc.status)
        return jsonify(dict(body, success=True))

    # ------------------------------------------------------------------
    # Signal Explorer and Horizon
    # ------------------------------------------------------------------

    def _load_saved_search(search_id):
        """Stored criteria of a saved search the caller may read, else None."""
        from Api.services.saved_searches_repository import can_read
        from Api.services.search_history import SavedSearchesService
        from core.criteria.model import from_dict

        row = SavedSearchesService._repo().get(search_id)
        user = current_user()
        user_id = getattr(user, "id", None)
        is_admin = bool(user_id is not None and user.has_role("admin"))
        if not can_read(row, user_id, is_admin):
            return None
        return from_dict(row["criteria"])

    def _signal_read(fn):
        """Parse the shared parameters, run ``fn(cur, filter, criteria,
        saved_search_id, reference_date)`` in a read-only transaction and map
        failures to the API error shape. Nothing is swallowed: an unexpected
        error is logged and returned as 500."""
        from services.detection import signal_query

        try:
            reference_date, source = _parse_reference_date(request.args.get("reference_date"))
            sig_filter = signal_query.parse_filter(request.args)
            criteria, saved_id = signal_query.parse_document_criteria(
                request.args, load_saved_search=_load_saved_search)
        except LookupError:
            return _error("NOT_FOUND", "Saved search not found", 404)
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        from Api.utils.utils import get_connection

        try:
            with get_connection() as conn:
                # The pool's health check (SELECT 1, autocommit off) leaves the
                # connection inside a transaction; end it so the read starts
                # its own REPEATABLE READ snapshot (signal_query._begin_read).
                conn.rollback()
                try:
                    with conn.cursor() as cur:
                        body = fn(cur, sig_filter, criteria, saved_id, reference_date)
                finally:
                    conn.rollback()  # read-only; never leave a transaction open
        except signal_query.SignalQueryError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        except LookupError:
            return _error("NOT_FOUND", "Signal not found", 404)
        except Exception as exc:
            if type(exc).__name__ == "QueryCanceled":
                logger.warning("signal read exceeded the statement timeout")
                return _error("QUERY_TIMEOUT", "The request took too long; narrow the filters",
                              503)
            logger.exception("signal read failed")
            return _error("SIGNALS_UNAVAILABLE", "Signals could not be read", 500)
        body["reference_date_source"] = source
        body["success"] = True
        return jsonify(body)

    @app.route("/api/signals", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_signal_explorer():
        from services.detection import signal_query

        return _signal_read(lambda cur, f, criteria, saved_id, ref: signal_query.explore(
            cur, f, criteria, scope_for(current_user()), ref,
            sort=request.args.get("sort") or "event_date", limit=request.args.get("limit"),
            offset=request.args.get("offset"), saved_search_id=saved_id))

    @app.route("/api/signals/horizon", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_signal_horizon():
        from services.detection import signal_query

        return _signal_read(lambda cur, f, criteria, saved_id, ref: signal_query.horizon(
            cur, f, criteria, scope_for(current_user()), ref,
            buckets=[b for b in request.args.getlist("bucket") if b],
            sort=request.args.get("sort") or "event_date",
            limit=request.args.get("limit"), offset=request.args.get("offset"),
            saved_search_id=saved_id))

    @app.route("/api/signals/<int:signal_id>", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_signal_detail(signal_id):
        from services.detection import signal_query

        return _signal_read(lambda cur, f, criteria, saved_id, ref: signal_query.signal_detail(
            cur, signal_id, scope_for(current_user()), ref))

    @app.route("/signals", methods=["GET"])
    @login_required
    def signals_page():
        from flask import render_template

        from database.queries import list_sides, list_sources
        from services.detection import signal_query

        # The filter lists are a convenience: the API accepts any id. They are
        # capped, so one row more than the cap is read to *know* whether the
        # list is complete, and the page says so either way - an empty or cut
        # list must not look like "there are no other sources".
        options_status = "complete"
        try:
            source_rows = list_sources(limit=FILTER_OPTION_LIMIT + 1)
            side_rows = list_sides(limit=FILTER_OPTION_LIMIT + 1)
        except Exception:
            logger.exception("signals page: sources/sides unavailable")
            source_rows, side_rows, options_status = [], [], "unavailable"
        if len(source_rows) > FILTER_OPTION_LIMIT or len(side_rows) > FILTER_OPTION_LIMIT:
            options_status = "truncated"
        sources = {r["id"]: r["name"] for r in source_rows[:FILTER_OPTION_LIMIT]}
        sides = {r["id"]: r["name"] for r in side_rows[:FILTER_OPTION_LIMIT]}
        return render_template(
            "Signals/signals.html",
            page_data={
                "sources": sources, "sides": sides, "options_status": options_status,
                "option_limit": FILTER_OPTION_LIMIT,
                "buckets": list(signal_query.DEFAULT_BUCKETS) + ["past"],
                "confidence": list(signal_query.CONFIDENCE_FILTER),
                "languages": list(signal_query.LANGUAGE_FILTER),
                "calendars": list(signal_query.CALENDARS),
                "detectors": list(detector_registry.NAMES),
                "signal_types": list(signal_query.SIGNAL_TYPES),
                "orientations": list(signal_query.ORIENTATION_FILTER),
                "sorts": list(signal_query.EXPLORER_SORTS),
                "text_modes": list(signal_query.TEXT_MATCH_MODES),
                "resolutions": list(signal_query.RESOLUTIONS),
                "max_page_size": signal_query.MAX_PAGE_SIZE,
                "kind_presets": [
                    {"id": p["id"], "detectors": list(p["detectors"]),
                     "signal_types": list(p["signal_types"]),
                     "identifier_kinds": list(p.get("identifier_kinds", ()))}
                    for p in signal_query.EXPLORER_KIND_PRESETS
                ],
            })
