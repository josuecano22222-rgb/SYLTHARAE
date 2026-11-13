"""Worker-budget policy of the resource coordinator.

Why these tests exist: the coordinator's worker budget is the one place where
the pipeline decides how much of the host it may use, and it has already been
wrong twice in ways that were invisible to every other test.

1. ``min(max_workers, 4)`` capped the pool at four workers regardless of the
   configured preference, so a host configured for 8 or 16 workers ran at 4 -
   a silent 4x throughput loss that no functional test could see.

2. CPU above 85 % was treated as overload. This pipeline is I/O-bound, so
   saturated cores are what a healthy run looks like; the pool shrank *because*
   the run was working, and the reader's overload check then pushed every
   extracted sub-tree through the sequential fallback (measured: a 19 566-
   attachment PST processed strictly serially).

3. The monitor restored workers against a single-instance ceiling while a
   second instance was running, over-provisioning the host.

These tests pin the policy itself: cores and memory set the ceiling, memory
(only memory) throttles, and every restore is bounded by the live instance
count.
"""

import json
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.resource_coordinator import (  # noqa: E402
    MAX_WORKERS_PER_INSTANCE,
    MEMORY_EMERGENCY_PERCENT,
    MEMORY_PRESSURE_PERCENT,
    RESERVED_CORES,
    WORKER_MEMORY_BUDGET_GB,
    ResourceCoordinator,
    ResourceLimits,
)


def _coordinator(tmp_path, *, cpu_count=8, total_memory_gb=32.0, workers=8,
                 instance_type="cli", others=()):
    """A coordinator wired by hand.

    The real constructor registers an instance in the project's lock file and
    starts a monitor thread; the policy under test needs none of that, so the
    object is created without running ``__init__``.
    """
    coordinator = object.__new__(ResourceCoordinator)
    coordinator.cpu_count = cpu_count
    coordinator.total_memory_gb = total_memory_gb
    coordinator.instance_type = instance_type
    coordinator.instance_id = "test_instance"
    coordinator.lock_file = Path(tmp_path) / ".app_instance.lock"
    coordinator.resource_limits = ResourceLimits(max_workers=workers, db_pool_max=5)
    coordinator._system_overload = False
    coordinator._last_cpu_percent = 0.0
    coordinator._last_memory_percent = 0.0
    if others:
        coordinator.lock_file.write_text(json.dumps({
            name: {"pid": 1, "type": "cli", "started_at": time.time(),
                   "last_update": time.time(), "workers": 4, "db_pool": 4}
            for name in others
        }))
    return coordinator


# ---------------------------------------------------------------------------
# The ceiling
# ---------------------------------------------------------------------------
def test_worker_ceiling_oversubscribes_cores_for_io_bound_work(tmp_path):
    """Cores * 2 is the useful ceiling; the old hard cap of 4 is gone."""
    coordinator = _coordinator(tmp_path, cpu_count=8)

    assert coordinator._worker_ceiling() == min(
        MAX_WORKERS_PER_INSTANCE, max(1, 8 - RESERVED_CORES) * 2
    )
    assert coordinator._worker_ceiling() > 4, (
        "a host that can keep more than four workers busy must be allowed to"
    )


def test_worker_ceiling_is_bounded_by_memory(tmp_path):
    """Memory, not cores, is the limit that kills a run when exceeded."""
    coordinator = _coordinator(tmp_path, cpu_count=16, total_memory_gb=1.0)

    expected = max(1, int(1.0 / WORKER_MEMORY_BUDGET_GB))
    assert coordinator._worker_ceiling() == expected
    assert expected < min(MAX_WORKERS_PER_INSTANCE, (16 - RESERVED_CORES) * 2)


def test_worker_ceiling_never_drops_below_one(tmp_path):
    coordinator = _coordinator(tmp_path, cpu_count=1, total_memory_gb=0.1)

    assert coordinator._worker_ceiling() == 1


def test_worker_ceiling_splits_between_instances(tmp_path):
    coordinator = _coordinator(tmp_path, cpu_count=8)
    whole_host = coordinator._worker_ceiling(1)

    assert coordinator._worker_ceiling(2) == max(1, whole_host // 2)
    assert coordinator._worker_ceiling(3) == max(1, whole_host // 3)


def test_worker_ceiling_counts_live_instances_when_not_told(tmp_path):
    """``None`` means "ask the lock file", including this instance."""
    alone = _coordinator(tmp_path, cpu_count=8)
    assert alone._count_instances() == 1
    assert alone._worker_ceiling() == alone._worker_ceiling(1)

    shared = _coordinator(tmp_path, cpu_count=8, others=("other_instance",))
    assert shared._count_instances() == 2
    assert shared._worker_ceiling() == shared._worker_ceiling(2)


def test_stale_instances_do_not_count(tmp_path):
    """A crashed instance must not keep reserving half the host forever."""
    coordinator = _coordinator(tmp_path, cpu_count=8)
    coordinator.lock_file.write_text(json.dumps({
        "crashed": {"pid": 1, "type": "cli", "started_at": 0,
                    "last_update": time.time() - 3600, "workers": 4, "db_pool": 4}
    }))

    assert coordinator._count_instances() == 1


# ---------------------------------------------------------------------------
# Pressure: memory throttles, CPU does not
# ---------------------------------------------------------------------------
def test_cpu_saturation_alone_is_not_overload(tmp_path):
    coordinator = _coordinator(tmp_path, cpu_count=8, workers=1)
    # Already at the ceiling, so the only thing a sample can do is throttle it.
    ceiling = coordinator._worker_ceiling()
    coordinator.resource_limits.max_workers = ceiling

    action = coordinator._evaluate_pressure(99.9, 40.0)

    assert action == "none", "a busy I/O-bound pipeline is not an overloaded one"
    assert coordinator.resource_limits.max_workers == ceiling
    assert coordinator.is_system_overloaded() is False


def test_cpu_saturation_does_not_prevent_restoring_the_pool(tmp_path):
    """Idle CPU is not required to grow the pool back - only free memory is."""
    coordinator = _coordinator(tmp_path, cpu_count=8, workers=2)

    assert coordinator._evaluate_pressure(99.9, 40.0) == "restored"
    assert coordinator.resource_limits.max_workers > 2


def test_memory_pressure_reduces_the_worker_budget(tmp_path):
    coordinator = _coordinator(tmp_path, cpu_count=8, workers=8)

    action = coordinator._evaluate_pressure(50.0, MEMORY_PRESSURE_PERCENT + 3)

    assert action == "reduced"
    assert coordinator.resource_limits.max_workers == 7
    assert coordinator.is_system_overloaded() is True


def test_emergency_memory_pressure_reduces_by_two(tmp_path):
    coordinator = _coordinator(tmp_path, cpu_count=8, workers=8)

    assert coordinator._evaluate_pressure(50.0, MEMORY_EMERGENCY_PERCENT + 3) == "reduced"
    assert coordinator.resource_limits.max_workers == 6


def test_worker_budget_never_reaches_zero_under_sustained_pressure(tmp_path):
    coordinator = _coordinator(tmp_path, cpu_count=8, workers=8)

    for _ in range(20):
        coordinator._evaluate_pressure(50.0, 99.0)

    assert coordinator.resource_limits.max_workers == 1


def test_pressure_cleared_restores_toward_the_ceiling(tmp_path):
    coordinator = _coordinator(tmp_path, cpu_count=8, workers=10)
    ceiling = coordinator._worker_ceiling()
    coordinator.resource_limits.max_workers = 2
    coordinator._system_overload = True

    action = coordinator._evaluate_pressure(30.0, 20.0)

    assert action == "restored"
    assert 2 < coordinator.resource_limits.max_workers <= ceiling
    assert coordinator.is_system_overloaded() is False


def test_restore_stops_at_the_ceiling(tmp_path):
    coordinator = _coordinator(tmp_path, cpu_count=8, workers=1)
    ceiling = coordinator._worker_ceiling()

    for _ in range(10):
        coordinator._evaluate_pressure(30.0, 20.0)

    assert coordinator.resource_limits.max_workers == ceiling


def test_restore_respects_a_second_live_instance(tmp_path):
    """The regression: the monitor used to restore against the whole host."""
    coordinator = _coordinator(tmp_path, cpu_count=8, workers=1,
                               others=("other_instance",))
    ceiling_for_two = coordinator._worker_ceiling(2)
    assert ceiling_for_two < coordinator._worker_ceiling(1)

    for _ in range(10):
        coordinator._evaluate_pressure(30.0, 20.0)

    assert coordinator.resource_limits.max_workers == ceiling_for_two, (
        "two live instances must split the host, not each grow to the"
        " single-instance ceiling"
    )


def test_evaluate_pressure_is_the_only_place_that_decides(tmp_path):
    """The monitor loop must not re-implement (or contradict) the policy."""
    import inspect

    from core.resource_coordinator import ResourceCoordinator as Coordinator

    source = inspect.getsource(Coordinator._monitor_loop)
    assert "_evaluate_pressure(" in source
    assert "MEMORY_PRESSURE_PERCENT" not in source.replace(
        "self._evaluate_pressure", ""
    ), "thresholds belong to _evaluate_pressure, not to the loop"
    assert "max_workers -" not in source.replace(" ", "").replace("\n", ""), (
        "the loop must not adjust the worker budget itself"
    )


# ---------------------------------------------------------------------------
# One budget for recalculation and restore (field report after step 13)
# ---------------------------------------------------------------------------
# The owner's Windows log showed "Restored workers to 4" every few seconds at
# 28 % memory with no "Reduced" line in between. Cause: every
# get_safe_worker_count()/get_safe_db_pool_size() call recalculated a *web*
# instance to ceiling - 1 (one worker kept for requests) and the monitor then
# "restored" it to the full ceiling. The same recalculation also undid a
# memory-pressure reduction the moment anything asked for a worker count.

def test_the_monitor_does_not_take_back_the_web_request_worker(tmp_path):
    coordinator = _coordinator(tmp_path, cpu_count=8, instance_type="web")
    coordinator._update_resource_limits()
    budget = coordinator.resource_limits.max_workers
    assert budget == max(1, coordinator._worker_ceiling() - 1)

    actions = []
    for _ in range(5):
        coordinator.get_safe_worker_count()
        actions.append(coordinator._evaluate_pressure(100.0, 28.0))

    assert actions == ["none"] * 5, "no restore without a preceding reduction"
    assert coordinator.resource_limits.max_workers == budget


def test_recalculation_does_not_undo_a_memory_pressure_reduction(tmp_path):
    coordinator = _coordinator(tmp_path, cpu_count=8, instance_type="web")
    coordinator._update_resource_limits()
    budget = coordinator.resource_limits.max_workers
    assert coordinator._evaluate_pressure(30.0, MEMORY_PRESSURE_PERCENT + 1) == "reduced"
    reduced = coordinator.resource_limits.max_workers
    assert reduced < budget

    assert coordinator.get_safe_worker_count() == reduced
    coordinator.get_safe_db_pool_size()
    assert coordinator.resource_limits.max_workers == reduced

    # Once the pressure is gone the restore goes back to the same budget,
    # not past it.
    for _ in range(10):
        coordinator._evaluate_pressure(30.0, 20.0)
    assert coordinator.resource_limits.max_workers == budget


def test_a_cli_instance_keeps_the_whole_ceiling(tmp_path):
    coordinator = _coordinator(tmp_path, cpu_count=8, instance_type="cli")
    coordinator._update_resource_limits()
    assert coordinator.resource_limits.max_workers == coordinator._worker_ceiling()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
