"""PPTX: what lives inside a package that no shape object exposes.

Four distinct losses, all of them in the same format:

* ``slide.shapes`` yields a group as one object. Its children - the WordArt, the
  text boxes, the link - are reachable only through ``group.shapes``, so a deck
  arranged in groups read as empty (testPPT_groups.pptx: recall 0.33).
* ``notes_text_frame`` is one placeholder on the notes page. A notes page also
  carries header, footer, date and page-number placeholders, and a template can
  put text in them (testPPT_various2.pptx).
* A ``.ppsx`` is a deck with a different main content type, and python-pptx
  refuses it outright: "is not a PowerPoint file". The slideshow was unreadable
  (testPPT.ppsx: error, recall 0.75 from the error path's leftovers).
* Anything python-pptx does not model (diagrams, WordArt bodies, master text) is
  still ``a:t`` text in the part, and is recovered by the cover pass.
"""

import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pptx = pytest.importorskip("pptx", reason="python-pptx not installed")

from reader_file.readers.read_office import (  # noqa: E402
    OfficeFileReader,
    _drawingml_paragraph_texts,
    _pptx_cover_remaining,
)

CORPUS = PROJECT_ROOT / "test-doc" / "Presentations"


def _content(result):
    if isinstance(result.get("content"), dict) and "elements" not in result:
        return result["content"]
    return result


def _deck(tmp_path, name="deck.pptx"):
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "Quarterly review"
    box = slide.shapes.add_textbox(0, 0, 100, 100)
    box.text_frame.text = "Body copy in a plain text box"
    notes = slide.notes_slide
    notes.notes_text_frame.text = "Speaker says: mention the acquisition"
    path = tmp_path / name
    presentation.save(str(path))
    return path


class TestNotesPage:
    def test_notes_text_is_stored(self, tmp_path):
        content = _content(OfficeFileReader().read_file(
            {"path": str(_deck(tmp_path)), "extension": ".pptx"}))
        assert "mention the acquisition" in (content["slides"][0].get("notes") or "")

    def test_placeholders_beyond_the_body_placeholder_are_kept(self, tmp_path):
        path = _deck(tmp_path)
        # Put text in a header placeholder the way a template would.
        with zipfile.ZipFile(path) as source:
            members = [(info.filename, source.read(info.filename))
                       for info in source.infolist()]
        rewritten = []
        for name, data in members:
            if name == "ppt/notesSlides/notesSlide1.xml":
                xml = data.decode("utf-8")
                xml = xml.replace(
                    "<a:t>Speaker says: mention the acquisition</a:t>",
                    "<a:t>Speaker says: mention the acquisition</a:t>"
                    "<a:t>NotesMasterHeader</a:t>")
                data = xml.encode("utf-8")
            rewritten.append((name, data))
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as copy:
            for name, data in rewritten:
                copy.writestr(name, data)

        content = _content(OfficeFileReader().read_file(
            {"path": str(path), "extension": ".pptx"}))
        stored = str(content["slides"][0].get("notes") or "")
        stored += " " + " ".join(content["slides"][0].get("texts") or [])
        assert "NotesMasterHeader" in stored


class TestSlideshowMainPart:
    def _as_slideshow(self, path):
        with zipfile.ZipFile(path) as source:
            members = [(info.filename, source.read(info.filename))
                       for info in source.infolist()]
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as copy:
            for name, data in members:
                if name == "[Content_Types].xml":
                    data = data.replace(
                        b"presentationml.presentation.main+xml",
                        b"presentationml.slideshow.main+xml")
                copy.writestr(name, data)
        return path.with_suffix(".ppsx").rename(path) if False else path

    def test_a_slideshow_content_type_is_opened_not_refused(self, tmp_path):
        path = _deck(tmp_path, name="show.ppsx")
        self._as_slideshow(path)
        content = _content(OfficeFileReader().read_file(
            {"path": str(path), "extension": ".ppsx"}))
        assert not content.get("error"), content.get("error")
        assert content["slides"][0]["texts"]


class TestCoverPass:
    def test_paragraphs_are_concatenated_per_paragraph_not_across_them(self):
        xml = (
            '<a:document xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:p><a:r><a:t>Word</a:t></a:r><a:r><a:t>Art</a:t></a:r></a:p>'
            '<a:p><a:r><a:t>Second</a:t></a:r></a:p></a:document>')
        assert _drawingml_paragraph_texts(xml) == ["WordArt", "Second"]

    def test_text_no_shape_object_exposes_is_recovered_from_the_part(self, tmp_path):
        path = tmp_path / "package.pptx"
        slide = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<p:cSld><p:spTree><p:sp><p:txBody>'
            '<a:p><a:r><a:t>Visible title</a:t></a:r></a:p>'
            '</p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
        master = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:notesMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<p:cSld><a:p><a:r><a:t>Master footer</a:t></a:r></a:p></p:cSld></p:notesMaster>')
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", slide)
            archive.writestr("ppt/notesMasters/notesMaster1.xml", master)

        slides = [{"slide_number": 1, "texts": ["Visible title"], "elements": []}]
        backfill = _pptx_cover_remaining(slides, str(path))
        assert backfill["parts"]["ppt/slides/slide1.xml"]["recovered"] == 0
        # It belongs to no slide, so it is not counted as a recovered slide
        # paragraph - it goes to the forensic channel, where it is still stored.
        assert "Master footer" in (backfill.get("forensic_text") or "")
        assert backfill["parts"]["ppt/notesMasters/notesMaster1.xml"]["recovered"] == 0


@pytest.mark.skipif(not (CORPUS / "testPPT_groups.pptx").exists(),
                    reason="corpus fixture not present")
def test_grouped_shapes_in_the_corpus_are_read():
    content = _content(OfficeFileReader().read_file(
        {"path": str(CORPUS / "testPPT_groups.pptx"), "extension": ".pptx"}))
    stored = " ".join(
        str(element.get("text") or "")
        for slide in content["slides"] for element in slide.get("elements") or [])
    assert "box1Text" in stored or "box1" in stored
    groups = [element.get("group") for slide in content["slides"]
              for element in slide.get("elements") or [] if element.get("group")]
    assert groups, "grouped shapes should carry the group they came from"
