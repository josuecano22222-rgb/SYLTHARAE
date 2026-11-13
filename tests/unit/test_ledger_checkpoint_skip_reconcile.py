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
