"""File types summary: the totals are dataset-wide and labelled as such.

The /files/types summary strip and the table's info line show every indexed
document and every format. They are NOT the visible rows: the table's column
filters only hide rows in the browser. These tests pin that the numbers are
the whole dataset (checked against independent SQL over ``paths``), that query
parameters cannot narrow them, and that the API declares the same scope.
"""
import re
import uuid

import pytest

from _seed import SeedLedger, connect, document, remove_seeded, side, source


@pytest.fixture(scope="module")
def seeded(pg_db):
    tag = "fts" + uuid.uuid4().hex[:8]
    ledger = SeedLedger()
    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            src, sd = source(cur), side(cur)
            ledger.sources.append(src)
            ledger.sides.append(sd)
            for ext, count in ((f"{tag}a", 3), (f"{tag}b", 2), ("   ", 1)):
                for i in range(count):
                    path_id, hash_id, context_id = document(
                        cur, source_id=src, side_id=sd,
                        text=f"{tag} {ext!r} {i}", file_type=ext)
                    ledger.paths.append(path_id)
                    ledger.hashes.append(hash_id)
                    ledger.contexts.append(context_id)
    finally:
        conn.close()
    yield tag
    remove_seeded(pg_db, ledger)


def db_totals(pg_db):
    """Documents and formats over the whole table, with the page's own rule
    for a blank format (it becomes 'Unknown')."""
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*),"
                " COUNT(DISTINCT COALESCE(NULLIF(BTRIM(file_type), ''), 'Unknown'))"
                " FROM paths")
            return cur.fetchone()
    finally:
        conn.close()


def summary_number(html, label):
    match = re.search(r"<strong>([\d,]+)</strong>\s*<span>" + re.escape(label) + "</span>", html)
    assert match, f"summary label not found: {label!r}"
    return int(match.group(1).replace(",", ""))


def row_counts(html):
    return [int(m.replace(",", "")) for m in
            re.findall(r'class="text-end file-type-count"[^>]*>([\d,]+)<', html)]


def test_the_summary_is_the_whole_dataset(admin_client, pg_db, seeded):
    total, formats = db_totals(pg_db)
    html = admin_client.get("/files/types").get_data(as_text=True)
    assert summary_number(html, "indexed documents in the whole dataset") == total
    assert summary_number(html, "detected formats in the whole dataset") == formats


def test_the_summary_is_labelled_dataset_wide_not_as_the_visible_rows(admin_client, seeded):
    html = admin_client.get("/files/types").get_data(as_text=True)
    assert "indexed documents in the whole dataset" in html
    assert "detected formats in the whole dataset" in html
    assert "Indexed file summary for the whole dataset" in html
    assert "detected formats across" in html and "in the whole dataset" in html


def test_query_parameters_do_not_narrow_the_summary(admin_client, pg_db, seeded):
    total, formats = db_totals(pg_db)
    for query in (f"?file_type={seeded}a", f"?q={seeded}", "?file_type=pdf&status=unread"):
        html = admin_client.get("/files/types" + query).get_data(as_text=True)
        assert summary_number(html, "indexed documents in the whole dataset") == total, query
        assert summary_number(html, "detected formats in the whole dataset") == formats, query


def test_the_rows_add_up_to_the_dataset_total(admin_client, pg_db, seeded):
    total, formats = db_totals(pg_db)
    html = admin_client.get("/files/types").get_data(as_text=True)
    counts = row_counts(html)
    assert len(counts) == formats
    assert sum(counts) == total


def test_the_api_declares_the_same_dataset_scope(admin_client, pg_db, seeded):
    total, formats = db_totals(pg_db)
    body = admin_client.get("/api/files/types").get_json()
    assert body["success"] is True
    assert body["total_files_scope"] == "dataset"
    assert body["total_files"] == total
    assert len(body["types"]) == formats
    assert sum(row["file_count"] for row in body["types"]) == body["total_files"]


def test_a_blank_format_is_counted_as_unknown(admin_client, seeded):
    body = admin_client.get("/api/files/types").get_json()
    unknown = [row for row in body["types"] if row["file_type"] == "Unknown"]
    assert unknown and unknown[0]["file_count"] >= 1
    assert unknown[0]["label"] == "Unknown format"
