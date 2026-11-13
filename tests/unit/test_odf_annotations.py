"""Round 18: an ODF comment must not be welded onto the sentence it annotates.

`Documents/testODTStyles3.odt` was the last file below COMPLETE after this
round's ODF work, at 0.9826 with `Rolf` and `00` missing. The stored text read:

    WOUTERS Rolf2017-01-31T00:00:00Beschermde persoon is overleden , advocaat,

Three things are joined with no separator: the name in the sentence, the
annotation's date and the annotation's own text. odfpy's ``extractText``
flattens ``office:annotation`` inline, so the author, the timestamp and the note
became part of the word before them and the tokens the oracle counts
(``Rolf``, the ``00`` of the timestamp) did not exist in the store.

Anchored comments are now removed from the paragraph text and stored as their
own labelled line, which is what a comment is - metadata about the paragraph,
not a run of it - and it keeps the author and timestamp attached to the note,
which flattening in place also destroyed.
"""
import os
import zipfile

import pytest

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "test-doc")

NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
      'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:dc="http://purl.org/dc/elements/1.1/" '
      'office:version="1.2"')


def _odt(tmp_path, body, name="annotated.odt"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    f'<office:document-content {NS}><office:body><office:text>'
                    f'{body}</office:text></office:body></office:document-content>')
    return path


def _read(path):
    from reader_file.readers.read_office import OfficeFileReader

    result = OfficeFileReader().read_file({"path": str(path), "extension": ".odt"})
    return result.get("content", result)


def _stored_text(result):
    from pipeline.storage_pipeline import StoragePipeline

    return StoragePipeline._extract_text_from_content.__get__(
        object.__new__(StoragePipeline))(result) or ""


ANNOTATED = ('<text:p>WOUTERS Rolf<office:annotation>'
             '<dc:creator>M. Peeters</dc:creator>'
             '<dc:date>2017-01-31T00:00:00</dc:date>'
             '<text:p>Beschermde persoon is overleden</text:p>'
             '</office:annotation>, advocaat</text:p>')


def test_the_comment_text_is_not_welded_into_the_sentence(tmp_path):
    stored = _stored_text(_read(_odt(tmp_path, ANNOTATED)))
    assert "Rolf2017" not in stored, stored
    assert "WOUTERS Rolf" in stored


def test_the_comment_is_stored_as_its_own_labelled_line(tmp_path):
    stored = _stored_text(_read(_odt(tmp_path, ANNOTATED)))
    line = next((l for l in stored.splitlines() if "Beschermde" in l), "")
    # The storage builder prefixes the paragraph style; the comment label and
    # its attribution come after it.
    assert "[comment" in line, line
    assert "M. Peeters" in line
    assert "2017-01-31T00:00:00" in line


def test_the_sentence_around_the_comment_survives(tmp_path):
    stored = _stored_text(_read(_odt(tmp_path, ANNOTATED)))
    assert "advocaat" in stored


def test_a_comment_without_an_author_still_stores_its_date(tmp_path):
    body = ('<text:p>alpha<office:annotation>'
            '<dc:date>2020-05-06T07:08:09</dc:date>'
            '<text:p>no author here</text:p>'
            '</office:annotation> beta</text:p>')
    stored = _stored_text(_read(_odt(tmp_path, body)))
    assert "no author here" in stored
    assert "2020-05-06T07:08:09" in stored
    assert "alpha2020" not in stored


def test_the_corpus_document_scores_one():
    path = os.path.join(CORPUS, "Documents", "testODTStyles3.odt")
    if not os.path.exists(path):
        pytest.skip("corpus file missing")
    from tools.audit.archive_extraction import _oracle_text, recall

    stored = _stored_text(_read(path))
    result = recall(_oracle_text(path, ".odt"), stored)
    assert result["recall"] == 1.0, result.get("missing_sample")
