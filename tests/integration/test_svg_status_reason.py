"""Integration: an SVG's text is indexed, and an empty one says why.

Reported from a real ingest:

    [STORAGE] ⚠️ Image OCR Content - No text extracted | Attempted: False |
    Successful: False | Reason: unknown

``read_svg_file`` regexed only ``<text>`` and recorded no ``reason``, so the
storage layer printed the literal placeholder for a real file. Two things are
asserted here against the database, because that is where the operator looked:

* text that lives in the SVG markup (including nested ``<tspan>`` runs) is
  indexed and searchable;
* an SVG with nothing to extract reaches a terminal state whose detail names
  the reason instead of saying "unknown".
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

TEXT_SVG = "chart.svg"
EMPTY_SVG = "vector_only.svg"
SVG_MARKER = "SVGCHART 9914"

CHART = f"""<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">
  <title>Status Chart</title>
  <text x="10" y="20">{SVG_MARKER} <tspan>total</tspan></text>
</svg>
"""

VECTOR_ONLY = """<svg xmlns="http://www.w3.org/2000/svg">
  <path d="M0 0 L10 10"/><path d="M1 1 L2 2"/>
</svg>
"""


@pytest.fixture(scope="module")
def corpus(pg_db, tmp_path_factory):
    from pipeline.integrated_reader import IntegratedFileReader

    tag = f"_svg_{next(_SEQ)}"
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

    root = tmp_path_factory.mktemp("svg_corpus")
    (root / TEXT_SVG).write_text(CHART, encoding="utf-8")
    (root / EMPTY_SVG).write_text(VECTOR_ONLY, encoding="utf-8")

    IntegratedFileReader(
        max_workers=1, enable_storage=True,
        storage_source=f"{tag}_src", storage_side=f"{tag}_side",
    ).process_folder(str(root))

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.file_name, p.processing_status, p.status_detail,"
                " p.file_status, p.extraction_provenance"
                " FROM paths p JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN sides s ON s.id = hc.side_id WHERE s.name = %s",
                (f"{tag}_side",),
            )
            rows = {name: {"processing_status": status, "status_detail": detail,
                           "file_status": fstatus, "provenance": prov or {}}
                    for name, status, detail, fstatus, prov in cur.fetchall()}
    finally:
        conn.close()
    return rows


def test_svg_text_is_indexed_and_searchable(corpus):
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(query=SVG_MARKER, limit=10)
    assert total >= 1, "SVG text was not indexed"
    assert TEXT_SVG in [r.get("file_name") for r in results]


def test_svg_with_text_is_a_clean_success(corpus):
    row = corpus[TEXT_SVG]
    assert row["file_status"] == "Read", row
    assert row["processing_status"] == "processed", row


def test_svg_without_text_names_the_reason_in_its_status(corpus):
    """The reported placeholder must be gone: 'unknown' explains nothing."""
    row = corpus[EMPTY_SVG]
    assert row["processing_status"] == "processed", row      # read, no text
    detail = row["status_detail"] or ""
    assert "vector path element" in detail, detail
    assert "unknown" not in detail.lower(), detail


def test_the_reason_is_queryable_after_the_run(corpus):
    diagnostics = corpus[EMPTY_SVG]["provenance"].get("diagnostics") or {}
    assert diagnostics.get("reason", "").startswith("svg_has_no_text_elements"), (
        diagnostics
    )
