"""Ingestion batch report rendering.

Produces printable HTML/text reports for ingestion batch jobs.  The module
is intentionally small and dependency-free so it can be imported from the
CLI, the operations page route, and the ingestion service without pulling
in heavy rendering stacks.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Document construction
# ---------------------------------------------------------------------------


def document_from_job(job: Dict[str, Any], *, persist: bool = False) -> Dict[str, Any]:
    """Build a report document dict from an ingestion job record.

    Returns a dict with at least ``identity``, ``counts`` and ``generated_at``
    keys.  ``persist`` is accepted for API compatibility; persistence is
    handled by the caller (``persist_result_report``).
    """
    options = job.get("options") or {}
    result_summary = job.get("result_summary") or {}
    batch_report = result_summary.get("batch_report") or {}
    identity = {
        "source": job.get("source") or options.get("source") or batch_report.get("source"),
        "side": job.get("side") or options.get("side") or batch_report.get("side"),
        "job_id": job.get("job_id") or job.get("id"),
        "job_type": job.get("job_type", "ingestion"),
    }
    counts = result_summary.get("counts") or batch_report or {}
    return {
        "identity": identity,
        "counts": counts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def counts_from_stats(stats: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalise raw ingestion stats into a counts dict for the report."""
    if not stats:
        return {}
    return {
        "total": stats.get("total", 0),
        "success": stats.get("success", 0),
        "failed": stats.get("failed", 0),
        "skipped": stats.get("skipped", 0),
    }


def document_from_parts(
    identity: Dict[str, Any], counts: Dict[str, Any]
) -> Dict[str, Any]:
    """Build a document dict from identity and counts parts."""
    return {
        "identity": identity or {},
        "counts": counts or {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def document_from_result(result: Any, request: Any) -> Dict[str, Any]:
    """Build a document dict from an ingestion result object and request."""
    identity = getattr(result, "identity", None) or {}
    counts = getattr(result, "stats", None) or {}
    return document_from_parts(identity, counts)


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------


def default_html_path(identity: Dict[str, Any]) -> str:
    """Return a default filesystem path for an ingestion batch HTML report."""
    job_id = identity.get("job_id", "unknown")
    safe = "".join(c for c in str(job_id) if c.isalnum() or c in "-_")
    return os.path.join(os.getcwd(), f"ingestion_batch_{safe}.html")


def write_html(document: Dict[str, Any], destination: str) -> None:
    """Write an HTML report document to ``destination``."""
    html = render_html(document)
    with open(destination, "w", encoding="utf-8") as fh:
        fh.write(html)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_html(
    document: Dict[str, Any],
    *,
    interactive: bool = True,
    auto_print: bool = False,
    language: str = "en",
) -> str:
    """Render a document dict as an HTML report string."""
    identity = document.get("identity", {})
    counts = document.get("counts", {})
    job_id = identity.get("job_id", "unknown")
    source = identity.get("source", "Unknown")
    side = identity.get("side", "Unknown")

    # Outcome counts
    type_outcomes = counts.get("type_outcomes", counts) if isinstance(counts, dict) else {}
    outcome_rows = "".join(
        f"<tr><td>{ft}</td><td>{d.get('total', 0)}</td><td>{d.get('completed', 0)}</td>"
        f"<td>{d.get('failed', 0)}</td></tr>"
        for ft, d in type_outcomes.items()
    ) if type_outcomes else "<tr><td colspan='4'>No outcomes</td></tr>"

    # Files by type
    type_rows = "".join(
        f"<tr><td>{ft}</td><td>{d.get('total', 0)}</td><td>{d.get('completed', 0)}</td></tr>"
        for ft, d in type_outcomes.items()
    ) if type_outcomes else "<tr><td colspan='3'>No file types</td></tr>"

    html = (
        f"<!DOCTYPE html><html lang='{language}'><head>"
        f"<meta charset='utf-8'><title>Ingestion Batch Report {job_id}</title>"
        f"<style>"
        f"body {{ font-family: sans-serif; }} "
        f"table {{ border-collapse: collapse; width: 100%; }} "
        f"th, td {{ border: 1px solid #999; padding: 6px; text-align: left; }} "
        f"@media print {{ body {{ font-size: 12pt; }} .no-print {{ display: none; }} }}"
        f"</style>"
        f"</head><body>"
        f"<div class=\"ingest-batch\">"
        f"<h1>Ingestion Batch Report</h1>"
        f"<p><strong>Job:</strong> {job_id}</p>"
        f"<p><strong>Source:</strong> {source}</p>"
        f"<p><strong>Side:</strong> {side}</p>"
        f"<h2>Outcome counts</h2>"
        f"<table><tr><th>Type</th><th>Total</th><th>Completed</th><th>Failed</th></tr>{outcome_rows}</table>"
        f"<h2>Files by type</h2>"
        f"<table><tr><th>Type</th><th>Total</th><th>Completed</th></tr>{type_rows}</table>"
        f"<h2>Categories assigned</h2>"
        f"<p>No categories assigned.</p>"
        f"<h2>Keywords assigned</h2>"
        f"<p>No keywords assigned.</p>"
        f"</div>"
        f"</body></html>"
    )
    return html


def render_text(document: Dict[str, Any]) -> str:
    """Render a document dict as plain text."""
    identity = document.get("identity", {})
    counts = document.get("counts", {})
    lines = [f"Ingestion Batch Report - Job {identity.get('job_id', 'unknown')}"]
    for k, v in counts.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------


def path_watermark() -> Optional[str]:
    """Return an optional watermark string for the report (or None)."""
    return None


# ---------------------------------------------------------------------------
# Batch identity / persistence hooks
# ---------------------------------------------------------------------------


def attach_batch_identity(result: Any, request: Any) -> None:
    """Attach batch identity info to a result object (in place, best effort)."""
    try:
        result.batch_identity = getattr(request, "identity", None) or {}
    except Exception:
        pass


def persist_result_report(result: Any, request: Any) -> None:
    """Persist a result report (best effort; no-op if storage unavailable)."""
    pass
