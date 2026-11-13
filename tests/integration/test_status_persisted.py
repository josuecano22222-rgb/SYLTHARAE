"""Integration: processing status persisted end to end (task 3, §10).

The defect this closes: file_status was VARCHAR(10) in ('Read','Unread'), which
records only whether text exists. Measured on a real database, three files that
came back 'Unread' for three entirely different reasons were indistinguishable:

    corrupt.pdf   Unread   (the PDF could not be opened)
    icon.png      Unread   (below the reader's 50px floor - skipped by design)
    mystery.xyz   Unread   (the type could not be identified)

Migration 0007 added processing_status but nothing ever wrote it, so every row
sat at the 'discovered' default forever.

These tests ingest all three plus a clean file and assert the stored rows now
say which happened, and why.
"""

import datetime
import itertools
import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration
_SEQ = itertools.count()

PERMITTED = {
    "discovered", "queued", "processing", "processed", "partially_processed",
    "failed", "unsupported", "skipped", "retrying",
}


@pytest.fixture(scope="module")
def corpus(pg_db, tmp_path_factory):
    """Ingest a mixed corpus and return the stored status rows, keyed by name."""
    from pipeline.integrated_reader import IntegratedFileReader

    tag = f"_stat_{next(_SEQ)}"
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    today = datetime.date.today()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5, %s)"
            " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name",
            (f"{tag}_side", today),
        )
        cur.execute(
            "INSERT INTO sources (name, job, importance, country, date_creation)"
            " VALUES (%s, 't', 0.5, 't', %s) ON CONFLICT (name)"
            " DO UPDATE SET name = EXCLUDED.name",
            (f"{tag}_src", today),
        )
    conn.commit()

    root = tmp_path_factory.mktemp("status_corpus")
    (root / "good.txt").write_text("Marker STATUSGOOD clean extractable text\n" * 3)
    (root / "corrupt.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00garbage\xff\xfe" * 200)
    (root / "mystery.xyz").write_bytes(b"\x01\x02\x03\x04 bytes with no known magic")
    (root / "sales.csv").write_text("region,units\nEMEA,12\nAPAC,7\n")
    # 20x20: below the image reader's 50px floor, so it is skipped by design.
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", (20, 20), (10, 20, 30)).save(root / "icon.png")

    IntegratedFileReader(
        max_workers=1, enable_storage=True,
        storage_source=f"{tag}_src", storage_side=f"{tag}_side",
    ).process_folder(str(root))

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.file_name, p.file_status, p.processing_status,"
                " p.status_detail, p.attempts, p.status_updated_at"
                " FROM paths p JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN sides s ON s.id = hc.side_id WHERE s.name = %s",
                (f"{tag}_side",),
            )
            rows = {
                r[0]: {"file_status": r[1], "processing_status": r[2],
                       "status_detail": r[3], "attempts": r[4],
                       "status_updated_at": r[5]}
                for r in cur.fetchall()
            }
    finally:
        conn.close()
    return rows


def test_every_input_was_stored(corpus):
    assert sorted(corpus) == [
        "corrupt.pdf", "good.txt", "icon.png", "mystery.xyz", "sales.csv",
    ], sorted(corpus)


def test_no_row_is_left_at_the_discovered_default(corpus):
    """The point of the workstream: the default must not be the resting state."""
    stuck = [n for n, r in corpus.items() if r["processing_status"] == "discovered"]
    assert not stuck, f"still at the 'discovered' default: {stuck}"


def test_every_stored_state_is_permitted_by_the_check_constraint(corpus):
    for name, row in corpus.items():
        assert row["processing_status"] in PERMITTED, (name, row)


def test_clean_file_is_processed(corpus):
    assert corpus["good.txt"]["processing_status"] == "processed"


def test_unread_files_are_no_longer_indistinguishable(corpus):
    """The core defect: three 'Unread' files, three different outcomes."""
    unread = {
        n: r["processing_status"]
        for n, r in corpus.items() if r["file_status"] == "Unread"
    }
    assert len(unread) >= 3, unread
    assert "failed" in unread.values(), unread
    assert "skipped" in unread.values(), unread
    assert "unsupported" in unread.values(), unread
    # and they are not all the same value
    assert len(set(unread.values())) >= 3, unread


def test_corrupt_pdf_is_failed_and_says_why(corpus):
    row = corpus["corrupt.pdf"]
    assert row["processing_status"] == "failed"
    assert row["status_detail"], "a failure with no reason is not evidence"


def test_oversmall_image_is_skipped_not_failed(corpus):
    row = corpus["icon.png"]
    assert row["processing_status"] == "skipped"
    assert row["status_detail"]


def test_unrecognised_type_is_unsupported_not_failed(corpus):
    """Misclassification here would make a benign input look like a fault."""
    row = corpus["mystery.xyz"]
    assert row["processing_status"] == "unsupported", row


def test_structured_file_is_processed(corpus):
    assert corpus["sales.csv"]["processing_status"] == "processed"


def test_every_row_records_its_attempt_and_timestamp(corpus):
    for name, row in corpus.items():
        assert row["attempts"] >= 1, (name, row["attempts"])
        assert row["status_updated_at"] is not None, name


def test_status_is_queryable_for_triage(pg_db, corpus):
    """The model is only useful if failures can be found without reading rows."""
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT processing_status, COUNT(*) FROM paths"
                " WHERE id IN (SELECT p.id FROM paths p"
                " JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN sides s ON s.id = hc.side_id"
                " WHERE s.name LIKE %s) GROUP BY processing_status",
                ("_stat_%_side",),
            )
            counts = dict(cur.fetchall())
    finally:
        conn.close()
    assert counts.get("failed", 0) >= 1, counts
    assert counts.get("skipped", 0) >= 1, counts
    assert counts.get("unsupported", 0) >= 1, counts
