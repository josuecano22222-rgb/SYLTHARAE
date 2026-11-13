"""Compute-mode policy: the operator's switch, and where it comes from.

The compute mode is a **user-controlled processing policy**, not an internal
implementation detail. It is selected on the command line or in the project
configuration, never hard-coded, and it is never changed behind the operator's
back - not because a GPU is missing, not because the host is loaded, and not
because a faster path happens to be available.

Precedence (deterministic, highest first)
-----------------------------------------

1. **Command line** - ``--compute-mode cpu|gpu|auto|cpu+gpu``
2. **Environment** - ``COMPUTE_MODE=...`` (set on the command line; used by CI
   and the measurement harness)
3. **Project configuration** - ``processing.compute_mode`` in
   ``data/settings.json`` (the same value the Operations UI edits)
4. **Default** - ``auto``

A command-line selection therefore always overrides the project configuration,
and the configuration always overrides the default. :func:`mode_selection`
reports which layer supplied the value, so startup output and run results can
state it instead of leaving the operator to guess.

Modes
-----

``cpu``     CPU-ONLY  - every workload on the CPU; no accelerator is used and
                        the mode never switches to one.
``gpu``     GPU-ONLY  - every workload that *has* a GPU implementation must run
                        on it; if the capability is missing the run fails with
                        an actionable error rather than substituting the CPU.
                        Workloads with no GPU implementation (hashing, format
                        identification, storage) are not substitutions - the CPU
                        is the only implementation that exists for them.
``cpu+gpu`` CPU+GPU   - CPU and GPU worker sets run concurrently with
                        independent limits.
``auto``    AUTOMATIC - the scheduler routes each workload using measured
                        capabilities and live resource state, and records the
                        decision.

Honouring the request
---------------------

:func:`mode_support` answers "can this host honour the selected mode?" *before*
work starts, so an impossible request fails at the top of the run with a clear
message and an action, instead of failing file by file. :func:`describe`
produces the startup block, and :func:`result_block` produces the record that
travels with the run's results - both carry the mode, its source, the selected
device, the actual device(s) used and any fallback.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .routing import ExecutionMode, WorkloadKind, allowed_devices

logger = logging.getLogger(__name__)

#: The default when nothing is configured. Kept in one place: no module may
#: hard-code a mode of its own.
DEFAULT_MODE = ExecutionMode.AUTO

#: Operator-facing names, exactly as they appear in startup output.
MODE_LABELS: Dict[ExecutionMode, str] = {
    ExecutionMode.CPU_ONLY: "CPU-ONLY",
    ExecutionMode.GPU_ONLY: "GPU-ONLY",
    ExecutionMode.CPU_GPU: "CPU+GPU",
    ExecutionMode.AUTO: "AUTOMATIC CPU/GPU ROUTING",
}

_LOCK = threading.Lock()
_override: Optional["ModeSelection"] = None


class ComputeModeError(ValueError):
    """An unusable compute mode was requested (bad value, bad layer)."""


@dataclass(frozen=True)
class ModeSelection:
    """A resolved mode plus the configuration layer that supplied it."""

    mode: ExecutionMode
    source: str
    raw: str

    @property
    def label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode.value)

    @property
    def strict_accelerator(self) -> bool:
        """True when the mode must refuse instead of using the CPU fallback."""
        return (self.mode is ExecutionMode.GPU_ONLY
                and os.environ.get("COMPUTE_GPU_FALLBACK", "0") in ("0", "false", "False", ""))

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["mode"] = self.mode.value
        payload["label"] = self.label
        return payload


@dataclass
class ModeSupport:
    """Whether this host can honour a selection, and what to do if it cannot."""

    ok: bool
    selection: ModeSelection
    problem: Optional[str] = None
    action: Optional[str] = None
    degraded: bool = False
    accelerators: List[str] = field(default_factory=list)
    cpu_units: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "degraded": self.degraded,
            "problem": self.problem,
            "action": self.action,
            "accelerators": list(self.accelerators),
            "cpu_units": self.cpu_units,
            "notes": list(self.notes),
            "selection": self.selection.to_dict(),
        }


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def parse_mode(value: Any) -> ExecutionMode:
    """Parse an operator-supplied mode string, with an actionable error."""
    try:
        return ExecutionMode.parse(value)
    except ValueError as exc:
        raise ComputeModeError(
            f"{exc}. Valid compute modes are: cpu, gpu, cpu+gpu, auto "
            f"(also accepted: cpu-only, gpu-only, automatic). "
            f"Set it with --compute-mode <mode> or processing.compute_mode "
            f"in data/settings.json."
        ) from exc


def set_mode_override(value: Any, source: str = "command line",
                      reset_gateway: bool = True) -> ModeSelection:
    """Pin the mode for this process (the command-line layer).

    ``reset_gateway`` drops an already-created process-wide gateway so the new
    mode takes effect deterministically even if something touched compute
    before the command line was parsed.
    """
    global _override
    mode = parse_mode(value)
    selection = ModeSelection(mode=mode, source=source,
                              raw=str(value).strip().lower())
    with _LOCK:
        _override = selection
    if reset_gateway:
        try:
            from .gateway import reset_compute_gateway

            reset_compute_gateway()
        except Exception:  # pragma: no cover - import cycle safety
            logger.debug("Could not reset compute gateway after mode override",
                         exc_info=True)
    logger.info("Compute mode set to %s by %s", selection.label, source)
    return selection


def clear_mode_override() -> None:
    """Drop the command-line override (tests, embedding callers)."""
    global _override
    with _LOCK:
        _override = None


def mode_override() -> Optional[ModeSelection]:
    with _LOCK:
        return _override


def _configured_mode_value() -> Optional[str]:
    """The project-configuration value, if the settings store provides one."""
    try:
        from settings import get_settings

        processing = getattr(get_settings(), "processing", None)
        value = getattr(processing, "compute_mode", None)
        if value:
            return str(value)
    except Exception:
        logger.debug("Compute mode not readable from settings", exc_info=True)
    return None


def mode_selection() -> ModeSelection:
    """Resolve the effective mode and the layer that supplied it.

    Highest precedence wins; see the module docstring for the ordering.
    """
    override = mode_override()
    if override is not None:
        return override

    env_value = os.environ.get("COMPUTE_MODE")
    if env_value:
        try:
            return ModeSelection(mode=parse_mode(env_value),
                                 source=f"environment (COMPUTE_MODE={env_value.strip()})",
                                 raw=env_value.strip().lower())
        except ComputeModeError:
            logger.warning("Ignoring invalid COMPUTE_MODE=%r", env_value)

    configured = _configured_mode_value()
    if configured:
        try:
            return ModeSelection(
                mode=parse_mode(configured),
                source=f"project configuration (processing.compute_mode={configured})",
                raw=configured.strip().lower(),
            )
        except ComputeModeError:
            logger.warning("Ignoring invalid configured compute_mode=%r; using %s",
                           configured, DEFAULT_MODE.value)

    return ModeSelection(mode=DEFAULT_MODE, source="default", raw=DEFAULT_MODE.value)


# ---------------------------------------------------------------------------
# Can this host honour it?
# ---------------------------------------------------------------------------
def mode_support(selection: Optional[ModeSelection] = None,
                 inventory: Any = None) -> ModeSupport:
    """Report whether ``selection`` can be honoured on this machine.

    ``gpu`` without a usable accelerator is **not** supported: the run must fail
    with an actionable error instead of quietly executing on the CPU. ``cpu+gpu``
    without an accelerator is supported but *degraded*, and says so - the mode
    permits CPU work, so it is reported rather than refused.
    """
    selection = selection or mode_selection()

    if inventory is None:
        try:
            from .capabilities import HardwareInventory

            inventory = HardwareInventory()
        except Exception as exc:  # pragma: no cover - probe failure path
            logger.warning("Hardware probe failed: %s", exc)
            inventory = None

    available: List[str] = []
    detail = "hardware probe unavailable"
    cpu_units = 0
    accelerator_names: List[str] = []
    if inventory is not None:
        try:
            available = [a.kind for a in inventory.available_accelerators]
            accelerator_names = [f"{a.kind}:{a.name}" for a in inventory.available_accelerators]
            cpu_units = int(getattr(inventory.cpu, "compute_units", 0) or 0)
        except Exception:  # pragma: no cover
            available = []
        try:
            accelerators = getattr(inventory, "accelerators", {}) or {}
            gpu_info = accelerators.get("gpu")
            if gpu_info is not None and getattr(gpu_info, "detail", ""):
                detail = gpu_info.detail
            elif accelerators:
                detail = "; ".join(
                    f"{info.kind}: {info.detail}" for info in accelerators.values()
                    if getattr(info, "detail", "")
                ) or detail
        except Exception:  # pragma: no cover
            pass

    support = ModeSupport(ok=True, selection=selection,
                          accelerators=available, cpu_units=cpu_units)
    if accelerator_names:
        support.notes.append("Accelerators in use: " + ", ".join(accelerator_names))

    if selection.mode is ExecutionMode.GPU_ONLY:
        if not available:
            support.ok = False
            support.problem = (
                f"GPU-ONLY was requested ({selection.source}) but no usable "
                f"accelerator is present on this host: {detail}."
            )
            support.action = (
                "Install/enable a supported GPU runtime (for example the CUDA "
                "runtime with a working device), or select another policy: "
                "--compute-mode cpu (all work on CPU), --compute-mode auto "
                "(route automatically), or --compute-mode cpu+gpu (both worker "
                "sets). To explicitly permit CPU execution *under the gpu policy* "
                "for this run, set COMPUTE_GPU_FALLBACK=1 - the fallback is then "
                "recorded, not silent."
            )
        else:
            support.notes.append(
                f"GPU-ONLY is strict: a workload with a GPU implementation that "
                f"cannot run on {', '.join(available)} is refused, never "
                f"substituted."
            )
    elif selection.mode is ExecutionMode.CPU_GPU and not available:
        support.degraded = True
        support.problem = (
            f"CPU+GPU was requested ({selection.source}) but no usable "
            f"accelerator is present: {detail}. CPU workers run within the "
            f"cpu+gpu policy; the GPU worker set is empty."
        )
        support.action = (
            "Install/enable a GPU runtime to use the GPU worker set, or select "
            "--compute-mode cpu to make CPU-only execution explicit."
        )
    elif selection.mode is ExecutionMode.CPU_ONLY:
        support.notes.append(
            "CPU-ONLY never uses an accelerator, even when one is available."
        )
    elif selection.mode is ExecutionMode.AUTO:
        support.notes.append(
            "AUTOMATIC routes each workload from measured capability and live "
            "resource state; every decision is recorded per workload."
        )

    return support


# ---------------------------------------------------------------------------
# Display and recording
# ---------------------------------------------------------------------------
def _selected_device_phrase(selection: ModeSelection) -> str:
    if selection.mode is ExecutionMode.CPU_ONLY:
        return "CPU (accelerators disabled by this policy)"
    if selection.mode is ExecutionMode.GPU_ONLY:
        return "GPU (required)"
    if selection.mode is ExecutionMode.CPU_GPU:
        return "CPU and GPU (concurrent worker sets)"
    return "per workload (measured routing)" 


def describe(selection: Optional[ModeSelection] = None,
             support: Optional[ModeSupport] = None,
             actual: Optional[Dict[str, Any]] = None) -> List[str]:
    """Startup block: mode, source, selected device, actual device, fallback."""
    selection = selection or mode_selection()
    support = support or mode_support(selection)

    lines = [
        f"Compute Mode: {selection.label}",
        f"Compute Mode Source: {selection.source}",
        f"Selected Device: {_selected_device_phrase(selection)}",
    ]
    if actual:
        used = ", ".join(f"{device} x{count}" for device, count in sorted(actual.items()))
        lines.append(f"Actual Device: {used}")
    else:
        lines.append("Actual Device: per workload - recorded in the run results")

    if selection.mode is ExecutionMode.GPU_ONLY:
        lines.append("Fallback: none - GPU-ONLY refuses rather than substituting the CPU"
                     if not support.degraded else "Fallback: none - strict")
    elif selection.mode is ExecutionMode.CPU_ONLY:
        lines.append("Fallback: not applicable (no accelerator is ever used)")
    else:
        lines.append("Fallback: none expected; any fallback is recorded with its reason")

    for note in support.notes:
        lines.append(f"Compute Policy Note: {note}")
    if support.problem:
        lines.append(f"Compute Policy Problem: {support.problem}")
    if support.action:
        lines.append(f"Compute Policy Action: {support.action}")
    return lines


def emit(selection: Optional[ModeSelection] = None,
         support: Optional[ModeSupport] = None,
         actual: Optional[Dict[str, Any]] = None,
         printer=print) -> List[str]:
    """Print the startup block and return the lines (for logs and tests)."""
    lines = describe(selection, support, actual)
    for line in lines:
        try:
            printer(f"[COMPUTE] {line}")
        except Exception:  # pragma: no cover - never fail a run over a banner
            logger.debug("Could not print compute banner line %r", line)
    return lines


def record(selection: Optional[ModeSelection] = None,
           support: Optional[ModeSupport] = None) -> None:
    """Write the selection into the action log, if one is active.

    Best-effort by design: a forensic run must not fail because its audit log
    could not be written.
    """
    selection = selection or mode_selection()
    support = support or mode_support(selection)
    try:
        from Hdg_Err_Ex_Log.logging_utils import record_command_line_action

        record_command_line_action(
            "COMPUTE_MODE",
            f"Compute mode: {selection.label}",
            {
                "mode": selection.mode.value,
                "label": selection.label,
                "source": selection.source,
                "cfg": support.to_dict(),
            },
        )
    except Exception:
        logger.debug("Compute mode not recorded to action log", exc_info=True)


def workflow_summary() -> List[Dict[str, Any]]:
    """Which workloads may run on which devices under each mode.

    Published so operators (and the report) can see exactly what "GPU" covers -
    including the honest statement that some stages have no accelerator
    implementation and therefore always run on the CPU.
    """
    rows: List[Dict[str, Any]] = []
    for workload in WorkloadKind:
        devices = allowed_devices(workload)
        rows.append({
            "workload": workload.value,
            "devices": list(devices),
            "accelerator_capable": any(d != "cpu" for d in devices),
        })
    return rows


def result_block(selection: Optional[ModeSelection] = None,
                 support: Optional[ModeSupport] = None,
                 gateway: Any = None) -> Dict[str, Any]:
    """The compute record that travels with a run's results.

    Contains the requested policy, its source, and - when a gateway exists -
    which devices actually executed work, any fallbacks and any capability
    refusals.
    """
    selection = selection or mode_selection()
    support = support or mode_support(selection)
    block: Dict[str, Any] = {
        "requested_mode": selection.mode.value,
        "mode_label": selection.label,
        "source": selection.source,
        "strict": selection.strict_accelerator,
        "selected_device": _selected_device_phrase(selection),
        "support": support.to_dict(),
        "actual_devices": {},
        "workloads": {},
        "fallbacks": [],
        "capability_errors": 0,
    }

    if gateway is None:
        try:
            from .gateway import get_compute_gateway

            gateway = get_compute_gateway()
        except Exception:
            gateway = None

    if gateway is not None:
        try:
            # ``by_workload`` aggregates per workload as on_cpu / on_accelerator
            # (bounded - it never grows with the number of files), which is what
            # "which device actually executed this run" has to be answered from.
            actual: Dict[str, int] = {}
            for name, entry in getattr(gateway, "by_workload", {}).items():
                on_cpu = int(entry.get("on_cpu", 0) or 0)
                on_accel = int(entry.get("on_accelerator", 0) or 0)
                if on_cpu:
                    actual["cpu"] = actual.get("cpu", 0) + on_cpu
                if on_accel:
                    device = entry.get("last_actual_device") or "accelerator"
                    actual[device] = actual.get(device, 0) + on_accel
                block["workloads"][name] = {
                    "executions": entry.get("executions"),
                    "on_cpu": on_cpu,
                    "on_accelerator": on_accel,
                    "last_actual_device": entry.get("last_actual_device"),
                    "fallbacks": entry.get("fallbacks"),
                    "capability_errors": entry.get("capability_errors"),
                }
            block["actual_devices"] = actual
            report = gateway.mode_report() if hasattr(gateway, "mode_report") else {}
            block["effective_mode"] = report.get("effective_mode")
            block["fallbacks"] = report.get("fallback_events", [])[:16]
            block["capability_errors"] = int(getattr(gateway.stats, "capability_errors", 0))
        except Exception:
            logger.debug("Could not assemble compute result block", exc_info=True)

    return block
