"""Gateway compute-control layer.

One place decides *where* compute-intensive work runs, *how much* concurrency
and memory it may use, whether the gateway's own networking/IO work is protected
from it, and how the system behaves when the requested accelerator is not
available.

Design rules (all verified by tests):

* **Honest capability.**  A device is only ever selected when the capability
  probes found a usable device *and* a usable software path.  Requesting a mode
  the machine cannot honour falls back to CPU and records a fallback event -
  it never silently pretends to have accelerated anything.
* **Bounded, back-pressured scheduling.**  Work is admitted through a bounded
  queue with a concurrency ceiling derived from the allocation; when the queue
  or the latency budget is exceeded the gateway applies back-pressure (waits,
  then reduces admission) instead of growing memory without limit.
* **Process isolation.**  The gateway reserves CPU cores for gateway/networking
  work and confines compute workers to the remaining cores via CPU affinity and
  nice level, so a saturated ingestion cannot starve the web/gateway path.
* **Measured monitoring.**  Utilization, memory, temperature, throughput, queue
  depth and latency are sampled from the OS, not estimated.
* **Graceful fallback.**  Any failure to place or execute on an accelerator
  falls back to the CPU path with the same semantics and outputs.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, FrozenSet, List, Optional, Tuple

from .capabilities import CPU, HardwareInventory
from .routing import (
    DeviceDecision,
    DeviceUnavailableError,
    ExecutionMode,
    WorkloadKind,
    accelerator_memory_fits,
    allowed_devices,
    choose_device,
    profile_for,
    requires_accelerator_in,
)

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")

#: Sliding window used for latency percentiles and throughput.
_METRIC_WINDOW = 512


@dataclass
class Allocation:
    """Resource budget the gateway hands to a workload class."""

    cpu_cores: int
    reserved_gateway_cores: int
    memory_bytes: int
    queue_depth: int
    concurrency: int
    gpu_devices: int = 0
    accelerator_kinds: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "cpu_cores": self.cpu_cores,
            "reserved_gateway_cores": self.reserved_gateway_cores,
            "memory_bytes": self.memory_bytes,
            "queue_depth": self.queue_depth,
            "concurrency": self.concurrency,
            "gpu_devices": self.gpu_devices,
            "accelerator_kinds": list(self.accelerator_kinds),
        }


@dataclass
class AcceleratorBackend:
    """A callable accelerator implementation for one or more workloads.

    A backend is only *usable* after its ``self_test`` passes on this machine:
    the gateway runs it once at registration and refuses to advertise or use a
    backend that cannot demonstrate a correct result. This is what keeps
    "supports acceleration" an implementation fact rather than a claim - a
    backend that is missing, broken or numerically wrong simply never gets
    selected, and the workload stays on the CPU.

    ``self_test`` must verify *equivalence*, not just "it ran": for a hashing or
    extraction backend that means comparing its output with the CPU
    implementation on a fixed input.
    """

    kind: str
    name: str
    self_test: Callable[[], bool]
    run: Callable[..., Any]
    workloads: FrozenSet["WorkloadKind"]
    detail: str = ""
    verified: bool = False
    verification_error: str = ""


@dataclass
class ComputeStats:
    submitted: int = 0
    completed: int = 0
    failed: int = 0
    rejected: int = 0
    fallbacks: int = 0
    capability_errors: int = 0
    peak_queue_depth: int = 0
    total_queue_wait_s: float = 0.0
    total_execution_s: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "submitted": self.submitted,
            "completed": self.completed,
            "failed": self.failed,
            "rejected": self.rejected,
            "fallbacks": self.fallbacks,
            "capability_errors": self.capability_errors,
            "peak_queue_depth": self.peak_queue_depth,
        }


class BackpressureError(RuntimeError):
    """Raised when work cannot be admitted within ``admission_timeout``."""


class ComputeGateway:
    """Selects execution devices and meters/limits compute work."""

    def __init__(self, mode: ExecutionMode = ExecutionMode.AUTO,
                 inventory: Optional[HardwareInventory] = None,
                 max_concurrency: Optional[int] = None,
                 queue_depth: Optional[int] = None,
                 memory_budget_bytes: Optional[int] = None,
                 reserved_gateway_cores: Optional[int] = None,
                 latency_budget_s: Optional[float] = None,
                 admission_timeout_s: float = 5.0,
                 enable_affinity: bool = True):
        self.inventory = inventory or HardwareInventory()
        self.mode = ExecutionMode.parse(mode)
        self.stats = ComputeStats()

        cpu_units = max(1, int(self.inventory.cpu.compute_units or os.cpu_count() or 1))
        #: Leave headroom so an ingestion cannot starve the gateway/network path.
        #: On a machine with few cores there is nothing to spare - reserving one
        #: would cut compute throughput in half for no isolation benefit, so the
        #: reservation is zero there and isolation falls back to scheduling
        #: priority (nice) instead.  Larger machines reserve a slice.
        default_reserved = 0 if cpu_units <= 4 else max(1, cpu_units // 8)
        self.reserved_gateway_cores = (default_reserved if reserved_gateway_cores is None
                                       else max(0, int(reserved_gateway_cores)))

        self.max_concurrency = int(max_concurrency or max(1, cpu_units - self.reserved_gateway_cores))
        # Memory budget: only a share of what is free, so a large batch cannot
        # push the host into swap.
        if memory_budget_bytes is None:
            try:
                import psutil

                free = psutil.virtual_memory().available
                memory_budget_bytes = int(free * 0.25)
            except Exception:
                memory_budget_bytes = 512 * 1024 * 1024
        self.memory_budget_bytes = max(16 * 1024 * 1024, int(memory_budget_bytes))

        self.queue_depth_limit = int(queue_depth or max(8, self.max_concurrency * 8))
        self.latency_budget_s = float(latency_budget_s or 10.0)
        self.admission_timeout_s = float(admission_timeout_s)
        self.enable_affinity = bool(enable_affinity)

        self._in_flight = 0
        self._queued = 0
        self._lock = threading.Lock()
        self._admission = threading.Semaphore(self.max_concurrency)
        self._waiting = 0
        self._latencies: Deque[float] = deque(maxlen=_METRIC_WINDOW)
        self._queue_waits: Deque[float] = deque(maxlen=_METRIC_WINDOW)
        self._completion_times: Deque[float] = deque(maxlen=_METRIC_WINDOW)
        self._fallback_events: List[Dict[str, Any]] = []
        #: Per-execution placement records, newest first, bounded. Each one
        #: answers "which device actually ran this workload, with what
        #: processor, for how long, and did it fall back and why" - the
        #: provenance the forensic report needs. Aggregates live in
        #: ``by_workload`` so nothing grows with the number of files.
        self._execution_records: Deque[Dict[str, Any]] = deque(maxlen=256)
        self.by_workload: Dict[str, Dict[str, Any]] = {}
        #: Explicit opt-in to CPU fallback in ``gpu`` mode. Off by default: the
        #: requested mode is honoured or refused, never quietly changed.
        self.allow_cpu_fallback = _env_flag("COMPUTE_GPU_FALLBACK", False)
        self._backends: Dict[str, AcceleratorBackend] = {}
        self._paused_until = 0.0
        self._gateway_threads: List[int] = []
        self._worker_threads: List[int] = []

        logger.info(
            "Compute gateway: mode=%s, concurrency=%d, queue=%d, reserved_cores=%d, "
            "memory_budget=%.1f MB",
            self.mode.value, self.max_concurrency, self.queue_depth_limit,
            self.reserved_gateway_cores, self.memory_budget_bytes / 1024 ** 2,
        )

    # ------------------------------------------------------------------
    # Capability / mode
    # ------------------------------------------------------------------
    @property
    def available_accelerators(self) -> Dict[str, bool]:
        """Device kinds that can actually be executed on right now.

        A kind is usable when the hardware probe found a device with a working
        runtime, **or** a registered backend of that kind passed its self-test.
        The second case is how an accelerator without a Python-visible runtime
        (an FPGA bitstream, a DPU offload) becomes usable: it is only usable
        once something can demonstrably compute with it.
        """
        available = {kind: info.available
                     for kind, info in self.inventory.accelerators.items()}
        for kind in self._backends:
            available[kind] = True
        return available

    def select_device(self, workload: WorkloadKind) -> DeviceDecision:
        """Where would ``workload`` run right now, and why.

        Selection is capability- and fit-aware: a device whose accelerator
        memory is smaller than the workload's declared requirement - or whose
        capacity could not be determined - is reported as not fitting, so a
        placement never claims a device it cannot hold the work on.
        """
        if workload in (WorkloadKind.OCR, WorkloadKind.CLASSIFICATION) and \
                self.mode is ExecutionMode.CPU_GPU:
            preferred = choose_device(workload, self.mode, self.available_accelerators)
            if preferred.device != CPU:
                return self._with_memory_fit(workload, preferred)
        return self._with_memory_fit(
            workload, choose_device(workload, self.mode, self.available_accelerators))

    def _with_memory_fit(self, workload: WorkloadKind,
                         decision: DeviceDecision) -> DeviceDecision:
        """Record whether the chosen device can hold the workload's working set."""
        if decision.device == CPU:
            return decision
        info = self.inventory.accelerators.get(decision.device)
        fits, reason = accelerator_memory_fits(
            workload, getattr(info, "memory_bytes", None) if info else None)
        decision.memory_fits = fits
        decision.memory_note = reason
        return decision

    def register_accelerator_backend(self, backend: AcceleratorBackend) -> bool:
        """Register an accelerator implementation, if it proves itself.

        Runs ``backend.self_test()`` immediately. A backend that fails (raises
        or returns False) is recorded as unverified, is not added to the usable
        set, and can never be selected - the workload keeps running on the CPU.
        Returns whether the backend was verified and registered.
        """
        verified = False
        error = ""
        try:
            verified = bool(backend.self_test())
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        backend.verified = verified
        backend.verification_error = error
        if verified:
            self._backends[backend.kind] = backend
            logger.info("Accelerator backend verified: %s (%s)", backend.name,
                        backend.kind)
        else:
            logger.warning(
                "Accelerator backend %s (%s) failed verification%s - it will not "
                "be used", backend.name, backend.kind,
                f": {error}" if error else " (self-test returned False)",
            )
        return verified

    def verified_backends(self) -> Dict[str, str]:
        """Kind -> backend name for every backend that passed verification."""
        return {kind: backend.name for kind, backend in self._backends.items()}

    def _backend_for(self, decision: DeviceDecision,
                     workload: WorkloadKind) -> Optional[AcceleratorBackend]:
        backend = self._backends.get(decision.device)
        if backend is None:
            return None
        if workload not in backend.workloads:
            return None
        return backend

    def _detected_devices_summary(self) -> Dict[str, Any]:
        """What hardware was actually detected, for error messages and reports."""
        summary: Dict[str, Any] = {
            "cpu": {
                "name": getattr(self.inventory.cpu, "name", "CPU"),
                "detail": getattr(self.inventory.cpu, "detail", ""),
                "concurrency": self.max_concurrency,
            },
            "devices": {},
            "verified_backends": self.verified_backends(),
        }
        for kind, info in self.inventory.accelerators.items():
            summary["devices"][kind] = {
                "available": bool(info.available),
                "detail": info.detail,
                "memory_bytes": getattr(info, "memory_bytes", None),
            }
        return summary

    def _record_execution_locked(self, workload: WorkloadKind, *,
                                 selected_device: str, actual_device: Optional[str],
                                 backend: Optional[str], duration_s: float,
                                 outcome: str, fallback_reason: Optional[str],
                                 mode: ExecutionMode) -> None:
        """Record one placement (caller holds the lock)."""
        record = {
            "workload": workload.value,
            "execution_mode": mode.value,
            "selected_device": selected_device,
            "actual_device": actual_device,
            "processor": backend or (actual_device if actual_device else None),
            "duration_ms": round(duration_s * 1000.0, 3),
            "outcome": outcome,
            "fallback_reason": fallback_reason,
            "at": time.time(),
        }
        self._execution_records.append(record)
        entry = self.by_workload.setdefault(workload.value, {
            "executions": 0, "on_cpu": 0, "on_accelerator": 0,
            "capability_errors": 0, "failures": 0, "fallbacks": 0,
            "total_duration_s": 0.0, "last_actual_device": None,
        })
        entry["executions"] += 1
        entry["total_duration_s"] = round(entry["total_duration_s"] + duration_s, 6)
        if actual_device:
            entry["last_actual_device"] = actual_device
        if outcome == "capability_error":
            entry["capability_errors"] += 1
        elif outcome == "failed":
            entry["failures"] += 1
        if actual_device == CPU:
            entry["on_cpu"] += 1
        elif actual_device:
            entry["on_accelerator"] += 1
        if fallback_reason:
            entry["fallbacks"] += 1

    def execution_records(self, limit: int = 32) -> List[Dict[str, Any]]:
        """Most recent placements, newest first (bounded ring buffer)."""
        with self._lock:
            records = list(self._execution_records)
        return list(reversed(records))[:limit]

    def placements(self) -> Dict[str, List[Dict[str, Any]]]:
        """Where each workload would run right now, and why."""
        return {workload.value: self.select_device(workload).as_dict()
                for workload in WorkloadKind}

    def mode_report(self) -> Dict[str, Any]:
        """Explain what each requested mode can actually do on this machine."""
        accelerators = self.available_accelerators
        usable = [k for k, ok in accelerators.items() if ok]
        # Workloads that have an accelerator implementation but cannot use one
        # here: the honest "unsupported on this machine" list.
        unsupported = sorted(
            workload.value for workload in WorkloadKind
            if any(d != CPU for d in allowed_devices(workload))
            and self.select_device(workload).device == CPU
        )
        cpu_only_workloads = sorted(
            workload.value for workload in WorkloadKind
            if all(d == CPU for d in allowed_devices(workload))
        )
        effective = "cpu"
        if usable:
            effective = "gpu" if self.mode is ExecutionMode.GPU_ONLY else "cpu+gpu"
        return {
            "requested_mode": self.mode.value,
            "effective_mode": effective,
            "usable_accelerators": usable,
            "unavailable_accelerators": {k: info.detail
                                         for k, info in self.inventory.accelerators.items()
                                         if not info.available},
            "detected": self._detected_devices_summary(),
            "supported_on_accelerator": sorted(
                workload.value for workload in WorkloadKind
                if any(d != CPU for d in allowed_devices(workload))
            ),
            "cpu_only_workloads": cpu_only_workloads,
            "unsupported_on_this_machine": unsupported,
            "placements": self.placements(),
            "execution_records": self.execution_records(16),
            "by_workload": {k: dict(v) for k, v in self.by_workload.items()},
            "stats": self.stats.as_dict(),
            "workload_profiles": {k.value: profile_for(k).as_dict()
                                  for k in WorkloadKind},
            "strict_gpu_mode": self.mode is ExecutionMode.GPU_ONLY
                               and not self.allow_cpu_fallback,
            "cpu_fallback_allowed": bool(self.allow_cpu_fallback),
            "fallback_events": list(self._fallback_events),
            "honest_limits": (
                "accelerator execution is used only when a verified device and "
                "software path are present; in 'gpu' mode a workload that has an "
                "accelerator implementation raises DeviceUnavailableError rather "
                "than running on the CPU, unless COMPUTE_GPU_FALLBACK=1"
            ),
        }

    def _record_fallback(self, workload: WorkloadKind, requested: ExecutionMode,
                         reason: str) -> None:
        with self._lock:
            self.stats.fallbacks += 1
            if len(self._fallback_events) < 64:
                self._fallback_events.append({
                    "workload": workload.value,
                    "requested_mode": requested.value,
                    "used": CPU,
                    "reason": reason,
                    "at": time.time(),
                })

    # ------------------------------------------------------------------
    # Allocation & isolation
    # ------------------------------------------------------------------
    def allocation(self) -> Allocation:
        """Current resource allocation for compute work."""
        return Allocation(
            cpu_cores=self.max_concurrency,
            reserved_gateway_cores=self.reserved_gateway_cores,
            memory_bytes=self.memory_budget_bytes,
            queue_depth=self.queue_depth_limit,
            concurrency=self.max_concurrency,
            gpu_devices=sum(1 for i in self.inventory.available_accelerators
                            if i.kind == "gpu"),
            accelerator_kinds=tuple(i.kind for i in self.inventory.available_accelerators),
        )

    def apply_isolation(self, process: Any = None) -> Dict[str, Any]:
        """Confine compute workers away from the gateway's reserved cores.

        Uses CPU affinity when the platform supports it (Linux) and a reduced
        scheduling priority otherwise, so ingestion cannot degrade the
        gateway/web path.  Failures are reported, never fatal: isolation is a
        protection, not a correctness requirement.
        """
        report: Dict[str, Any] = {"affinity": None, "nice": None, "reason": ""}
        process = process or _current_process_handle()
        if process is None:
            report["reason"] = "process handle unavailable"
            return report

        if self.enable_affinity and self.reserved_gateway_cores and \
                (self.inventory.cpu.compute_units or 0) > self.reserved_gateway_cores + 1:
            try:
                affinity = list(process.cpu_affinity())
            except Exception as exc:
                report["reason"] = f"cpu affinity unavailable: {exc}"
            else:
                worker_cores = affinity[self.reserved_gateway_cores:]
                if worker_cores:
                    try:
                        process.cpu_affinity(worker_cores)
                        report["affinity"] = worker_cores
                    except Exception as exc:
                        report["reason"] = f"could not set affinity: {exc}"
                else:
                    report["reason"] = "not enough cores to reserve any for the gateway"
        try:
            current = process.nice()
            target = min(19, max(current, 10))
            if target != current:
                process.nice(target)
            report["nice"] = process.nice()
        except Exception:
            pass
        return report

    # ------------------------------------------------------------------
    # Submission with back-pressure
    # ------------------------------------------------------------------
    def _admit(self) -> None:
        """Reserve a slot, applying back-pressure when the system is saturated."""
        with self._lock:
            if self._queued >= self.queue_depth_limit:
                self.stats.rejected += 1
                raise BackpressureError(
                    f"compute queue is full ({self._queued}/{self.queue_depth_limit}); "
                    "work was refused rather than queued without bound"
                )
            self._queued += 1
            depth = self._queued
            if depth > self.stats.peak_queue_depth:
                self.stats.peak_queue_depth = depth
            self.stats.submitted += 1
            # Latency-driven back-pressure: when recent work is exceeding the
            # latency budget, admit fewer workers at once so the queue drains
            # instead of growing.
            if self._recent_latency_locked() > self.latency_budget_s and self._admission._value > 1:
                try:
                    self._admission.acquire(blocking=False)
                except Exception:
                    pass

        acquired = self._admission.acquire(timeout=self.admission_timeout_s)
        with self._lock:
            self._queued -= 1
            if not acquired:
                self.stats.rejected += 1
                raise BackpressureError(
                    f"compute concurrency limit ({self.max_concurrency}) was not "
                    f"available within {self.admission_timeout_s}s"
                )
            self._in_flight += 1

    def _release(self, queue_wait: float, execution: float, error: bool) -> None:
        with self._lock:
            self._in_flight -= 1
            self._queue_waits.append(queue_wait)
            self._latencies.append(execution)
            self._completion_times.append(time.time())
            self.stats.total_queue_wait_s += queue_wait
            self.stats.total_execution_s += execution
            if error:
                self.stats.failed += 1
            else:
                self.stats.completed += 1
        self._admission.release()

    def _recent_latency_locked(self) -> float:
        if not self._latencies:
            return 0.0
        recent = list(self._latencies)[-32:]
        return sum(recent) / len(recent)

    def submit(self, workload: WorkloadKind, fn: Callable[..., Any], *args,
               device: Optional[str] = None, enforce_admission: bool = False,
               **kwargs) -> Any:
        """Run ``fn`` under the gateway's device policy, returning its result.

        Inline calls (the default) are *metered, not throttled*: the pipeline's
        workers already bound their own concurrency, and adding a second
        admission gate in front of them would serialise work that is meant to
        run in parallel - the measured throughput collapse this layer must not
        reintroduce.  Pass ``enforce_admission=True`` (or use :meth:`admit`)
        for work that arrives from outside the pipeline's own scheduling and
        therefore needs a bounded queue.

        Raises:
            BackpressureError: with ``enforce_admission=True``, when the
                queue/concurrency limits stay saturated beyond
                ``admission_timeout_s``.
        """
        queue_wait = 0.0
        admitted = False
        if enforce_admission:
            t_queue = time.perf_counter()
            self._admit()
            admitted = True
            queue_wait = time.perf_counter() - t_queue
        else:
            with self._lock:
                self.stats.submitted += 1

        decision = self.select_device(workload)
        if device and device != decision.device:
            # A caller may pin a device; honour it only if it is usable.
            if device == CPU or self.available_accelerators.get(device, False):
                decision = DeviceDecision(workload=workload, device=device,
                                          backend=decision.backend,
                                          reason="device pinned by caller",
                                          accelerated=device != CPU)
            else:
                self._record_fallback(workload, self.mode,
                                      f"requested device {device} is unavailable")
        # Note: plain CPU execution in 'cpu+gpu'/'auto' is *not* a fallback.
        # Those modes mean "use whatever is available" and the CPU is the
        # authoritative implementation, so counting every such execution as a
        # fallback both inflated the metric and misrepresented normal operation.
        # A fallback is only recorded when something was requested and could not
        # be used: a pinned device that is unavailable, a verified backend that
        # failed mid-run, or an explicit opt-in fallback in 'gpu' mode.

        # STRICT MODE. ``gpu`` means GPU. A workload that has an accelerator
        # implementation must not quietly run on the CPU instead - the operator
        # asked for a device, and handing back CPU results without saying so is
        # exactly the silent substitution this layer forbids. The capability
        # error names what was detected so the request can be met or fixed.
        if requires_accelerator_in(self.mode, workload, decision.device):
            reason = decision.reason or "no usable accelerator for this workload"
            if self.allow_cpu_fallback:
                self._record_fallback(
                    workload, self.mode,
                    f"{reason}; COMPUTE_GPU_FALLBACK is set, so the CPU ran it",
                )
            else:
                with self._lock:
                    self.stats.capability_errors += 1
                    self.stats.submitted = max(0, self.stats.submitted - 1)
                    self._record_execution_locked(
                        workload, selected_device=decision.device,
                        actual_device=None, backend=None,
                        duration_s=0.0, outcome="capability_error",
                        fallback_reason=reason, mode=self.mode,
                    )
                raise DeviceUnavailableError(
                    f"execution mode '{self.mode.value}' requires an accelerator "
                    f"for workload '{workload.value}', but {reason}. "
                    f"Detected devices: "
                    f"{self._detected_devices_summary()}. "
                    f"Either provide the accelerator path or request a different "
                    f"mode (cpu / cpu+gpu / auto); set COMPUTE_GPU_FALLBACK=1 to "
                    f"permit an explicit CPU fallback.",
                    workload=workload.value,
                    requested_mode=self.mode.value,
                    detected=self._detected_devices_summary(),
                )

        backend = self._backend_for(decision, workload)
        downgrade_reason = None

        # A device can be present and still have nothing that can execute *this*
        # workload on it (a verified GPU with no GPU implementation registered
        # for the stage). Reaching the CPU callable in that state without saying
        # so is the silent substitution this layer forbids, and it used to be
        # exactly what happened: the placement record said
        # ``selected_device=gpu, actual_device=cpu, fallback_reason=None``.
        # The decision is therefore downgraded *here*, explicitly, with its
        # reason - and strict GPU mode refuses instead.
        if backend is None and decision.accelerated:
            reason = (f"no executable {decision.device} backend is registered for "
                      f"workload '{workload.value}'")
            if requires_accelerator_in(self.mode, workload, CPU):
                if self.allow_cpu_fallback:
                    self._record_fallback(
                        workload, self.mode,
                        f"{reason}; COMPUTE_GPU_FALLBACK is set, so the CPU ran it",
                    )
                else:
                    with self._lock:
                        self.stats.capability_errors += 1
                        self.stats.submitted = max(0, self.stats.submitted - 1)
                        self._record_execution_locked(
                            workload, selected_device=decision.device,
                            actual_device=None, backend=None,
                            duration_s=0.0, outcome="capability_error",
                            fallback_reason=reason, mode=self.mode,
                        )
                    raise DeviceUnavailableError(
                        f"execution mode '{self.mode.value}' requires an accelerator "
                        f"for workload '{workload.value}', but {reason}. "
                        f"Detected devices: {self._detected_devices_summary()}. "
                        f"Either provide the accelerator path or request a different "
                        f"mode (cpu / cpu+gpu / auto); set COMPUTE_GPU_FALLBACK=1 to "
                        f"permit an explicit CPU fallback.",
                        workload=workload.value,
                        requested_mode=self.mode.value,
                        detected=self._detected_devices_summary(),
                    )
            self._record_fallback(workload, self.mode, reason)
            decision = DeviceDecision(workload=workload, device=CPU, backend="cpu",
                                      reason=reason, accelerated=False)
            downgrade_reason = reason

        t_exec = time.perf_counter()
        error = False
        actual_device = CPU
        fallback_reason = downgrade_reason
        try:
            if backend is not None:
                # The backend receives the same arguments as the CPU callable
                # and must return the same result; if it cannot complete, the
                # CPU path runs instead and the fallback is recorded.
                try:
                    result = backend.run(fn, *args, **kwargs)
                    actual_device = decision.device
                    return result
                except Exception as backend_exc:
                    fallback_reason = (f"{backend.name} failed "
                                       f"({type(backend_exc).__name__}); ran on CPU")
                    self._record_fallback(workload, self.mode, fallback_reason)
                    return fn(*args, **kwargs)
            return fn(*args, **kwargs)
        except Exception:
            error = True
            raise
        finally:
            duration = time.perf_counter() - t_exec
            backend_name = backend.name if backend is not None and \
                actual_device == decision.device else None
            with self._lock:
                self._record_execution_locked(
                    workload, selected_device=decision.device,
                    actual_device=actual_device, backend=backend_name,
                    duration_s=duration,
                    outcome="failed" if error else "completed",
                    fallback_reason=fallback_reason, mode=self.mode,
                )
            if admitted:
                self._release(queue_wait, duration, error)
            else:
                with self._lock:
                    self._latencies.append(duration)
                    self._completion_times.append(time.time())
                    if error:
                        self.stats.failed += 1
                    else:
                        self.stats.completed += 1

    class _Admission:
        """Context manager for work that must be admitted through the queue."""

        def __init__(self, gateway: "ComputeGateway"):
            self.gateway = gateway
            self._queue_wait = 0.0
            self._started = 0.0

        def __enter__(self):
            t0 = time.perf_counter()
            self.gateway._admit()
            self._queue_wait = time.perf_counter() - t0
            self._started = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.gateway._release(self._queue_wait,
                                  time.perf_counter() - self._started,
                                  exc_type is not None)
            return False

    def admit(self, workload: Optional[WorkloadKind] = None) -> "ComputeGateway._Admission":
        """Bounded admission for work submitted from outside the pipeline.

        Example::

            with gateway.admit(WorkloadKind.OCR):
                run_ocr_page(page)

        Raises :class:`BackpressureError` when the queue cannot be entered
        within the admission timeout, which is the signal for a caller to shed
        or defer load instead of queueing without bound.
        """
        return ComputeGateway._Admission(self)

    def suggested_workers(self, requested: int) -> int:
        """Worker count that fits the current allocation and host pressure.

        Never returns more than ``requested`` and never less than 1.  On a host
        with memory pressure it returns a reduced count so an ingestion cannot
        push the machine into swap; otherwise it returns ``requested`` unchanged
        (the pipeline's own worker policy stays authoritative).
        """
        workers = max(1, int(requested or 1))
        try:
            import psutil

            memory = psutil.virtual_memory()
            if memory.percent >= 92:
                workers = max(1, workers // 2)
        except Exception:
            pass
        if self.reserved_gateway_cores:
            workers = max(1, min(workers, max(1, (self.inventory.cpu.compute_units
                                                 or workers) - self.reserved_gateway_cores)))
        return workers

    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------
    def _percentile(self, values: List[float], fraction: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
        return ordered[index]

    def _throughput_locked(self) -> float:
        now = time.time()
        recent = [t for t in self._completion_times if now - t <= 10.0]
        if len(recent) < 2:
            return float(len(recent))
        span = max(0.001, recent[-1] - recent[0])
        return len(recent) / span

    def snapshot(self) -> Dict[str, Any]:
        """Real-time resource and queue state (measured, not estimated)."""
        host: Dict[str, Any] = {}
        try:
            import psutil

            host["cpu_percent"] = psutil.cpu_percent(None)
            host["cpu_percent_per_core"] = psutil.cpu_percent(None, percpu=True)
            memory = psutil.virtual_memory()
            host["memory_used_bytes"] = memory.used
            host["memory_available_bytes"] = memory.available
            host["memory_percent"] = memory.percent
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    host["temperatures_c"] = {
                        name: [entry.current for entry in entries]
                        for name, entries in temps.items()
                    }
            except Exception:
                pass
        except Exception as exc:
            host["error"] = f"host metrics unavailable: {exc}"

        with self._lock:
            latencies = list(self._latencies)
            queue_waits = list(self._queue_waits)
            snapshot = {
                "mode": self.mode.value,
                "in_flight": self._in_flight,
                "queued": self._queued,
                "queue_depth_limit": self.queue_depth_limit,
                "concurrency_limit": self.max_concurrency,
                "throughput_per_s": round(self._throughput_locked(), 2),
                "latency_s": {
                    "p50": round(self._percentile(latencies, 0.5), 6),
                    "p95": round(self._percentile(latencies, 0.95), 6),
                    "max": round(max(latencies), 6) if latencies else 0.0,
                },
                "queue_wait_s": {
                    "p50": round(self._percentile(queue_waits, 0.5), 6),
                    "p95": round(self._percentile(queue_waits, 0.95), 6),
                },
                "samples": len(latencies),
                "backpressure": self._recent_latency_locked() > self.latency_budget_s,
                "reserved_gateway_cores": self.reserved_gateway_cores,
                "memory_budget_bytes": self.memory_budget_bytes,
                "accelerators": self.available_accelerators,
            }
        snapshot.update(host)
        return snapshot

    def status(self) -> Dict[str, Any]:
        """Everything an operator needs to see what the gateway is doing."""
        return {
            "mode": self.mode_report(),
            "allocation": self.allocation().as_dict(),
            "stats": self.stats.as_dict(),
            "monitor": self.snapshot(),
            "inventory": self.inventory.as_dict(),
            "workload_placement": {
                kind.value: self.select_device(kind).as_dict()
                for kind in WorkloadKind
            },
        }


def _current_process_handle():
    try:
        import psutil

        return psutil.Process()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Process-wide gateway
# ---------------------------------------------------------------------------
_gateway: Optional[ComputeGateway] = None
_gateway_lock = threading.Lock()


def _processing_settings():
    """The project's ProcessingSettings, or None when unavailable."""
    try:
        from settings import get_settings

        return getattr(get_settings(), "processing", None)
    except Exception:
        return None


def configured_limits() -> Dict[str, Any]:
    """Compute-layer settings, from project settings with env overrides.

    Precedence: environment variable > settings file/UI > derived default. The
    env names exist so the measurement harness and CI can pin values without
    touching the settings store; the settings fields are what an operator in the
    UI/config file uses. Zero/negative values mean "derive from this host".
    """
    settings = _processing_settings()
    limits: Dict[str, Any] = {
        "mode": "auto",
        "max_concurrency": 0,
        "reserved_gateway_cores": -1,
        "queue_depth": 0,
        "memory_budget_mb": 0,
        "latency_budget_s": 10.0,
        "isolation": True,
    }
    if settings is not None:
        limits.update({
            "mode": getattr(settings, "compute_mode", "auto"),
            "max_concurrency": getattr(settings, "compute_max_concurrency", 0),
            "reserved_gateway_cores": getattr(
                settings, "compute_reserved_gateway_cores", -1),
            "queue_depth": getattr(settings, "compute_queue_depth", 0),
            "memory_budget_mb": getattr(settings, "compute_memory_budget_mb", 0),
            "latency_budget_s": getattr(settings, "compute_latency_budget_s", 10.0),
            "isolation": getattr(settings, "compute_isolation", True),
        })

    if os.environ.get("COMPUTE_MODE"):
        limits["mode"] = os.environ["COMPUTE_MODE"]
    for env_name, key, cast in (
        ("COMPUTE_MAX_CONCURRENCY", "max_concurrency", int),
        ("COMPUTE_RESERVED_CORES", "reserved_gateway_cores", int),
        ("COMPUTE_QUEUE_DEPTH", "queue_depth", int),
        ("COMPUTE_MEMORY_BUDGET_MB", "memory_budget_mb", int),
        ("COMPUTE_LATENCY_BUDGET_S", "latency_budget_s", float),
    ):
        if os.environ.get(env_name):
            try:
                limits[key] = cast(os.environ[env_name])
            except ValueError:
                logger.warning("Ignoring invalid %s=%r", env_name, os.environ[env_name])
    if os.environ.get("COMPUTE_ISOLATION"):
        limits["isolation"] = os.environ["COMPUTE_ISOLATION"] not in ("0", "false", "False")
    return limits


def _configured_mode() -> ExecutionMode:
    """The effective mode, resolved by the operator-facing policy layer.

    Precedence (see :mod:`core.compute.policy`): command line
    (``--compute-mode``) > environment (``COMPUTE_MODE``) > project
    configuration (``processing.compute_mode``) > default (``auto``).

    Resolution lives in one place so the CLI, the web application and the
    gateway can never disagree about which mode was selected. The mode is a
    user-controlled policy: nothing here changes it because of load, missing
    hardware or measured performance.
    """
    from .policy import mode_selection

    return mode_selection().mode
    return ExecutionMode.AUTO


def get_compute_gateway() -> ComputeGateway:
    """The process-wide gateway (created once, on first use)."""
    global _gateway
    with _gateway_lock:
        if _gateway is None:
            limits = configured_limits()
            _gateway = ComputeGateway(
                mode=_configured_mode(),
                max_concurrency=limits["max_concurrency"] or None,
                reserved_gateway_cores=(limits["reserved_gateway_cores"]
                                        if limits["reserved_gateway_cores"] >= 0 else None),
                queue_depth=limits["queue_depth"] or None,
                memory_budget_bytes=((limits["memory_budget_mb"] * 1024 * 1024)
                                     if limits["memory_budget_mb"] else None),
                latency_budget_s=limits["latency_budget_s"],
                enable_affinity=bool(limits["isolation"]),
            )
            if limits["isolation"]:
                report = _gateway.apply_isolation()
                logger.info("Compute isolation: %s", report)
        return _gateway


def reset_compute_gateway() -> None:
    """Drop the process-wide gateway (tests, configuration changes)."""
    global _gateway
    with _gateway_lock:
        _gateway = None
