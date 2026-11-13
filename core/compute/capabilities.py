"""Hardware capability detection (measured, never assumed).

Every probe answers one question with evidence from this machine: *is this
accelerator actually usable right now?*  A probe only reports ``available=True``
when a device **and** a usable software path are both present, and it checks the
software path by importing/validating the runtime rather than trusting its
presence on disk.

Nothing here claims an accelerator exists that cannot be used: when a probe
finds no device, the returned record carries ``available=False`` and a human
readable ``detail`` naming what was looked for.  That is what lets the gateway
fall back honestly instead of advertising capability it does not have.
"""
from __future__ import annotations

import glob
import importlib
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

CPU = "cpu"
GPU = "gpu"
NPU = "npu"
FPGA = "fpga"
DPU = "dpu"

ACCELERATOR_KINDS = (GPU, NPU, FPGA, DPU)


@dataclass
class AcceleratorInfo:
    """One processor or accelerator as this machine presents it."""

    kind: str
    name: str
    available: bool
    backend: str = ""
    detail: str = ""
    memory_bytes: Optional[int] = None
    compute_units: Optional[int] = None
    extra: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "available": self.available,
            "backend": self.backend,
            "detail": self.detail,
            "memory_bytes": self.memory_bytes,
            "compute_units": self.compute_units,
            "extra": dict(self.extra),
        }


def _run(cmd: List[str], timeout: float = 3.0) -> Optional[str]:
    """Run a probe command; ``None`` when it is missing, slow or failing."""
    if not shutil.which(cmd[0]):
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def detect_cpu() -> AcceleratorInfo:
    """CPU: cores plus the instruction sets that actually accelerate hashing."""
    try:
        import psutil

        physical = psutil.cpu_count(logical=False) or 0
        logical = psutil.cpu_count(logical=True) or 0
        frequency = None
        try:
            freq = psutil.cpu_freq()
            frequency = round(freq.max or freq.current or 0) or None
        except Exception:
            frequency = None
        model = ""
        try:
            with open("/proc/cpuinfo") as fh:
                for line in fh:
                    if line.lower().startswith("model name"):
                        model = line.split(":", 1)[1].strip()
                        break
        except Exception:
            model = ""
        flags = set()
        try:
            with open("/proc/cpuinfo") as fh:
                for line in fh:
                    if line.lower().startswith("flags"):
                        flags = set(line.split(":", 1)[1].split())
                        break
        except Exception:
            flags = set()
        acceleration_flags = sorted(f for f in ("aes", "sha_ni", "avx2", "avx512f", "sse4_2")
                                    if f in flags)
        extra = {
            "physical_cores": str(physical),
            "logical_cores": str(logical),
            "acceleration_flags": ",".join(acceleration_flags),
        }
        if frequency:
            extra["max_mhz"] = str(frequency)
        detail = f"{physical} physical / {logical} logical cores"
        if acceleration_flags:
            detail += f"; accelerated by {', '.join(acceleration_flags)}"
        return AcceleratorInfo(
            kind=CPU, name=model or "CPU", available=True, backend="stdlib",
            detail=detail, compute_units=logical, extra=extra,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return AcceleratorInfo(kind=CPU, name="CPU", available=True,
                               backend="stdlib",
                               detail=f"core count unavailable ({exc})")


def detect_gpu() -> AcceleratorInfo:
    """GPU: a device node *and* an importable compute runtime are required."""
    nodes = sorted(glob.glob("/dev/nvidia[0-9]*")) + sorted(glob.glob("/dev/dri/renderD*"))
    nvidia_smi = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,compute_cap",
        "--format=csv,noheader",
    ])
    # A CUDA device without a driver query, or without a compute library, is
    # not a usable execution path: report it as unavailable rather than
    # advertising hardware the pipeline cannot actually use.
    runtime = None
    for module_name in ("cupy", "torch"):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        try:
            if module_name == "cupy":
                if module.cuda.runtime.getDeviceCount() > 0:
                    runtime = f"{module_name} {getattr(module, '__version__', '')}".strip()
                    break
            else:
                if module.cuda.is_available():
                    runtime = f"{module_name} {getattr(module, '__version__', '')}".strip()
                    break
        except Exception:
            continue

    if runtime:
        name = "GPU"
        memory = None
        if nvidia_smi:
            first = nvidia_smi.splitlines()[0]
            parts = [p.strip() for p in first.split(",")]
            name = parts[0] or name
            if len(parts) > 1 and parts[1].endswith("MiB"):
                try:
                    memory = int(parts[1].split()[0]) * 1024 ** 2
                except ValueError:
                    memory = None
        return AcceleratorInfo(kind=GPU, name=name, available=True, backend=runtime,
                               detail=f"usable via {runtime}", memory_bytes=memory)

    looked_for = "nvidia-smi, CUDA runtime (cupy/torch), /dev/nvidia*, /dev/dri/renderD*"
    if nvidia_smi:
        detail = ("a GPU answers nvidia-smi but no usable CUDA runtime is importable "
                  f"(looked for: {looked_for})")
    elif nodes:
        detail = f"device node(s) present ({', '.join(nodes[:2])}) but no usable runtime"
    else:
        detail = f"no GPU device or runtime found (looked for: {looked_for})"
    return AcceleratorInfo(kind=GPU, name="GPU", available=False, backend="",
                           detail=detail)


def detect_npu() -> AcceleratorInfo:
    """NPU/VPU: kernel accelerator node or a vendor runtime must be present."""
    nodes = sorted(glob.glob("/dev/accel*")) + sorted(glob.glob("/dev/dri/accel*"))
    vendor_paths = [
        "/sys/class/accel",                     # DRM compute accelerators
        "/sys/devices/pci0000:00/*/intel_vpu",
        "/sys/module/intel_vpu",
        "/sys/kernel/debug/accel",
    ]
    found = [p for p in vendor_paths if glob.glob(p)]
    runtime = None
    for module_name in ("openvino", "pyopenvino"):
        try:
            importlib.import_module(module_name)
            runtime = module_name
            break
        except Exception:
            continue
    if nodes and runtime:
        return AcceleratorInfo(kind=NPU, name="NPU", available=True,
                               backend=runtime,
                               detail=f"accelerator node(s) {', '.join(nodes)} via {runtime}")
    if nodes or found:
        return AcceleratorInfo(
            kind=NPU, name="NPU", available=False, backend="",
            detail=("accelerator device present but no inference runtime importable; "
                    "no verified NPU execution path"),
        )
    return AcceleratorInfo(kind=NPU, name="NPU", available=False, backend="",
                           detail="no NPU/VPU device or runtime found")


def detect_fpga() -> AcceleratorInfo:
    """FPGA: needs a programmed device and a bitstream/runtime interface."""
    paths = glob.glob("/sys/class/fpga*") + glob.glob("/sys/class/fpga_manager*")
    if paths:
        return AcceleratorInfo(
            kind=FPGA, name="FPGA", available=False, backend="",
            detail=("FPGA manager present but no verified bitstream/runtime path is "
                    "implemented by this project"),
        )
    return AcceleratorInfo(kind=FPGA, name="FPGA", available=False, backend="",
                           detail="no FPGA device or runtime found")


def detect_dpu() -> AcceleratorInfo:
    """DPU/SmartNIC: needs a programmable NIC with an attachable runtime."""
    candidates = glob.glob("/sys/class/net/*/device/vendor")
    smart_nic = []
    for vendor_file in candidates:
        try:
            with open(vendor_file) as fh:
                vendor = fh.read().strip().lower()
        except Exception:
            continue
        if vendor in ("0x15b3",):  # Mellanox/NVIDIA -- BlueField family
            smart_nic.append(os.path.basename(os.path.dirname(os.path.dirname(vendor_file))))
    if smart_nic:
        return AcceleratorInfo(
            kind=DPU, name="DPU/SmartNIC", available=False, backend="",
            detail=(f"programmable NIC(s) {', '.join(smart_nic)} present but no verified "
                    "offload path is implemented by this project"),
        )
    rshim = shutil.which("rshim")
    if rshim:
        return AcceleratorInfo(kind=DPU, name="DPU/SmartNIC", available=False, backend="",
                               detail="rshim tool present but no verified offload path")
    return AcceleratorInfo(kind=DPU, name="DPU/SmartNIC", available=False, backend="",
                           detail="no DPU/SmartNIC device or offload runtime found")


class HardwareInventory:
    """Cached, measured view of what this machine can execute on."""

    def __init__(self, probe: bool = True):
        self.cpu = AcceleratorInfo(kind=CPU, name="CPU", available=True, backend="stdlib",
                                   detail="not probed")
        self.accelerators: Dict[str, AcceleratorInfo] = {}
        if probe:
            self.refresh()

    def refresh(self) -> "HardwareInventory":
        self.cpu = detect_cpu()
        self.accelerators = {
            GPU: detect_gpu(),
            NPU: detect_npu(),
            FPGA: detect_fpga(),
            DPU: detect_dpu(),
        }
        logger.info("Compute inventory: %s (%s)", self.cpu.name, self.cpu.detail)
        for kind, info in self.accelerators.items():
            logger.info("Compute inventory: %s %s - %s", kind.upper(),
                        "available" if info.available else "unavailable", info.detail)
        return self

    @property
    def available_accelerators(self) -> List[AcceleratorInfo]:
        return [info for info in self.accelerators.values() if info.available]

    def as_dict(self) -> Dict[str, object]:
        return {
            "cpu": self.cpu.as_dict(),
            "accelerators": {k: v.as_dict() for k, v in self.accelerators.items()},
            "available_accelerator_kinds": [i.kind for i in self.available_accelerators],
        }
