"""Serialization helpers for job records returned by the API."""
from typing import Any, Dict, Optional


def _first(stats: Dict[str, Any], *keys: str) -> Optional[Any]:
    """Return the first key that is present and not None.

    ``a or b`` was used before, which silently substituted the fallback
    whenever the real value was 0 - so a job that had genuinely completed zero
    files reported the *stored* count instead, and a discovered total of 0
    looked like missing data. Zero is a meaningful progress value.
    """
    for key in keys:
        if key in stats and stats[key] is not None:
            return stats[key]
    return None


def _int(value: Optional[Any]) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _issue_lists(job: Dict[str, Any]) -> tuple:
    errors = job.get("errors") or []
    warnings = job.get("warnings") or []
    if not isinstance(errors, list):
        errors = []
    if not isinstance(warnings, list):
        warnings = []
    return errors, warnings


def job_to_api(job: Dict[str, Any], *, include_issues: bool = False) -> Dict[str, Any]:
    """Shape a jobs-table row for JSON responses (client-safe)."""
    if job is None:
        return {}
    stats = job.get("stats") or {}
    options = job.get("options") or {}
    intake_path = options.get("path") or ""
    if not intake_path and options.get("file_paths"):
        paths = options.get("file_paths") or []
        if len(paths) == 1:
            intake_path = paths[0]
        elif paths:
            intake_path = f"{len(paths)} selected file(s)"

    discovered = _int(_first(stats, "files_discovered", "total_files"))
    processed = _int(_first(stats, "files_done", "files_processed"))
    initial = _int(_first(stats, "files_initial"))
    nested = _int(_first(stats, "files_nested", "nested_discovered"))
    if nested is None and discovered is not None and initial is not None:
        nested = max(0, discovered - initial)

    errors, warnings = _issue_lists(job)
    issue_block: Dict[str, Any] = {
        "errors_count": len(errors),
        "warnings_count": len(warnings),
        "errors_preview": errors[:5],
        "warnings_preview": warnings[:5],
    }
    if include_issues:
        issue_block["errors"] = errors[:500]
        issue_block["warnings"] = warnings[:500]

    return {
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "status": job.get("status"),
        "progress": job.get("progress"),
        "current_phase": job.get("current_phase"),
        "current_item": job.get("current_item"),
        "source": job.get("source"),
        "classification_source": options.get("source") or "",
        "classification_side": options.get("side") or "",
        "intake_path": intake_path or job.get("source") or "",
        "created_by": job.get("created_by"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "cancellation_requested": job.get("cancellation_requested"),
        "pause_requested": job.get("pause_requested"),
        "statistics": {
            # Denominator: top-level files *plus* everything materialised out of
            # containers while the job ran (archives, compressed files, email
            # attachments, embedded Office/OpenDocument objects, and any
            # recursive descendants of those).
            "files_discovered": discovered,
            "files_initial": initial,
            "files_nested": nested,
            "containers_opened": _int(_first(stats, "containers_opened")),
            # Terminal buckets. All of them advance the bar, so a job containing
            # only unsupported objects still reaches 100% instead of stalling.
            "files_processed": processed,
            "files_succeeded": _int(_first(stats, "files_completed", "files_succeeded",
                                           "files_stored")),
            "files_failed": _int(_first(stats, "files_failed")),
            "files_skipped": _int(_first(stats, "files_skipped")),
            "files_unsupported": _int(_first(stats, "files_unsupported")),
            "files_retryable": _int(_first(stats, "files_retryable")),
            # Locked (retryable once the holder releases it) and cancelled
            # (operator asked to stop) are terminal states too; leaving them out
            # of the payload made their objects look unaccounted for.
            "files_locked": _int(_first(stats, "files_locked", "locked")),
            "files_cancelled": _int(_first(stats, "files_cancelled", "cancelled")),
            # Non-terminal: what is running and what has not started yet. A
            # non-zero pending count is why the bar is legitimately below 100%.
            "files_in_progress": _int(_first(stats, "in_progress", "files_in_progress")),
            "files_pending": _int(_first(stats, "files_pending")),
            "percent": _int(_first(stats, "percent")),
            "duplicates": _int(_first(stats, "files_duplicates", "duplicates")),
            "bytes_processed": _first(stats, "bytes_processed", "estimated_bytes"),
            # Recursive descendants stay attributable to the container that
            # produced them: {parent_path: children_discovered}.
            "children_by_parent": stats.get("children_by_parent") or {},
            # Per-container attribution is bounded; nested units beyond the
            # bound are counted here so map + overflow stays exact.
            "children_by_parent_overflow": _int(stats.get("children_by_parent_overflow")),
            # Containers that have published work which has not settled: a run
            # with work in flight is not finished, however the buckets read.
            "containers_in_flight": _int(_first(stats, "containers_in_flight")),
            "container_work_outstanding": _int(
                _first(stats, "container_work_outstanding")
            ),
        },
        "result_summary": job.get("result_summary"),
        **issue_block,
    }
