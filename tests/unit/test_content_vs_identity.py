"""Content and metadata must not be confused with one another.

Regression guards for a defect that came in with the identity/provenance work:
the flattened identity record (declared name and extension, detected format,
MIME type, discrepancies) was appended to the artifact's *content* text. A file
that yielded no content at all therefore looked readable - the skipped 20x20
icon in tests/integration/test_status_persisted.py was stored as
``file_status='Read'`` with 202 characters of "content" that were nothing but
labels such as ``Original name: icon.png`` - the word index gained ~10
boilerplate labels per artifact, and the labels were shown to the examiner as
though they were file content.

The identity record is still recorded per artifact (that is what the provenance
tests cover); what these tests pin is that it is recorded *as metadata*, and
that only real extracted content - including the readers' forensic text, which
is content - reaches the content channel.
"""

import sys
import typing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.storage_pipeline import StoragePipeline  # noqa: E402


def _text(content):
    """Run the real content-extraction path on a real, uninitialised instance.

    The method reads ``self._media_metadata_text`` (reader provenance, 1c60be5),
    so passing ``None`` used to raise inside a broad ``except`` and return "";
    ``object.__new__`` gives a real instance without opening a database.
    """
    return StoragePipeline._extract_text_from_content(
        object.__new__(StoragePipeline), content)


def _detection_payload():
    """What the router attaches to every artifact's result."""
    return {
        "type_detection": {
            "declared_name": "icon.png",
            "declared_extension": ".png",
            "detected_extension": ".png",
            "format_id": "image.png",
            "format_family": "image",
            "mime_type": "image/png",
            "detection_method": "magic-bytes",
            "detection_confidence": "certain",
            "extension_mismatch": False,
            "features": {},
        }
    }


def test_identity_metadata_alone_is_not_content():
    """The exact shape that used to be stored as 202 characters of content."""
    assert _text(_detection_payload()) == ""


def test_identity_metadata_does_not_displace_real_content():
    content = _detection_payload()
    content["content"] = "the only real sentence"
    text = _text(content)
    assert "the only real sentence" in text
    for label in ("Original name", "Declared extension", "MIME type"):
        assert label not in text, "metadata was rendered as file content"


def test_forensic_text_is_content_and_is_still_joined():
    """Comments, tracked changes and formulas are content, not metadata."""
    content = _detection_payload()
    content["content"] = "body text"
    content["forensic_text"] = "deleted paragraph: payments to the ministry"
    text = _text(content)
    assert "deleted paragraph" in text
    assert "Original name" not in text


def test_identity_is_still_recorded_as_structured_metadata():
    """Removing it from the content channel must not lose the record."""
    provenance = StoragePipeline._build_extraction_provenance(
        None, _detection_payload()
    )
    assert provenance is not None, "identity record disappeared entirely"
    detection = provenance.get("detection")
    assert detection, "detection record missing from provenance"
    assert detection.get("declared_name") == "icon.png"
    assert detection.get("mime_type") == "image/png"


def test_metadata_only_artifact_is_not_reported_as_having_text():
    status, detail = StoragePipeline._resolve_processing_status(
        None, _detection_payload(), "Unread"
    )
    assert status == "processed", status
    assert detail == "no extractable text", detail


def test_skipped_artifact_stays_skipped_even_with_identity_metadata():
    content = _detection_payload()
    content["extraction_info"] = {"skipped": True, "skip_reason": "too_small"}
    status, detail = StoragePipeline._resolve_processing_status(
        None, content, "Unread"
    )
    assert status == "skipped"
    assert detail == "too_small"


def test_compute_annotation_names_resolve():
    """Annotations must name types the module can actually resolve."""
    from core.compute.gateway import AcceleratorBackend
    from core.compute.routing import accelerator_memory_fits

    hints = typing.get_type_hints(AcceleratorBackend)
    assert "workloads" in hints
    assert typing.get_type_hints(accelerator_memory_fits)
