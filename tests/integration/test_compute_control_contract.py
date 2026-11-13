"""The compute-control contract: four modes, explicit placement, no surprises.

Requirement being pinned: device selection may change *performance only*. It
must never change forensic output, and it must never quietly change the
requested mode. Concretely:

* ``cpu``      - nothing but the CPU runs, whatever hardware exists.
* ``gpu``      - a workload that has an accelerator implementation runs on it or
                 raises a capability error; it is never silently run on the CPU.
* ``cpu+gpu``  - both may run, concurrently, with independent limits.
* ``auto``     - routes on measured capabilities and says where and why.

Every execution is recorded (mode, selected device, actual device, processor,
duration, fallback reason) and the machine's real capabilities are reported, so
"which device produced this evidence?" is answerable from the run itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.compute.capabilities import CPU, DPU, FPGA, GPU, NPU, AcceleratorInfo  # noqa: E402
from core.compute.gateway import (  # noqa: E402
    AcceleratorBackend,
    ComputeGateway,
)
from core.compute.routing import (  # noqa: E402
    WORKLOAD_DEVICES,
    DeviceUnavailableError,
    ExecutionMode,
    WorkloadKind,
    allowed_devices,
)


class _Inventory:
    """A machine whose accelerator set the test chooses."""

    def __init__(self, available):
        self.cpu = AcceleratorInfo(kind=CPU, name="test-cpu", available=True,
                                   backend="stdlib", detail="test")
        self.accelerators = {
            kind: AcceleratorInfo(kind=kind, name=kind, available=bool(available.get(kind)),
                                  backend="test", detail="test inventory")
            for kind in (GPU, NPU, FPGA, DPU)
        }

    @property
    def available_accelerators(self):
        """The same accessor the real probe exposes (usable devices only)."""
        return [info for info in self.accelerators.values() if info.available]


def _gateway(mode, available=None, **kwargs):
    return ComputeGateway(mode=mode, inventory=_Inventory(available or {}), **kwargs)


def _gpu_backend(seen=None):
    def run(fn, *args, **kwargs):
        if seen is not None:
            seen.append(args)
        return fn(*args, **kwargs)

    return AcceleratorBackend(kind=GPU, name="test-gpu", self_test=lambda: True,
                              run=run, workloads=frozenset({WorkloadKind.OCR}))


# ---------------------------------------------------------------------------
# Mode behaviour
# ---------------------------------------------------------------------------
class TestModes:
    def test_cpu_only_never_uses_an_accelerator_even_when_one_is_verified(self):
        gateway = _gateway(ExecutionMode.CPU_ONLY, {GPU: True})
        seen = []
        gateway.register_accelerator_backend(_gpu_backend(seen))
        assert gateway.submit(WorkloadKind.OCR, lambda: "result") == "result"
        assert seen == [], "cpu mode must not hand work to the accelerator"
        assert gateway.select_device(WorkloadKind.OCR).device == CPU

    def test_gpu_only_uses_the_verified_device(self):
        gateway = _gateway(ExecutionMode.GPU_ONLY, {GPU: True})
        seen = []

        def run(fn, *args, **kwargs):
            seen.append(args)
            return fn(*args, **kwargs)

        gateway.register_accelerator_backend(
            AcceleratorBackend(kind=GPU, name="gpu", self_test=lambda: True,
                               run=run, workloads=frozenset({WorkloadKind.OCR})))
        assert gateway.submit(WorkloadKind.OCR, lambda a, b: a + b, 20, 22) == 42
        assert seen == [(20, 22)], "the verified GPU backend did not receive the work"
        record = gateway.execution_records(1)[0]
        assert record["actual_device"] == GPU
        assert record["selected_device"] == GPU
        assert record["fallback_reason"] is None

    def test_gpu_only_without_a_device_raises_and_runs_nothing(self):
        gateway = _gateway(ExecutionMode.GPU_ONLY, {})
        executed = []
        with pytest.raises(DeviceUnavailableError):
            gateway.submit(WorkloadKind.OCR, lambda: executed.append(1))
        assert executed == [], "refused work must not have executed anywhere"
        assert gateway.execution_records(1)[0]["outcome"] == "capability_error"

    def test_cpu_gpu_allows_both_and_keeps_limits_independent(self):
        gateway = _gateway(ExecutionMode.CPU_GPU, {GPU: True})
        gateway.register_accelerator_backend(_gpu_backend())
        assert gateway.submit(WorkloadKind.OCR, lambda: "ocr") == "ocr"       # GPU
        assert gateway.submit(WorkloadKind.HASHING, lambda: "digest") == "digest"  # CPU
        devices = {r["actual_device"] for r in gateway.execution_records(5)}
        assert devices == {GPU, CPU}
        allocation = gateway.allocation().as_dict()
        assert allocation["cpu_cores"] >= 1 and allocation["queue_depth"] >= 0

    def test_auto_routes_and_explains_itself(self):
        gateway = _gateway(ExecutionMode.AUTO, {})
        assert gateway.submit(WorkloadKind.OCR, lambda: "ok") == "ok"
        report = gateway.mode_report()
        assert report["requested_mode"] == "auto"
        assert report["effective_mode"] == "cpu"
        placement = report["placements"]["ocr"]
        assert placement["device"] == CPU and placement["reason"]


# ---------------------------------------------------------------------------
# The capability report
# ---------------------------------------------------------------------------
class TestCapabilityReport:
    def test_report_lists_devices_capabilities_and_unsupported_work(self):
        report = _gateway(ExecutionMode.AUTO, {}).mode_report()
        detected = report["detected"]
        for kind in (GPU, NPU, FPGA, DPU):
            assert kind in detected["devices"]
            assert "available" in detected["devices"][kind]
        assert set(report["cpu_only_workloads"]) == {
            WorkloadKind.HASHING.value, WorkloadKind.CONTENT_EXTRACTION.value,
            WorkloadKind.INDEXING.value, WorkloadKind.COMPRESSION.value,
            WorkloadKind.METADATA.value,
        }
        # OCR/classification have accelerator implementations, so on this
        # machine they are reported as unsupported on an accelerator.
        assert WorkloadKind.OCR.value in report["unsupported_on_this_machine"]
        assert report["supported_on_accelerator"], report

    def test_report_is_honest_when_nothing_is_detected(self):
        report = _gateway(ExecutionMode.AUTO, {}).mode_report()
        assert report["usable_accelerators"] == []
        assert report["effective_mode"] == "cpu"
        assert all(not info["available"] for info in report["detected"]["devices"].values())
        assert report["unavailable_accelerators"], "the reasons must be reported"

    def test_no_unsupported_claim_about_workloads_without_an_accelerator_path(self):
        report = _gateway(ExecutionMode.AUTO, {}).mode_report()
        for workload in report["cpu_only_workloads"]:
            assert all(d == CPU for d in allowed_devices(WorkloadKind(workload)))


# ---------------------------------------------------------------------------
# Provenance and equivalence
# ---------------------------------------------------------------------------
class TestProvenanceAndEquivalence:
    def test_every_execution_records_mode_device_processor_and_duration(self):
        gateway = _gateway(ExecutionMode.AUTO, {})
        gateway.submit(WorkloadKind.METADATA, lambda: {"m": 1})
        record = gateway.execution_records(1)[0]
        assert record["execution_mode"] == "auto"
        assert record["selected_device"] == CPU
        assert record["actual_device"] == CPU
        assert record["processor"] == CPU
        assert "duration_ms" in record and "outcome" in record

    def test_records_and_aggregates_are_bounded(self):
        gateway = _gateway(ExecutionMode.AUTO, {})
        for _ in range(600):
            gateway.submit(WorkloadKind.METADATA, lambda: 1)
        assert len(gateway.execution_records(1000)) <= 256, (
            "execution provenance must not grow without bound"
        )
        entry = gateway.by_workload["metadata"]
        assert entry["executions"] == 600, (
            "the aggregate must stay exact even though the ring buffer is bounded"
        )

    def test_device_selection_is_not_an_input_to_the_result(self):
        """The same callable under every mode returns identical output."""
        payload = {"text": "final evidence", "words": ["a", "b"], "hash": "f" * 64}
        outputs = []
        for mode in (ExecutionMode.CPU_ONLY, ExecutionMode.GPU_ONLY,
                     ExecutionMode.CPU_GPU, ExecutionMode.AUTO):
            gateway = _gateway(mode, {GPU: True})
            if mode is not ExecutionMode.CPU_ONLY:
                gateway.register_accelerator_backend(_gpu_backend())
            outputs.append(gateway.submit(
                WorkloadKind.OCR, lambda: dict(payload)))
        assert all(output == outputs[0] for output in outputs), (
            f"a device changed the output: {outputs}"
        )

    def test_workload_table_separates_cpu_only_from_accelerator_capable(self):
        for workload in (WorkloadKind.HASHING, WorkloadKind.CONTENT_EXTRACTION,
                         WorkloadKind.INDEXING, WorkloadKind.METADATA,
                         WorkloadKind.COMPRESSION):
            assert WORKLOAD_DEVICES[workload] == frozenset({CPU}), (
                f"{workload} must be CPU-only: a digest or stored byte stream "
                f"must not depend on the device that produced it"
            )
        for workload in (WorkloadKind.OCR, WorkloadKind.CLASSIFICATION):
            assert any(d != CPU for d in WORKLOAD_DEVICES[workload])


# ---------------------------------------------------------------------------
# Scheduling: declared requirements, admission control, placement reporting
# ---------------------------------------------------------------------------
class TestSchedulingContract:
    def test_every_workload_declares_its_requirements(self):
        from core.compute.routing import WORKLOAD_PROFILES, WorkloadKind, profile_for

        assert set(WORKLOAD_PROFILES) == set(WorkloadKind)
        for workload in WorkloadKind:
            profile = profile_for(workload)
            assert profile.supported_devices, workload
            assert profile.preferred_device in profile.supported_devices, workload
            assert profile.estimated_cpu_units > 0
            assert profile.estimated_memory_bytes > 0
            assert profile.estimated_duration_ms > 0
            # The two tables must agree: only a workload that lists an
            # accelerator device may declare accelerator memory.
            accelerator_devices = profile.supported_devices - {CPU}
            if profile.accelerator_memory_bytes:
                assert accelerator_devices, workload

    def test_report_publishes_requirements_alongside_placements(self):
        report = _gateway(ExecutionMode.AUTO, {}).mode_report()
        profiles = report["workload_profiles"]
        assert set(profiles) == {kind.value for kind in WorkloadKind}
        placement = report["placements"]["hashing"]
        assert placement["device"] == CPU
        assert profiles["hashing"]["supported_devices"] == [CPU]

    def test_accelerator_memory_is_required_to_be_known_before_use(self):
        from core.compute.routing import accelerator_memory_fits

        fits, reason = accelerator_memory_fits(WorkloadKind.OCR, None)
        assert fits is False and "could not be determined" in reason
        fits, reason = accelerator_memory_fits(WorkloadKind.OCR, 128 * 1024 * 1024)
        assert fits is False and "needs" in reason
        fits, _ = accelerator_memory_fits(WorkloadKind.OCR, 8 * 1024 ** 3)
        assert fits is True
        fits, _ = accelerator_memory_fits(WorkloadKind.HASHING, None)
        assert fits is True, "a workload with no accelerator requirement needs no memory check"

    def test_a_gpu_without_enough_memory_is_not_selected(self):
        """Detected hardware is not the same as usable hardware."""
        from core.compute.capabilities import GPU, AcceleratorInfo

        class _Inventory:
            def __init__(self):
                from core.compute.capabilities import CPU
                self.cpu = AcceleratorInfo(kind=CPU, name="cpu", available=True,
                                           backend="stdlib", detail="test")
                self.accelerators = {
                    GPU: AcceleratorInfo(kind=GPU, name="gpu", available=True,
                                         backend="test", detail="small",
                                         memory_bytes=64 * 1024 * 1024),
                }

            @property
            def available_accelerators(self):
                return [i for i in self.accelerators.values() if i.available]

        gateway = ComputeGateway(mode=ExecutionMode.AUTO, inventory=_Inventory())
        decision = gateway.select_device(WorkloadKind.OCR)
        assert decision.memory_fits is False
        assert "needs" in decision.memory_note


class TestAdmissionControl:
    def _controller(self, window=8, **policy):
        from core.compute.backpressure import AdmissionController, AdmissionPolicy, HostPressure

        clock = {"now": 1000.0}
        samples = {"pressure": HostPressure(measured=True, source="test",
                                            cpu_percent=10.0, memory_percent=30.0,
                                            load_per_core=0.2, sampled_at=0.0)}
        policy.setdefault("cooldown_s", 0.0)
        controller = AdmissionController(
            configured_window=window,
            policy=AdmissionPolicy(**policy),
            sampler=lambda: samples["pressure"],
            clock=lambda: clock["now"],
            sample_interval_s=0.0,
        )
        return controller, samples, clock, HostPressure

    def test_memory_pressure_shrinks_the_window_and_says_why(self):
        controller, samples, _clock, HostPressure = self._controller()
        assert controller.window() == 8
        samples["pressure"] = HostPressure(measured=True, source="test",
                                           cpu_percent=20.0, memory_percent=95.0,
                                           load_per_core=0.5)
        assert controller.window() == 4
        assert controller.window() == 2
        decision = controller.decisions(1)[0]
        assert "memory" in " ".join(decision["reasons"])
        assert decision["pressure"]["memory_percent"] == 95.0

    def test_the_window_never_drops_below_the_minimum_or_above_the_ceiling(self):
        controller, samples, _clock, HostPressure = self._controller(window=3)
        for level in (99.0, 99.0, 99.0, 99.0):
            samples["pressure"] = HostPressure(measured=True, source="test",
                                               memory_percent=level)
            assert 1 <= controller.window() <= 3
        samples["pressure"] = HostPressure(measured=True, source="test",
                                           cpu_percent=1.0, memory_percent=10.0,
                                           load_per_core=0.1)
        for _ in range(10):
            assert controller.window() <= 3

    def test_unmeasurable_pressure_keeps_the_configured_window(self):
        from core.compute.backpressure import AdmissionController, HostPressure

        controller = AdmissionController(
            configured_window=6,
            sampler=lambda: HostPressure(measured=False, source="unavailable"),
            sample_interval_s=0.0)
        assert controller.window() == 6
        assert controller.decisions() == []
        assert controller.snapshot()["throttled"] is False

    def test_shrinking_is_immediate_and_growth_waits_for_the_cooldown(self):
        from core.compute.backpressure import AdmissionController, AdmissionPolicy, HostPressure

        clock = {"now": 0.0}
        samples = {"pressure": HostPressure(measured=True, source="test",
                                            memory_percent=95.0)}
        controller = AdmissionController(
            configured_window=8, policy=AdmissionPolicy(cooldown_s=30.0),
            sampler=lambda: samples["pressure"], clock=lambda: clock["now"],
            sample_interval_s=0.0)
        assert controller.window() == 4                      # shrink immediately
        samples["pressure"] = HostPressure(measured=True, source="test",
                                           cpu_percent=5.0, memory_percent=20.0,
                                           load_per_core=0.1)
        assert controller.window() == 4                      # growth blocked
        clock["now"] = 31.0
        assert controller.window() == 5                      # one step, after cooldown
        clock["now"] = 62.0
        assert controller.window() == 6

    def test_decisions_are_bounded(self):
        controller, samples, clock, HostPressure = self._controller(window=64, cooldown_s=0.0)
        for index in range(500):
            clock["now"] += 1.0
            samples["pressure"] = HostPressure(
                measured=True, source="test",
                memory_percent=95.0 if index % 2 else 10.0)
            controller.window()
        assert len(controller.snapshot()["decisions"]) <= 16
        assert controller.snapshot()["samples"] > 0
