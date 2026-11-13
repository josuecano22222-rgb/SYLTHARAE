"""Integration: a RAR archive ingested on a machine with no RAR decoder.

The defect, as reported from a real Windows ingest:

    WARNING reader_file.readers.read_archive: RAR extraction unavailable for
    C:\\Users\\Solo\\Desktop\\...\\Deleted Items.rar: RarCannotExec
    ...
    Extraction failed        (stored as Unread, 0 words)

66.5 MB of evidence was recorded as unreadable, and nothing in the log said
that the only missing piece was an external binary. The extraction was
all-or-nothing: the first member that needed a decoder aborted the whole
archive, and the reader turned the exception into ``None``.

This test runs the real pipeline (reader -> router -> storage -> PostgreSQL)
with no decoder present, on an archive whose first member is compressed, and
asserts the stored outcome the operator sees:

* the archive is stored as ``partially_processed`` with a detail naming how
  many members could not be read and why - not ``processed`` (a claim that the
  whole archive was read) and not ``failed`` (which discards the members that
  were read);
* members that could be read are ingested as children with lineage;
* the archive's own manifest is searchable, so the container's listing is
  evidence even when its members are not;
* the condition is queryable after the run through
  ``paths.extraction_provenance -> 'diagnostics'``;
* every row reaches a terminal state.
"""

import datetime
import itertools
import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.unit._rar_fixtures import rar5_archive  # noqa: E402

pytestmark = pytest.mark.integration

_SEQ = itertools.count()

ARCHIVE_NAME = "Deleted Items.rar"
CHILD_NAME = "evidence.txt"
CHILD_MARKER = "RAREVIDENCE 7742"
PACKED_NAME = "photo_archive.jpg"

#: Terminal states m0007 defines; a row in any other state means the run left
#: a file unfinished.
TERMINAL_STATES = {
    "processed", "partially_processed", "failed", "unsupported", "skipped",
}


@pytest.fixture(scope="module")
def corpus(pg_db, tmp_path_factory, monkeypatch_module):
    """Ingest one decoderless RAR; return the stored rows and connection info."""
    from pipeline.integrated_reader import IntegratedFileReader

    # No decoder on this machine: the support that the reported ingest ran on.
    monkeypatch_module.setattr(
        "core.archive_safety.find_rar_decoder", lambda: None
    )
    monkeypatch_module.setattr(
        "core.archive_safety.configure_rar_decoder", lambda: None
    )
    # rarfile also probes PATH itself (unrar, unar, 7z, bsdtar) and caches the
    # first tool that works, so a runner with any of them installed would
    # otherwise decode the archive and this would test a different machine.
    # Make its probe fail the way it does when nothing is installed.
    import rarfile

    def _no_tool(*_args, **_kwargs):
        raise rarfile.RarCannotExec("Cannot find working tool")

    monkeypatch_module.setattr(rarfile, "CURRENT_SETUP", None)
    monkeypatch_module.setattr(rarfile, "tool_setup", _no_tool)

    tag = f"_rar_{next(_SEQ)}"
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

    root = tmp_path_factory.mktemp("decoderless_rar")
    archive = root / ARCHIVE_NAME
    archive.write_bytes(rar5_archive([
        # Declared first so the defect triggers immediately: the compressed
        # member used to abort the extraction before the readable one was
        # reached.
        {"name": PACKED_NAME, "data": b"jpeg-bytes-needing-a-decoder", "method": 3},
        {"name": CHILD_NAME,
         "data": f"Marker {CHILD_MARKER} member stored uncompressed\n".encode()},
    ]))

    IntegratedFileReader(
        max_workers=1, enable_storage=True,
        storage_source=f"{tag}_src", storage_side=f"{tag}_side",
    ).process_folder(str(root))

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.id, p.file_name, p.parent_path_id, p.hierarchy_path,"
                " p.processing_status, p.status_detail, p.file_status,"
                " p.extraction_provenance"
                " FROM paths p JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN sides s ON s.id = hc.side_id WHERE s.name = %s",
                (f"{tag}_side",),
            )
            rows = {}
            for (pid, name, parent, hier, status, detail, fstatus,
                 prov) in cur.fetchall():
                rows.setdefault(name, []).append({
                    "id": pid, "parent_path_id": parent,
                    "hierarchy_path": hier, "processing_status": status,
                    "status_detail": detail, "file_status": fstatus,
                    "provenance": prov or {},
                })
    finally:
        conn.close()
    return {"tag": tag, "rows": rows, "conn_info": pg_db}


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch

    patcher = MonkeyPatch()
    yield patcher
    patcher.undo()


def _one(corpus, name):
    matches = corpus["rows"].get(name)
    assert matches, f"{name} was not stored; have {sorted(corpus['rows'])}"
    assert len(matches) == 1, f"{name} stored {len(matches)} times"
    return matches[0]


# ------------------------------------------------------------ stored outcome
def test_archive_row_is_stored_not_missing(corpus):
    """The reported failure produced a row; it must not be an opaque one."""
    row = _one(corpus, ARCHIVE_NAME)
    assert row["file_status"] == "Read", row


def test_partial_read_is_recorded_as_partially_processed(corpus):
    row = _one(corpus, ARCHIVE_NAME)
    assert row["processing_status"] == "partially_processed", (
        f"{row['processing_status']}: {row['status_detail']}"
    )


def test_status_detail_names_the_members_and_the_reason(corpus):
    detail = _one(corpus, ARCHIVE_NAME)["status_detail"]
    assert "1 of 2 archive members could not be read" in detail, detail
    assert "decoder required" in detail, detail
    assert "1 read" in detail, detail


def test_readable_member_was_ingested_as_a_child(corpus):
    archive = _one(corpus, ARCHIVE_NAME)
    child = _one(corpus, CHILD_NAME)
    assert child["parent_path_id"] == archive["id"]
    assert child["hierarchy_path"].split("::")[-1] == CHILD_NAME


def test_member_that_needed_a_decoder_was_not_invented(corpus):
    assert PACKED_NAME not in corpus["rows"]


def test_no_row_was_left_unfinished(corpus):
    for name, matches in corpus["rows"].items():
        for row in matches:
            assert row["processing_status"] in TERMINAL_STATES, (name, row)


# ------------------------------------------------------------ provenance
def test_the_condition_is_queryable_after_the_run(corpus):
    diagnostics = _one(corpus, ARCHIVE_NAME)["provenance"].get("diagnostics") or {}
    assert diagnostics.get("decoder_missing") is True, diagnostics
    assert diagnostics.get("members_total") == 2, diagnostics
    assert diagnostics.get("members_read") == 1, diagnostics
    assert diagnostics.get("members_unreadable") == {"decoder_required": 1}


def test_the_operator_can_find_every_archive_waiting_for_a_decoder(corpus):
    """The triage query an operator would actually run."""
    info = corpus["conn_info"]
    conn = psycopg2.connect(
        host=info["host"], port=info["port"], user=info["user"],
        password=info["password"], dbname=info["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.file_name FROM paths p"
                " WHERE p.extraction_provenance -> 'diagnostics'"
                "       ->> 'decoder_missing' = 'true'"
            )
            names = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    assert ARCHIVE_NAME in names, names


# ------------------------------------------------------------ searchability
def test_child_content_is_searchable(corpus):
    from Api.services.search_service import SearchService

    _, total = SearchService.full_text_search(query=CHILD_MARKER, limit=10)
    assert total >= 1, "the extracted member's content was not indexed"


def test_archive_manifest_is_searchable(corpus):
    """The container's own listing is evidence; it must reach the index."""
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(query="decoder", limit=20)
    assert total >= 1, "the archive's explanation was not indexed"
    assert ARCHIVE_NAME in [r.get("file_name") for r in results]
