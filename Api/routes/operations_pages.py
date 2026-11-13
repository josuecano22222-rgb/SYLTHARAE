"""Page routes for the unified Operations UI (Input, Import, Jobs)."""
import logging

from flask import Response, render_template, request

logger = logging.getLogger(__name__)


def register_operations_pages(app):
    """Register the Operations Center pages (authenticated by middleware)."""

    @app.route("/operations/input")
    def operations_input_page():
        return render_template("Operations/input.html")

    @app.route("/operations/input/reports/<job_id>", methods=["GET"])
    def ingestion_batch_print(job_id):
        """Dedicated printable report for one ingestion batch."""
        from flask_babel import get_locale

        from services.jobs.manager import JobManager
        from services.reporting.ingestion_batch import (
            document_from_job, render_html,
        )

        job = JobManager.get_instance().get(job_id)
        if job is None:
            return Response("Job not found", status=404, mimetype="text/plain")
        if job.get("job_type") not in (None, "ingestion") and not (
            (job.get("result_summary") or {}).get("batch_report")
        ):
            return Response(
                "This job has no ingestion batch report",
                status=400, mimetype="text/plain",
            )
        locale = request.args.get("lang") or request.values.get("lang")
        if not locale:
            current = get_locale()
            locale = str(current) if current else "en"
        language = locale.split("_")[0].split("-")[0]
        auto_print = request.args.get("print") in ("1", "true", "yes")
        try:
            document = document_from_job(job, persist=True)
            body = render_html(
                document, interactive=True, auto_print=auto_print,
                language=language)
        except Exception:
            logger.exception("Failed to render ingestion batch report for %s", job_id)
            return Response(
                "Could not build the ingestion batch report",
                status=500, mimetype="text/plain",
            )
        response = Response(body, mimetype="text/html; charset=utf-8")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/operations/import")
    def operations_import_page():
        return render_template("Operations/import_center.html")

    @app.route("/operations/jobs")
    def operations_jobs_page():
        return render_template("Operations/jobs.html")

    @app.route("/operations/jobs/<job_id>")
    def operations_job_detail_page(job_id):
        return render_template("Operations/job_detail.html", job_id=job_id)
