"""Plan structured folder exports from metadata (analyst category, source, side, …).

The browser writes files via the File System Access API; the server only
returns a deterministic folder layout and per-file metadata.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from Api.services.analyst_categories import AnalystCategoryService
from Api.utils import compute_percentage, execute_query, select_classification

MAX_STRUCTURED_EXPORT_FILES = 5_000

LAYOUT_PRESETS: Dict[str, List[str]] = {
    "analyst_category": ["analyst_category"],
    "source": ["source"],
    "side": ["side"],
    "source__analyst_category": ["source", "analyst_category"],
    "analyst_category__source": ["analyst_category", "source"],
    "source__side__analyst_category": ["source", "side", "analyst_category"],
    "file_type": ["file_type"],
    "year_month": ["year_month"],
    "smart_category": ["smart_category"],
    "smart_category__analyst_category": ["smart_category", "analyst_category"],
}

DEFAULT_UNASSIGNED = "_Unassigned"


def _sanitize_segment(value: str, *, fallback: str = DEFAULT_UNASSIGNED) -> str:
    text = (value or "").strip()
    if not text:
        text = fallback
    text = re.sub(r"[\u0000-\u001f<>:\"/\\|?*]", "_", text)
    text = re.sub(r"\s+", " ", text).strip().strip(".")
    if not text:
        text = fallback
    return text[:80]


def resolve_layout(layout: Any) -> List[str]:
    """Return ordered segment keys from a preset name or explicit list."""
    if isinstance(layout, list):
        segments = [str(s).strip() for s in layout if str(s).strip()]
        if segments:
            return segments
    key = str(layout or "analyst_category").strip()
    return list(LAYOUT_PRESETS.get(key, LAYOUT_PRESETS["analyst_category"]))


def _year_month_segment(file_date: Any) -> str:
    if file_date is None:
        return DEFAULT_UNASSIGNED
    if hasattr(file_date, "strftime"):
        return file_date.strftime("%Y-%m")
    text = str(file_date).strip()
    if len(text) >= 7 and text[4] == "-":
        return text[:7]
    return DEFAULT_UNASSIGNED


def _top_smart_category(file_id: int) -> str:
    rows = select_classification(file_id) or []
    percentages = compute_percentage(rows) or {}
    if not percentages:
        return ""
    best = max(percentages.items(), key=lambda item: item[1].get("count") or 0)
    return str(best[0] or "").strip()


def _segment_value(row: Dict[str, Any], key: str, *, unassigned: str) -> str:
    if key == "analyst_category":
        return row.get("analyst_category_name") or unassigned
    if key == "source":
        return row.get("source_name") or unassigned
    if key == "side":
        return row.get("side_name") or unassigned
    if key == "file_type":
        ext = (row.get("file_type") or "").strip().lower()
        return ext or unassigned
    if key == "year_month":
        return _year_month_segment(row.get("file_date"))
    if key == "smart_category":
        return row.get("smart_category_name") or unassigned
    return unassigned


def build_relative_path(
    row: Dict[str, Any],
    segments: Sequence[str],
    filename: str,
    *,
    unassigned: str = DEFAULT_UNASSIGNED,
) -> str:
    parts = [_sanitize_segment(_segment_value(row, seg, unassigned=unassigned), fallback=unassigned)
             for seg in segments]
    safe_name = _sanitize_segment(filename, fallback=f"file_{row.get('path_id')}")
    return "/".join(parts + [safe_name])


def _fetch_rows_for_path_ids(path_ids: Sequence[int]) -> List[Dict[str, Any]]:
    cleaned = [int(i) for i in path_ids if isinstance(i, int) or str(i).isdigit()]
    cleaned = [i for i in cleaned if i > 0]
    if not cleaned:
        return []
    placeholders = ",".join(["%s"] * len(cleaned))
    rows = execute_query(
        f"""
        SELECT
            p.id,
            p.file_name,
            p.file_path,
            p.file_type,
            p.file_date,
            COALESCE(src.name, '') AS source_name,
            COALESCE(sd.name, '') AS side_name,
            ac.name AS analyst_category_name,
            ac.id AS analyst_category_id
        FROM paths p
        LEFT JOIN hash_contexts hc ON hc.id = p.context_id
        LEFT JOIN sources src ON src.id = hc.source_id
        LEFT JOIN sides sd ON sd.id = hc.side_id
        LEFT JOIN analyst_file_categories afc ON afc.path_id = p.id
        LEFT JOIN analyst_categories ac ON ac.id = afc.category_id
        WHERE p.id IN ({placeholders})
        ORDER BY p.id
        """,
        tuple(cleaned),
        fetch="all",
    ) or []
    out: List[Dict[str, Any]] = []
    for row in rows:
        path_id = int(row[0])
        out.append({
            "path_id": path_id,
            "file_name": row[1] or f"file_{path_id}",
            "file_path": row[2] or "",
            "file_type": row[3] or "",
            "file_date": row[4],
            "source_name": row[5] or "",
            "side_name": row[6] or "",
            "analyst_category_name": row[7] or "",
            "analyst_category_id": row[8],
            "smart_category_name": "",
        })
    return out


def _resolve_path_ids_from_scope(
    scope: str,
    *,
    file_ids: Optional[Sequence[int]] = None,
    filters: Optional[Dict[str, Any]] = None,
    limit: int = MAX_STRUCTURED_EXPORT_FILES,
) -> Tuple[List[int], int, bool]:
    """Return (path_ids, total_matched, truncated)."""
    filters = filters or {}
    scope = (scope or "selection").strip().lower()

    if scope in ("selection", "selected", "file_ids"):
        ids = []
        for raw in file_ids or []:
            try:
                val = int(raw)
                if val > 0:
                    ids.append(val)
            except (TypeError, ValueError):
                continue
        return ids[:limit], len(ids), len(ids) > limit

    if scope in ("analyst_filtered", "analyst_filters", "analyst_view"):
        assignments, total = AnalystCategoryService.list_assignments(
            category_id=filters.get("category_id"),
            analyst_id=filters.get("analyst_id"),
            date_from=filters.get("date_from"),
            date_to=filters.get("date_to"),
            query=filters.get("q") or filters.get("query"),
            file_query=filters.get("file_query"),
            limit=limit,
            offset=0,
        )
        ids = [int(a["path_id"]) for a in assignments if a.get("path_id")]
        return ids, int(total or 0), int(total or 0) > limit

    if scope in ("analyst_all", "all_categorized", "analyst_all_categorized"):
        assignments, total = AnalystCategoryService.list_assignments(
            limit=limit,
            offset=0,
        )
        ids = [int(a["path_id"]) for a in assignments if a.get("path_id")]
        return ids, int(total or 0), int(total or 0) > limit

    raise ValueError(f"Unknown export scope: {scope}")


def build_structured_export_plan(
    *,
    scope: str,
    layout: Any = "analyst_category",
    file_ids: Optional[Sequence[int]] = None,
    filters: Optional[Dict[str, Any]] = None,
    unassigned_label: str = DEFAULT_UNASSIGNED,
    include_smart_category: bool = False,
    limit: int = MAX_STRUCTURED_EXPORT_FILES,
) -> Dict[str, Any]:
    """Build the client-side folder layout plan."""
    limit = min(max(int(limit or MAX_STRUCTURED_EXPORT_FILES), 1), MAX_STRUCTURED_EXPORT_FILES)
    path_ids, total_matched, truncated = _resolve_path_ids_from_scope(
        scope, file_ids=file_ids, filters=filters, limit=limit,
    )
    if not path_ids:
        return {
            "success": True,
            "total_matched": total_matched,
            "total": 0,
            "truncated": truncated,
            "layout": resolve_layout(layout),
            "entries": [],
            "folder_stats": {},
        }

    rows = _fetch_rows_for_path_ids(path_ids)
    row_by_id = {r["path_id"]: r for r in rows}

    segments = resolve_layout(layout)
    need_smart = include_smart_category or "smart_category" in segments
    if need_smart:
        for pid in path_ids:
            row = row_by_id.get(pid)
            if row is not None:
                row["smart_category_name"] = _top_smart_category(pid) or unassigned_label

    entries: List[Dict[str, Any]] = []
    folder_stats: Dict[str, int] = {}

    for pid in path_ids:
        row = row_by_id.get(pid)
        if not row:
            continue
        filename = row["file_name"] or f"file_{pid}"
        rel = build_relative_path(row, segments, filename, unassigned=unassigned_label)
        folder_key = "/".join(rel.split("/")[:-1]) or "."
        folder_stats[folder_key] = folder_stats.get(folder_key, 0) + 1
        entries.append({
            "file_id": pid,
            "relative_path": rel,
            "filename": filename,
            "folder": folder_key,
            "metadata": {
                "analyst_category": row.get("analyst_category_name") or None,
                "source": row.get("source_name") or None,
                "side": row.get("side_name") or None,
                "file_type": row.get("file_type") or None,
                "file_date": str(row.get("file_date") or "") or None,
                "smart_category": row.get("smart_category_name") or None,
                "library_path": row.get("file_path") or None,
            },
        })

    return {
        "success": True,
        "total_matched": total_matched,
        "total": len(entries),
        "truncated": truncated,
        "layout": segments,
        "layout_preset": layout if isinstance(layout, str) else None,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "folder_stats": folder_stats,
        "entries": entries,
    }
