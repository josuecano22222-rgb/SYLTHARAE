"""Heuristic cost estimation for search routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from core.search.enums import SearchComplexity


@dataclass(frozen=True)
class CostEstimate:
    complexity: SearchComplexity
    estimated_candidates: int
    estimated_cost: int
    factors: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "complexity": self.complexity.value,
            "estimated_candidates": self.estimated_candidates,
            "estimated_cost": self.estimated_cost,
            "factors": self.factors,
        }


def estimate_search_cost(
    *,
    query: str,
    term_count: int,
    has_metadata_filters: bool,
    case_sensitive: bool,
    whole_word: bool,
    has_boolean_ops: bool,
    has_quotes: bool,
    regex_hint: bool,
    resolved_word_ids: int,
    corpus_paths_estimate: int = 1_000_000,
) -> CostEstimate:
    """Rough cost score — calibrate with benchmarks, not intuition."""
    factors: List[str] = []
    cost = 10
    est_candidates = 500

    if regex_hint:
        cost += 80
        est_candidates = min(corpus_paths_estimate, 500_000)
        factors.append("regex")
        complexity = SearchComplexity.VERY_EXPENSIVE
    elif case_sensitive or whole_word or has_boolean_ops or has_quotes:
        cost += 45
        est_candidates = 50_000
        factors.append("literal_constraints")
        complexity = SearchComplexity.EXPENSIVE
    elif resolved_word_ids >= term_count and term_count > 0:
        cost += term_count * 5
        est_candidates = min(10_000 * term_count, 200_000)
        factors.append("exact_word_id_lookup")
        complexity = SearchComplexity.FAST if term_count <= 3 else SearchComplexity.NORMAL
    else:
        cost += 25 + term_count * 8
        est_candidates = 100_000
        factors.append("inverted_fallback")
        complexity = SearchComplexity.NORMAL

    if has_metadata_filters:
        cost -= 15
        est_candidates = max(1_000, est_candidates // 4)
        factors.append("metadata_prefilter")

    cost = max(1, min(100, cost))
    return CostEstimate(
        complexity=complexity,
        estimated_candidates=est_candidates,
        estimated_cost=cost,
        factors=factors,
    )
