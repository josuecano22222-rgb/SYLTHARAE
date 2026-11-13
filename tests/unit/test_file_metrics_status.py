"""Unit: the per-file metrics block tells the truth about the read.

Reported from an image-suite ingest: a reader failed with

    [EXTRACTION] ❌ Error processing fake-png.png: cannot identify image file

and the metrics block for the same file, printed immediately afterwards, said

    Status:   ✓ Success

The block looked only at ``Content.error``, while ``read_img_fast`` reports its
failures inside ``Content.extraction_info`` and the router passes a reader's
payload through unchanged. An operator scanning the log saw a success line for
a file that had just failed.
"""

import contextlib
import io
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.time_utils import calculate_file_processing_metrics  # noqa: E402


def _metrics_for(result):
    file_info = {"path": "/tmp/file.bin", "size_bytes": 413}
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        calculate_file_processing_metrics(file_info, result)
    return buffer.getvalue()


def _result(content):
    return {
        "Metadata": {
            "path": "/tmp/file.bin",
            "name": "file.bin",
            "processing_time": "0.0700 seconds",
        },
        "Content": content,
    }


def test_error_inside_extraction_info_is_reported_as_a_failure():
    output = _metrics_for(_result({
        "text": "",
        "extraction_info": {
            "error": "cannot identify image file",
            "extracted": False,
        },
    }))
    assert "✗ Failed" in output
    assert "cannot identify image file" in output


def test_error_at_the_top_level_is_still_reported():
    output = _metrics_for(_result({"error": "Unsupported file type: .zzz"}))
    assert "✗ Failed" in output
    assert "Unsupported file type" in output


def test_a_clean_read_is_still_a_success():
    output = _metrics_for(_result({"text": "hello"}))
    assert "✓ Success" in output
    assert "✗ Failed" not in output


def test_a_deliberate_skip_is_not_a_failure():
    """The size-floor skip is recorded as a skip; it did not go wrong."""
    output = _metrics_for(_result({
        "text": "",
        "extraction_info": {"skipped": True, "skip_reason": "too_small"},
    }))
    assert "✓ Success" in output
    assert "✗ Failed" not in output
