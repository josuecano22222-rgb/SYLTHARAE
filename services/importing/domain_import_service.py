"""Domain import service (the engine formerly driven by run_import.py).

Wraps ``apps.importing.import_domains.Application`` so the Import Center and
the CLI compatibility adapter share one implementation.
"""
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Dict, List, Optional

from core.errors import ClientSafeError


class DomainImportValidationError(ClientSafeError, ValueError):
    """Invalid import request; ``str(exc)`` is client-safe."""


@dataclass
class DomainImportRequest:
    data_file: Optional[str] = None
    dry_run: bool = False
    created_by: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "data_file": self.data_file,
            "dry_run": self.dry_run,
            "created_by": self.created_by,
        }


@dataclass
class DomainImportResult:
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


class DomainImportService:
    """Run/validate the domain classification data import."""

    # Server-side allowlist: data files must live in the importing app's
    # data directory (or be uploaded to it) - not arbitrary server paths.
    DEFAULT_SEARCH_PATHS = (
        "apps/importing/data",
        "data",
    )

    def __init__(self, app_factory: Optional[Callable[[], Any]] = None):
        self._app_factory = app_factory
        self._cancel = threading.Event()
        self._pause = threading.Event()

    # -- path policy -----------------------------------------------------
    def _search_paths(self) -> tuple:
        try:
            from apps.importing.utils.constants import DATA_FILE_SEARCH_PATHS
        except Exception:
            return self.DEFAULT_SEARCH_PATHS
        return tuple(DATA_FILE_SEARCH_PATHS) + self.DEFAULT_SEARCH_PATHS

    @staticmethod
    def _default_name() -> str:
        try:
            from apps.importing.utils.constants import DEFAULT_DATA_FILE
        except Exception:
            return "data.xlsx"
        return DEFAULT_DATA_FILE

    def _resolve_data_file(self, data_file: Optional[str]) -> str:
        default_name = self._default_name()
        search_paths = self._search_paths()

        if not data_file:
            for sp in search_paths:
                candidate = Path(sp) / default_name
                if candidate.is_file():
                    return str(candidate)
            raise DomainImportValidationError(
                f"No domain data file found (looked for '{default_name}' in "
                f"{', '.join(str(sp) for sp in search_paths)}). Upload one via "
                f"the Import Center first."
            )

        name = Path(data_file).name  # filenames only; no traversal
        for sp in search_paths:
            candidate = Path(sp) / name
            if candidate.is_file():
                return str(candidate)
        raise DomainImportValidationError(
            f"Data file not found. It must be in one of: "
            f"{', '.join(str(sp) for sp in search_paths)}"
        )

    # -- validation ------------------------------------------------------
    def validate(self, request: DomainImportRequest) -> DomainImportRequest:
        if not isinstance(request, DomainImportRequest):
            raise DomainImportValidationError("Invalid request type")
        if request.data_file:
            # Separators of either platform: a Windows path typed into a
            # server running on Linux must be refused too. The input is not
            # echoed back (the page inserts messages as HTML).
            name = request.data_file
            if (Path(name).name != name or PureWindowsPath(name).name != name
                    or name in (".", "..")):
                raise DomainImportValidationError(
                    f"Enter only the file name (for example {self._default_name()}), "
                    f"not a path. The file must be in one of: "
                    f"{', '.join(str(sp) for sp in self._search_paths())}"
                )
        # Always resolve (even for dry runs) so callers learn immediately
        # when no data file is available instead of failing mid-job.
        self._resolve_data_file(request.data_file)
        return request

    # -- execution -------------------------------------------------------
    def run(
        self,
        request: DomainImportRequest,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
        pause_cb: Optional[Callable[[], bool]] = None,
    ) -> DomainImportResult:
        request = self.validate(request)
        result = DomainImportResult(
            success=False, dry_run=request.dry_run,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        if progress_cb:
            progress_cb({"percent": 5, "current_phase": "Initializing import"})
        try:
            if request.dry_run:
                result.stats = self._dry_run(request, progress_cb)
                result.success = True
                return result

            if progress_cb:
                progress_cb({"percent": 10, "current_phase": "Importing domain data"})

            factory = self._app_factory or self._default_factory
            app = factory()
            data_file = self._resolve_data_file(request.data_file)
            results = app.run(data_file=data_file)
            if results:
                result.stats = self._normalize_stats(results)
                result.success = True
            else:
                result.errors.append(
                    "Domain import failed - see server logs (correlation id in log)"
                )
        except DomainImportValidationError:
            raise
        except Exception as exc:
            from core.errors import client_safe_message

            result.errors.append(
                client_safe_message(exc, subsystem="services.importing.domain")
            )
        finally:
            result.finished_at = datetime.now(timezone.utc).isoformat()
        return result

    def _dry_run(self, request: DomainImportRequest, progress_cb) -> Dict[str, Any]:
        """Parse + validate only; the orchestrator's DB writes never run."""
        data_file = self._resolve_data_file(request.data_file)
        self._bootstrap_importing_app()
        from apps.importing.data.parser import DataParser
        from apps.importing.data.loader import DataLoaderFactory

        loader = DataLoaderFactory()
        raw = loader.load(data_file)
        parser = DataParser()
        parsed = parser.parse(raw)
        if progress_cb:
            progress_cb({"percent": 80, "current_phase": "Validating"})
        if isinstance(parsed, dict):  # legacy dict shape (defensive)
            words = parsed.get("words") or []
            categories = parsed.get("categories") or []
            keywords = parsed.get("keywords") or parsed.get("phrases") or []
            return {
                "data_file": Path(data_file).name,
                "records_discovered": len(words) + len(categories) + len(keywords),
                "words": len(words),
                "categories": len(categories),
                "keywords": len(keywords),
                "dry_run": True,
            }
        # Current parser contract: a list of Domain objects, each carrying
        # its Term list; classify terms by shape exactly as the processors do.
        domains = list(parsed)
        word_count = sum(
            1 for d in domains for t in d.terms if t.is_single_word()
        )
        phrase_count = sum(1 for d in domains for t in d.terms if t.is_phrase())
        return {
            "data_file": Path(data_file).name,
            "records_discovered": len(domains) + word_count + phrase_count,
            "words": word_count,
            "categories": len(domains),
            "keywords": phrase_count,
            "dry_run": True,
        }

    @staticmethod
    def _normalize_stats(results: Any) -> Dict[str, Any]:
        if isinstance(results, dict):
            stats = dict(results)
        else:
            stats = {"result": str(results)[:200]}
        for key in ("words", "categories", "keywords", "phrases", "errors"):
            if key in stats and not isinstance(stats[key], (int, float, str)):
                try:
                    stats[key] = len(stats[key])
                except TypeError:
                    stats[key] = None
        return stats

    @staticmethod
    def _bootstrap_importing_app():
        """Import the domain-import entry module once.

        All apps.importing modules use full package paths now, so no
        sys.path manipulation is needed (the old entry points inserted
        apps/importing into sys.path, which shadowed the real ``core`` and
        ``database`` packages - that hack was removed).
        """
        import apps.importing.import_domains  # noqa: F401

    @classmethod
    def _default_factory(cls):
        cls._bootstrap_importing_app()
        from apps.importing.import_domains import Application

        return Application()
