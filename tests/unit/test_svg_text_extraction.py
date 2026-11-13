"""Unit: SVG text extraction and the reason every empty result carries.

The defect this file guards (reported from a real ingest):

    [STORAGE] ⚠️  Image OCR Content - No text extracted | Attempted: False |
    Successful: False | Reason: unknown

``read_svg_file`` regexed only ``<text>...</text>`` and reported no ``reason``
key at all, so the storage layer fell back to the literal string "unknown" for
a real file. "Reason: unknown" is worse than a refusal: it records that a
condition happened while withholding what it was.

What must hold, and is asserted here:

* text is read from the document structure - nested ``<tspan>`` runs,
  ``<title>``/``<desc>``, editor flow trees - with entities decoded and no
  markup leaking into the index;
* a well-formed document whose text is not inside text elements still yields
  its text, labelled as recovered rather than parsed;
* a malformed document does not lose its text;
* every outcome states a reason, so "unknown" can no longer appear;
* content the reader does not turn into text (embedded raster images, vector
  paths, embedded script/style) is counted and named.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reader_file.readers.read_img_fast import ImageFileReader  # noqa: E402


@pytest.fixture
def reader():
    return ImageFileReader()


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _read(reader, path):
    return reader.read_svg_file(str(path))


CHART = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600"
     viewBox="0 0 800 600">
  <title>Status Chart &amp; Legend</title>
  <desc>Quarterly revenue - Q3 2024</desc>
  <g><text x="10" y="20">Total: <tspan font-weight="bold">42</tspan> units</text></g>
  <text x="10" y="40">Second line</text>
  <flowRoot><flowPara>Editor flow text</flowPara></flowRoot>
</svg>
"""


class TestTextExtraction:
    def test_nested_runs_and_metadata_elements_are_read(self, reader, tmp_path):
        result = _read(reader, _write(tmp_path, "chart.svg", CHART))
        assert result["text"] == (
            "Status Chart & Legend\n"
            "Quarterly revenue - Q3 2024\n"
            "Total: 42 units\n"
            "Second line\n"
            "Editor flow text"
        )
        assert "<tspan" not in result["text"]      # no markup in the index
        assert result["ocr_successful"] is True

    def test_extraction_info_counts_what_was_read(self, reader, tmp_path):
        info = _read(reader, _write(tmp_path, "chart.svg", CHART))["extraction_info"]
        assert info["reason"] == "svg_text_extracted"
        assert info["text_elements"] == 5
        assert info["word_count"] == len(
            "Status Chart & Legend Quarterly revenue - Q3 2024 Total: 42 units "
            "Second line Editor flow text".split()
        )
        assert info["image_width"] == "800"
        assert info["image_height"] == "600"
        assert info["viewBox"] == "0 0 800 600"
        assert info["file_type"] == "SVG"

    def test_entities_and_non_ascii_survive(self, reader, tmp_path):
        svg = ('<svg><text>Fish &amp; chips \u2014 caf\u00e9 \u00abquoted\u00bb '
               '&lt;tag&gt;</text></svg>')
        text = _read(reader, _write(tmp_path, "e.svg", svg))["text"]
        assert text == "Fish & chips \u2014 caf\u00e9 \u00abquoted\u00bb <tag>"

    def test_text_outside_text_elements_is_recovered_and_labelled(
        self, reader, tmp_path
    ):
        svg = ('<svg><style>.a{color:red}</style><script>var x = 1;</script>'
               'Loose text in the markup</svg>')
        result = _read(reader, _write(tmp_path, "loose.svg", svg))
        assert result["text"] == "Loose text in the markup"
        info = result["extraction_info"]
        assert info["recovered_from_markup"] is True
        assert info["reason"].startswith("svg_text_recovered_from_markup")
        assert info["embedded_code_chars"] > 0      # script/style counted

    def test_malformed_document_keeps_its_text(self, reader, tmp_path):
        svg = '<svg><text x="1">Broken doc <tspan>still readable</svg>'
        result = _read(reader, _write(tmp_path, "broken.svg", svg))
        assert result["text"] == "Broken doc still readable"
        assert result["extraction_info"]["recovered_from_markup"] is True
        assert "svg_malformed_xml" in result["extraction_info"]["reason"]


class TestEveryOutcomeStatesAReason:
    """The reported symptom: the reason was literally 'unknown'."""

    def test_no_text_and_no_vectors_explains_itself(self, reader, tmp_path):
        info = _read(
            reader, _write(tmp_path, "empty.svg", "<svg></svg>")
        )["extraction_info"]
        assert info["reason"] == "svg_has_no_text_elements"
        assert info["extracted"] is False

    def test_vector_paths_are_named_as_possible_outlined_text(
        self, reader, tmp_path
    ):
        svg = '<svg><path d="M0 0 L10 10"/><path d="M1 1 L2 2"/></svg>'
        info = _read(reader, _write(tmp_path, "paths.svg", svg))["extraction_info"]
        assert info["vector_paths"] == 2
        assert "2 vector path element(s)" in info["reason"]

    def test_embedded_raster_images_are_named_not_ocrd(self, reader, tmp_path):
        svg = '<svg><image href="data:image/png;base64,AAAA"/></svg>'
        info = _read(reader, _write(tmp_path, "img.svg", svg))["extraction_info"]
        assert info["embedded_images"] == 1
        assert "not OCR'd" in info["reason"]

    def test_missing_file_states_its_reason(self, reader, tmp_path):
        result = _read(reader, tmp_path / "nope.svg")
        assert result["extraction_info"]["reason"] == "file_not_found"
        assert result["extraction_info"]["error"] == "File not found"

    def test_no_outcome_ever_reports_unknown(self, reader, tmp_path):
        cases = {
            "chart.svg": CHART,
            "empty.svg": "<svg></svg>",
            "paths.svg": '<svg><path d="M0 0"/></svg>',
            "broken.svg": '<svg><text>broken',
            "loose.svg": "<svg>loose</svg>",
        }
        for name, content in cases.items():
            info = _read(reader, _write(tmp_path, name, content))["extraction_info"]
            reason = info.get("reason")
            assert reason, f"{name}: no reason recorded"
            assert reason != "unknown", f"{name}: reason is the placeholder"
            assert "unknown" not in reason.lower(), f"{name}: {reason}"

    def test_the_storage_log_can_name_the_condition(self, reader, tmp_path):
        """The line that printed 'Reason: unknown' reads extraction_info['reason']."""
        payload = _read(reader, _write(tmp_path, "paths.svg",
                                        '<svg><path d="M0 0"/></svg>'))
        reason = payload["extraction_info"].get("reason", "unknown")
        assert reason != "unknown"
        assert payload["text"] == ""
        assert payload["ocr_attempted"] is False
