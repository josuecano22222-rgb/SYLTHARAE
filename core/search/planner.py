"""Search complexity classification and engine routing (measurement-driven)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.search.cost import CostEstimate, estimate_search_cost
from core.search.enums import (
    CountStrategy,
    PaginationStrategy,
    RankStrategy,
    SearchClass,
    SearchComplexity,
    SearchEngineRoute,
)
from core.search.limits import SearchLimits


@dataclass(frozen=True)
class SearchPlan:
    complexity: SearchComplexity
    search_class: SearchClass
    primary_engine: SearchEngineRoute
    snippet_engine: SearchEngineRoute
    use_sql_pagination: bool
    defer_snippets: bool
    allow_pcre2: bool
    reasons: List[str]
    count_strategy: CountStrategy = CountStrategy.TIMED
    rank_strategy: RankStrategy = RankStrategy.SQL_RELEVANCE
    pagination_strategy: PaginationStrategy = PaginationStrategy.KEYSET
    max_candidates: int = 500
    snippet_concurrency: int = 4
    cost: Optional[CostEstimate] = None
    use_fast_word_ids: bool = False

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "complexity": self.complexity.value,
            "search_class": self.search_class.value,
            "primary_engine": self.primary_engine.value,
            "snippet_engine": self.snippet_engine.value,
            "use_sql_pagination": self.use_sql_pagination,
            "defer_snippets": self.defer_snippets,
            "allow_pcre2": self.allow_pcre2,
            "count_strategy": self.count_strategy.value,
            "rank_strategy": self.rank_strategy.value,
            "pagination_strategy": self.pagination_strategy.value,
            "max_candidates": self.max_candidates,
            "snippet_concurrency": self.snippet_concurrency,
            "use_fast_word_ids": self.use_fast_word_ids,
            "reasons": self.reasons,
        }
        if self.cost:
            payload["cost"] = self.cost.to_dict()
        return payload


_RE_PCRE_HINT = re.compile(r"(\(\?[=!<])|(\\[AZz])|(\(\?P<)")


def plan_search(
    *,
    query: str,
    has_metadata_filters: bool,
    case_sensitive: bool = False,
    whole_word: bool = False,
    has_boolean_ops: bool = False,
    has_quotes: bool = False,
    fast_mode: bool = True,
    defer_snippets: bool = True,
    regex_requested: bool = False,
    resolved_word_ids: int = 0,
    term_count: int = 0,
    count_mode: str = "timed",
    use_bm25: bool = False,
    cursor_present: bool = False,
) -> SearchPlan:
    """Select execution strategy before touching the database."""
    q = (query or "").strip()
    reasons: List[str] = []
    limits = SearchLimits.from_settings()
    terms = len(q.split()) if q else 0
    if term_count:
        terms = term_count
    cost = estimate_search_cost(
        query=q,
        term_count=terms,
        has_metadata_filters=has_metadata_filters,
        case_sensitive=case_sensitive,
        whole_word=whole_word,
        has_boolean_ops=has_boolean_ops,
        has_quotes=has_quotes,
        regex_hint=regex_requested,
        resolved_word_ids=resolved_word_ids,
    )
    count_strategy = CountStrategy.SKIP if count_mode == "skip" else (
        CountStrategy.TIMED if count_mode == "timed" else CountStrategy.EXACT
    )
    rank_strategy = RankStrategy.BM25_PYTHON if use_bm25 else RankStrategy.SQL_RELEVANCE
    pagination_strategy = (
        PaginationStrategy.KEYSET
        if (fast_mode and cursor_present) or (fast_mode and not use_bm25)
        else PaginationStrategy.OFFSET
    )
    use_fast_word_ids = resolved_word_ids > 0 and resolved_word_ids == terms and terms > 0

    if not q and has_metadata_filters:
        return SearchPlan(
            complexity=SearchComplexity.FAST,
            search_class=SearchClass.METADATA,
            primary_engine=SearchEngineRoute.PG_METADATA,
            snippet_engine=SearchEngineRoute.PG_METADATA,
            use_sql_pagination=True,
            defer_snippets=True,
            allow_pcre2=False,
            reasons=["metadata-only filter"],
            count_strategy=count_strategy,
            rank_strategy=RankStrategy.NONE,
            pagination_strategy=pagination_strategy,
            max_candidates=limits.max_candidates,
            snippet_concurrency=limits.max_snippet_workers,
            cost=cost,
        )

    if not q or len(q) < 2:
        return SearchPlan(
            complexity=SearchComplexity.TRIVIAL,
            search_class=SearchClass.METADATA,
            primary_engine=SearchEngineRoute.PG_METADATA,
            snippet_engine=SearchEngineRoute.PG_METADATA,
            use_sql_pagination=True,
            defer_snippets=True,
            allow_pcre2=False,
            reasons=["empty or too-short query"],
            count_strategy=count_strategy,
            rank_strategy=RankStrategy.NONE,
            pagination_strategy=pagination_strategy,
            max_candidates=limits.max_candidates,
            snippet_concurrency=limits.max_snippet_workers,
            cost=cost,
        )

    if regex_requested or _RE_PCRE_HINT.search(q):
        reasons.append("regex/pcre semantics detected")
        return SearchPlan(
            complexity=SearchComplexity.VERY_EXPENSIVE,
            search_class=SearchClass.REGEX_PCRE,
            primary_engine=SearchEngineRoute.PG_LITERAL,
            snippet_engine=SearchEngineRoute.RG_EXTRACTED,
            use_sql_pagination=False,
            defer_snippets=defer_snippets,
            allow_pcre2=True,
            reasons=reasons,
            count_strategy=count_strategy,
            rank_strategy=rank_strategy,
            pagination_strategy=PaginationStrategy.OFFSET,
            max_candidates=limits.max_candidates,
            snippet_concurrency=limits.max_snippet_workers,
            cost=cost,
        )

    if case_sensitive or whole_word or has_boolean_ops or has_quotes:
        reasons.append("literal/boolean/phrase constraints")
        complexity = SearchComplexity.EXPENSIVE
        engine = SearchEngineRoute.PG_LITERAL
    else:
        complexity = SearchComplexity.FAST if len(q.split()) <= 3 else SearchComplexity.NORMAL
        engine = SearchEngineRoute.PG_INVERTED
        reasons.append("indexed term path")

    if has_metadata_filters and engine == SearchEngineRoute.PG_INVERTED:
        reasons.append("metadata filters + inverted index")
        search_class = SearchClass.HYBRID
        primary = SearchEngineRoute.HYBRID_PG_THEN_RG if defer_snippets else SearchEngineRoute.PG_INVERTED
    else:
        search_class = SearchClass.INDEXED_TERM
        primary = engine

    if use_fast_word_ids:
        reasons.append("exact word_id inverted path")
    return SearchPlan(
        complexity=cost.complexity,
        search_class=search_class,
        primary_engine=primary,
        snippet_engine=SearchEngineRoute.RG_EXTRACTED,
        use_sql_pagination=fast_mode and not (case_sensitive or whole_word or has_boolean_ops),
        defer_snippets=defer_snippets,
        allow_pcre2=False,
        reasons=reasons,
        count_strategy=count_strategy,
        rank_strategy=rank_strategy,
        pagination_strategy=pagination_strategy,
        max_candidates=limits.max_candidates,
        snippet_concurrency=limits.max_snippet_workers,
        cost=cost,
        use_fast_word_ids=use_fast_word_ids,
    )
