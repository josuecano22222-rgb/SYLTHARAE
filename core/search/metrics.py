"""In-process search performance aggregates (dashboard seed)."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional


@dataclass
class SearchMetricsStore:
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _latencies_ms: Deque[float] = field(default_factory=lambda: deque(maxlen=2000))
    _snippet_latencies_ms: Deque[float] = field(default_factory=lambda: deque(maxlen=2000))
    _cancellations: int = 0
    _timeouts: int = 0
    _slow: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200))
    _engine_counts: Dict[str, int] = field(default_factory=dict)
    _requests: int = 0

    def record_list(self, duration_ms: float, meta: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._requests += 1
            self._latencies_ms.append(duration_ms)
            if meta and meta.get("engine"):
                eng = str(meta["engine"])
                self._engine_counts[eng] = self._engine_counts.get(eng, 0) + 1
            if duration_ms >= 500 and meta:
                self._slow.append({"at": time.time(), "kind": "list", "ms": duration_ms, **meta})

    def record_plan_engine(self, engine: Optional[str]) -> None:
        if not engine:
            return
        with self._lock:
            key = str(engine)
            self._engine_counts[key] = self._engine_counts.get(key, 0) + 1

    def record_snippets(self, duration_ms: float) -> None:
        with self._lock:
            self._snippet_latencies_ms.append(duration_ms)

    def record_cancel(self) -> None:
        with self._lock:
            self._cancellations += 1

    def record_timeout(self) -> None:
        with self._lock:
            self._timeouts += 1

    @staticmethod
    def _percentile(values: List[float], pct: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = min(len(ordered) - 1, int(len(ordered) * pct))
        return round(ordered[idx], 2)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            lat = list(self._latencies_ms)
            snip = list(self._snippet_latencies_ms)
            slow = list(self._slow)[-20:]
            cancels = self._cancellations
            timeouts = self._timeouts
            requests = self._requests
            engine_counts = dict(self._engine_counts)
        total_eng = sum(engine_counts.values()) or 1
        engine_pct = {
            k: round(100.0 * v / total_eng, 2)
            for k, v in sorted(engine_counts.items(), key=lambda kv: -kv[1])
        }
        return {
            "requests": requests,
            "engine_distribution": engine_pct,
            "list_latency_ms": {
                "p50": self._percentile(lat, 0.50),
                "p95": self._percentile(lat, 0.95),
                "p99": self._percentile(lat, 0.99),
                "samples": len(lat),
            },
            "snippet_latency_ms": {
                "p50": self._percentile(snip, 0.50),
                "p95": self._percentile(snip, 0.95),
                "samples": len(snip),
            },
            "cancellations": cancels,
            "timeouts": timeouts,
            "recent_slow": slow,
        }


STORE = SearchMetricsStore()
