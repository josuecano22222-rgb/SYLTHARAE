"""End-to-end progress accounting through the real processing engine.

Regression coverage for the reported defect: the progress indicator sat at 0%
for an entire run and then jumped to 100%, because the denominator only ever
counted top-level files and nested children were processed by a second reader
with private counters and no callback.

Every test drives the real ``IntegratedFileReader`` (storage disabled, so no
database is needed) and records the snapshot stream the engine pushes at the
job boundary - the same stream the API persists and the progress bar renders.
Recording via the callback rather than by sampling makes the assertions exact
and free of timing flakiness.

Workloads covered, per the verification matrix:
  * top-level files only
  * archive with multiple members
  * recursively nested archives
  * email with attachments
  * embedded Office objects
  * mixed nesting: email -> ZIP -> DOCX -> embedded image
  * unsupported / empty objects
  * a single container through ``process_single_file``
"""

import base64
import io
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.integrated_reader import IntegratedFileReader  # noqa: E402

BODY = "lorem ipsum dolor sit amet consectetur " * 40


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class Trace:
    """Records every snapshot the engine pushes through progress_callback."""

    def __init__(self):
        self.snapshots = []

    def __call__(self, snapshot):
        self.snapshots.append(dict(snapshot))

    @property
    def percents(self):
        return [s.get("percent", 0) for s in self.snapshots]

    @property
    def totals(self):
        return [s.get("total_files", 0) for s in self.snapshots]

    @property
    def intermediates(self):
        """Percents strictly between 0 and 100 - proof of real-time movement."""
        return [p for p in self.percents if 0 < p < 100]

    def last(self):
        return dict(self.snapshots[-1]) if self.snapshots else {}


def run_folder(root, workers=3):
    trace = Trace()
    reader = IntegratedFileReader(
        max_workers=workers, enable_monitoring=False, enable_storage=False
    )
    reader.progress_callback = trace
    reader.process_folder(str(root))
    return reader.get_live_progress(), trace


def run_single(path, workers=1):
    trace = Trace()
    reader = IntegratedFileReader(
        max_workers=workers, enable_monitoring=False, enable_storage=False
    )
    reader.progress_callback = trace
    reader.process_single_file(str(path))
    return reader.get_live_progress(), trace


def write_text(path, body=BODY):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(body)


def make_zip(zip_path, entries):
    """entries: {arcname: bytes}"""
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)


def make_eml(path, attachments):
    """attachments: [(filename, content_type, bytes)]"""
    boundary = "PROGRESSBOUNDARY"
    buf = io.StringIO()
    buf.write(
        "From: sender@example.com\r\nTo: recipient@example.com\r\n"
        "Subject: progress accounting\r\nMIME-Version: 1.0\r\n"
        f'Content-Type: multipart/mixed; boundary="{boundary}"\r\n\r\n'
    )
    buf.write(
        f"--{boundary}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        "Message body that must survive alongside the attachments.\r\n"
    )
    for filename, ctype, payload in attachments:
        buf.write(
            f"--{boundary}\r\nContent-Type: {ctype}\r\n"
            "Content-Transfer-Encoding: base64\r\n"
            f'Content-Disposition: attachment; filename="{filename}"\r\n\r\n'
        )
        buf.write(base64.encodebytes(payload).decode() + "\r\n")
    buf.write(f"--{boundary}--\r\n")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(buf.getvalue())


def png_bytes():
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (60, 30), (200, 40, 40)).save(buf, format="PNG")
    return buf.getvalue()


def docx_bytes(with_image=True):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Body text that must survive the container merge.")
    if with_image:
        document.add_picture(io.BytesIO(png_bytes()))
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# The progress contract, asserted the same way for every workload
# ---------------------------------------------------------------------------
def assert_progress_contract(final, trace, min_total, expect_nested=True):
    assert final["total_files"] >= min_total, (
        f"denominator {final['total_files']} below the real workload {min_total}"
    )
    # Every discovered unit reached a terminal state.
    assert final["files_done"] == final["total_files"]
    assert final["files_pending"] == 0
    assert final["in_progress"] == 0
    assert final["complete"] is True
    assert final["percent"] == 100

    # Real-time: intermediate values were pushed *during* the run.
    assert len(trace.snapshots) > 2, "progress was pushed only at the boundaries"
    assert trace.intermediates, (
        f"no intermediate percentages were emitted: {trace.percents}"
    )

    # Monotonic: the bar never retreats, even as the denominator grows.
    assert all(b >= a for a, b in zip(trace.percents, trace.percents[1:], strict=False)), (
        f"percent went backwards: {trace.percents}"
    )
    assert all(b >= a for a, b in zip(trace.totals, trace.totals[1:], strict=False)), (
        f"total shrank: {trace.totals}"
    )

    # 100% is never reported before the end.
    first_full = next(
        (i for i, p in enumerate(trace.percents) if p == 100), None
    )
    if first_full is not None:
        assert all(p == 100 for p in trace.percents[first_full:]), (
            "100% was reported and then work continued"
        )

    if expect_nested:
        assert final["files_nested"] > 0
        assert final["files_initial"] < final["files_discovered"]
        assert final["containers_opened"] >= 1
        assert final["children_by_parent"], "nested children lost their attribution"
        assert len(set(trace.totals)) > 1, (
            f"total never grew during the run: {trace.totals}"
        )


# ---------------------------------------------------------------------------
# 1. Top-level files only
# ---------------------------------------------------------------------------
def test_flat_folder_reports_real_time_progress():
    root = Path(tempfile.mkdtemp(prefix="flat_"))
    try:
        for i in range(4):
            write_text(str(root / f"doc{i}.txt"))
        final, trace = run_folder(root)

        assert_progress_contract(final, trace, min_total=4, expect_nested=False)
        assert final["files_completed"] == 4
        assert final["files_nested"] == 0
        assert final["total_files"] == 4
        assert len(set(trace.totals)) == 1, "a flat workload has a static total"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2. Archive with multiple members
# ---------------------------------------------------------------------------
def test_archive_members_are_counted_and_total_grows():
    root = Path(tempfile.mkdtemp(prefix="arch_"))
    try:
        make_zip(str(root / "bundle.zip"),
                 {f"m{i}.txt": BODY.encode() for i in range(5)})
        write_text(str(root / "top.txt"))
        final, trace = run_folder(root)

        # 1 top-level txt + 1 zip + 5 members
        assert_progress_contract(final, trace, min_total=7)
        assert final["files_initial"] == 2
        assert final["files_nested"] == 5
        assert final["children_by_parent"] == {str(root / "bundle.zip"): 5}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_archive_only_workload_no_longer_reads_as_one_unit():
    """The headline symptom: a single archive used to be 1 unit of work."""
    root = Path(tempfile.mkdtemp(prefix="archonly_"))
    try:
        make_zip(str(root / "only.zip"),
                 {f"m{i}.txt": BODY.encode() for i in range(8)})
        final, trace = run_folder(root)

        assert final["total_files"] == 9      # 1 zip + 8 members
        assert final["files_initial"] == 1
        assert final["files_nested"] == 8
        assert_progress_contract(final, trace, min_total=9)
        # The bar must have moved well before the archive finished.
        assert len(trace.intermediates) >= 4
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. Recursively nested archives
# ---------------------------------------------------------------------------
def test_recursively_nested_archives_are_all_counted():
    root = Path(tempfile.mkdtemp(prefix="nest_"))
    try:
        level3 = io.BytesIO()
        with zipfile.ZipFile(level3, "w") as zf:
            for i in range(2):
                zf.writestr(f"deep{i}.txt", BODY)
        level2 = io.BytesIO()
        with zipfile.ZipFile(level2, "w") as zf:
            for i in range(2):
                zf.writestr(f"mid{i}.txt", BODY)
            zf.writestr("level3.zip", level3.getvalue())
        level1 = io.BytesIO()
        with zipfile.ZipFile(level1, "w") as zf:
            zf.writestr("level2.zip", level2.getvalue())

        (root / "level1.zip").write_bytes(level1.getvalue())
        write_text(str(root / "top.txt"))
        final, trace = run_folder(root)

        # top.txt + level1 + level2 + level3 + 2 mid + 2 deep = 8
        assert_progress_contract(final, trace, min_total=8)
        assert final["containers_opened"] == 3
        assert final["files_initial"] == 2
        assert final["files_nested"] == 6

        # Each level's children stay attributable to their own container.
        by_parent = {os.path.basename(k): v
                     for k, v in final["children_by_parent"].items()}
        assert by_parent == {"level1.zip": 1, "level2.zip": 3, "level3.zip": 2}

        # The denominator grew stepwise, not all at once.
        distinct = sorted(set(trace.totals))
        assert len(distinct) >= 3, f"total did not grow progressively: {distinct}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. Email with attachments
# ---------------------------------------------------------------------------
def test_email_attachments_are_counted():
    root = Path(tempfile.mkdtemp(prefix="email_"))
    try:
        make_eml(str(root / "message.eml"), [
            (f"att{i}.txt", "text/plain", BODY.encode()) for i in range(3)
        ])
        final, trace = run_folder(root)

        # 1 eml + 3 attachments
        assert_progress_contract(final, trace, min_total=4)
        assert final["files_initial"] == 1
        assert final["files_nested"] == 3
        assert final["children_by_parent"] == {str(root / "message.eml"): 3}
        assert final["files_failed"] == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_email_attachment_that_is_itself_an_archive_recurses():
    root = Path(tempfile.mkdtemp(prefix="emailzip_"))
    try:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as zf:
            for i in range(3):
                zf.writestr(f"inner{i}.txt", BODY)
        make_eml(str(root / "thread.eml"), [
            ("payload.zip", "application/zip", payload.getvalue()),
            ("note.txt", "text/plain", BODY.encode()),
        ])
        final, trace = run_folder(root)

        # eml + zip + note.txt + 3 inner = 6
        assert_progress_contract(final, trace, min_total=6)
        assert final["containers_opened"] == 2
        assert final["files_nested"] == 5
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. Embedded Office objects
# ---------------------------------------------------------------------------
def test_embedded_office_objects_are_counted():
    root = Path(tempfile.mkdtemp(prefix="docx_"))
    try:
        (root / "report.docx").write_bytes(docx_bytes(with_image=True))
        write_text(str(root / "top.txt"))
        final, trace = run_folder(root)

        # top.txt + report.docx + at least the embedded image
        assert_progress_contract(final, trace, min_total=3)
        assert final["files_initial"] == 2
        assert final["files_nested"] >= 1
        assert str(root / "report.docx") in final["children_by_parent"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 6. Mixed nesting: email -> ZIP -> DOCX -> embedded image
# ---------------------------------------------------------------------------
def test_mixed_nesting_email_zip_docx_image():
    root = Path(tempfile.mkdtemp(prefix="mixed_"))
    try:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as zf:
            zf.writestr("inner.docx", docx_bytes(with_image=True))
            for i in range(2):
                zf.writestr(f"side{i}.txt", BODY)
        make_eml(str(root / "thread.eml"), [
            ("payload.zip", "application/zip", payload.getvalue()),
        ])
        final, trace = run_folder(root)

        # eml + zip + docx + 2 sides + >=1 embedded image = >= 6
        assert_progress_contract(final, trace, min_total=6)
        assert final["files_initial"] == 1
        assert final["containers_opened"] >= 3

        by_parent = {os.path.basename(k): v
                     for k, v in final["children_by_parent"].items()}
        assert by_parent.get("thread.eml") == 1
        assert by_parent.get("payload.zip") == 3
        assert by_parent.get("inner.docx", 0) >= 1

        # The total must have grown at several distinct points.
        assert len(sorted(set(trace.totals))) >= 3
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 7. Unsupported / failed objects stay in the accounting
# ---------------------------------------------------------------------------
def test_unsupported_objects_are_counted_not_dropped():
    root = Path(tempfile.mkdtemp(prefix="unsup_"))
    try:
        write_text(str(root / "good.txt"))
        (root / "mystery.zzzq").write_bytes(b"\x00\x01\x02not a format\x03" * 60)
        (root / "empty.bin").write_bytes(b"")
        final, trace = run_folder(root)

        assert final["total_files"] == 3
        assert final["files_done"] == 3, "unsupported objects vanished from the total"
        assert final["files_pending"] == 0
        assert final["percent"] == 100
        assert final["complete"] is True
        assert (final["files_unsupported"] + final["files_failed"]) == 2
        assert final["files_completed"] == 1
        assert trace.intermediates
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unsupported_object_inside_an_archive_is_still_counted():
    root = Path(tempfile.mkdtemp(prefix="unsupnest_"))
    try:
        make_zip(str(root / "mixed.zip"), {
            "ok.txt": BODY.encode(),
            "weird.zzzq": b"\x00\x01not a format" * 60,
        })
        final, trace = run_folder(root)

        # 1 zip + 2 members, all terminal
        assert final["total_files"] == 3
        assert final["files_done"] == 3
        assert final["files_pending"] == 0
        assert final["percent"] == 100
        assert final["files_nested"] == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 8. Single container through process_single_file
# ---------------------------------------------------------------------------
def test_single_archive_via_process_single_file():
    """The worst reported case: total stayed 0, then the job stamped 100%."""
    root = Path(tempfile.mkdtemp(prefix="single_"))
    try:
        make_zip(str(root / "bundle.zip"),
                 {f"m{i}.txt": BODY.encode() for i in range(5)})
        final, trace = run_single(root / "bundle.zip")

        assert final["total_files"] == 6, "single-file path lost the nested total"
        assert final["files_initial"] == 1
        assert final["files_nested"] == 5
        assert_progress_contract(final, trace, min_total=6)

        # The very first snapshot must already carry a denominator, not 0/0.
        assert trace.snapshots[0]["total_files"] >= 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_single_plain_file_reports_one_unit_immediately():
    root = Path(tempfile.mkdtemp(prefix="singleflat_"))
    try:
        target = root / "note.txt"
        write_text(str(target))
        final, trace = run_single(target)

        assert final["total_files"] == 1
        assert final["files_done"] == 1
        assert final["percent"] == 100
        assert trace.snapshots[0]["total_files"] == 1, (
            "the denominator must exist before the file finishes"
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Structural guarantees
# ---------------------------------------------------------------------------
def test_nested_reader_shares_the_parent_ledger():
    """A nested reader must not keep private counters nobody reads."""
    root = Path(tempfile.mkdtemp(prefix="shared_"))
    try:
        make_zip(str(root / "bundle.zip"),
                 {f"m{i}.txt": BODY.encode() for i in range(3)})
        trace = Trace()
        reader = IntegratedFileReader(
            max_workers=2, enable_monitoring=False, enable_storage=False
        )
        reader.progress_callback = trace
        reader.process_folder(str(root))

        # The parent's own ledger saw the children, so they were not accounted
        # in some second reader's private counters.
        snap = reader.progress_ledger.snapshot()
        assert snap["files_nested"] == 3
        assert snap["total_files"] == 4
        assert reader.get_live_progress()["files_nested"] == 3
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_sequential_worker_path_also_reports_progress():
    """max_workers == 1 previously notified nothing at all."""
    root = Path(tempfile.mkdtemp(prefix="seq_"))
    try:
        for i in range(4):
            write_text(str(root / f"doc{i}.txt"))
        final, trace = run_folder(root, workers=1)

        assert final["total_files"] == 4
        assert final["files_done"] == 4
        assert final["percent"] == 100
        assert len(trace.snapshots) > 2
        assert trace.intermediates, "sequential path emitted no live progress"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_parent_and_children_are_not_double_counted():
    root = Path(tempfile.mkdtemp(prefix="nocount_"))
    try:
        make_zip(str(root / "bundle.zip"),
                 {f"m{i}.txt": BODY.encode() for i in range(4)})
        final, _ = run_folder(root)

        # Exactly 1 container + 4 members. A double-counting bug shows up as
        # files_done exceeding total_files or as a total of 10.
        assert final["total_files"] == 5
        assert final["files_done"] == 5
        assert final["files_completed"] == 5
        assert final["files_pending"] == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_statistics_view_agrees_with_live_progress():
    """get_statistics() is a compatibility view; it must not diverge."""
    root = Path(tempfile.mkdtemp(prefix="view_"))
    try:
        make_zip(str(root / "bundle.zip"),
                 {f"m{i}.txt": BODY.encode() for i in range(3)})
        reader = IntegratedFileReader(
            max_workers=2, enable_monitoring=False, enable_storage=False
        )
        reader.process_folder(str(root))

        stats = reader.get_statistics()
        live = reader.get_live_progress()
        assert stats["total"] == live["total_files"] == 4
        assert stats["done"] == live["files_done"] == 4
        assert stats["nested"] == live["files_nested"] == 3
        assert stats["in_progress"] == live["in_progress"] == 0
        assert stats["percent"] == live["percent"] == 100
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_progress_left_pending_after_a_cancel():
    """Cancelled work must still reach a terminal state, not stay queued."""
    root = Path(tempfile.mkdtemp(prefix="cancel_"))
    try:
        for i in range(6):
            write_text(str(root / f"doc{i}.txt"))
        reader = IntegratedFileReader(
            max_workers=1, enable_monitoring=False, enable_storage=False
        )
        reader.request_cancel()
        reader.process_folder(str(root))

        snap = reader.get_live_progress()
        assert snap["files_pending"] == 0, "cancelled files stayed queued forever"
        assert snap["in_progress"] == 0
        assert snap["files_done"] == snap["total_files"]
        assert snap["percent"] == 100
        # Cancellation is its own terminal state, not SKIPPED. Conflating the
        # two made "we stopped on request" indistinguishable from "we chose not
        # to process this" in reports and in discovered=filed reconciliation.
        assert snap["files_cancelled"] > 0, snap
        assert snap["files_skipped"] == 0, snap
        assert (snap["files_completed"] + snap["files_failed"] + snap["files_skipped"]
                + snap["files_unsupported"] + snap.get("files_retryable", 0)
                + snap["files_cancelled"] == snap["total_files"]), snap
    finally:
        shutil.rmtree(root, ignore_errors=True)
