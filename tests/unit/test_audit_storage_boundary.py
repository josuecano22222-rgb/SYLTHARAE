"""What a reader produces is not automatically what the pipeline stores.

Phase 0.4 of the audit asks where the storage boundary lies: which keys of a
reader's content dict reach the stored text. The answer has to come from the
code that does the storing, ``StoragePipeline._extract_text_from_content``, so
it cannot drift from it - these tests read that function's syntax tree and pin
both directions.

Measured consequence: the PDF reader returns ``revisions``, ``success_rate``,
``processing_time``, ``avg_chars_per_page`` and ``methods_used``, and none of
them is consumed; a PDF's revision history is the one of those that is content.
"""
from tools.audit.archive_extraction import (
    _storage_keys,
    storage_boundary,
    storage_consumed_keys,
)


def test_the_consumed_set_is_read_from_the_storing_function():
    keys = _storage_keys()
    # Content categories the pipeline demonstrably gates on.
    for key in ("pages", "sheets", "slides", "paragraphs", "text_content",
                "email_content", "extracted_images", "rows"):
        assert key in keys, f"{key} should be consumed by _extract_text_from_content"


def test_values_the_function_tests_are_not_mistaken_for_keys():
    keys = _storage_keys()
    # 'Normal' is a value compared on the way through, not a content key; an
    # over-collecting scan would hide real loss by marking it as persisted.
    assert "Normal" not in keys
    assert "SQLite" not in keys


def test_a_key_the_pipeline_never_reads_is_reported_as_unpersisted():
    boundary = storage_boundary({"pages": [{"page_number": 1, "text": "x"}],
                                 "revisions": ["created", "edited"]})
    assert "pages" not in boundary["keys_not_persisted"]
    assert "revisions" in boundary["keys_not_persisted"]


def test_empty_values_are_not_counted_as_captured_content():
    boundary = storage_boundary({"pages": [], "text": "", "tables": None})
    assert boundary["content_keys"] == []
    assert boundary["keys_not_persisted"] == []


def test_a_non_dict_content_is_handled_without_raising():
    assert storage_boundary("not a dict")["content_keys"] == []


def test_the_scan_is_stable_between_calls():
    assert storage_consumed_keys() == sorted(_storage_keys())
