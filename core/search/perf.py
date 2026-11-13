"""Structured timing spans for search requests (correlation + budgets)."""

from __future__ import annotations

import contextvars
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_current_trace: contextvars.ContextVar[Optional["SearchTrace"]] = contextvars.ContextVar(
    "search_trace", default=None
)

# Millisecond budgets (P95 targets for interactive search — tune via settings later).
BUDGET_LIST_P95_MS = 300
BUDGET_SNIPPET_P95_MS = 750
BUDGET_DB_QUERY_WARN_MS = 50


@dataclass
class SpanRecord:
    operation: str
    start_time: float
    end_time: float
    duration_ms: float
    rows_examined: Optional[int] = None
    rows_returned: Optional[int] = None
    bytes_read: Optional[int] = None
    database_time_ms: Optional[float] = None
    python_time_ms: Optional[float] = None
    io_time_ms: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "operation": self.operation,
            "duration_ms": round(self.duration_ms, 3),
        }
        for key in (
            "rows_examined",
            "rows_returned",
            "bytes_read",
            "database_time_ms",
            "python_time_ms",
            "io_time_ms",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.extra:
            payload["extra"] = self.extra
        return payload


class SearchTrace:
    """Per-request trace; attach to Flask ``g.search_trace`` or use context var."""

    def __init__(self, correlation_id: Optional[str] = None):
        self.correlation_id = correlation_id or uuid.uuid4().hex
        self.spans: List[SpanRecord] = []
        self._stack: List[tuple[str, float]] = []

    @classmethod
    def current(cls) -> Optional["SearchTrace"]:
        return _current_trace.get()

    @classmethod
    def bind(cls, trace: Optional["SearchTrace"]) -> contextvars.Token:
        return _current_trace.set(trace)

    @classmethod
    def reset(cls, token: contextvars.Token) -> None:
        _current_trace.reset(token)

    def span(
        self,
        operation: str,
        *,
        rows_examined: Optional[int] = None,
        rows_returned: Optional[int] = None,
        bytes_read: Optional[int] = None,
        database_time_ms: Optional[float] = None,
        python_time_ms: Optional[float] = None,
        io_time_ms: Optional[float] = None,
        **extra: Any,
    ):
        return _SpanContext(
            self,
            operation,
            rows_examined=rows_examined,
            rows_returned=rows_returned,
            bytes_read=bytes_read,
            database_time_ms=database_time_ms,
            python_time_ms=python_time_ms,
            io_time_ms=io_time_ms,
            extra=extra,
        )

    def add_span(self, record: SpanRecord) -> None:
        self.spans.append(record)

    def summary_ms(self) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        for span in self.spans:
            totals[span.operation] = totals.get(span.operation, 0.0) + span.duration_ms
        return {key: round(value, 3) for key, value in totals.items()}

    def performance_model(self) -> Dict[str, float]:
        """Map spans into T_total components (directive §2)."""
        by_op = self.summary_ms()
        mapping = {
            "T_plan": by_op.get("plan", 0.0) + by_op.get("advanced_search", 0.0) * 0.05,
            "T_db": by_op.get("db_count", 0.0) + by_op.get("db_candidates", 0.0),
            "T_candidate": by_op.get("db_candidates", 0.0),
            "T_content": by_op.get("io_text_windows", 0.0),
            "T_snippet": by_op.get("snippets", 0.0) + by_op.get("snippet_enrich", 0.0),
            "T_rank": by_op.get("rank", 0.0),
            "T_serialize": by_op.get("serialize", 0.0),
        }
        mapped = sum(mapping.values())
        total = round(sum(s.duration_ms for s in self.spans), 3)
        mapping["T_unattributed"] = round(max(0.0, total - mapped), 3)
        mapping["T_total"] = total
        return mapping

    def to_dict(self) -> Dict[str, Any]:
        total_ms = round(sum(s.duration_ms for s in self.spans), 3)
        return {
            "correlation_id": self.correlation_id,
            "request_id": self.correlation_id,
            "total_ms": total_ms,
            "timings": self.performance_model(),
            "spans": [s.to_dict() for s in self.spans],
            "by_operation_ms": self.summary_ms(),
            "budgets_ms": {
                "list_p95_target": BUDGET_LIST_P95_MS,
                "snippet_p95_target": BUDGET_SNIPPET_P95_MS,
                "db_query_warn": BUDGET_DB_QUERY_WARN_MS,
            },
        }


class _SpanContext:
    def __init__(self, trace: SearchTrace, operation: str, **kwargs: Any):
        self.trace = trace
        self.operation = operation
        self.kwargs = kwargs
        self._start = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        end = time.perf_counter()
        duration_ms = (end - self._start) * 1000.0
        self.trace.add_span(
            SpanRecord(
                operation=self.operation,
                start_time=self._start,
                end_time=end,
                duration_ms=duration_ms,
                **self.kwargs,
            )
        )
        return False


def resolve_correlation_id(header_value: Optional[str] = None) -> str:
    if header_value and str(header_value).strip():
        return str(header_value).strip()[:128]
    return uuid.uuid4().hex
