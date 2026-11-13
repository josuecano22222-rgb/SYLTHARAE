"""Taxonomy sync service: keep the database in step with the workbook.

This is the seam between "the workbook is authoritative" and everything that
needs to act on it: the Import Center's job, the drift endpoint, the scheduler,
and the command line. Each of them supplies a request and gets the same result
shape, because they all call this and nothing re-implements the comparison
(:mod:`services.importing.taxonomy_sync`).

Two rules make automatic propagation safe enough to leave running:

* a workbook that breaks a data rule is never written to the database - the run
  fails with the sheet and cell that broke it, and the database is untouched;
* an ``apply`` takes a snapshot of the taxonomy first, so the write can be put
  back (:func:`taxonomy_sync.restore_state`). The snapshot is returned in the
  result and stored with the run, not kept only in memory.

``dry_run`` is the default. The scheduler is the only caller that sets it to
False, and it does so only when an administrator has enabled the schedule.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Dict, List, Optional

from core.errors import ClientSafeError


#: Where the fingerprint of the last applied run is kept. It lives beside the
#: application's other runtime state (``APP_DATA_DIR``), never in the repository.
STATE_FILE = "taxonomy_sync_state.json"


def state_path() -> Path:
    try:
        from core.app_paths import get_data_root

        root = get_data_root()
    except Exception:                                   # pragma: no cover
        root = Path(os.environ.get("APP_DATA_DIR") or "data")
    return Path(root) / STATE_FILE


def read_state() -> Dict[str, Any]:
    """The last applied run, or ``{}`` when the workbook was never synced."""
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(**fields) -> None:
    """Record a successful apply so the next check can skip the work."""
    state = read_state()
    state.update(fields)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


class TaxonomySyncValidationError(ClientSafeError, ValueError):
    """Invalid sync request; ``str(exc)`` is client-safe."""


@dataclass
class TaxonomySyncRequest:
    """What to do. ``data_file`` is a file name, never a path."""

    data_file: Optional[str] = None
    apply: bool = False
    auto_renames: bool = True
    prune_categories: bool = False
    prune_words: bool = False
    snapshot: bool = True
    created_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data_file": self.data_file,
            "apply": self.apply,
            "auto_renames": self.auto_renames,
            "prune_categories": self.prune_categories,
            "prune_words": self.prune_words,
            "created_by": self.created_by,
        }


@dataclass
class TaxonomySyncResult:
    success: bool
    applied: bool = False
    dry_run: bool = True
    stats: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    snapshot: Optional[Dict[str, Any]] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "applied": self.applied,
            "dry_run": self.dry_run,
            "stats": self.stats,
            "warnings": self.warnings[:100],
            "errors": self.errors[:100],
            "has_snapshot": self.snapshot is not None,
            "result_summary": dict(self.stats),
        }


class TaxonomySyncService:
    """Plan (and optionally apply) the workbook-to-database difference."""

    DEFAULT_SEARCH_PATHS = ("classification",)
    DEFAULT_NAME = "domain_data.xlsx"

    def __init__(self, connection_factory: Optional[Callable[[], Any]] = None,
                 search_paths: Optional[tuple] = None):
        self._connection_factory = connection_factory
        self._search_paths = tuple(search_paths) if search_paths else None
        self._cancel = threading.Event()

    # -- paths -----------------------------------------------------------
    def _paths(self) -> tuple:
        if self._search_paths:
            return self._search_paths
        try:
            from apps.importing.utils.constants import DATA_FILE_SEARCH_PATHS
        except Exception:                                   # pragma: no cover
            return self.DEFAULT_SEARCH_PATHS
        # De-duplicated but ordered: "classification/, ./, classification" in
        # an error message reads like a bug in the message.
        seen, paths = set(), []
        for path in tuple(DATA_FILE_SEARCH_PATHS) + self.DEFAULT_SEARCH_PATHS:
            key = str(path).rstrip("/")
            if key not in seen:
                seen.add(key)
                paths.append(path)
        return tuple(paths)

    def resolve_data_file(self, data_file: Optional[str]) -> str:
        """The workbook's path, or a client-safe refusal.

        Names only: a separator in either platform's spelling means somebody
        typed a path, and a server path is not an argument an HTTP caller gets
        to choose.
        """
        if data_file:
            name = data_file
            if (Path(name).name != name or PureWindowsPath(name).name != name
                    or name in (".", "..")):
                raise TaxonomySyncValidationError(
                    f"Enter only the file name (for example {self.DEFAULT_NAME}), "
                    f"not a path. The file must be in one of: "
                    f"{', '.join(str(p) for p in self._paths())}")
        else:
            name = self.DEFAULT_NAME

        for base in self._paths():
            candidate = Path(base) / name
            if candidate.is_file():
                return str(candidate)
        raise TaxonomySyncValidationError(
            f"Data file not found. It must be in one of: "
            f"{', '.join(str(p) for p in self._paths())}")

    # -- execution -------------------------------------------------------
    def _connect(self):
        if self._connection_factory is not None:
            return self._connection_factory()
        from Api.utils.utils import get_connection

        return get_connection()

    def run(self, request: TaxonomySyncRequest,
            progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None
            ) -> TaxonomySyncResult:
        if not isinstance(request, TaxonomySyncRequest):
            raise TaxonomySyncValidationError("Invalid request type")
        result = TaxonomySyncResult(
            success=False, dry_run=not request.apply,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        from services.importing import taxonomy_sync as sync

        try:
            path = self.resolve_data_file(request.data_file)
            result.stats["data_file"] = Path(path).name
            if progress_cb:
                progress_cb({"percent": 10, "current_phase": "Reading the workbook"})

            conn = self._connect()
            try:
                outcome = sync.sync_workbook(
                    conn, path,
                    apply=request.apply,
                    prune_categories=request.prune_categories,
                    prune_words=request.prune_words,
                    auto_renames=request.auto_renames,
                    snapshot=request.snapshot,
                )
            finally:
                conn.close()

            result.success = bool(outcome.get("ok"))
            result.applied = bool(outcome.get("applied"))
            if result.applied:
                write_state(
                    fingerprint=outcome.get("fingerprint"),
                    data_file=Path(path).name,
                    written=outcome.get("written", {}),
                    applied_by=request.created_by or "system",
                )
            result.snapshot = outcome.get("snapshot")
            result.stats.update({
                "fingerprint": outcome.get("fingerprint"),
                "layout": outcome.get("layout"),
                "workbook": outcome.get("workbook"),
                "plan": outcome.get("plan"),
                "written": outcome.get("written", {}),
                "planned_changes": outcome.get("planned_changes", 0),
                "detected_renames": bool(
                    outcome.get("plan", {}).get("detected_renames")),
                "reason": outcome.get("reason"),
                # The standing facts, in words, beside the counts. Section D of
                # round 22: a membership the schema cannot store is a fact about
                # the workbook, not a warning that scrolls away.
                "summary": sync.summarise(outcome.get("plan") or {}),
                "not_stored_count": len(
                    (outcome.get("plan") or {}).get("not_stored_shared") or []),
                "orphan_words_sample":
                    (outcome.get("plan") or {}).get("orphan_words_sample") or [],
                "orphan_words_count": (
                    (outcome.get("plan") or {}).get("orphan_words")
                    if isinstance((outcome.get("plan") or {}).get("orphan_words"), int)
                    else len((outcome.get("plan") or {}).get("orphan_words") or [])),
                "removal": outcome.get("removal"),
            })
            result.errors = list(outcome.get("errors") or [])
            for decision in (outcome.get("plan", {}).get("not_stored") or [])[:5]:
                result.warnings.append(
                    f"{decision['phrase']!r} is listed in {decision['sheet']!r} but "
                    f"belongs to {decision['owner']!r}: {decision['reason']}")
            if progress_cb:
                progress_cb({"percent": 100, "current_phase": "Done"})
        except TaxonomySyncValidationError as exc:
            result.errors.append(str(exc))
        except Exception as exc:                     # pragma: no cover - defensive
            from core.errors import client_safe_message

            result.errors.append(
                client_safe_message(exc, subsystem="services.importing.taxonomy"))
        finally:
            result.finished_at = datetime.now(timezone.utc).isoformat()
        return result

    # -- convenience used by the API and the scheduler -------------------
    def drift(self) -> TaxonomySyncResult:
        """What a sync would change, without writing."""
        return self.run(TaxonomySyncRequest(apply=False))
