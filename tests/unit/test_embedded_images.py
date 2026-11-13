"""Unit: embedded images in Office/OpenDocument files are actually extracted.

EMBED-01. read_office.py located embedded images correctly - it looks in
word/media/, xl/media/ and ppt/media/, and the equivalent for ODF - then OCR'd
them with:

    from .read_img_fast import read_image_file_fast

But read_image_file_fast is a METHOD on ImageFileReader, not a module-level
function, so that import raised ImportError on every single call. There were
six such sites: DOCX, XLSX, PPTX, ODT, ODS and ODP. Each was wrapped in

    except Exception as e:
        logger.debug(f"Failed to process image {image_file}: {e}")

so the failure was logged at DEBUG and swallowed. The net effect: embedded image
extraction has never worked for any Office or OpenDocument format, and nothing
reported it. A DOCX containing a scanned page yielded extracted_images == [].

This file builds real documents with embedded images and asserts the OCR text
comes back, which is impossible while the import is broken.
"""

import io
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reader_file.readers.read_img_fast import ImageFileReader  # noqa: E402
from reader_file.readers.read_office import OfficeFileReader  # noqa: E402

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MARKER = "EMBEDOCR 4412"


def rasterise(text):
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    img = Image.new("RGB", (900, 220), "white")
    ImageDraw.Draw(img).text((30, 80), text,
                             font=ImageFont.truetype(FONT, 34), fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def reader():
    return OfficeFileReader()


@pytest.fixture
def png_file(tmp_path):
    path = tmp_path / "source.png"
    path.write_bytes(rasterise(f"Marker {MARKER}"))
    return str(path)


# --------------------------------------------------- the phantom import
def test_read_image_file_fast_is_a_method_not_a_module_function():
    """The exact mistake: importing a method as if it were a function."""
    import reader_file.readers.read_img_fast as mod

    assert hasattr(ImageFileReader, "read_image_file_fast")
    assert not hasattr(mod, "read_image_file_fast"), (
        "a module-level read_image_file_fast has appeared; the office reader "
        "must go through ImageFileReader"
    )


def test_no_source_file_imports_the_phantom_name():
    """Textual sweep across the readers that OCR embedded images."""
    root = PROJECT_ROOT / "reader_file" / "readers"
    offenders = []
    for path in sorted(root.glob("*.py")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if "import read_image_file_fast" in line and not line.strip().startswith("#"):
                offenders.append(f"{path.name}:{i}")
    assert not offenders, offenders


def test_office_reader_exposes_the_shared_helper(reader):
    assert callable(reader._ocr_embedded_image)
    assert callable(reader._image_reader)


def test_image_reader_is_cached_not_reconstructed(reader):
    """A document with many images must not build a reader per image."""
    first = reader._image_reader()
    assert reader._image_reader() is first
    assert isinstance(first, ImageFileReader)


def test_helper_delegates_to_the_real_image_reader(reader, png_file):
    result = reader._ocr_embedded_image(png_file)
    assert result is not None
    assert "text" in result or "error" in result


# ------------------------------------------------------- DOCX end to end
@pytest.fixture
def docx_with_image(tmp_path, png_file):
    docx = pytest.importorskip("docx")
    shared = pytest.importorskip("docx.shared")
    path = tmp_path / "report.docx"
    doc = docx.Document()
    doc.add_paragraph("Marker EMBEDDOCTEXT visible paragraph")
    doc.add_picture(png_file, width=shared.Inches(4))
    doc.save(str(path))
    return str(path)


def test_docx_embedded_image_is_extracted(reader, docx_with_image):
    """Before the fix this returned an empty list, silently."""
    result = reader.read_file({"path": docx_with_image, "extension": ".docx"})
    assert result.get("error") is None, result.get("error")
    images = result.get("extracted_images") or []
    assert len(images) == 1, f"expected one embedded image, got {len(images)}"


def test_docx_embedded_image_text_was_ocrd(reader, docx_with_image):
    result = reader.read_file({"path": docx_with_image, "extension": ".docx"})
    image = (result.get("extracted_images") or [{}])[0]
    text = (image.get("text") or "").replace("\n", " ")
    assert "EMBEDOCR" in text and "4412" in text, repr(image.get("text"))


def test_docx_embedded_image_carries_ocr_provenance(reader, docx_with_image):
    """An OCR-derived image must not be indistinguishable from a text image."""
    result = reader.read_file({"path": docx_with_image, "extension": ".docx"})
    image = (result.get("extracted_images") or [{}])[0]
    assert image.get("ocr_derived") is True, image
    assert image.get("ocr_engine") in ("tesseract", "rapidocr"), image
    assert 0.0 < float(image.get("ocr_confidence") or 0) <= 1.0, image


def test_docx_embedded_image_keeps_its_part_name(reader, docx_with_image):
    """The part name is the only identifier of where in the package it came from."""
    result = reader.read_file({"path": docx_with_image, "extension": ".docx"})
    image = (result.get("extracted_images") or [{}])[0]
    assert image.get("name") == "image1.png", image.get("name")


def test_docx_without_images_reports_none_not_an_error(reader, tmp_path):
    docx = pytest.importorskip("docx")
    path = tmp_path / "plain.docx"
    doc = docx.Document()
    doc.add_paragraph("no images here")
    doc.save(str(path))
    result = reader.read_file({"path": str(path), "extension": ".docx"})
    assert result.get("error") is None
    assert (result.get("extracted_images") or []) == []


def test_temp_file_is_not_left_behind(reader, docx_with_image, tmp_path):
    """The old code wrote each image to a NamedTemporaryFile and unlinked it."""
    before = set(Path("/tmp").glob("tmp*.png"))
    reader.read_file({"path": docx_with_image, "extension": ".docx"})
    after = set(Path("/tmp").glob("tmp*.png"))
    assert after - before == set(), "embedded image temp files were left on disk"


# ----------------------------------------------------------- PPTX / XLSX
def test_pptx_embedded_image_is_extracted(reader, tmp_path, png_file):
    pptx = pytest.importorskip("pptx")
    util = pytest.importorskip("pptx.util")
    path = tmp_path / "deck.pptx"
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.add_picture(png_file, util.Inches(1), util.Inches(1),
                             width=util.Inches(4))
    prs.save(str(path))
    result = reader.read_file({"path": str(path), "extension": ".pptx"})
    assert result.get("error") is None, result.get("error")
    images = result.get("extracted_images") or []
    assert len(images) >= 1, f"no embedded image found in {sorted(result)}"
    assert "EMBEDOCR" in (images[0].get("text") or "").replace("\n", " ")


def test_xlsx_embedded_image_is_extracted(reader, tmp_path, png_file):
    openpyxl = pytest.importorskip("openpyxl")
    drawing = pytest.importorskip("openpyxl.drawing.image")
    path = tmp_path / "book.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Marker EMBEDSHEETTEXT"
    ws.add_image(drawing.Image(png_file), "C3")
    wb.save(str(path))
    result = reader.read_file({"path": str(path), "extension": ".xlsx"})
    assert result.get("error") is None, result.get("error")
    images = result.get("extracted_images") or []
    assert len(images) >= 1, f"no embedded image found in {sorted(result)}"
    assert "EMBEDOCR" in (images[0].get("text") or "").replace("\n", " ")


# ---------------------------------------------------- child-object contract
def test_reader_publishes_extraction_path_so_the_router_can_recurse(reader, docx_with_image):
    """EMBED-02: the reader now hands the router a directory to descend into.

    Before this the image was extracted and OCR'd but returned only inside
    extracted_images, so it never became an object - no paths row, no hash, no
    parent_path_id, no hierarchy_path. The reader cannot create those rows
    itself; it publishes extraction_path and the ordinary pipeline does the rest.
    tests/integration/test_universal_recursion.py proves the other end.
    """
    result = reader.read_file({"path": docx_with_image, "extension": ".docx"})
    assert result.get("extracted_images"), "precondition: image was extracted"
    extraction_path = result.get("extraction_path")
    assert extraction_path, f"router would have nothing to recurse into: {sorted(result)}"
    assert Path(extraction_path).is_dir(), extraction_path
    children = sorted(c.name for c in Path(extraction_path).iterdir())
    assert children, f"extraction_path {extraction_path} is empty"
    assert children == ["image1.png"], children


def test_pptx_publishes_extraction_path_without_a_result_dict(reader, tmp_path, png_file):
    """The PPTX helper returns a bare list, so it has no result dict to stamp.

    Regression: the shared helper referenced `result` unconditionally and raised
    NameError inside the site's except handler, which logged "Failed to process
    embedded image ppt/media/image1.png" and dropped the image entirely.
    """
    pptx = pytest.importorskip("pptx")
    util = pytest.importorskip("pptx.util")
    path = tmp_path / "deck2.pptx"
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.add_picture(png_file, util.Inches(1), util.Inches(1),
                             width=util.Inches(4))
    prs.save(str(path))
    result = reader.read_file({"path": str(path), "extension": ".pptx"})
    assert result.get("error") is None, result.get("error")
    assert result.get("extracted_images"), sorted(result)
    extraction_path = result.get("extraction_path")
    assert extraction_path, f"PPTX never published a child directory: {sorted(result)}"
    assert sorted(c.name for c in Path(extraction_path).iterdir()), extraction_path


def test_extraction_dir_is_cached_not_reallocated(reader, docx_with_image):
    """get_extraction_name_file mints a NEW numbered folder if the name exists.

    Calling it per child would scatter the images across image1, image2, ... and
    the router would descend into only the last one.
    """
    first = reader._embedded_extraction_dir(docx_with_image)
    second = reader._embedded_extraction_dir(docx_with_image)
    assert first == second, (first, second)


def test_windows_safe_component_is_applied_to_embedded_names(reader, tmp_path):
    """A part named for a Windows reserved device must not poison extraction."""
    dest = reader._materialise_embedded_image(b"\x89PNG\r\n", "media/CON.png",
                                              str(tmp_path / "src.docx"))
    # windows_safe_component neutralises a reserved device name by prefixing it,
    # so the leaf is _CON.png - no longer a reserved name on Windows.
    assert Path(dest).name == "_CON.png", dest
    assert Path(dest).name != "CON.png", dest
    assert Path(dest).exists(), dest
