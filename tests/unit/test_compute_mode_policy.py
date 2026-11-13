"""Compute mode is a user-controlled policy, not an implementation detail.

These tests pin the operator-facing contract:

* the mode is selectable from the command line and from the project
  configuration, and the documented precedence (command line > environment >
  project configuration > default) is deterministic;
* a command-line selection overrides the project configuration;
* CPU-ONLY never selects an accelerator, even when one is present;
* GPU-ONLY refuses instead of substituting the CPU, and the refusal is
  actionable;
* the selected mode is reported with its source, and recorded;
* the mode is never changed because of load, memory pressure or the adaptive
  worker windows - those change *workers*, never the device policy.
"""
from __future__ import annotations

import pytest

from core.compute import policy
from core.compute.capabilities import (
    DPU,
    FPGA,
    GPU,
    NPU,
    AcceleratorInfo,
    HardwareInventory,
)
from core.compute.gateway import ComputeGateway
from core.compute.routing import DeviceUnavailableError, ExecutionMode, WorkloadKind


def _inventory(available) -> HardwareInventory:
    inv = HardwareInventory(probe=False)
    inv.accelerators = {
        kind: AcceleratorInfo(
            kind=kind, name=kind.upper(), available=bool(available.get(kind)),
            backend="stub" if available.get(kind) else "",
            detail=f"stub {kind} {'present' if available.get(kind) else 'absent'}",
            # A device whose capacity is unknown is deliberately not used (see
            # the memory-fit contract), so the stub reports a real size.
            memory_bytes=8 * 1024 ** 3 if available.get(kind) else None,
            compute_units=64 if available.get(kind) else None,
        )
        for kind in (GPU, NPU, FPGA, DPU)
    }
    return inv


@pytest.fixture(autouse=True)
def _clean_policy():
    """Each test starts with no override and no leaked environment."""
    policy.clear_mode_override()
    saved = {name: __import__("os").environ.pop(name, None)
             for name in ("COMPUTE_MODE", "COMPUTE_GPU_FALLBACK")}
    yield
    policy.clear_mode_override()
    for name, value in saved.items():
        if value is not None:
            __import__("os").environ[name] = value


class _Settings:
    """Minimal stand-in for the settings store's processing section."""

    def __init__(self, mode):
        self.processing = type("P", (), {"compute_mode": mode})()


# ---------------------------------------------------------------------------
# Selection and precedence
# ---------------------------------------------------------------------------
class TestPrecedence:
    def test_default_is_auto_when_nothing_is_configured(self, monkeypatch):
        monkeypatch.setattr(policy, "_configured_mode_value", lambda: None)
        selection = policy.mode_selection()
        assert selection.mode is ExecutionMode.AUTO
        assert selection.source == "default"

    def test_project_configuration_supplies_the_mode(self, monkeypatch):
        monkeypatch.setattr(policy, "_configured_mode_value", lambda: "cpu")
        selection = policy.mode_selection()
        assert selection.mode is ExecutionMode.CPU_ONLY
        assert "project configuration" in selection.source
        assert "compute_mode=cpu" in selection.source

    def test_command_line_overrides_the_project_configuration(self, monkeypatch):
        monkeypatch.setattr(policy, "_configured_mode_value", lambda: "cpu")
        policy.set_mode_override("gpu", source="command line (--compute-mode gpu)")
        selection = policy.mode_selection()
        assert selection.mode is ExecutionMode.GPU_ONLY, (
            "a command-line selection must override the project configuration"
        )
        assert selection.source == "command line (--compute-mode gpu)"

    def test_environment_sits_between_command_line_and_configuration(self, monkeypatch):
        monkeypatch.setenv("COMPUTE_MODE", "cpu+gpu")
        monkeypatch.setattr(policy, "_configured_mode_value", lambda: "cpu")
        selection = policy.mode_selection()
        assert selection.mode is ExecutionMode.CPU_GPU
        assert "COMPUTE_MODE" in selection.source

        policy.set_mode_override("cpu", source="command line (--compute-mode cpu)")
        assert policy.mode_selection().mode is ExecutionMode.CPU_ONLY

    def test_invalid_command_line_value_is_actionable(self):
        with pytest.raises(policy.ComputeModeError) as excinfo:
            policy.set_mode_override("quantum")
        message = str(excinfo.value)
        assert "cpu" in message and "gpu" in message and "auto" in message
        assert "--compute-mode" in message, "the error must say how to fix it"

    def test_documented_spellings_resolve_to_the_same_modes(self):
        for spelling, expected in (
            ("cpu", ExecutionMode.CPU_ONLY),
            ("CPU-ONLY", ExecutionMode.CPU_ONLY),
            ("gpu", ExecutionMode.GPU_ONLY),
            ("GPU_ONLY", ExecutionMode.GPU_ONLY),
            ("auto", ExecutionMode.AUTO),
            ("automatic", ExecutionMode.AUTO),
            ("cpu+gpu", ExecutionMode.CPU_GPU),
        ):
            assert policy.parse_mode(spelling) is expected, spelling

    def test_labels_are_the_operator_facing_names(self):
        assert policy.MODE_LABELS[ExecutionMode.CPU_ONLY] == "CPU-ONLY"
        assert policy.MODE_LABELS[ExecutionMode.GPU_ONLY] == "GPU-ONLY"
        assert policy.MODE_LABELS[ExecutionMode.AUTO] == "AUTOMATIC CPU/GPU ROUTING"


# ---------------------------------------------------------------------------
# Can this host honour the selection?
# ---------------------------------------------------------------------------
class TestSupport:
    def test_cpu_only_is_always_supported(self):
        selection = policy.ModeSelection(ExecutionMode.CPU_ONLY, "test", "cpu")
        support = policy.mode_support(selection, _inventory({GPU: True}))
        assert support.ok and not support.degraded

    def test_gpu_only_is_refused_without_a_gpu(self):
        selection = policy.ModeSelection(ExecutionMode.GPU_ONLY, "test", "gpu")
        support = policy.mode_support(selection, _inventory({}))
        assert support.ok is False
        assert "no usable accelerator" in support.problem
        assert "--compute-mode cpu" in support.action
        assert "COMPUTE_GPU_FALLBACK=1" in support.action, (
            "the explicit escape hatch must be documented in the error"
        )

    def test_gpu_only_is_supported_when_a_gpu_exists(self):
        selection = policy.ModeSelection(ExecutionMode.GPU_ONLY, "test", "gpu")
        support = policy.mode_support(selection, _inventory({GPU: True}))
        assert support.ok is True
        assert GPU in support.accelerators

    def test_cpu_gpu_without_an_accelerator_is_supported_but_reported(self):
        selection = policy.ModeSelection(ExecutionMode.CPU_GPU, "test", "cpu+gpu")
        support = policy.mode_support(selection, _inventory({}))
        assert support.ok is True, "cpu+gpu legitimately runs CPU work"
        assert support.degraded is True, "an empty GPU worker set must be stated"
        assert "no usable accelerator" in support.problem


# ---------------------------------------------------------------------------
# Device policy
# ---------------------------------------------------------------------------
class TestDevicePolicy:
    def test_cpu_only_never_uses_an_accelerator_even_when_one_exists(self):
        gateway = ComputeGateway(inventory=_inventory({GPU: True, NPU: True}),
                                 mode=ExecutionMode.CPU_ONLY)
        assert gateway.submit(WorkloadKind.OCR, lambda: "text") == "text"
        entry = gateway.by_workload[WorkloadKind.OCR.value]
        assert entry["on_cpu"] == 1 and entry["on_accelerator"] == 0
        assert entry["last_actual_device"] == "cpu"
        assert gateway._execution_records[-1]["actual_device"] == "cpu"

    def test_gpu_only_refuses_rather_than_substituting_the_cpu(self):
        gateway = ComputeGateway(inventory=_inventory({}), mode=ExecutionMode.GPU_ONLY)
        with pytest.raises(DeviceUnavailableError):
            gateway.submit(WorkloadKind.OCR, lambda: "text")
        assert gateway.stats.capability_errors == 1
        assert gateway.stats.completed == 0

    def test_auto_routes_to_a_verified_accelerator_and_records_it(self):
        from core.compute.gateway import AcceleratorBackend

        gateway = ComputeGateway(inventory=_inventory({GPU: True}), mode=ExecutionMode.AUTO)
        ran_on_backend = []

        def run(fn, *args, **kwargs):
            ran_on_backend.append(True)
            return fn(*args, **kwargs)

        gateway.register_accelerator_backend(AcceleratorBackend(
            kind=GPU, name="stub-gpu", self_test=lambda: True, run=run,
            workloads=frozenset({WorkloadKind.OCR})))
        assert gateway.submit(WorkloadKind.OCR, lambda: "text") == "text"
        assert ran_on_backend, "auto must use a verified accelerator that is present"
        record = gateway._execution_records[-1]
        for key in ("execution_mode", "selected_device", "actual_device",
                    "processor", "duration_ms", "outcome"):
            assert key in record, f"the routing decision must record {key}"
        assert record["execution_mode"] == "auto"
        assert record["actual_device"] == GPU
        assert gateway.by_workload[WorkloadKind.OCR.value]["on_accelerator"] == 1

    def test_an_accelerator_without_an_executable_backend_is_never_silent(self):
        """A present device with no backend for this stage must be explicit.

        This is the state that used to record ``selected_device=gpu,
        actual_device=cpu, fallback_reason=None`` - a CPU execution wearing a
        GPU decision.
        """
        gateway = ComputeGateway(inventory=_inventory({GPU: True}), mode=ExecutionMode.AUTO)
        assert gateway.submit(WorkloadKind.OCR, lambda: "text") == "text"
        record = gateway._execution_records[-1]
        assert record["actual_device"] == "cpu"
        assert record["selected_device"] == "cpu", (
            "the decision must be downgraded, not left claiming the accelerator"
        )
        assert record["fallback_reason"], "the reason must be recorded"
        assert gateway.stats.fallbacks == 1

    def test_gpu_only_refuses_when_the_device_has_no_executable_backend(self):
        gateway = ComputeGateway(inventory=_inventory({GPU: True}), mode=ExecutionMode.GPU_ONLY)
        with pytest.raises(DeviceUnavailableError):
            gateway.submit(WorkloadKind.OCR, lambda: "text")
        assert gateway.stats.capability_errors == 1
        assert gateway.stats.completed == 0, "nothing may run under a refused mode"


# ---------------------------------------------------------------------------
# The mode is not changed behind the operator's back
# ---------------------------------------------------------------------------
class TestModeIsNotSilentlyChanged:
    def test_adaptive_scheduling_changes_workers_not_the_mode(self, monkeypatch):
        monkeypatch.setenv("COMPUTE_ADAPTIVE_SCHEDULING", "1")
        policy.set_mode_override("cpu", source="command line (--compute-mode cpu)")
        gateway = ComputeGateway(inventory=_inventory({GPU: True}),
                                 mode=policy.mode_selection().mode)
        before = gateway.mode
        for _ in range(5):
            gateway.submit(WorkloadKind.CLASSIFICATION, lambda: "c")
        assert gateway.mode is before is ExecutionMode.CPU_ONLY

    def test_memory_pressure_shrinks_the_window_not_the_mode(self):
        from core.compute.backpressure import (
            AdmissionController, AdmissionPolicy, HostPressure,
        )

        gateway = ComputeGateway(inventory=_inventory({}), mode=ExecutionMode.CPU_ONLY)
        samples = {"pressure": HostPressure(measured=True, source="test",
                                            cpu_percent=10.0, memory_percent=95.0,
                                            load_per_core=0.2, sampled_at=0.0)}
        controller = AdmissionController(
            configured_window=8, policy=AdmissionPolicy(cooldown_s=0.0),
            sampler=lambda: samples["pressure"], clock=lambda: 1000.0,
        )
        assert controller.window() < 8, "pressure must shrink the worker window"
        assert gateway.mode is ExecutionMode.CPU_ONLY, (
            "resource pressure changes workers, never the selected device policy"
        )

    def test_strict_gpu_is_never_downgraded_by_the_gateway(self):
        gateway = ComputeGateway(inventory=_inventory({}), mode=ExecutionMode.GPU_ONLY)
        report = gateway.mode_report()
        assert report["strict_gpu_mode"] is True
        assert report["cpu_fallback_allowed"] is False
        assert gateway.mode is ExecutionMode.GPU_ONLY


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
class TestReporting:
    def test_startup_block_states_mode_source_device_and_fallback(self):
        selection = policy.ModeSelection(ExecutionMode.CPU_ONLY,
                                         "command line (--compute-mode cpu)", "cpu")
        lines = policy.describe(selection, policy.mode_support(selection, _inventory({GPU: True})))
        joined = "\n".join(lines)
        assert "Compute Mode: CPU-ONLY" in joined
        assert "Compute Mode Source: command line (--compute-mode cpu)" in joined
        assert "Selected Device:" in joined
        assert "Actual Device:" in joined
        assert "Fallback:" in joined

    def test_result_block_records_requested_and_actual_devices(self):
        policy.set_mode_override("cpu", source="command line (--compute-mode cpu)")
        gateway = ComputeGateway(inventory=_inventory({}), mode=ExecutionMode.CPU_ONLY)
        gateway.submit(WorkloadKind.OCR, lambda: "text")
        block = policy.result_block(gateway=gateway)
        assert block["requested_mode"] == "cpu"
        assert block["mode_label"] == "CPU-ONLY"
        assert block["source"] == "command line (--compute-mode cpu)"
        assert block["actual_devices"] == {"cpu": 1}
        assert block["capability_errors"] == 0

    def test_workload_table_separates_cpu_only_stages_from_accelerator_capable(self):
        rows = {row["workload"]: row for row in policy.workflow_summary()}
        assert rows[WorkloadKind.HASHING.value]["devices"] == ["cpu"]
        assert rows[WorkloadKind.HASHING.value]["accelerator_capable"] is False
        assert rows[WorkloadKind.OCR.value]["accelerator_capable"] is True
