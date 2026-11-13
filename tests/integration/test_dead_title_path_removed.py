"""Regression: the dead title-storage path must not come back.

`StoragePipeline._store_title_pipeline` called `self.db_hub.word_operations`
and `.title_operations`. Neither attribute exists on `DatabaseHub` - verified:

    hasattr(DatabaseHub, 'word_operations')  -> False
    hasattr(DatabaseHub, 'title_operations') -> False
    hasattr(DatabaseHub, '__getattr__')      -> False

so it raised AttributeError on every file that had a title, was swallowed by a
broad `except Exception`, logged
"Error in title storage pipeline: 'DatabaseHub' object has no attribute
'word_operations'", and returned False. Titles were in fact stored by
`contents_db_service.create_title_content`. It was deleted rather than repaired.

These tests assert both halves: the dead code is gone, and the live path still
writes titles without logging that error.
"""

import datetime
import logging
import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration


def test_dead_method_is_gone():
    from pipeline.storage_pipeline import StoragePipeline

    assert not hasattr(StoragePipeline, "_store_title_pipeline"), (
        "_store_title_pipeline was dead code that logged an AttributeError on "
        "every file with a title; it was deleted, not repaired"
    )


def test_pipeline_does_not_call_the_missing_hub_attributes_for_titles():
    """Guard against reintroducing the exact broken call."""
    source = (
        PROJECT_ROOT / "pipeline" / "storage_pipeline.py"
    ).read_text(encoding="utf-8")
    assert "db_hub.title_operations" not in source


def test_database_hub_really_lacks_those_attributes():
    """The premise of the deletion, asserted rather than assumed."""
    from database import DatabaseHub

    for attribute in ("word_operations", "title_operations"):
        assert not hasattr(DatabaseHub, attribute), attribute
    assert not hasattr(DatabaseHub, "__getattr__")


def test_titles_are_stored_without_the_error_log(pg_db, tmp_path, caplog):
    """The live path must still write titles_content, with no swallowed error."""
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    today = datetime.date.today()
    tag = f"_deadtitle_{today}"
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation)"
                " VALUES (%s, 0.5, %s) ON CONFLICT (name)"
                " DO UPDATE SET name = EXCLUDED.name",
                (f"{tag}_side", today),
            )
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES (%s, 't', 0.5, 't', %s) ON CONFLICT (name)"
                " DO UPDATE SET name = EXCLUDED.name",
                (f"{tag}_src", today),
            )
        conn.commit()

        pymupdf = pytest.importorskip("pymupdf")
        doc = pymupdf.open()
        doc.new_page().insert_text(
            (72, 72),
            "Quarterly Ledger Reconciliation Report for the period ending March "
            "with a variance requiring auditor review before the close.",
            fontsize=11,
        )
        path = tmp_path / "titled.pdf"
        path.write_bytes(doc.tobytes())
        doc.close()

        from pipeline.integrated_reader import IntegratedFileReader

        with caplog.at_level(logging.ERROR, logger="pipeline.storage_pipeline"):
            IntegratedFileReader(
                max_workers=1, enable_storage=True,
                storage_source=f"{tag}_src", storage_side=f"{tag}_side",
            ).process_folder(str(tmp_path))

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM titles_content")
            assert cur.fetchone()[0] >= 1, "no title row was written"
    finally:
        conn.close()

    assert not any(
        "Error in title storage pipeline" in r.getMessage() for r in caplog.records
    ), "the deleted dead path logged again"
