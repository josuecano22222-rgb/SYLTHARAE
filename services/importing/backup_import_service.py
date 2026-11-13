"""Backup import + server batch import services.

Thin, reusable wrappers over the *existing*, already-hardened import
implementations so the Import Center jobs run exactly the same code paths
as the current API endpoints:

* ``BackupImportService``  -> ``Api.services.import_service.ImportService``
  (allowlist-validated, transactional restore; SEC-03/SEC-04).
* ``BatchImportService``   -> server-path file lists (SEC-06) executed
  through the ingestion engine as a job (previously a synchronous request).
"""
import io
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from core.errors import ClientSafeError


class BackupImportValidationError(ClientSafeError, ValueError):
    """Invalid backup import request; ``str(exc)`` is client-safe."""


@dataclass
class BackupImportRequest:
    # Either an uploaded backup file staged by the API layer...
    backup_path: Optional[str] = None
    # ...or a previously exported backup stored under the app data root.
    backup_name: Optional[str] = None
    dry_run: bool = False
    created_by: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "backup_path": self.backup_path,
            "backup_name": self.backup_name,
            "dry_run": self.dry_run,
            "created_by": self.created_by,
        }


@dataclass
class SimpleResult:
    success: bool
    cancelled: bool = False
    paused: bool = False
    dry_run: bool = False
    stats: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "cancelled": self.cancelled,
            "paused": self.paused,
            "dry_run": self.dry_run,
            "stats": self.stats,
            "warnings": self.warnings[:200],
            "errors": self.errors[:200],
            "result_summary": dict(self.stats),
        }


class BackupImportService:
    """Restore an application backup (admin-only operation)."""

    def validate(self, request: BackupImportRequest) -> BackupImportRequest:
        if not isinstance(request, BackupImportRequest):
            raise BackupImportValidationError("Invalid request type")
        if not request.backup_path and not request.backup_name:
            raise BackupImportValidationError("No backup file provided")
        if request.backup_path:
            if not Path(request.backup_path).is_file():
                raise BackupImportValidationError("Staged backup file not found")
        return request

    def validate_backup(self, backup_path: str) -> Dict[str, Any]:
        """Dry-run: validate via the existing hardened ImportService (no writes)."""

        bp = Path(backup_path)
        if not bp.is_file():
            raise BackupImportValidationError("Backup file not found")
        from Api.services.import_service import ImportService

        payload = bp.read_bytes()
        try:
            out = ImportService.import_database_backup(
                backup_file=io.BytesIO(payload), restore_data=False
            )
        except Exception as exc:
            raise BackupImportValidationError(
                "Backup validation failed - file rejected"
            ) from exc
        if not isinstance(out, dict):
            raise BackupImportValidationError("Backup validation returned no result")
        tables = (out.get("tables") or {}) if isinstance(out.get("tables"), dict) else {}
        return {
            "valid": bool(out.get("valid", False)),
            "tables_discovered": len(tables),
            "rows_by_table": {
                t: (len(i.get("rows") or []) if isinstance(i, dict) else 0)
                for t, i in tables.items()
            },
            "estimated_records": sum(
                (len(i.get("rows") or []) if isinstance(i, dict) else 0)
                for i in tables.values()
            ),
            "errors": out.get("errors") or [],
            "warnings": out.get("warnings") or [],
            "dry_run": True,
        }

    def run(
        self,
        request: BackupImportRequest,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
        pause_cb: Optional[Callable[[], bool]] = None,
    ) -> SimpleResult:
        request = self.validate(request)
        result = SimpleResult(
            success=False, dry_run=request.dry_run,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            backup_path = request.backup_path
            if not backup_path:
                from core.app_paths import get_data_root

                candidate = (Path(get_data_root()) / "backups") / Path(request.backup_name).name
                if not candidate.is_file():
                    raise BackupImportValidationError("Named backup not found")
                backup_path = str(candidate)

            if progress_cb:
                progress_cb({"percent": 10, "current_phase": "Validating backup"})
            if request.dry_run:
                result.stats = self.validate_backup(backup_path)
                result.success = True
                return result

            if progress_cb:
                progress_cb({"percent": 30, "current_phase": "Restoring backup"})
            from Api.services.import_service import ImportService

            out = ImportService.import_database_backup(
                backup_file=io.BytesIO(Path(backup_path).read_bytes()),
                restore_data=True,
            )
            ok = bool(out.get("success", out.get("valid", True))) if isinstance(out, dict) else bool(out)
            result.success = ok
            if isinstance(out, dict):
                result.stats = {
                    k: v for k, v in out.items()
                    if isinstance(v, (int, float, str, bool))
                }
            if progress_cb:
                progress_cb({"percent": 100, "current_phase": "Completed"})
        except BackupImportValidationError:
            raise
        except Exception as exc:
            from core.errors import client_safe_message

            result.errors.append(
                client_safe_message(exc, subsystem="services.importing.backup")
            )
        finally:
            result.finished_at = datetime.now(timezone.utc).isoformat()
        return result


class BatchImportService:
    """Server-side file list ingestion as a job (was synchronous endpoint)."""

    def validate(self, file_paths: List[str], source: str, side: str) -> Dict[str, Any]:
        from services.ingesting.service import IngestionService, IngestionRequest

        req = IngestionRequest(
            file_paths=[str(p) for p in (file_paths or [])],
            source=source, side=side,
        )
        svc = IngestionService()
        svc.validate(req)
        return {"validated_paths": list(req.file_paths), "source": req.source, "side": req.side}

    def preview(self, file_paths: List[str]) -> Dict[str, Any]:
        """Validate a server-path list without touching the database."""
        from core.path_safety import validate_ingestion_path, PathSafetyError

        results, accepted, rejected = [], 0, 0
        for raw in (file_paths or [])[:5000]:
            entry = {"path": str(raw)[:512]}
            try:
                resolved = validate_ingestion_path(raw)
                if not resolved.is_file():
                    entry.update({"status": "rejected", "error": "File not found"})
                    rejected += 1
                else:
                    entry.update({
                        "status": "accepted",
                        "resolved_path": str(resolved),
                        "size": resolved.stat().st_size,
                    })
                    accepted += 1
            except PathSafetyError:
                entry.update({"status": "rejected", "error": "Path not allowed"})
                rejected += 1
            results.append(entry)
        return {
            "files_discovered": len(results),
            "accepted": accepted,
            "rejected": rejected,
            "estimated_bytes": sum(r.get("size") or 0 for r in results),
            "results": results[:500],
            "dry_run": True,
        }
