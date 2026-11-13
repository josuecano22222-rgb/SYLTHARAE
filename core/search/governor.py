"""Global backpressure for interactive search I/O."""

from __future__ import annotations

import threading
from typing import Optional

from core.search.limits import SearchLimits

_semaphore: Optional[threading.BoundedSemaphore] = None
_db_semaphore: Optional[threading.BoundedSemaphore] = None
_lock = threading.Lock()


def interactive_db_semaphore() -> threading.BoundedSemaphore:
    global _db_semaphore
    if _db_semaphore is None:
        with _lock:
            if _db_semaphore is None:
                limits = SearchLimits.from_settings()
                slots = max(2, min(limits.max_concurrent_searches, 32))
                _db_semaphore = threading.BoundedSemaphore(slots)
    return _db_semaphore


def snippet_slot_semaphore() -> threading.BoundedSemaphore:
    global _semaphore
    if _semaphore is None:
        with _lock:
            if _semaphore is None:
                limits = SearchLimits.from_settings()
                _semaphore = threading.BoundedSemaphore(
                    max(1, limits.max_snippet_workers * limits.max_concurrent_searches // 4)
                )
    return _semaphore
