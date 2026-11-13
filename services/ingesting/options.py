"""Ingestion request/options models.

Every field maps to a real backend capability of the processing engine
(``IntegratedFileReader`` / ``process_folder`` / ``process_single_file``).
There are no cosmetic options.
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class IngestionOptions:
    """Advanced processing options (all optional; defaults = engine defaults)."""

    # Worker concurrency for the processing engine (0 = configured default)
    max_workers: int = 0
    # Checkpoint policy for folder ingestions: auto | fresh | off
    #   auto  = resume from existing checkpoint if present, else create one
    #   fresh = discard any existing checkpoint and start clean
    #   off   = no checkpointing (pause/resume unavailable for the job)
    checkpoint: str = "auto"
    # Monitoring of thread workers inside the engine
    enable_monitoring: bool = True

    def sanitized(self) -> "IngestionOptions":
        """Server-side clamps for untrusted input (spec section 16)."""
        opts = IngestionOptions(
            max_workers=max(0, min(int(self.max_workers or 0), 64)),
            checkpoint=self.checkpoint if self.checkpoint in ("auto", "fresh", "off") else "auto",
            enable_monitoring=bool(self.enable_monitoring),
        )
        return opts

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IngestionRequest:
    """A validated ingestion request (already passed path-safety checks)."""

    # Exactly one of path / file_paths must be set.
    path: Optional[str] = None
    file_paths: List[str] = field(default_factory=list)

    # Storage taxonomy (mandatory, as enforced by the engine forever)
    source: str = ""
    side: str = ""

    # Basic-mode option: walk nested directories (folders only).
    recursive: bool = True

    options: IngestionOptions = field(default_factory=IngestionOptions)
    # Dry-run: discover/validate/estimate only; no database writes.
    dry_run: bool = False

    # Job correlation
    created_by: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "file_paths": list(self.file_paths),
            "source": self.source,
            "side": self.side,
            "recursive": self.recursive,
            "options": self.options.to_dict(),
            "dry_run": self.dry_run,
            "created_by": self.created_by,
        }
