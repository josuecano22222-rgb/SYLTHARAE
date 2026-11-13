"""Source/side management service.

Extracted from the interactive CLI (``apps/cli.main.get_or_select_source`` /
``get_or_select_side``) so the Input UI, the API and the CLI share one
implementation. Pure service: no input()/print/argparse.
"""
import re
from typing import Any, Dict, List

from database.queries import (
    list_sources, search_sources, create_source, get_source_by_name,
    list_sides, search_sides, create_side, get_side_by_name,
)

_NAME_RE = re.compile(r"^[\w][\w .\-()]{0,127}$")


class SourceSideError(ValueError):
    """Validation or lookup failure (client-safe message in ``str(exc)``)."""


def _validate_common_name(name: str, kind: str) -> str:
    if not name or not isinstance(name, str):
        raise SourceSideError(f"{kind} name is required")
    name = name.strip()
    if not _NAME_RE.match(name):
        raise SourceSideError(
            f"Invalid {kind} name: use letters, digits, spaces, . - _ ( ) only (max 128)"
        )
    return name


def list_sources_svc(limit: int = 200) -> List[Dict[str, Any]]:
    rows = list_sources(limit=max(1, min(int(limit), 1000))) or []
    return rows


def search_sources_svc(term: str, limit: int = 50) -> List[Dict[str, Any]]:
    term = (term or "").strip()
    if not term:
        return []
    return (search_sources(term) or [])[: max(1, min(int(limit), 200))]


def list_sides_svc(limit: int = 200) -> List[Dict[str, Any]]:
    rows = list_sides(limit=max(1, min(int(limit), 1000))) or []
    return rows


def search_sides_svc(term: str, limit: int = 50) -> List[Dict[str, Any]]:
    term = (term or "").strip()
    if not term:
        return []
    return (search_sides(term) or [])[: max(1, min(int(limit), 200))]


def create_source_svc(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a source from a JSON-able payload (was interactive CLI step 2)."""
    name = _validate_common_name(str(payload.get("name", "")), "source")
    existing = get_source_by_name(name)
    if existing:
        raise SourceSideError(f"Source '{name}' already exists")
    job = str(payload.get("job") or "").strip()
    if not job:
        raise SourceSideError("job/role is required")
    try:
        importance = float(payload.get("importance", 0.5))
    except (TypeError, ValueError):
        raise SourceSideError("importance must be a number between 0.0 and 1.0")
    if not 0.0 <= importance <= 1.0:
        raise SourceSideError("importance must be between 0.0 and 1.0")
    country = str(payload.get("country") or "").strip()
    if not country:
        raise SourceSideError("country is required")
    created = create_source(
        name,
        job,
        country=country,
        city=payload.get("city") or None,
        description=payload.get("description") or None,
        importance=importance,
        accounts=payload.get("accounts") or None,
        note=payload.get("note") or None,
        attachments=payload.get("attachments") or None,
        ownership=payload.get("ownership") or None,
        access_status=payload.get("access_status") or None,
        entry_date=payload.get("entry_date") or None,
        category_id=payload.get("category_id") or None,
    )
    return {"name": name, "created": bool(created), "source": created}


def create_side_svc(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a side from a JSON-able payload (was interactive CLI step 3)."""
    name = _validate_common_name(str(payload.get("name", "")), "side")
    existing = get_side_by_name(name)
    if existing:
        raise SourceSideError(f"Side '{name}' already exists")
    try:
        importance = float(payload.get("importance", 0.5))
    except (TypeError, ValueError):
        raise SourceSideError("importance must be a number between 0.0 and 1.0")
    if not 0.0 <= importance <= 1.0:
        raise SourceSideError("importance must be between 0.0 and 1.0")
    created = create_side(
        name,
        importance=importance,
        date_creation=payload.get("date_creation") or None,
    )
    return {"name": name, "created": bool(created), "side": created}


def resolve_source_side(source_name: str, side_name: str) -> Dict[str, int]:
    """Resolve names to ids; raise SourceSideError when unknown."""
    src = get_source_by_name((source_name or "").strip())
    if not src:
        raise SourceSideError(f"Unknown source: {source_name}")
    side = get_side_by_name((side_name or "").strip())
    if not side:
        raise SourceSideError(f"Unknown side: {side_name}")
    return {
        "source_id": int(src["source_id"] if "source_id" in src else src["id"]),
        "side_id": int(side["side_id"] if "side_id" in side else side["id"]),
    }
