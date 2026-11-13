"""A capped read must be distinguishable from a complete one.

`Spreadsheets/testRecordSizeExceeded.xlsx` is read up to 250,000 cells and then
stopped (measured: 10,000,085 characters stored, `truncated: True` on the
reader's result). That cap is deliberate - F-11 records the 181 MB text body a
12 MB workbook produced before it existed - but the fact that it was hit was
not persisted: `extraction_provenance.diagnostics` whitelisted error/reason/
skip keys and nothing about scale, so after ingestion a capped sheet and a
complete sheet look identical. A completeness claim that cannot see its own
truncation is the failure mode this whole audit exists to remove.
"""
from pipeline.storage_pipeline import StoragePipeline


def provenance(content):
    pipeline = object.__new__(StoragePipeline)
    return pipeline._build_extraction_provenance(content)


def test_a_truncated_spreadsheet_says_so_in_provenance():
    content = {
        "sheets": {"Sheet": {"data": [["a"]], "truncated": True,
                             "cells_read": 249990, "cells_available": 3000000}},
        "truncated": True,
        "cells_read": 249990,
    }
    scale = (provenance(content) or {}).get("scale") or {}
    assert scale.get("truncated") is True
    assert scale.get("cells_read") == 249990
    assert scale.get("cells_available") == 3000000


def test_an_untruncated_read_does_not_claim_truncation():
    content = {"sheets": {"Sheet": {"data": [["a"]]}}, "cells_read": 1}
    scale = (provenance(content) or {}).get("scale") or {}
    assert "truncated" not in scale


def test_html_inline_code_truncation_is_already_recorded():
    content = {"text_content": "text",
               "extraction_info": {"inline_code_truncated": True}}
    diagnostics = (provenance(content) or {}).get("diagnostics") or {}
    assert diagnostics.get("inline_code_truncated") is True
