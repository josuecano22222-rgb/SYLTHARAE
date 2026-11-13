"""Revocable search sessions (cancel rg / mark abandoned work)."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SearchSession:
    correlation_id: str
    cancelled: threading.Event = field(default_factory=threading.Event)
    rg_processes: List[subprocess.Popen] = field(default_factory=list)
    pg_backend_pids: List[int] = field(default_factory=list)
    cancel_requested_at: Optional[float] = None
    cancel_observed_at: Optional[float] = None

    def register_rg(self, proc: subprocess.Popen) -> None:
        self.rg_processes.append(proc)

    def register_pg_backend(self, pid: int) -> None:
        if pid and int(pid) not in self.pg_backend_pids:
            self.pg_backend_pids.append(int(pid))

    def cancel(self) -> None:
        if not self.cancel_requested_at:
            self.cancel_requested_at = time.time()
        self.cancelled.set()
        self.cancel_observed_at = time.time()
        for proc in list(self.rg_processes):
            try:
                if proc.poll() is None:
                    proc.terminate()
            except OSError:
                pass
        self._cancel_postgres_backends()

    def _cancel_postgres_backends(self) -> None:
        if not self.pg_backend_pids:
            return
        try:
            from Api.utils import execute_query

            for pid in list(self.pg_backend_pids):
                try:
                    execute_query(
                        "SELECT pg_cancel_backend(%s)",
                        (pid,),
                        fetch="one",
                    )
                except Exception as exc:
                    logger.debug("pg_cancel_backend(%s) failed: %s", pid, exc)
        except Exception as exc:
            logger.debug("PostgreSQL cancel unavailable: %s", exc)

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled.is_set()

    def cancel_timing_ms(self) -> Optional[float]:
        if self.cancel_requested_at and self.cancel_observed_at:
            return round((self.cancel_observed_at - self.cancel_requested_at) * 1000.0, 3)
        return None


class SearchSessionRegistry:
    _lock = threading.Lock()
    _sessions: Dict[str, SearchSession] = {}

    @classmethod
    def start(cls, correlation_id: str) -> SearchSession:
        session = SearchSession(correlation_id=correlation_id)
        with cls._lock:
            cls._sessions[correlation_id] = session
        return session

    @classmethod
    def get(cls, correlation_id: Optional[str]) -> Optional[SearchSession]:
        if not correlation_id:
            return None
        with cls._lock:
            return cls._sessions.get(correlation_id)

    @classmethod
    def cancel(cls, correlation_id: str) -> bool:
        with cls._lock:
            session = cls._sessions.get(correlation_id)
        if not session:
            return False
        session.cancel()
        return True

    @classmethod
    def end(cls, correlation_id: str) -> None:
        with cls._lock:
            cls._sessions.pop(correlation_id, None)
