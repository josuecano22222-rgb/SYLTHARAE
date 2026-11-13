"""Unit tests for structured folder export planning."""

from __future__ import annotations

import pytest

from Api.services.structured_folder_export import (
    build_relative_path,
    resolve_layout,
    _sanitize_segment,
)

pytestmark = pytest.mark.unit


def test_resolve_layout_presets():
    assert resolve_layout("source__analyst_category") == ["source", "analyst_category"]
    assert resolve_layout(["side", "file_type"]) == ["side", "file_type"]


def test_sanitize_segment_strips_unsafe_chars():
    assert _sanitize_segment('foo/bar:baz') == "foo_bar_baz"
    assert _sanitize_segment("") == "_Unassigned"


def test_build_relative_path_nested():
    row = {
        "path_id": 1,
        "source_name": "Alpha Source",
        "analyst_category_name": "Priority",
        "side_name": "",
        "file_type": "pdf",
        "file_date": None,
        "smart_category_name": "",
    }
    rel = build_relative_path(row, ["source", "analyst_category"], "report.pdf")
    assert rel == "Alpha Source/Priority/report.pdf"


def test_build_structured_export_plan_selection_empty():
    from Api.services.structured_folder_export import build_structured_export_plan

    plan = build_structured_export_plan(scope="selection", file_ids=[])
    assert plan["success"] is True
    assert plan["total"] == 0
    assert plan["entries"] == []
