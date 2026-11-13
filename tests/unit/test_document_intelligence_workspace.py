from __future__ import annotations

from Api.services.document_intelligence import (
    extract_contact_occurrences,
    first_page_text,
    group_similar_documents,
    normalize_file_ids,
    spreadsheet_safe_text,
)


def test_selection_ids_are_positive_deduplicated_and_bounded():
    assert normalize_file_ids([7, "8", 7], max_files=3) == [7, 8]

    try:
        normalize_file_ids([0])
    except ValueError as error:
        assert "positive whole numbers" in str(error)
    else:
        raise AssertionError("zero must not become a file identifier")

    try:
        normalize_file_ids(list(range(1, 4)), max_files=2)
    except ValueError as error:
        assert "no more than 2" in str(error)
    else:
        raise AssertionError("oversized batch selections must be refused")

    try:
        normalize_file_ids([7, 7, 7], max_files=2)
    except ValueError as error:
        assert "no more than 2" in str(error)
    else:
        raise AssertionError("large duplicate payloads must not bypass the cap")


def test_spreadsheet_export_text_cannot_become_a_formula():
    assert spreadsheet_safe_text("=HYPERLINK('https://example.org')").startswith("'=")
    assert spreadsheet_safe_text("  @SUM(A1:A2)").startswith("'  @")
    assert spreadsheet_safe_text("alice@example.org") == "alice@example.org"


def test_first_page_fallback_respects_a_logical_form_feed_boundary():
    preview = first_page_text(
        file_name="memo.txt",
        file_path=None,
        extracted_text="first page\nmore text\fsecond page must not appear",
    )
    assert preview == {
        "text": "first page\nmore text",
        "method": "extracted-text-preview",
    }


def test_contact_extraction_deduplicates_values_per_file_and_counts_occurrences():
    text = (
        "Contact alice@example.org, then Alice@example.org again.\n"
        "Visit https://example.org/path?x=1)."
    )
    results = extract_contact_occurrences(text, file_id=11, file_name="notice.pdf")

    assert [(item["kind"], item["value"], item["occurrences"])
            for item in results] == [
                ("email", "alice@example.org", 2),
                ("url", "https://example.org/path?x=1", 1),
            ]
    assert all(item["file_id"] == 11 for item in results)
    assert results[0]["first_line"] == 1
    assert results[1]["first_line"] == 2
    assert results[0]["location"] == "Line 1"
    assert results[1]["location"] == "Line 2"
    assert "example.org" in results[1]["context"]


def test_similarity_groups_keep_order_and_cluster_related_text():
    documents = [
        {"id": 4, "content": "the quick brown fox jumps over the lazy dog"},
        {"id": 5, "content": "a quick brown fox jumps over a dog"},
        {"id": 6, "content": "database migrations and schema constraints"},
    ]

    groups = group_similar_documents(documents, threshold=0.4)

    assert groups[0]["file_ids"] == [4, 5]
    assert groups[0]["size"] == 2
    assert groups[0]["similarity"] >= 0.4
    assert groups[1]["file_ids"] == [6]


def test_similarity_grouping_rejects_an_invalid_threshold():
    try:
        group_similar_documents([], threshold=1.1)
    except ValueError as error:
        assert "between 0.05 and 0.95" in str(error)
    else:
        raise AssertionError("the grouping threshold must be bounded")
