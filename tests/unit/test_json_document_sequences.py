"""JSON text files that are a *sequence* of documents, not a single one.

Real corpora contain JSON Lines / NDJSON, concatenated pretty-printed
documents and log exports.  ``json.load`` rejects every one of them with
"Extra data", which used to fail the whole file: no content, no words, a
``failed`` row.  Measured on a 100 020-file mixed corpus that was 19 589 of
20 000 ``.json`` files - a 19.6 % coverage loss that had nothing to do with the
files being unreadable.

These tests pin both halves of the contract:

* a document sequence yields its real content, with the exact document count
  and an explicit retained-window report;
* anything that is *not* a clean sequence keeps the original strict-parse
  error - a partially parsed file is never presented as content.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reader_file.readers.read_remaining import (  # noqa: E402
    JSON_DOCUMENTS_MAX_RETAINED,
    RemainingFileReader,
)


@pytest.fixture()
def reader():
    return RemainingFileReader()


def _write(tmp_path: Path, name: str, text: str, encoding: str = "utf-8") -> str:
    path = tmp_path / name
    path.write_text(text, encoding=encoding)
    return str(path)


def test_single_document_is_unchanged(reader, tmp_path):
    result = reader.read_json_file(_write(tmp_path, "one.json", '{"a": 1, "b": [1, 2]}'))
    assert result["data_type"] == "dict"
    assert result["key_count"] == 2
    assert "data_format" not in result
    assert "error" not in result


def test_single_array_is_unchanged(reader, tmp_path):
    result = reader.read_json_file(_write(tmp_path, "array.json", "[1, 2, 3]"))
    assert result["data_type"] == "list"
    assert result["item_count"] == 3
    assert "data_format" not in result


def test_json_lines_yields_all_documents(reader, tmp_path):
    text = "".join(json.dumps({"id": i, "name": f"n{i}"}) + "\n" for i in range(50))
    result = reader.read_json_file(_write(tmp_path, "lines.json", text))
    assert "error" not in result
    assert result["data_format"] == "json_documents"
    assert result["document_count"] == 50
    assert result["retained_count"] == 50
    assert result["item_count"] == 50
    assert result["data"][7]["id"] == 7
    assert "truncated" not in result


def test_concatenated_pretty_printed_documents(reader, tmp_path):
    text = '{\n  "a": 1\n}\n{\n  "b": 2\n}\n'
    result = reader.read_json_file(_write(tmp_path, "concat.json", text))
    assert result["document_count"] == 2
    assert result["data"] == [{"a": 1}, {"b": 2}]


def test_literal_json_line_without_newline_handling(reader, tmp_path):
    """A JSONL file whose last line has no trailing newline still parses."""
    text = '{"a": 1}\n{"b": 2}'
    result = reader.read_json_file(_write(tmp_path, "nonl.json", text))
    assert result["document_count"] == 2


def test_valid_document_followed_by_garbage_is_still_an_error(reader, tmp_path):
    result = reader.read_json_file(
        _write(tmp_path, "garbage.json", '{"a": 1}\nthis is not json\n')
    )
    assert "error" in result
    assert "Extra data" in str(result["error"])
    assert "data" not in result, "a partial parse must not be presented as content"


def test_trailing_junk_on_a_single_document_is_still_an_error(reader, tmp_path):
    result = reader.read_json_file(_write(tmp_path, "junk.json", '{"a": 1} trailing'))
    assert "error" in result
    assert "data" not in result


def test_empty_file_still_errors(reader, tmp_path):
    result = reader.read_json_file(_write(tmp_path, "empty.json", ""))
    assert "error" in result
    assert "data" not in result


def test_whitespace_only_file_still_errors(reader, tmp_path):
    result = reader.read_json_file(_write(tmp_path, "blank.json", "\n\n   \n"))
    assert "error" in result


def test_large_sequence_counts_exactly_and_bounds_retention(reader, tmp_path):
    text = "".join(json.dumps({"i": i, "pad": "x" * 200}) + "\n" for i in range(200_000))
    result = reader.read_json_file(_write(tmp_path, "big.jsonl", text))
    assert result["document_count"] == 200_000, "the count must be exact, not the retained window"
    assert result["retained_count"] == JSON_DOCUMENTS_MAX_RETAINED
    assert result["truncated"] is True
    assert len(result["data"]) == JSON_DOCUMENTS_MAX_RETAINED


def test_sequence_with_latin1_encoding_falls_back(reader, tmp_path):
    path = tmp_path / "latin.json"
    path.write_bytes('{"a": "café"}\n{"b": "naïve"}\n'.encode("latin-1"))
    result = reader.read_json_file(str(path))
    assert result["document_count"] == 2
    assert result["encoding_used"] == "latin-1"


def test_single_huge_unterminated_document_is_refused(reader, tmp_path):
    """A file that fails strict parsing with no document boundary is not a
    sequence; refusing keeps memory bounded instead of buffering a blob."""
    path = tmp_path / "huge.json"
    path.write_text("[" + "1," * 200_000, encoding="utf-8")  # ~400 KB, unterminated
    result = reader.read_json_file(str(path))
    assert "error" in result
    assert "data" not in result
