"""Ingestion service: the engine behind the Input UI, the jobs API and the
CLI compatibility layer.

Wraps the existing processing engine (``pipeline.integrated_reader.
IntegratedFileReader``) with:

* request validation + path-safety enforcement (SEC-06);
* dry-run discovery (no database writes);
* cooperative cancel/pause wiring into the engine;
* real progress callbacks (never fake percentages);
* structured results.

No argparse / input() / print / exit codes — that belongs to adapters.
"""
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.path_safety import (
    configured_ingestion_roots,
    validate_ingestion_path,
)
from services.ingesting.options import IngestionOptions, IngestionRequest  # noqa: F401

from core.errors import ClientSafeError


class IngestionValidationError(ClientSafeError, ValueError):
    """Raised for invalid requests; ``str(exc)`` is client-safe."""


@dataclass
class IngestionResult:
    """Structured outcome of one ingestion execution."""

    success: bool
    cancelled: bool = False
    paused: bool = False
    dry_run: bool = False
    stats: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    results: List[Dict[str, Any]] = field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    path_watermark: Optional[int] = None
    batch_report: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        summary = {
            "files_total": self.stats.get("files_total"),
            "files_stored": self.stats.get("files_stored"),
            "files_duplicates": self.stats.get("files_duplicates"),
            "files_failed": self.stats.get("files_failed"),
            "files_skipped": self.stats.get("files_skipped"),
        }
        if self.batch_report:
            summary["batch_report"] = {
                "path_ids": (self.batch_report.get("path_ids") or [])[:20000],
                "path_watermark": self.batch_report.get("path_watermark"),
                "type_outcomes": self.batch_report.get("type_outcomes") or {},
                "source": self.batch_report.get("source"),
                "side": self.batch_report.get("side"),
                "path": self.batch_report.get("path"),
                "html_path": self.batch_report.get("html_path"),
                "recovery": self.batch_report.get("recovery"),
            }
        return {
            "success": self.success,
            "cancelled": self.cancelled,
            "paused": self.paused,
            "dry_run": self.dry_run,
            "stats": self.stats,
            "warnings": self.warnings[:200],
            "errors": self.errors[:200],
            "result_summary": summary,
        }


class _ControlRequested(Exception):
    """Internal: cooperative cancel/pause hit during engine execution."""

    def __init__(self, paused: bool):
        super().__init__("pause" if paused else "cancel")
        self.paused = paused


class IngestionService:
    """Runs ingestion requests against the shared processing engine."""

    MAX_FILE_PATHS = 5000

    def __init__(self, reader_factory: Optional[Callable[..., Any]] = None):
        # Injectable for tests; production uses the real engine.
        self._reader_factory = reader_factory

    @staticmethod
    def _resolve_entity_name(value: str, kind: str) -> Optional[str]:
        """INJ-03: map a numeric source/side id to its name (returns None when
        ``value`` is not a numeric id or the id is unknown)."""
        if not value.isdigit():
            return None
        try:
            from database.services.contents_db_service import ContentDBService
            db = ContentDBService()
            if kind == "source":
                entities = db.get_all_sources() or []
            else:
                entities = db.get_all_sides() or []
            for eid, ename in entities:
                if int(eid) == int(value) and ename:
                    return str(ename)
        except Exception:
            return None
        return None

    def _resolve_identifier(self, value: str, kind: str) -> str:
        """INJ-04: resolve an identifier according to the API contract.

        Precedence is deterministic and idempotent (validate() runs more than
        once per job, and a first pass may rewrite an id into a numeric-looking
        name such as side '9'):

        1. exact existing *name*      -> used as-is
        2. numeric + existing *id*    -> resolved to that entity's name
        3. non-numeric, new           -> used as-is (pipeline creates it)
        4. numeric, matches neither   -> rejected (previously fell through to
           the name-based pipeline, silently creating garbage entities
           literally named '999999')

        Documented ambiguity: an id whose value equals a *different* entity's
        name (id 9 vs a side named '9') is treated as the name — clients
        address such entities by name.
        """
        try:
            from database.services.contents_db_service import ContentDBService
            db = ContentDBService()
            entities = (db.get_all_sources() if kind == "source" else db.get_all_sides()) or []
        except Exception:
            entities = []
        for _eid, ename in entities:
            if ename is not None and str(ename) == value:
                return value
        if not value.isdigit():
            return value
        name = self._resolve_entity_name(value, kind)
        if name is not None:
            return name
        raise IngestionValidationError(
            f"{kind} '{value}' matches no existing id or name - pass an "
            "existing id or an entity name"
        )

    # ------------------------------------------------------------------
    # Validation (spec sections 16/23): all request input is untrusted.
    # ------------------------------------------------------------------
    def validate(self, request: IngestionRequest) -> IngestionRequest:
        if not isinstance(request, IngestionRequest):
            raise IngestionValidationError("Invalid request type")
        if bool(request.path) == bool(request.file_paths):
            raise IngestionValidationError(
                "Provide exactly one of 'path' or 'file_paths'"
            )
        if not str(request.source or "").strip() or not str(request.side or "").strip():
            raise IngestionValidationError(
                "source and side are mandatory - no defaults are allowed"
            )
        request.options = request.options.sanitized() if request.options else IngestionOptions()

        paths: List[str] = []
        if request.path:
            paths = [request.path]
        else:
            if not isinstance(request.file_paths, list):
                raise IngestionValidationError("file_paths must be a list")
            if len(request.file_paths) > self.MAX_FILE_PATHS:
                raise IngestionValidationError(
                    f"Too many file paths (max {self.MAX_FILE_PATHS})"
                )
            paths = [str(p) for p in request.file_paths]
        if not paths:
            raise IngestionValidationError("No paths provided")

        validated: List[str] = []
        for raw in paths:
            try:
                resolved = validate_ingestion_path(raw)
            except Exception as exc:
                # Fail closed: without configured roots or with a bad path,
                # server-side access is denied (SEC-06). Surface actionable
                # guidance for the disabled-roots case.
                if "disabled" in str(exc):
                    raise IngestionValidationError(str(exc)) from exc
                # Echo the path as submitted. `Path(raw).name` was wrong twice
                # over: on Windows it reduces `C:\Windows\System32` to
                # `System32`, and on POSIX it turns `/etc` into `etc`, so the
                # operator is told about a path they did not type.
                raise IngestionValidationError(
                    f"Path not allowed: {str(raw).strip()}"
                ) from exc
            if not resolved.exists():
                raise IngestionValidationError(
                    f"Path does not exist: {str(raw).strip()}"
                )
            validated.append(str(resolved))
        # Second pass invariants that don't need per-path errors surfaced.
        if request.path:
            request.path = validated[0]
        else:
            request.file_paths = validated
        # INJ-02: normalize source/side to str (API clients may send JSON ints)
        request.source = str(request.source).strip()
        request.side = str(request.side).strip()

        # INJ-03/INJ-04: the storage pipeline is *name*-based (it compares
        # against source/side names and creates a new entity named "1" when
        # given the numeric id 1). Numeric identifiers resolve to their
        # canonical names here — the single entry point shared by the API,
        # the job runner, and the CLI. Unknown numeric ids are rejected
        # instead of silently becoming entity names.
        request.source = self._resolve_identifier(request.source, "source")
        request.side = self._resolve_identifier(request.side, "side")

        return request

    # ------------------------------------------------------------------
    # Discovery / dry run (spec section 8): no DB writes, real numbers.
    # ------------------------------------------------------------------
    def discover(self, request: IngestionRequest) -> Dict[str, Any]:
        """Walk the requested paths and report what *would* be processed."""
        roots = configured_ingestion_roots()
        target = Path(request.path) if request.path else None
        files: List[Dict[str, Any]] = []
        if target and target.is_file():
            files = [self._file_entry(target)]
        elif target:
            files = self._walk(target, recursive=request.recursive)
        else:
            for p in request.file_paths:
                fp = Path(p)
                if fp.is_file():
                    files.append(self._file_entry(fp))
                elif fp.is_dir():
                    files.extend(self._walk(fp, recursive=request.recursive))

        try:
            from reader_file.services.file_router_service import FileRouterService

            supported = {
                str(e).lower().lstrip(".")
                for e in (FileRouterService().get_supported_extensions() or set())
            }
        except Exception:
            supported = set()

        eligible, unsupported = [], 0
        total_bytes = 0
        for f in files:
            ext = (f.get("extension") or "").lower().lstrip(".")
            if ext and ext not in supported:
                unsupported += 1
                continue
            eligible.append(f)
            total_bytes += f.get("size_bytes") or 0

        return {
            "files_discovered": len(files),
            "files_eligible": len(eligible),
            "files_unsupported": unsupported,
            "estimated_bytes": total_bytes,
            "sample_files": [
                {"path": f["path"], "size": f.get("size_bytes")}
                for f in eligible[:20]
            ],
            "ingestion_roots_configured": bool(roots),
            "dry_run": True,
        }

    @staticmethod
    def _file_entry(p: Path) -> Dict[str, Any]:
        try:
            size = p.stat().st_size
        except OSError:
            size = None
        return {
            "path": str(p),
            "name": p.name,
            "extension": p.suffix.lower(),
            "size_bytes": size,
            "type": "FILE",
        }

    def _walk(self, root: Path, recursive: bool) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            entries = list(os.scandir(root))
        except OSError:
            return out
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False):
                    out.append(self._file_entry(Path(entry.path)))
                elif entry.is_dir(follow_symlinks=False) and recursive:
                    out.extend(self._walk(Path(entry.path), recursive=True))
            except OSError:
                continue
        return out

    # ------------------------------------------------------------------
    # Execution. Called by the JobManager worker thread (or the CLI
    # adapter in compatibility mode). Reports real progress via callback.
    # ------------------------------------------------------------------
    def run(
        self,
        request: IngestionRequest,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
        pause_cb: Optional[Callable[[], bool]] = None,
        engine_cb: Optional[Callable[[Any], None]] = None,
        issue_registry=None,
    ) -> IngestionResult:
        request = self.validate(request)
        result = IngestionResult(
            success=False, dry_run=request.dry_run,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        if not request.dry_run:
            try:
                from services.reporting.ingestion_batch import path_watermark

                result.path_watermark = path_watermark()
            except Exception:
                result.path_watermark = None
        if request.dry_run:
            result.stats = self.discover(request)
            result.success = True
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result

        IntegratedFileReader = self._reader_factory or self._default_reader_factory
        reader = self._make_reader(IntegratedFileReader, request, issue_registry)
        try:
            if engine_cb is not None:
                try:
                    engine_cb(reader)
                except Exception:
                    pass
            reader.progress_callback = self._wrap_progress(progress_cb)

            # PROGRESS: publish immediately, before any file is touched. The UI
            # otherwise has nothing to render until the first unit settles, and
            # for a single container that can be the entire job.
            self._emit_initial(reader, request)

            # Poll control flags from a background sentinel so a cancel
            # request lands even while the engine waits on worker threads.
            sentinel_stop = threading.Event()

            def _sentinel():
                while not sentinel_stop.wait(0.5):
                    try:
                        if cancel_cb and cancel_cb():
                            reader.request_cancel()
                        if pause_cb and pause_cb():
                            reader.request_pause()
                    except Exception:
                        continue

            sentinel = threading.Thread(target=_sentinel, name="job-control-sentinel", daemon=True)
            sentinel.start()
            try:
                if request.path:
                    target = Path(request.path)
                    if target.is_file():
                        # PROGRESS: process_single_file registers the file in the
                        # ledger itself, and grows it if the file is a container.
                        # Seeding files_total=1 here used to be the *only* total
                        # the job ever saw, so an archive with 500 members
                        # reported 1 unit of work.
                        res = reader.process_single_file(request.path)
                        result.results = [res] if res else []
                    else:
                        result.results = reader.process_folder(request.path) or []
                else:
                    result.results = self._run_file_list(reader, request)
            finally:
                sentinel_stop.set()

            # Outcome
            cancelled = reader.is_cancel_requested()
            paused = (not cancelled) and reader.is_pause_requested()
            result.cancelled = cancelled
            result.paused = paused
            result.stats.update(self._collect_stats(reader, result.results))
            if issue_registry is not None:
                issue_registry.merge_into_result(result)
            self._append_stats_issue_summary(result)
            result.success = not cancelled and not paused
            if cancelled:
                result.warnings.append("Job cancelled by request; files already stored remain in the database")
            if paused:
                result.warnings.append("Job paused at a safe boundary; resume to continue")
        except _ControlRequested as ctrl:
            result.cancelled = not ctrl.paused
            result.paused = ctrl.paused
            result.warnings.append(
                "Job paused at a safe boundary" if ctrl.paused
                else "Job cancelled by request"
            )
        except Exception as exc:
            # Surface a sanitized message; full detail goes to server logs.
            from core.errors import client_safe_message

            result.errors.append(client_safe_message(exc, subsystem="services.ingesting"))
        finally:
            try:
                reader.__exit__(None, None, None)
            except Exception:
                pass
            result.finished_at = datetime.now(timezone.utc).isoformat()
            if not result.dry_run:
                try:
                    from services.reporting.ingestion_batch import (
                        attach_batch_identity, persist_result_report,
                    )

                    attach_batch_identity(result, request)
                    persist_result_report(result, request)
                except Exception:
                    pass
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _append_stats_issue_summary(result: IngestionResult) -> None:
        """Ensure terminal stats are reflected when per-file lines were capped."""
        stats = result.stats or {}
        failed = int(stats.get("files_failed") or 0)
        retryable = int(stats.get("files_retryable") or 0)
        unsupported = int(stats.get("files_unsupported") or 0)
        locked = int(stats.get("files_locked") or 0)
        storage_failed = int(stats.get("storage_failed") or 0)
        has_failed_lines = any("[failed]" in (e or "") for e in result.errors)
        if failed and not has_failed_lines:
            result.warnings.append(
                f"{failed} file(s) failed during processing (see batch report for paths)"
            )
        if retryable and not any("[retryable]" in (w or "") for w in result.warnings):
            result.warnings.append(f"{retryable} file(s) marked retryable")
        if unsupported and not any("[unsupported]" in (w or "") for w in result.warnings):
            result.warnings.append(f"{unsupported} unsupported file(s)")
        if locked and not any("[locked]" in (w or "") for w in result.warnings):
            result.warnings.append(f"{locked} file(s) skipped because they were locked")
        if storage_failed and not any("[storage]" in (e or "") for e in result.errors):
            result.errors.append(f"{storage_failed} file(s) could not be stored")

    def _make_reader(self, factory, request: IngestionRequest, issue_registry=None):
        import inspect

        opts = request.options
        workers = opts.max_workers or 0
        kwargs: Dict[str, Any] = {
            "enable_storage": True,
            "storage_source": request.source,
            "storage_side": request.side,
        }
        if issue_registry is not None:
            kwargs["issue_registry"] = issue_registry
        if workers:
            kwargs["max_workers"] = workers
        if opts.enable_monitoring is not None:
            kwargs["enable_monitoring"] = bool(opts.enable_monitoring)
        try:
            sig = inspect.signature(factory)
            if "checkpoint_file" in sig.parameters:
                kwargs["checkpoint_file"] = self._checkpoint_file(request)
        except (TypeError, ValueError):
            pass
        return factory(**kwargs)

    @staticmethod
    def _checkpoint_file(request: IngestionRequest) -> Optional[str]:
        """Checkpoint policy -> concrete file (mirrors the CLI's scheme)."""
        import hashlib

        policy = request.options.checkpoint if request.options else "auto"
        if policy == "off" or not request.path:
            return None
        try:
            from core.app_paths import get_checkpoints_dir

            ckpt_dir = get_checkpoints_dir()
        except Exception:
            ckpt_dir = Path("data/checkpoints")
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(str(Path(request.path).resolve()).encode()).hexdigest()[:16]
        path = ckpt_dir / f"checkpoint_{digest}_{request.source}_{request.side}.json"
        if policy == "fresh":
            # A fresh run must forget everything: the snapshot, its append-only
            # journal, and the per-container member checkpoints. Removing only
            # the snapshot left the journal behind, and it would still skip files.
            import shutil
            for stale in (path, path.with_suffix(path.suffix + ".journal")):
                try:
                    if stale.exists():
                        stale.unlink()
                except OSError:
                    pass
            try:
                members_dir = path.with_name(path.stem + ".members")
                if members_dir.is_dir():
                    shutil.rmtree(members_dir, ignore_errors=True)
            except OSError:
                pass
        return str(path)

    def _run_file_list(self, reader, request: IngestionRequest) -> List[Dict[str, Any]]:
        """Process an explicit file list through the engine workers."""
        files = []
        for p in request.file_paths:
            fp = Path(p)
            files.append({
                "path": str(fp),
                "name": fp.name,
                "extension": fp.suffix.lower(),
                "size_bytes": fp.stat().st_size if fp.exists() else 0,
                "type": "FILE",
            })

        # PROGRESS: register the whole list up front. This used to write
        # ``_processing_stats["total"]`` *after* the loop finished, so every
        # snapshot taken during the run had total=0 and rendered as 0%.
        ledger = getattr(reader, "progress_ledger", None)
        if ledger is not None:
            ledger.add_discovered(len(files), key=f"list::{id(request)}", initial=True)
        reader._set_current(phase="Processing files")
        reader._notify_progress()

        results: List[Dict[str, Any]] = []
        # Single worker per file keeps per-file results identical to the
        # engine's own semantics; concurrency comes from the engine's batch
        # path for folder jobs and from job-level concurrency for lists.
        for file_info in files:
            if reader._control_requested():
                break
            reader._set_current(file_path=file_info["path"])
            res = reader.process_single_file(file_info["path"])
            if res:
                results.append(res)
            reader._notify_progress()

        # Anything not reached (cancel/pause) is terminal work, not pending.
        # abandon() rather than record(): these paths are already counted in the
        # denominator, so only their terminal state is missing.
        if ledger is not None:
            outstanding = ledger.snapshot().get("files_pending", 0)
            if outstanding > 0:
                from pipeline.progress_ledger import OUTCOME_SKIPPED
                ledger.abandon(outstanding, OUTCOME_SKIPPED)
        return results

    @staticmethod
    def _emit_initial(reader, request: IngestionRequest) -> None:
        """Push a first snapshot so the UI has state before work starts."""
        try:
            ledger = getattr(reader, "progress_ledger", None)
            if ledger is not None:
                label = request.path or (
                    f"{len(request.file_paths or [])} selected file(s)"
                )
                ledger.set_phase(f"Preparing: {Path(str(label)).name or label}")
            reader._notify_progress()
        except Exception:
            pass

    def _wrap_progress(self, cb):
        if cb is None:
            return None

        def _cb(snapshot):
            cb(snapshot)

        return _cb

    @staticmethod
    def _default_reader_factory(**kwargs):
        from pipeline.integrated_reader import IntegratedFileReader

        return IntegratedFileReader(**kwargs)

    @staticmethod
    def _collect_stats(reader, results) -> Dict[str, Any]:
        stats: Dict[str, Any] = {}
        try:
            live = reader.get_live_progress()
            stats.update({
                "files_total": live.get("total_files"),
                "files_processed": live.get("files_done"),
                "files_succeeded": live.get("files_completed"),
                "files_failed": live.get("files_failed"),
                # PROGRESS: nested accounting. ``files_total`` is now the
                # *discovered* total (top level + everything materialised out of
                # containers), so the initial figure is reported separately and
                # the difference is the nested work that used to be invisible.
                "files_initial": live.get("files_initial"),
                "files_discovered": live.get("files_discovered"),
                "files_nested": live.get("files_nested"),
                "containers_opened": live.get("containers_opened"),
                "files_skipped": live.get("files_skipped"),
                "files_unsupported": live.get("files_unsupported"),
                "files_retryable": live.get("files_retryable"),
                "files_locked": live.get("files_locked"),
                "files_cancelled": live.get("files_cancelled"),
                "files_in_progress": live.get("in_progress"),
                "files_pending": live.get("files_pending"),
                "children_by_parent": live.get("children_by_parent"),
                "children_by_parent_overflow": live.get("children_by_parent_overflow"),
                "containers_in_flight": live.get("containers_in_flight"),
                "container_work_outstanding": live.get("container_work_outstanding"),
                # The closing percentage and completion flag. Without these the
                # final persisted stats describe the counts but not the state the
                # progress bar should render, so the API's statistics.percent was
                # empty for every finished job.
                "percent": live.get("percent"),
                "complete": live.get("complete"),
                "elapsed_seconds": live.get("elapsed_seconds"),
            })
        except Exception:
            pass
        try:
            storage = reader.get_storage_statistics() or {}
            stats.update({
                "files_stored": storage.get("completed", 0),
                "files_duplicates": storage.get("duplicates", 0),
                "storage_failed": storage.get("failed", 0),
            })
        except Exception:
            pass
        # Bytes processed: prefer the reader's exact running total.  The
        # per-file result list is a bounded window on large runs (each entry
        # carries extracted content), so summing it would silently under-report
        # once the window filled; the reader tracks every file's size whether
        # or not its dictionary was retained.
        exact = None
        try:
            exact = (reader.get_statistics() or {}).get("bytes_processed")
        except Exception:
            exact = None
        if exact is not None:
            stats["bytes_processed"] = exact
        elif results:
            bytes_total = 0
            for r in results:
                if isinstance(r, dict):
                    try:
                        bytes_total += int(r.get("file_size") or 0)
                    except (TypeError, ValueError):
                        pass
            stats["bytes_processed"] = bytes_total
        return stats
