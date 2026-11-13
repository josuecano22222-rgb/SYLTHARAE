"""Automatic slow-search capture (no document content)."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

def _git_commit() -> Optional[str]:
    try:
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or None
    except Exception:
        return None


@dataclass
class SlowSearchCapture:
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _events: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=500))
    list_threshold_ms: float = 500.0
    snippet_threshold_ms: float = 1000.0

    def maybe_record(
        self,
        kind: str,
        duration_ms: float,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        threshold = (
            self.snippet_threshold_ms if kind == "snippet" else self.list_threshold_ms
        )
        if duration_ms < threshold:
            return
        payload = {
            "at": time.time(),
            "kind": kind,
            "duration_ms": round(duration_ms, 3),
            "commit": _git_commit(),
        }
        if meta:
            payload.update(meta)
        with self._lock:
            self._events.append(payload)

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._events)[-limit:]


SLOW = SlowSearchCapture()
