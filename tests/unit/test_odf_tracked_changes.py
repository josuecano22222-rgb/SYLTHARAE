"""Round 18, task 2: ODF tracked changes must keep their author, date and copies.

`Documents/testODFwithOOo3.odt` scores 0.9434 and the six missing tokens are
exactly one change-info record plus the bibliography title template:

* `office:change-info` carries `dc:creator` and `dc:date`. The reader stores
  those for two of the three changed regions; the third is dropped because the
  paragraph recorder de-duplicates by *text*, and two insertions made by the
  same person at the same second are the same text. That is a real loss: an
  examiner counting who changed what, and how often, gets one instead of the
  whole set.
* `text:index-title-template` is the *template* LibreOffice uses to generate
  the rendered title `text:index-title`. The rendered title is stored; the
  template is not body text, and the oracle counts it only because it strips
  every tag. That one is an oracle artifact, corrected in the oracle rather
  than by storing the same heading twice.

Both are pinned here, plus the odfpy path, which never looked at tracked
changes at all - the fallback did, so the answer depended on which parser
accepted the package.
"""
import os
import zipfile

import pytest

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "test-doc")

NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
      'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:dc="http://purl.org/dc/elements/1.1/" '
      'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
      ' office:version="1.2"')

CHANGE_REGION = """<text:changed-region text:id="ct{index}">
  <text:insertion><office:change-info>
    <dc:creator>{author}</dc:creator><dc:date>{date}</dc:date>
  </office:change-info></text:insertion>
</text:changed-region>"""


def _content_xml(regions_author="Bart Hanssens", regions_date="2009-10-05T19:09:00",
                 regions=3, body=""):
    changes = "".join(
        CHANGE_REGION.format(index=i, author=regions_author, date=regions_date)
        for i in range(regions))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document-content {NS}><office:body><office:text>'
        f'<text:tracked-changes>{changes}</text:tracked-changes>'
        f'{body}'
        '</office:text></office:body></office:document-content>')


def _write_odt(path, content_xml):
    """A minimal ODF package: mimetype, manifest, content.xml."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        zf.writestr("META-INF/manifest.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:'
                    'opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
                    '<manifest:file-entry manifest:full-path="/" '
                    'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                    '<manifest:file-entry manifest:full-path="content.xml" '
                    'manifest:media-type="text/xml"/></manifest:manifest>')
        zf.writestr("content.xml", content_xml)
    return path


def _stored_text(reader_result):
    """Body text the pipeline would store for this reader result."""
    from pipeline.storage_pipeline import StoragePipeline

    content = reader_result.get("content", reader_result)
    return StoragePipeline._extract_text_from_content.__get__(
        object.__new__(StoragePipeline))(content) or ""


def _read(path):
    from reader_file.readers.read_office import OfficeFileReader

    return OfficeFileReader().read_file({"path": str(path), "extension": ".odt"})


# --------------------------------------------------------------------------- #
# Every change record survives, including identical ones
# --------------------------------------------------------------------------- #
def test_three_identical_change_records_are_all_kept(tmp_path):
    path = _write_odt(tmp_path / "repeats.odt", _content_xml(regions=3))
    text = _stored_text(_read(path))
    assert text.count("Bart Hanssens") == 3, (
        "identical changed regions were collapsed; the author appears "
        f"{text.count('Bart Hanssens')} times, not 3")


def test_the_change_date_is_stored_with_the_author(tmp_path):
    path = _write_odt(tmp_path / "dated.odt", _content_xml(regions=1))
    text = _stored_text(_read(path))
    assert "Bart Hanssens" in text
    assert "2009-10-05T19:09:00" in text


def test_different_authors_in_identical_slots_are_both_kept(tmp_path):
    body = ("<text:p>alpha</text:p>")
    xml = _content_xml(regions=1, body=body).replace(
        "<text:changed-region text:id=\"ct0\">",
        "<text:changed-region text:id=\"ctA\">")
    xml = xml.replace(
        "</text:tracked-changes>",
        CHANGE_REGION.format(index="B", author="Kyle Reese",
                             date="2011-01-02T03:04:05")
        + "</text:tracked-changes>")
    path = _write_odt(tmp_path / "two_authors.odt", xml)
    text = _stored_text(_read(path))
    assert "Bart Hanssens" in text and "Kyle Reese" in text


def test_the_corpus_document_keeps_every_changed_region():
    path = os.path.join(CORPUS, "Documents", "testODFwithOOo3.odt")
    if not os.path.exists(path):
        pytest.skip("corpus file missing")
    text = _stored_text(_read(path))
    # content.xml holds three change-info blocks, all by the same author at the
    # same instant; the reader used to store one.
    assert text.count("Bart Hanssens") >= 3, text.count("Bart Hanssens")


# --------------------------------------------------------------------------- #
# The odfpy path must answer the same question the fallback does
# --------------------------------------------------------------------------- #
def test_the_odfpy_parser_path_also_stores_tracked_changes(monkeypatch, tmp_path):
    """A package odfpy accepts must not lose what a package it refuses keeps."""
    path = _write_odt(tmp_path / "odfpy_path.odt", _content_xml(regions=2))
    from reader_file.readers import read_office

    def refuse(*args, **kwargs):
        raise RuntimeError("force the content.xml fallback off")

    # Prove the odfpy path is the one under test by making the fallback fail.
    monkeypatch.setattr(read_office.OfficeFileReader,
                        "_read_odt_from_content_xml",
                        lambda self, filepath: refuse())
    result = _read(path)
    text = _stored_text(result)
    assert "Bart Hanssens" in text, (
        "the odfpy path never reads office:change-info, so the author is lost "
        "whenever odfpy accepts the package")


# --------------------------------------------------------------------------- #
# The oracle must not count a title *template* as document text
# --------------------------------------------------------------------------- #
def test_the_oracle_ignores_an_unrendered_index_title_template(tmp_path):
    from tools.audit.archive_extraction import _oracle_text

    body = ('<text:p>Solr: a bibliography follows.</text:p>'
            '<text:bibliography text:name="Bibliography1">'
            '<text:bibliography-source>'
            '<text:index-title-template>Bibliography</text:index-title-template>'
            '</text:bibliography-source>'
            '<text:index-body>'
            '<text:index-title><text:p>Bibliography</text:p></text:index-title>'
            '</text:index-body></text:bibliography>')
    path = _write_odt(tmp_path / "index.odt", _content_xml(regions=0, body=body))
    oracle = _oracle_text(path, ".odt")
    # The rendered title is counted once; the template is not counted at all.
    assert oracle.count("Bibliography") == 1, oracle
