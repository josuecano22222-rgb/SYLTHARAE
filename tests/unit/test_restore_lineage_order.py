"""Unit tests for ImportService.order_paths_by_lineage.

paths.parent_path_id is a self-referencing foreign key (m0007), so restoring a
backup must insert parents before children. Found by a real regression:
restoring a backup taken after nested-archive ingestion failed with

    ForeignKeyViolation: ... violates foreign key constraint "fk_paths_parent"
    DETAIL: Key (parent_path_id)=(9) is not present in table "paths".
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from Api.services.import_service import order_paths_by_lineage  # noqa: E402


def test_empty_and_none_inputs():
    assert order_paths_by_lineage([]) == []


def test_child_before_parent_is_reordered():
    rows = [
        {"id": 3, "file_name": "scan.png", "parent_path_id": 2},
        {"id": 2, "file_name": "inner.zip", "parent_path_id": 1},
        {"id": 1, "file_name": "outer.zip", "parent_path_id": None},
    ]
    ordered = order_paths_by_lineage(rows)
    assert [r["id"] for r in ordered] == [1, 2, 3]


def test_already_correct_order_is_preserved():
    rows = [
        {"id": 1, "parent_path_id": None},
        {"id": 2, "parent_path_id": 1},
    ]
    assert [r["id"] for r in order_paths_by_lineage(rows)] == [1, 2]


def test_every_row_survives_exactly_once():
    """Reordering must not drop or duplicate rows."""
    rows = [{"id": i, "parent_path_id": i - 1 if i > 1 else None} for i in range(1, 21)]
    import random

    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    ordered = order_paths_by_lineage(shuffled)
    assert sorted(r["id"] for r in ordered) == list(range(1, 21))
    assert len(ordered) == 20
    # and the invariant holds: parent appears before child
    positions = {r["id"]: i for i, r in enumerate(ordered)}
    for row in ordered:
        parent = row.get("parent_path_id")
        if parent in positions:
            assert positions[parent] < positions[row["id"]]


def test_deep_chain_does_not_recurse():
    """Iterative on purpose: a crafted backup must not exhaust the stack."""
    depth = 5000
    rows = [
        {"id": i, "parent_path_id": i - 1 if i > 1 else None}
        for i in range(1, depth + 1)
    ]
    ordered = order_paths_by_lineage(list(reversed(rows)))
    assert [r["id"] for r in ordered] == list(range(1, depth + 1))


def test_missing_parent_is_emitted_in_place():
    """A dangling parent must still reach the INSERT so the FK rejects it."""
    rows = [
        {"id": 5, "parent_path_id": 99},  # 99 is not in the batch
        {"id": 1, "parent_path_id": None},
    ]
    ordered = order_paths_by_lineage(rows)
    assert sorted(r["id"] for r in ordered) == [1, 5]


def test_cycle_does_not_hang():
    """A corrupt backup with a cycle must terminate."""
    rows = [
        {"id": 1, "parent_path_id": 2},
        {"id": 2, "parent_path_id": 1},
    ]
    ordered = order_paths_by_lineage(rows)
    assert sorted(r["id"] for r in ordered) == [1, 2]


def test_rows_without_ids_pass_through():
    rows = [{"file_name": "a"}, {"id": None, "file_name": "b"}]
    assert len(order_paths_by_lineage(rows)) == 2


def test_non_dict_rows_pass_through():
    rows = ["not-a-row", {"id": 1, "parent_path_id": None}]
    assert len(order_paths_by_lineage(rows)) == 2
