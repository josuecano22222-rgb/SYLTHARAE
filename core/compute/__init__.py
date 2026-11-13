"""Gateway compute-control layer.

Public surface:

* :func:`get_compute_gateway` - the process-wide gateway.
* :class:`ExecutionMode` - ``cpu`` / ``gpu`` / ``cpu+gpu`` / ``auto``.
* :class:`WorkloadKind` - routable pipeline stages.
* :class:`ComputeGateway` - device selection, allocation, isolation,
  back-pressure and monitoring.
* :func:`detect_hardware` - measured capability inventory.

The pipeline uses :func:`submit_compute` for compute-intensive stages; the CPU
path is always available and produces identical results, and accelerator paths
are only used when this machine actually has a verified device and runtime.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .capabilities import (
    ACCELERATOR_KINDS,
    CPU,
    HardwareInventory,
    AcceleratorInfo,
    detect_cpu,
    detect_dpu,
    detect_fpga,
    detect_gpu,
    detect_npu,
)
from .gateway import (
    Allocation,
    BackpressureError,
    ComputeGateway,
    ComputeStats,
    get_compute_gateway,
    reset_compute_gateway,
)
from .policy import (
    DEFAULT_MODE,
    MODE_LABELS,
    ComputeModeError,
    ModeSelection,
    ModeSupport,
    clear_mode_override,
    describe,
    emit,
    mode_selection,
    mode_support,
    parse_mode,
    record,
    result_block,
    set_mode_override,
    workflow_summary,
)
from .routing import (
    DeviceDecision,
    ExecutionMode,
    WorkloadKind,
    allowed_devices,
    choose_device,
)

__all__ = [
    "ACCELERATOR_KINDS",
    "CPU",
    "AcceleratorInfo",
    "Allocation",
    "BackpressureError",
    "ComputeGateway",
    "ComputeStats",
    "DeviceDecision",
    "ExecutionMode",
    "HardwareInventory",
    "WorkloadKind",
    "allowed_devices",
    "choose_device",
    "DEFAULT_MODE",
    "MODE_LABELS",
    "ComputeModeError",
    "ModeSelection",
    "ModeSupport",
    "clear_mode_override",
    "describe",
    "emit",
    "mode_selection",
    "mode_support",
    "parse_mode",
    "record",
    "result_block",
    "set_mode_override",
    "workflow_summary",
    "detect_cpu",
    "detect_dpu",
    "detect_fpga",
    "detect_gpu",
    "detect_hardware",
    "detect_npu",
    "get_compute_gateway",
    "reset_compute_gateway",
    "submit_compute",
]


def detect_hardware(probe: bool = True) -> HardwareInventory:
    """Measured hardware inventory for this machine."""
    return HardwareInventory(probe=probe)


def submit_compute(workload: "WorkloadKind | str", fn: Callable[..., Any],
                   *args: Any, gateway: Optional[ComputeGateway] = None,
                   enforce_admission: bool = False, **kwargs: Any) -> Any:
    """Run ``fn`` through the gateway for ``workload``.

    Falls back to a direct call when the gateway is disabled
    (``COMPUTE_GATEWAY=0``), so callers can use this in hot paths without
    changing behaviour when the layer is switched off.

    ``enforce_admission`` opts into queue-based back-pressure (see
    :meth:`ComputeGateway.submit`); inline pipeline work leaves it off because
    the pipeline already bounds its own concurrency.
    """
    import os

    if os.environ.get("COMPUTE_GATEWAY", "1") in ("0", "false", "False"):
        return fn(*args, **kwargs)
    if isinstance(workload, str):
        workload = WorkloadKind(workload)
    gw = gateway or get_compute_gateway()
    return gw.submit(workload, fn, *args, enforce_admission=enforce_admission, **kwargs)
