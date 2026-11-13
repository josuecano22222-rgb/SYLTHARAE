"""Thread-safe accumulation of job-visible errors and warnings.

Processing records per-file outcomes in the progress ledger (counts only).
This registry holds human-readable lines for the job detail page, the
``/api/jobs/<id>/errors`` endpoint, and ingestion batch reports.

Lines are capped; overflow is summarized once so large runs stay bounded.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, List, Optional, Tuple

from core.errors import sanitize_message
from pipeline.progress_ledger import (
    OUTCOME_CANCELLED,
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_LOCKED,
    OUTCOME_RETRYABLE,
    OUTCOME_SKIPPED,
    OUTCOME_UNSUPPORTED,
    _result_layers,
    classify_result,
)

DEFAULT_ERROR_CAP = 500
DEFAULT_WARNING_CAP = 500


def _label_path(file_path: Optional[str]) -> str:
    if not file_path:
        return "(unknown file)"
    try:
        return Path(str(file_path)).name or str(file_path)
    except Exception:
        return str(file_path)


def _extract_detail(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    error = reason = None
    for layer in _result_layers(result):
        if error is None and layer.get("error"):
            error = str(layer["error"])
        if reason is None and layer.get("reason"):
            reason = str(layer["reason"])
        if error and reason:
            break
    text = (error or reason or "").strip()
    return sanitize_message(text)[:2000] if text else ""


def _line(file_path: Optional[str], tag: str, detail: str) -> str:
    name = _label_path(file_path)
    if detail:
        return f"[{tag}] {name}: {detail}"
    return f"[{tag}] {name}"


class JobIssueRegistry:
    """Collects errors and warnings for one job run."""

    def __init__(
        self,
        *,
        error_cap: int = DEFAULT_ERROR_CAP,
        warning_cap: int = DEFAULT_WARNING_CAP,
    ):
        self._lock = threading.Lock()
        self._errors: List[str] = []
        self._warnings: List[str] = []
        self._error_cap = max(1, int(error_cap))
        self._warning_cap = max(1, int(warning_cap))
        self._errors_truncated = 0
        self._warnings_truncated = 0
        self._dirty = False

    @property
    def dirty(self) -> bool:
        with self._lock:
            return self._dirty

    def clear_dirty(self) -> None:
        with self._lock:
            self._dirty = False

    def record_outcome(
        self,
        file_path: Optional[str],
        result: Any,
        outcome: Optional[str] = None,
        *,
        storage_failed: bool = False,
    ) -> None:
        """Register one settled unit of work."""
        if outcome is None:
            outcome = classify_result(result)
        if storage_failed:
            self._append_error(_line(file_path, "storage", "not stored in database"))
            return
        if outcome in (OUTCOME_COMPLETED, OUTCOME_CANCELLED):
            return
        detail = _extract_detail(result)
        if outcome == OUTCOME_FAILED:
            self._append_error(_line(file_path, "failed", detail))
        elif outcome == OUTCOME_UNSUPPORTED:
            self._append_warning(_line(file_path, "unsupported", detail or "unsupported type"))
        elif outcome == OUTCOME_RETRYABLE:
            self._append_warning(_line(file_path, "retryable", detail or "transient failure"))
        elif outcome == OUTCOME_LOCKED:
            self._append_warning(_line(file_path, "locked", detail or "file in use"))
        elif outcome == OUTCOME_SKIPPED:
            if detail:
                self._append_warning(_line(file_path, "skipped", detail))
        else:
            self._append_warning(_line(file_path, outcome or "issue", detail))

    def record_error(self, message: str) -> None:
        text = sanitize_message(str(message or "").strip())
        if text:
            self._append_error(text)

    def record_warning(self, message: str) -> None:
        text = sanitize_message(str(message or "").strip())
        if text:
            self._append_warning(text)

    def _append_error(self, line: str) -> None:
        with self._lock:
            if len(self._errors) < self._error_cap:
                if line not in self._errors:
                    self._errors.append(line)
                    self._dirty = True
            else:
                self._errors_truncated += 1
                self._dirty = True

    def _append_warning(self, line: str) -> None:
        with self._lock:
            if len(self._warnings) < self._warning_cap:
                if line not in self._warnings:
                    self._warnings.append(line)
                    self._dirty = True
            else:
                self._warnings_truncated += 1
                self._dirty = True

    def snapshot(self) -> Tuple[List[str], List[str]]:
        with self._lock:
            errors = list(self._errors)
            warnings = list(self._warnings)
            if self._errors_truncated:
                errors.append(
                    f"… {self._errors_truncated} additional error(s) not shown (cap {self._error_cap})"
                )
            if self._warnings_truncated:
                warnings.append(
                    f"… {self._warnings_truncated} additional warning(s) not shown "
                    f"(cap {self._warning_cap})"
                )
            return errors, warnings

    def merge_into_result(self, result) -> None:
        """Append registry lines onto a service result object."""
        errors, warnings = self.snapshot()
        if hasattr(result, "errors") and errors:
            existing = set(result.errors or [])
            for line in errors:
                if line not in existing:
                    result.errors.append(line)
                    existing.add(line)
        if hasattr(result, "warnings") and warnings:
            existing = set(result.warnings or [])
            for line in warnings:
                if line not in existing:
                    result.warnings.append(line)
                    existing.add(line)


def registry_for_ledger(ledger) -> Optional[JobIssueRegistry]:
    if ledger is None:
        return None
    return getattr(ledger, "issue_registry", None)
