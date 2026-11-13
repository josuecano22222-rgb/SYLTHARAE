"""Analyst CSV export column allowlist."""

from __future__ import annotations

import pytest

from Api.services.analyst_categories import AnalystCategoryService

pytestmark = pytest.mark.unit


def test_resolve_export_columns_defaults_to_full_set():
    cols = AnalystCategoryService.resolve_export_columns(None)
    assert [c[0] for c in cols] == [c[0] for c in AnalystCategoryService.ANALYST_CSV_COLUMNS]


def test_resolve_export_columns_subset_preserves_canonical_order():
    cols = AnalystCategoryService.resolve_export_columns("file_name,analyst_category,file_id")
    assert [c[0] for c in cols] == ["file_id", "file_name", "analyst_category"]


def test_resolve_export_columns_rejects_unknown():
    with pytest.raises(ValueError, match="Unknown export column"):
        AnalystCategoryService.resolve_export_columns("file_name,not_a_column")
