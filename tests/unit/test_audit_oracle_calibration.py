"""Round 18: oracle calibration for ODF, found while chasing two near-misses.

`testODT-TIKA-6000.odt` scored 0.9712 and `testODFwithOOo3.odt` 0.9434. Part of
both gaps was the *oracle*, not the reader:

* tag-stripping inserted a space at every tag boundary, so
  ``1<text:span>er</text:span> cycle`` became the two tokens ``1`` and ``er``
  while the reader correctly stored ``1er cycle``;
* XML escapes were tokenised as words, so the reader was marked down for not
  storing ``apos``, ``quot``, ``lt``, ``gt``;
* ``text:index-title-template`` - the model an index title is regenerated
  from, not a rendered heading - was counted alongside the rendered copy.

An oracle that invents tokens makes a correct reader look lossy, so each of
those is pinned here. The reader-side losses found in the same two files are in
``test_odf_tracked_changes.py``.
"""
import os
import zipfile

import pytest

from tools.audit.archive_extraction import _oracle_text, tokens

NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
      'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:dc="http://purl.org/dc/elements/1.1/" '
      'office:version="1.2"')

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "test-doc")


def _odt(tmp_path, body, name="oracle.odt"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    f'<office:document-content {NS}><office:body><office:text>'
                    f'{body}</office:text></office:body></office:document-content>')
    return path


def test_an_inline_span_does_not_split_a_word(tmp_path):
    path = _odt(tmp_path, '<text:p>étudiants de 1<text:span '
                          'text:style-name="T20">er</text:span> cycle</text:p>')
    found = tokens(_oracle_text(path, ".odt"))
    assert found["1er"] == 1, dict(found)
    assert found["er"] == 0


def test_xml_escapes_are_not_words(tmp_path):
    path = _odt(tmp_path, "<text:p>l&apos;apostrophe &lt;ici&gt; &quot;ok&quot;</text:p>")
    found = tokens(_oracle_text(path, ".odt"))
    for markup in ("apos", "quot", "lt", "gt"):
        assert found[markup] == 0, (markup, dict(found))
    assert found["l"] == 1 and found["apostrophe"] == 1


def test_block_boundaries_still_separate_words(tmp_path):
    path = _odt(tmp_path, "<text:p>alpha</text:p><text:p>beta</text:p>")
    found = tokens(_oracle_text(path, ".odt"))
    assert found["alpha"] == 1 and found["beta"] == 1
    assert found["alphabeta"] == 0


def test_the_corpus_french_document_now_scores_one():
    """The measured near-miss: 0.9712 before calibration, 1.0 after."""
    path = os.path.join(CORPUS, "Documents", "testODT-TIKA-6000.odt")
    if not os.path.exists(path):
        pytest.skip("corpus file missing")
    found = tokens(_oracle_text(path, ".odt"))
    assert found["er"] == 0
    assert found["1er"] == 2
