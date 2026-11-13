"""Execution modes and workload routing for the compute gateway.

The routing table is the honest contract of this layer: it says which
execution devices a workload *may* use, and the gateway refuses to place a
workload on a device whose backend is not present and verified on this machine.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Tuple

from .capabilities import CPU, DPU, FPGA, GPU, NPU


class ExecutionMode(str, enum.Enum):
    """Execution modes selectable by configuration or per request."""

    CPU_ONLY = "cpu"
    GPU_ONLY = "gpu"
    CPU_GPU = "cpu+gpu"
    AUTO = "auto"

    @classmethod
    def parse(cls, value) -> "ExecutionMode":
        if isinstance(value, cls):
            return value
        text = (str(value or "auto").strip().lower()
                .replace("_", "").replace("-", "").replace("+", "").replace(" ", ""))
        aliases = {
            "cpu": cls.CPU_ONLY, "cpuonly": cls.CPU_ONLY, "cpumode": cls.CPU_ONLY,
            "gpu": cls.GPU_ONLY, "gpuonly": cls.GPU_ONLY, "gpumode": cls.GPU_ONLY,
            "cpugpu": cls.CPU_GPU, "both": cls.CPU_GPU, "hybrid": cls.CPU_GPU,
            "auto": cls.AUTO, "automatic": cls.AUTO,
        }
        if text not in aliases:
            raise ValueError(f"Unknown execution mode {value!r}; expected one of "
                             f"{[m.value for m in cls]}")
        return aliases[text]


class WorkloadKind(str, enum.Enum):
    """Compute-intensive stages the pipeline can route."""

    HASHING = "hashing"
    CONTENT_EXTRACTION = "content_extraction"
    OCR = "ocr"
    CLASSIFICATION = "classification"
    INDEXING = "indexing"
    COMPRESSION = "compression"
    METADATA = "metadata"


#: Devices each workload may run on.  A workload appears here only with the
#: devices whose *results are identical* to the CPU path: the pipeline's
#: extraction-fidelity and accuracy requirements forbid a device that would
#: change output.  Hashing, extraction, indexing and metadata are therefore
#: CPU-only - a digest or a stored byte stream must not depend on which device
#: produced it - while inference-shaped workloads (OCR, classification) may run
#: on any device that can execute the model exactly.
#:
#: Listing a device here does **not** make it usable: it still has to pass the
#: capability probe (a device with a working runtime) or a self-tested
#: accelerator backend.  On a machine with neither, every workload runs on the
#: CPU, which is what the placement report shows.
WORKLOAD_DEVICES: Dict[WorkloadKind, FrozenSet[str]] = {
    WorkloadKind.HASHING: frozenset({CPU}),
    WorkloadKind.CONTENT_EXTRACTION: frozenset({CPU}),
    WorkloadKind.OCR: frozenset({CPU, GPU, NPU, FPGA, DPU}),
    WorkloadKind.CLASSIFICATION: frozenset({CPU, GPU, NPU, FPGA, DPU}),
    WorkloadKind.INDEXING: frozenset({CPU}),
    WorkloadKind.COMPRESSION: frozenset({CPU}),
    WorkloadKind.METADATA: frozenset({CPU}),
}

#: Accelerator kinds the gateway knows how to probe (and will only use when a
#: verified backend exists on this machine).
KNOWN_ACCELERATORS = (GPU, NPU, FPGA, DPU)


@dataclass(frozen=True)
class WorkloadProfile:
    """What a workload needs, declared rather than discovered at runtime.

    The routing architecture requires every workload to expose its required
    capabilities, preferred device, supported devices and estimated resource
    requirements *before* it is scheduled. Estimates are deliberately coarse and
    labelled as estimates: they exist to keep a scheduler from admitting work
    that cannot fit (an OCR pass on a device with 512 MB free), not to promise a
    runtime.

    Attributes:
        workload: The kind this profile describes.
        requires_accelerator: True when the CPU is not an implementation of this
            workload at all (as opposed to being a slower one).
        preferred_device: Device the scheduler should use when several are
            available.
        estimated_cpu_units: Relative CPU cost of one unit of work (1.0 == a
            metadata read).
        estimated_memory_bytes: Peak memory one unit of work may need on the
            chosen device.
        estimated_duration_ms: Typical duration of one unit on the CPU.
        accelerator_memory_bytes: Faster-device memory one unit needs, when it
            can run on an accelerator.
        parallel_safe: Whether several units may run concurrently on the same
            device without changing results.
    """

    workload: "WorkloadKind"
    requires_accelerator: bool
    preferred_device: str
    estimated_cpu_units: float
    estimated_memory_bytes: int
    estimated_duration_ms: float
    accelerator_memory_bytes: int = 0
    parallel_safe: bool = True

    @property
    def supported_devices(self) -> FrozenSet[str]:
        return WORKLOAD_DEVICES.get(self.workload, frozenset({CPU}))

    def as_dict(self) -> Dict[str, object]:
        return {
            "workload": self.workload.value,
            "requires_accelerator": self.requires_accelerator,
            "preferred_device": self.preferred_device,
            "supported_devices": sorted(self.supported_devices),
            "estimated_cpu_units": self.estimated_cpu_units,
            "estimated_memory_bytes": self.estimated_memory_bytes,
            "estimated_duration_ms": self.estimated_duration_ms,
            "accelerator_memory_bytes": self.accelerator_memory_bytes,
            "parallel_safe": self.parallel_safe,
        }


#: Estimated requirements per workload. Values are relative costs measured on
#: the reference host (2 cores, ~3.9 GB) during the scalability runs: read them
#: as "this is 20x a metadata read and needs tens of MB", not as a benchmark.
WORKLOAD_PROFILES: Dict[WorkloadKind, WorkloadProfile] = {
    WorkloadKind.HASHING: WorkloadProfile(
        WorkloadKind.HASHING, False, CPU, 0.5, 1 << 20, 12.0),
    WorkloadKind.CONTENT_EXTRACTION: WorkloadProfile(
        WorkloadKind.CONTENT_EXTRACTION, False, CPU, 20.0, 64 << 20, 240.0),
    WorkloadKind.OCR: WorkloadProfile(
        WorkloadKind.OCR, False, GPU, 60.0, 256 << 20, 1800.0,
        accelerator_memory_bytes=512 << 20),
    WorkloadKind.CLASSIFICATION: WorkloadProfile(
        WorkloadKind.CLASSIFICATION, False, GPU, 8.0, 128 << 20, 120.0,
        accelerator_memory_bytes=256 << 20),
    WorkloadKind.INDEXING: WorkloadProfile(
        WorkloadKind.INDEXING, False, CPU, 2.0, 32 << 20, 30.0),
    WorkloadKind.COMPRESSION: WorkloadProfile(
        WorkloadKind.COMPRESSION, False, CPU, 3.0, 16 << 20, 40.0),
    WorkloadKind.METADATA: WorkloadProfile(
        WorkloadKind.METADATA, False, CPU, 1.0, 4 << 20, 20.0),
}


def profile_for(workload: WorkloadKind) -> WorkloadProfile:
    """The declared profile for ``workload`` (never None)."""
    return WORKLOAD_PROFILES.get(
        workload,
        WorkloadProfile(workload, False, CPU, 1.0, 4 << 20, 20.0),
    )


def accelerator_memory_fits(workload: WorkloadKind,
                            device_memory_bytes: Optional[int]) -> Tuple[bool, str]:
    """Whether an accelerator with ``device_memory_bytes`` can take the work.

    Returns ``(fits, reason)``. An unknown device capacity is treated as *not*
    verified, with the reason recorded: assuming capacity that was never
    detected is exactly how a GPU path fails halfway through a run.
    """
    profile = profile_for(workload)
    if profile.accelerator_memory_bytes <= 0:
        return True, "workload declares no accelerator memory requirement"
    if device_memory_bytes is None:
        return False, ("accelerator memory could not be determined, so the "
                       "requirement cannot be verified")
    if device_memory_bytes < profile.accelerator_memory_bytes:
        return False, (f"device has {device_memory_bytes} bytes, workload needs "
                       f"{profile.accelerator_memory_bytes}")
    return True, (f"device has {device_memory_bytes} bytes, workload needs "
                  f"{profile.accelerator_memory_bytes}")


class DeviceUnavailableError(RuntimeError):
    """A workload that has an accelerator implementation cannot run on it.

    Raised instead of silently substituting the CPU when the requested mode
    requires the accelerator (``gpu``/``gpu-only``).  Silently changing the
    requested execution mode is the failure this exception exists to prevent:
    an operator who asked for GPU execution must be told the capability is
    missing, not handed CPU results that look like the ones they asked for.

    Carries the machine-readable facts (workload, requested mode, what was
    detected) so a caller can report them without parsing the message.
    """

    def __init__(self, message: str, *, workload: Optional[str] = None,
                 requested_mode: Optional[str] = None,
                 detected: Optional[Dict[str, object]] = None):
        super().__init__(message)
        self.workload = workload
        self.requested_mode = requested_mode
        self.detected = detected or {}

    def as_dict(self) -> Dict[str, object]:
        return {
            "error": "device_unavailable",
            "message": str(self),
            "workload": self.workload,
            "requested_mode": self.requested_mode,
            "detected": self.detected,
        }


@dataclass
class DeviceDecision:
    """Where a workload will run, and why."""

    workload: WorkloadKind
    device: str
    backend: str
    reason: str
    accelerated: bool = False
    memory_fits: Optional[bool] = None
    memory_note: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "workload": self.workload.value,
            "device": self.device,
            "backend": self.backend,
            "accelerated": self.accelerated,
            "memory_fits": self.memory_fits,
            "memory_note": self.memory_note,
            "reason": self.reason,
        }


def mode_allows(mode: ExecutionMode, device: str) -> bool:
    """True when ``mode`` may execute on ``device`` (CPU is the fallback)."""
    if device == CPU:
        return True
    return device in accelerator_priority(mode)


def allowed_devices(workload: WorkloadKind) -> FrozenSet[str]:
    return WORKLOAD_DEVICES.get(workload, frozenset({CPU}))


def requires_accelerator_in(mode: ExecutionMode, workload: WorkloadKind,
                            device: str) -> bool:
    """True when ``mode`` demands an accelerator that ``device`` does not satisfy.

    Only ``gpu`` is strict.  ``cpu+gpu`` and ``auto`` legitimately execute on
    the CPU - that is the mode's documented meaning, and the CPU is the
    authoritative implementation - so they never raise here.  A workload with no
    accelerator implementation at all (hashing, extraction, indexing, metadata:
    see :data:`WORKLOAD_DEVICES`) is also never strict: demanding GPU execution
    of a workload that has no GPU path would be an error in the request, not a
    missing capability, and the CPU path is the only implementation that exists.
    """
    if mode is not ExecutionMode.GPU_ONLY:
        return False
    if device != CPU:
        return False
    return any(d != CPU for d in allowed_devices(workload))


def accelerator_priority(mode: ExecutionMode) -> tuple:
    """Accelerator kinds to try, in order, for ``mode``.

    ``gpu`` and ``cpu+gpu`` mean the GPU specifically, so no other accelerator
    may be substituted.  ``auto`` considers every accelerator kind.
    """
    if mode in (ExecutionMode.GPU_ONLY, ExecutionMode.CPU_GPU):
        return (GPU,)
    if mode is ExecutionMode.AUTO:
        return (GPU, NPU, FPGA, DPU)
    return ()


def choose_device(
    workload: WorkloadKind,
    mode: ExecutionMode,
    available: Optional[Dict[str, bool]] = None,
) -> DeviceDecision:
    """Pick a device for ``workload`` under ``mode``.

    ``available`` maps accelerator kind -> usability (as measured by the
    capability probes).  The function never selects a device that is not both
    allowed for the workload and present; when the requested mode cannot be
    honoured it falls back to the CPU and says so in ``reason`` rather than
    pretending.  Reaching this function's CPU branch is therefore always an
    explicit, explainable outcome.
    """
    available = available or {}
    permitted = allowed_devices(workload)

    for device in accelerator_priority(mode):
        if device not in permitted or not available.get(device, False):
            continue
        return DeviceDecision(workload=workload, device=device,
                              backend=f"{device} backend",
                              reason=f"{device} execution", accelerated=True)

    # CPU is always permitted: it is the defined fallback for every workload.
    if mode is ExecutionMode.GPU_ONLY:
        reason = ("CPU fallback: GPU-only mode was requested but no verified GPU "
                  "execution path is available for this workload")
    elif mode is ExecutionMode.CPU_GPU:
        reason = ("CPU execution: CPU+GPU mode was requested, but no verified "
                  "accelerator path is available for this workload")
    else:
        reason = "CPU execution"
    return DeviceDecision(workload=workload, device=CPU, backend="stdlib",
                          reason=reason, accelerated=False)

# Nothing selectable: the workload's device set is empty (defensive).
    return DeviceDecision(workload=workload, device=CPU, backend="stdlib",
                          reason="no permitted device; defaulting to CPU",
                          accelerated=False)
