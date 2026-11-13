"""The taxonomy's status and its one-button reconciliation.

``GET  /api/taxonomy/status``  what the workbook says, what the database holds,
                               and how many changes a sync would make.
``POST /api/taxonomy/sync``    run the reconciliation as a job (or inline with
                               ``{"sync": true}``), dry-run by default.

The workbook is authoritative (see ``docs/DATA_ENTRY.md``), so the interface
needs to be able to answer "is the database up to date?" without shell access.
Both routes call :mod:`services.importing.taxonomy_sync_service` - the same code
the command line and the job use - so the answer here is the answer everywhere.
"""

from __future__ import annotations

import logging
import uuid

from flask import jsonify, request

from core.security.flask_ext import current_user, login_required, write_access_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)


#: How many rows of each plan list an API response carries. The counts are the
#: answer; a thousand examples of it are not, and the plans can hold exactly one
#: sample per entry in the workbook.
SAMPLE = 10


def _summarise(plan):
    """Counts plus a sample, never the whole plan."""
    if not plan:
        return {}
    out = {}
    for key, value in plan.items():
        if isinstance(value, list):
            out[key] = value[:SAMPLE]
            out[f"{key}_count"] = len(value)
        elif isinstance(value, dict):
            out[key] = value
        else:
            out[key] = value
    return out


def _error(code, message, status):
    return jsonify({"success": False, "error": {
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:12]}}), status


def _audit(action, detail=None):
    from core.security.service import get_auth_service

    user = current_user()
    get_auth_service().audit(
        action, user_id=getattr(user, "id", None),
        username=getattr(user, "username", None),
        resource="taxonomy:workbook", detail=detail, ip_address=request.remote_addr)


def register_taxonomy_routes(app):
    from services.importing.taxonomy_sync_service import (
        TaxonomySyncRequest, TaxonomySyncService, TaxonomySyncValidationError,
    )

    @app.route("/api/taxonomy/status", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_taxonomy_status():
        requested = request.args.get("data_file") or None
        try:
            service = TaxonomySyncService()
            service.resolve_data_file(requested)     # a bad name is a 400, below
            request_obj = TaxonomySyncRequest(data_file=requested, apply=False)
            result = service.run(request_obj)
        except TaxonomySyncValidationError as exc:
            # The request named a file that is not where workbooks live. That
            # is the caller's mistake (400), not a missing feature.
            return _error("VALIDATION_FAILED", str(exc), 400)
        if not result.success:
            return _error("TAXONOMY_INVALID", "; ".join(result.errors[:5]) or
                          "the workbook could not be read", 409)
        stats = result.stats
        plan = stats.get("plan") or {}
        return jsonify({
            "success": True,
            "status": {
                "in_sync": not stats.get("planned_changes"),
                "planned_changes": stats.get("planned_changes", 0),
                "data_file": stats.get("data_file"),
                "fingerprint": stats.get("fingerprint"),
                "layout": stats.get("layout"),
                "workbook": stats.get("workbook"),
                "plan": _summarise(plan),
                # The same sentences the sync job returns, so "is the database
                # up to date?" has an answer in words and not only in counts.
                "summary": stats.get("summary") or [],
                "not_stored_count": stats.get("not_stored_count", 0),
                "orphan_words_count": stats.get("orphan_words_count", 0),
                "orphan_words_sample": stats.get("orphan_words_sample") or [],
                "warnings": result.warnings,
            },
        })

    @app.route("/api/taxonomy/sync", methods=["POST"])
    @write_access_required
    def api_taxonomy_sync():
        body = request.get_json(silent=True) or {}
        apply_changes = bool(body.get("apply", False))
        options = {
            "data_file": body.get("data_file") or None,
            "apply": apply_changes,
            "auto_renames": bool(body.get("auto_renames", True)),
            # Deletions are never implied by a button press: an operator asks
            # for them by name, because a half-saved workbook and a deliberate
            # removal look identical to the reader.
            "prune_categories": bool(body.get("prune_categories", False)),
            "prune_words": bool(body.get("prune_words", False)),
        }
        try:
            # Validate before creating a job: a refusal the page can show beats
            # a failed job the operator has to go and read.
            TaxonomySyncService().resolve_data_file(options["data_file"])
        except TaxonomySyncValidationError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)

        if body.get("sync") or body.get("inline"):
            result = TaxonomySyncService().run(TaxonomySyncRequest(**options))
            _audit("taxonomy.sync", {"applied": result.applied,
                                     "written": result.stats.get("written")})
            if not result.success:
                return _error("TAXONOMY_INVALID", "; ".join(result.errors[:5]) or
                              "the workbook could not be read", 409)
            return jsonify({"success": True, "result": result.to_dict()})

        from services.jobs.manager import JobManager

        user = current_user()
        job = JobManager.get_instance().create_job(
            "taxonomy_sync",
            source=options["data_file"] or "domain_data.xlsx",
            options=options,
            created_by=getattr(user, "username", None) or "system",
        )
        _audit("taxonomy.sync.queued", {"job_id": job.get("job_id"),
                                        "apply": apply_changes})
        return jsonify({"success": True, "job": job}), 202
