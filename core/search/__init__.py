"""Search performance: ripgrep, planner, instrumentation, bounded retrieval."""

from core.search.perf import SearchTrace, resolve_correlation_id
from core.search.planner import SearchPlan, plan_search
from core.search.ripgrep import RipgrepScanner, get_scanner_from_settings

__all__ = [
    "RipgrepScanner",
    "get_scanner_from_settings",
    "SearchTrace",
    "resolve_correlation_id",
    "SearchPlan",
    "plan_search",
]
