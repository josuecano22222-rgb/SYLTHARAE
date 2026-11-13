"""Machine-readable optimization decision records from benchmark JSONL."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def dominant_stage(row: Dict[str, Any]) -> tuple[str, float]:
    stages = {
        "candidate_generation": float(row.get("candidate_ms") or 0.0),
        "count": float(row.get("count_ms") or 0.0),
        "snippet": float(row.get("snippet_ms") or 0.0),
        "db_other": max(
            0.0,
            float(row.get("db_ms") or 0.0)
            - float(row.get("candidate_ms") or 0.0)
            - float(row.get("count_ms") or 0.0),
        ),
        "plan": float(row.get("plan_ms") or 0.0),
        "rank": float(row.get("rank_ms") or 0.0),
        "serialize": float(row.get("serialize_ms") or 0.0),
    }
    total = float(row.get("p95_ms") or row.get("median_ms") or 0.0) or sum(stages.values())
    if total <= 0:
        return "unknown", 0.0
    name = max(stages, key=stages.get)
    share = round(100.0 * stages[name] / total, 1) if total else 0.0
    return name, share


def hypothesis_for(stage: str, row: Dict[str, Any]) -> str:
    qid = str(row.get("query_id") or "")
    if stage == "candidate_generation":
        if qid in {"Q05", "Q06", "Q07"}:
            return "multi-term candidate intersection or union cost"
        if qid in {"Q01", "Q02", "Q14"}:
            return "high-selectivity term may inflate candidate pool or word ILIKE in relevance SQL"
        return "inspect EXPLAIN for candidate SQL and inverted-index usage"
    if stage == "count":
        return "exact COUNT may exceed interactive budget; validate timed mode"
    if stage == "snippet":
        return "compare ripgrep structured path vs Python line scan on bounded windows"
    if stage == "db_other":
        return "word resolution or planner path outside candidate span"
    return "profile spans in SearchTrace for this query class"


def recommended_next_test(stage: str, row: Dict[str, Any]) -> str:
    engine = row.get("engine") or row.get("plan", {}).get("primary_engine") or ""
    qid = str(row.get("query_id") or "")
    if engine == "PG_LITERAL" or qid in {"Q07", "Q08"}:
        return "explain_literal_candidate_sql"
    if qid == "Q10":
        return "explain_literal_candidate_sql"
    mapping = {
        "candidate_generation": "explain_capture + word_resolution_benchmark",
        "count": "count_mode timed vs exact benchmark",
        "snippet": "snippet hydration benchmark with rg on/off",
        "db_other": "word_resolution_benchmark + EXPLAIN word lookup",
        "plan": "reduce pre-DB work; cache word_id resolution",
        "rank": "SQL relevance vs bounded rank pool measurement",
        "serialize": "API payload size audit",
    }
    return mapping.get(stage, "search_benchmark_harness rerun with --cold --warm")


def build_decision_record(
    row: Dict[str, Any],
    *,
    benchmark: str,
    corpus: Optional[str] = None,
    hardware_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    stage, share = dominant_stage(row)
    engine = row.get("engine") or row.get("plan", {}).get("primary_engine") or "unknown"
    return {
        "benchmark": benchmark,
        "corpus": corpus or row.get("dataset_files") or "live",
        "hardware_profile": hardware_profile,
        "query_id": row.get("query_id"),
        "query_class": row.get("label"),
        "query": row.get("query"),
        "engine": engine,
        "p50_ms": row.get("p50_ms"),
        "p95_ms": row.get("p95_ms"),
        "p99_ms": row.get("p99_ms"),
        "candidate_count": row.get("candidate_count"),
        "result_count": row.get("result_count"),
        "dominant_stage": stage,
        "dominant_share_pct": share,
        "hypothesis": hypothesis_for(stage, row),
        "recommended_next_test": recommended_next_test(stage, row),
        "confidence": "measured" if row.get("p95_ms") and not row.get("error") else "blocked",
        "error": row.get("error"),
    }


def records_from_jsonl_rows(
    rows: List[Dict[str, Any]],
    *,
    benchmark: str,
    hardware_profile: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("type") in {"meta", "error"}:
            continue
        if row.get("query_id") and row.get("p95_ms") is not None:
            out.append(
                build_decision_record(
                    row,
                    benchmark=benchmark,
                    hardware_profile=hardware_profile,
                )
            )
    return out
