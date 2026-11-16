"""A checkpoint-skipped archive member must not keep its container waiting.

Root cause (reproduced on a real PostgreSQL database): the router registers an
extraction with its container (N members). The nested reader then re-registers
the same extraction with the post-checkpoint count (N minus the members already
stored). The ledger reconciled the total but left the container's outstanding
count at N, so the end-of-run wait ran to its 120 s stall limit on every restart
of a container whose members were already done.
"""

from __future__ import annotations

from pipeline.progress_ledger import OUTCOME_COMPLETED, OUTCOME_SKIPPED, ProgressLedger


def test_reconciliation_lowers_the_parent_containers_outstanding_work():
    ledger = ProgressLedger()
    ledger.add_discovered(4, key="extract-1", parent="bundle.zip")
    assert ledger.container_outstanding("bundle.zip") == 4

    # Nested reader: two members were already stored, so only two remain.
    ledger.add_discovered(2, key="extract-1")
    assert ledger.container_outstanding("bundle.zip") == 2
    assert ledger.pending == 2


def test_container_finishes_when_remaining_members_settle():
    ledger = ProgressLedger()
    ledger.add_discovered(4, key="extract-1", parent="bundle.zip")
    ledger.add_discovered(2, key="extract-1")  # two skipped by checkpoint

    for _ in range(2):
        unit = ledger.begin(path="m.txt", phase="x")
        ledger.settle(unit, OUTCOME_COMPLETED, container="bundle.zip")

    assert "bundle.zip" not in ledger.live_containers()
    assert ledger.container_outstanding("bundle.zip") == 0


def test_all_members_skipped_leaves_no_outstanding_work():
    ledger = ProgressLedger()
    ledger.add_discovered(3, key="extract-1", parent="bundle.zip")
    ledger.add_discovered(0, key="extract-1")  # every member already stored
    assert ledger.live_containers() == {}
    assert ledger.pending == 0


def test_increase_on_reconciliation_still_counts_as_new_work():
    ledger = ProgressLedger()
    ledger.add_discovered(2, key="extract-1", parent="bundle.zip")
    ledger.add_discovered(5, key="extract-1")
    assert ledger.container_outstanding("bundle.zip") == 5


def test_skip_settlement_does_not_go_negative():
    ledger = ProgressLedger()
    ledger.add_discovered(2, key="extract-1", parent="bundle.zip")
    ledger.abandon(1, OUTCOME_SKIPPED, container="bundle.zip")
    assert ledger.container_outstanding("bundle.zip") == 1


def test_already_discovered_skips_are_settled_not_recorded():
    """`record` on work that is already in the denominator double-counts it.

    A resumed nested run finds the container's members already in the
    checkpoint. Those members were published into the denominator when the
    container was opened, so they are discovered work that will not run.
    `record` adds them to `discovered_total` a *second* time and settles them,
    which leaves `pending` permanently non-zero: the end-of-run sweep then
    found residue with nothing cancelled and recorded it as failed, so a
    resumed run reported files it had stored successfully as
    "could not be processed" (reproduced against PostgreSQL: 3 spurious
    failures on every restart of a container whose members were all stored).

    The correct call for work already in the denominator is `abandon`, which
    settles without inflating.
    """
    # The container publishes three members into the denominator.
    ledger = ProgressLedger()
    ledger.add_discovered(3, key="extract-1", parent="bundle.zip")
    assert ledger.pending == 3

    # The nested run finds all three already in the checkpoint and settles
    # them, releasing the container's outstanding work for them.
    ledger.abandon(3, OUTCOME_SKIPPED, container="bundle.zip")

    assert ledger.pending == 0, "already-counted work must not stay pending"
    assert ledger.counts.get("failed", 0) == 0
    assert ledger.counts.get(OUTCOME_SKIPPED) == 3
    assert ledger.container_outstanding("bundle.zip") == 0
    assert ledger.is_complete


def test_record_is_what_adds_to_the_denominator():
    """The counterpart: work filtered out before discovery is new work.

    At the top level a checkpointed file never reaches `add_discovered`, so
    `record` is correct there - it is the only call that grows the denominator
    to include it. This is the other half of the contract above.
    """
    ledger = ProgressLedger()
    ledger.add_discovered(1, key="bundle.zip")

    # Two top-level files were filtered out by the checkpoint before anything
    # registered them.
    ledger.record(OUTCOME_SKIPPED, 2)

    assert ledger.discovered_total == 3
    assert ledger.pending == 1, "only bundle.zip is still awaiting a worker"
    assert ledger.counts.get(OUTCOME_SKIPPED) == 2


def test_recording_discovered_work_twice_is_the_failure_being_guarded():
    """The bug, stated so it cannot be reintroduced unnoticed.

    `record` was called for work the container had already published. The
    denominator grew by six for three real files, leaving three units that
    could never be settled - the exact residue that became three failures.
    """
    ledger = ProgressLedger()
    ledger.add_discovered(3, key="extract-1", parent="bundle.zip")
    ledger.record(OUTCOME_SKIPPED, 3)  # wrong: these were already counted

    assert ledger.discovered_total == 6, "three files counted twice"
    assert ledger.pending == 3, "residue that the end-of-run sweep calls failed"
