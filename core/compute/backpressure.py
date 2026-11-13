"""Admission control: how many files may be in flight, and why.

Requirements this satisfies:

* no uncontrolled worker creation - the concurrency window is bounded by the
  configured worker count and can only *shrink* from there;
* overload must not silently abandon files - a reduced window delays submission,
  it never drops work: every discovered artifact still reaches a worker and a
  terminal state, because the ledger, not this controller, owns accounting;
* back-pressure must be observable - every window change records the measured
  pressure that caused it.

The controller is deliberately small and honest about measurement. It samples
CPU, memory and load when the platform can measure them (psutil, then
``/proc/loadavg``), and when nothing can be measured it says so and leaves the
configured window alone instead of inventing a number.

Hysteresis matters more than the thresholds: a controller that reacts to every
sample thrashes between windows, which is worse than a fixed one. The window
shrinks immediately on high pressure and grows back only after the pressure has
been low for a cooldown period, one step at a time.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

#: Bounded history of admission decisions (each one is a dict with the measured
#: values that caused it).
MAX_RECORDED_DECISIONS = 128


@dataclass(frozen=True)
class HostPressure:
    """One host-pressure sample.

    ``measured`` is False when the platform offered no way to sample; in that
    case every percentage is ``None`` and the caller must not treat the missing
    values as zero.
    """

    measured: bool
    source: str
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    load_per_core: Optional[float] = None
    total_memory_bytes: Optional[int] = None
    available_memory_bytes: Optional[int] = None
    sampled_at: float = 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "measured": self.measured,
            "source": self.source,
            "cpu_percent": self.cpu_percent,
            "memory_percent": self.memory_percent,
            "load_per_core": self.load_per_core,
            "total_memory_bytes": self.total_memory_bytes,
            "available_memory_bytes": self.available_memory_bytes,
            "sampled_at": self.sampled_at,
        }


@dataclass
class AdmissionPolicy:
    """Thresholds and their hysteresis band."""

    min_window: int = 1
    cpu_high: float = 92.0
    cpu_low: float = 70.0
    memory_high: float = 90.0
    memory_low: float = 78.0
    load_per_core_high: float = 3.0
    load_per_core_low: float = 1.5
    cooldown_s: float = 5.0

    def evaluate(self, pressure: HostPressure, window: int,
                 configured: int) -> Tuple[int, List[str]]:
        """Return ``(new_window, reasons)`` for one sample."""
        reasons: List[str] = []
        if not pressure.measured:
            return configured, ["pressure not measurable; configured window kept"]

        shrinking = False
        if pressure.memory_percent is not None and pressure.memory_percent >= self.memory_high:
            shrinking = True
            reasons.append(f"memory {pressure.memory_percent:.0f}% >= {self.memory_high:.0f}%")
        if pressure.cpu_percent is not None and pressure.cpu_percent >= self.cpu_high:
            shrinking = True
            reasons.append(f"cpu {pressure.cpu_percent:.0f}% >= {self.cpu_high:.0f}%")
        if pressure.load_per_core is not None and pressure.load_per_core >= self.load_per_core_high:
            shrinking = True
            reasons.append(f"load/core {pressure.load_per_core:.2f} >= {self.load_per_core_high:.2f}")

        if shrinking:
            return max(self.min_window, window // 2), reasons

        low = True
        if pressure.memory_percent is not None and pressure.memory_percent > self.memory_low:
            low = False
        if pressure.cpu_percent is not None and pressure.cpu_percent > self.cpu_low:
            low = False
        if pressure.load_per_core is not None and pressure.load_per_core > self.load_per_core_low:
            low = False
        if low and window < configured:
            reasons.append("pressure low; window increased by one step")
            return min(configured, window + 1), reasons
        return window, reasons


def _default_sampler() -> HostPressure:
    """Sample host pressure, or report that it cannot be measured."""
    now = time.time()
    try:
        import psutil

        memory = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None)
        cores = psutil.cpu_count() or 1
        load = os.getloadavg()[0] / cores if hasattr(os, "getloadavg") else None
        return HostPressure(
            measured=True,
            source="psutil",
            cpu_percent=float(cpu),
            memory_percent=float(memory.percent),
            load_per_core=load,
            total_memory_bytes=int(memory.total),
            available_memory_bytes=int(memory.available),
            sampled_at=now,
        )
    except Exception:
        pass
    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as handle:
            load = float(handle.read().split()[0])
        cores = os.cpu_count() or 1
        return HostPressure(
            measured=True,
            source="/proc/loadavg",
            load_per_core=load / cores,
            sampled_at=now,
        )
    except Exception as exc:
        return HostPressure(measured=False, source=f"unavailable: {exc}", sampled_at=now)


class AdmissionController:
    """Bounded, observable admission control for the ingestion window.

    Args:
        configured_window: The window the caller would use with no pressure
            (the worker count plus its reaping margin). The controller never
            returns more than this.
        policy: Thresholds and hysteresis.
        sampler: Pressure sampler override, for tests.
        clock: Monotonic clock override, for tests.
        sample_interval_s: Minimum time between samples; sampling on every
            submission would cost more than it saves.
    """

    def __init__(self, configured_window: int, policy: Optional[AdmissionPolicy] = None,
                 sampler: Optional[Callable[[], HostPressure]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 sample_interval_s: float = 0.25):
        self.configured_window = max(1, int(configured_window))
        self.policy = policy or AdmissionPolicy()
        self._sampler = sampler or _default_sampler
        self._clock = clock or time.monotonic
        self._sample_interval_s = sample_interval_s
        self._window = self.configured_window
        self._last_sample_at = 0.0
        self._last_change_at = 0.0
        self._pressure: HostPressure = HostPressure(
            measured=False, source="not sampled yet", sampled_at=0.0)
        self._decisions: Deque[Dict[str, object]] = deque(maxlen=MAX_RECORDED_DECISIONS)
        self._samples = 0

    # ------------------------------------------------------------------ API
    def sample(self, force: bool = False) -> HostPressure:
        """Take (or reuse) a pressure sample."""
        now = self._clock()
        if not force and (now - self._last_sample_at) < self._sample_interval_s:
            return self._pressure
        self._last_sample_at = now
        self._pressure = self._sampler()
        self._samples += 1
        return self._pressure

    def window(self, in_flight: int = 0, force_sample: bool = False) -> int:
        """The number of workers allowed in flight right now.

        Args:
            in_flight: Current in-flight count; a window below it does not kill
                running work, it simply stops admitting more until it drains.
            force_sample: Bypass the sample interval (used by tests and by the
                caller when it wants a fresh decision at a phase boundary).

        Returns:
            The allowed window, between ``policy.min_window`` and
            ``configured_window``.
        """
        pressure = self.sample(force=force_sample)
        now = self._clock()
        new_window, reasons = self.policy.evaluate(
            pressure, self._window, self.configured_window)
        changed = new_window != self._window
        if changed:
            if new_window > self._window and (now - self._last_change_at) < self.policy.cooldown_s:
                # Growth waits for the cooldown; shrinkage is immediate.
                changed = False
            else:
                self._last_change_at = now
        if changed:
            self._window = new_window
            self._decisions.append({
                "at": now,
                "previous_window": self._window,
                "window": new_window,
                "in_flight": in_flight,
                "reasons": reasons,
                "pressure": pressure.as_dict(),
            })
        return self._window

    @property
    def current_window(self) -> int:
        return self._window

    @property
    def pressure(self) -> HostPressure:
        return self._pressure

    def decisions(self, limit: int = 32) -> List[Dict[str, object]]:
        """The most recent window changes, newest last."""
        if limit <= 0:
            return []
        return list(self._decisions)[-limit:]

    def snapshot(self) -> Dict[str, object]:
        return {
            "configured_window": self.configured_window,
            "current_window": self._window,
            "min_window": self.policy.min_window,
            "samples": self._samples,
            "pressure": self._pressure.as_dict(),
            "decisions": self.decisions(16),
            "throttled": self._window < self.configured_window,
        }
