"""Hard resource limits for search (graceful degradation)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from settings import get_settings


@dataclass(frozen=True)
class SearchLimits:
    max_candidates: int = 500
    max_rank_pool: int = 500
    max_snippets_per_document: int = 5
    max_snippet_bytes: int = 4096
    max_total_snippet_bytes: int = 65536
    max_document_bytes: int = 262144
    max_search_time_ms: int = 30_000
    max_snippet_workers: int = 8
    max_concurrent_searches: int = 32
    max_response_results: int = 200

    @classmethod
    def from_settings(cls, cfg: Optional[Dict[str, Any]] = None) -> "SearchLimits":
        if cfg is None:
            cfg = get_settings().get_search_config()
        return cls(
            max_candidates=int(cfg.get("search_max_candidates", 500)),
            max_rank_pool=int(cfg.get("sql_rank_pool_max", 500)),
            max_snippets_per_document=int(cfg.get("max_snippets_per_document", 5)),
            max_snippet_bytes=int(cfg.get("max_snippet_bytes", 4096)),
            max_total_snippet_bytes=int(cfg.get("max_total_snippet_bytes", 65536)),
            max_document_bytes=int(cfg.get("snippet_load_max_bytes", 262144)),
            max_snippet_workers=int(cfg.get("snippet_worker_concurrency", 8)),
            max_response_results=int(cfg.get("max_results", 1000)),
        )

    def to_dict(self) -> Dict[str, int]:
        return {
            "max_candidates": self.max_candidates,
            "max_rank_pool": self.max_rank_pool,
            "max_snippets_per_document": self.max_snippets_per_document,
            "max_snippet_bytes": self.max_snippet_bytes,
            "max_total_snippet_bytes": self.max_total_snippet_bytes,
            "max_document_bytes": self.max_document_bytes,
            "max_search_time_ms": self.max_search_time_ms,
            "max_snippet_workers": self.max_snippet_workers,
            "max_concurrent_searches": self.max_concurrent_searches,
        }
