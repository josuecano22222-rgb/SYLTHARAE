"""
Unit tests for file date extraction and image orientation auto-rotation.
"""

import os
import io
import tempfile
import datetime
from PIL import Image, ImageDraw
import pytest

from core.file_utils import get_file_creation_and_modification_date, parse_date_string
from reader_file.readers.read_img_fast import ImageFileReader
from reader_file.readers.read_pdf import PDFFileReader
from core.ocr import get_ocr_engine


def test_parse_date_string():
    """Test parsing various document date string formats."""
    # PDF date format D:YYYYMMDDHHMMSS
    pdf_dt = parse_date_string("D:20230512143000Z")
    assert pdf_dt is not None
    assert pdf_dt.year == 2023
    assert pdf_dt.month == 5
    assert pdf_dt.day == 12

    # EXIF date format YYYY:MM:DD HH:MM:SS
    exif_dt = parse_date_string("2021:11:20 09:15:30")
    assert exif_dt is not None
    assert exif_dt.year == 2021
    assert exif_dt.month == 11
    assert exif_dt.day == 20

    # ISO date string
    iso_dt = parse_date_string("2022-08-10T10:00:00")
    assert iso_dt is not None
    assert iso_dt.year == 2022
    assert iso_dt.month == 8
    assert iso_dt.day == 10


def test_get_file_creation_date_from_embedded_metadata(tmp_path):
    """File date must come from embedded metadata when present."""
    fake_file = tmp_path / "doc.pdf"
    fake_file.write_text("dummy")

    content = {
        "metadata": {
            "creationDate": "D:20200115000000Z",
            "modDate": "D:20200116000000Z",
        }
    }
    c_date, m_date = get_file_creation_and_modification_date(str(fake_file), content)
    assert c_date == datetime.date(2020, 1, 15)
    assert m_date == datetime.date(2020, 1, 16)


def test_get_file_creation_date_preserves_filesystem_mtime(tmp_path):
    """File date must preserve file modification time on disk over system date."""
    test_file = tmp_path / "sample.txt"
    test_file.write_text("sample content")

    # Set mtime to 2019-06-01
    past_ts = datetime.datetime(2019, 6, 1, 12, 0, 0).timestamp()
    os.utime(test_file, (past_ts, past_ts))

    c_date, m_date = get_file_creation_and_modification_date(str(test_file))
    assert c_date == datetime.date(2019, 6, 1)
    assert m_date == datetime.date(2019, 6, 1)


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_standalone_rotated_image_text_extraction(tmp_path, angle):
    """Rotated image text extraction must succeed across candidate angles."""
    img = Image.new("RGB", (1000, 300), "white")
    draw = ImageDraw.Draw(img)
    marker = f"ROTATEDIMAGE_{angle}_9911"
    draw.text((60, 100), marker, fill="black")

    rot_img = img.rotate(angle, expand=True)
    img_path = tmp_path / f"rot_{angle}.png"
    rot_img.save(img_path)

    reader = ImageFileReader()
    res = reader.read_image_file_fast(str(img_path))
    extracted_text = res.get("text", "")
    assert "9911" in extracted_text.replace(" ", ""), f"Angle {angle}° failed: {extracted_text}"


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_pdf_page_rotated_image_text_extraction(tmp_path, angle):
    """Scanned PDF page with rotated image must be auto-oriented and extracted."""
    pymupdf = pytest.importorskip("pymupdf")

    img = Image.new("RGB", (1000, 300), "white")
    draw = ImageDraw.Draw(img)
    marker = f"PDFROTATED_{angle}_7744"
    draw.text((60, 100), marker, fill="black")

    rot_img = img.rotate(angle, expand=True)
    png_buf = io.BytesIO()
    rot_img.save(png_buf, format="PNG")

    doc = pymupdf.open()
    page = doc.new_page(width=400, height=1100)
    page.insert_image(pymupdf.Rect(0, 0, 400, 1100), stream=png_buf.getvalue())
    pdf_bytes = doc.tobytes()
    doc.close()

    pdf_path = tmp_path / f"pdf_rot_{angle}.pdf"
    pdf_path.write_bytes(pdf_bytes)

    reader = PDFFileReader()
    res = reader.read_pdf_file(str(pdf_path))
    assert res.get("pages") and len(res["pages"]) > 0
    extracted_text = res["pages"][0]["text"]
    assert "7744" in extracted_text.replace(" ", ""), f"PDF Angle {angle}° failed: {extracted_text}"
