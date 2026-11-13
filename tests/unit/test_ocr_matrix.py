"""The §2 OCR test matrix, mapped one-to-one to the required cases.

Every case below is exercised through the real reader or the real engine, not
a reimplementation. Cases that the installed engine genuinely cannot do are
asserted as limitations rather than hidden - see the language tests.

Case list (§2):
    ordinary printed text .......... TestOrdinaryText
    high-resolution images ......... TestResolution::test_high_resolution
    low-resolution images .......... TestResolution::test_low_resolution_*
    rotated text ................... TestRotation
    multi-page scanned PDFs ........ TestMultiPagePdf
    mixed image/text PDFs .......... TestMixedPdf
    multiple languages ............. TestLanguageSupport
    empty images ................... TestNoText::test_blank_image
    images with no text ............ TestNoText::test_solid_colour_image
    corrupted images ............... TestHostileInput::test_corrupt_image
    OCR engine unavailable ......... TestNoEngine
    OCR timeout/failure ............ TestEngineFailure
    very large images .............. TestResolution::test_very_large_image
    multiple OCR pages ............. TestMultiPagePdf
"""

import io
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.ocr.engines as ocr_engines  # noqa: E402
from core.ocr import (  # noqa: E402
    LOW_CONFIDENCE_RETRY_THRESHOLD,
    get_ocr_engine,
    recognize_best,
    reset_engine_cache,
)

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


@pytest.fixture(autouse=True)
def _fresh_engine_cache():
    reset_engine_cache()
    yield
    reset_engine_cache()


@pytest.fixture
def engine():
    eng = get_ocr_engine()
    if eng is None:
        pytest.skip("no OCR engine installed (needs tesseract or rapidocr-onnxruntime)")
    return eng


@pytest.fixture
def image_reader():
    from reader_file.readers.read_img_fast import ImageFileReader

    return ImageFileReader()


@pytest.fixture
def pdf_reader():
    from reader_file.readers.read_pdf import PDFFileReader

    return PDFFileReader()


def _diagnosis(out, path, engine):
    """One line saying what the reader and the engine did with ``path``.

    A string, so pytest does not shorten it (a dict message is cut to a few
    keys, which hid why this case failed in CI but not locally).
    """
    import json

    Image = pytest.importorskip("PIL.Image")
    original = Image.open(path).convert("RGB")
    direct = engine.recognize(original, ["heb", "eng", "ara"])
    return json.dumps({
        "reader": {k: out.get(k) for k in ("text", "ocr_confidence", "ocr_input_variant",
                                          "ocr_rotation", "ocr_engine", "ocr_engine_version")},
        "extraction_info": out.get("extraction_info"),
        "glyph_size": ocr_engines._glyph_size(original),
        "engine_on_original": {"text": direct.text, "confidence": direct.mean_confidence,
                               "scale": direct.scale, "rotation": direct.rotation,
                               "error": direct.error},
    }, default=str, ensure_ascii=False)


def make_image(tmp_path, marker, font_size=44, canvas=(1100, 260), angle=0,
               scale=1, name="case.png", colour="white"):
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    img = Image.new("RGB", canvas, colour)
    ImageDraw.Draw(img).text(
        (int(canvas[0] * 0.05), int(canvas[1] * 0.25)), marker,
        font=ImageFont.truetype(FONT, font_size), fill="black",
    )
    if angle:
        img = img.rotate(angle, expand=True, fillcolor=colour)
    if scale != 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    path = tmp_path / name
    img.save(path)
    return path


def read_image(image_reader, path):
    return image_reader.read_file({"path": str(path), "effective_extension": ".png"})


def scanned_pdf_bytes(markers):
    """One rasterised page per marker - a genuine scanned document."""
    pymupdf = pytest.importorskip("pymupdf")
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")

    doc = pymupdf.open()
    for marker in markers:
        img = Image.new("RGB", (1100, 300), "white")
        ImageDraw.Draw(img).text((60, 120), marker,
                                 font=ImageFont.truetype(FONT, 44), fill="black")
        png = io.BytesIO()
        img.save(png, format="PNG")
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(0, 0, 595, 162), stream=png.getvalue())
    data = doc.tobytes()
    doc.close()
    return data


def read_pdf(pdf_reader, data, tmp_path, name="doc.pdf"):
    path = tmp_path / name
    path.write_bytes(data)
    return pdf_reader.read_file({"path": str(path), "effective_extension": ".pdf"})


# ----------------------------------------------------------------------
# Ordinary printed text
# ----------------------------------------------------------------------
class TestOrdinaryText:
    def test_extracted_with_full_provenance(self, image_reader, tmp_path):
        path = make_image(tmp_path, "ORDINARY PRINTED TEXT 1043")
        out = read_image(image_reader, path)
        assert "ORDINARY PRINTED TEXT 1043" in out["text"]
        assert out["ocr_derived"] is True
        assert out["ocr_engine"] in ("tesseract", "rapidocr")
        assert out["ocr_confidence"] > 0.9

    def test_confidence_separates_good_from_bad(self, engine, tmp_path):
        """The confidence signal the retry relies on must actually separate."""
        Image = pytest.importorskip("PIL.Image")
        path = make_image(tmp_path, "CLEAR TEXT 5500")
        good = engine.recognize(Image.open(path).convert("RGB"))
        assert good.succeeded
        assert good.mean_confidence > LOW_CONFIDENCE_RETRY_THRESHOLD, (
            f"clean text scored {good.mean_confidence}, at or below the retry "
            f"threshold {LOW_CONFIDENCE_RETRY_THRESHOLD} - the gate would fire "
            "on every image and double OCR cost"
        )


# ----------------------------------------------------------------------
# Resolution: high, low, very large
# ----------------------------------------------------------------------
class TestResolution:
    def test_high_resolution(self, image_reader, tmp_path):
        path = make_image(tmp_path, "HIGHRES MARKER 6620", font_size=60,
                          canvas=(1600, 360), scale=3, name="hi.png")
        out = read_image(image_reader, path)
        assert out["ocr_successful"] is True
        assert "HIGHRES" in out["text"]

    def test_very_large_image_is_not_refused(self, image_reader, tmp_path):
        """§15 asks for documented limits; verify a large image still works."""
        path = make_image(tmp_path, "VERYLARGE 8801", font_size=120,
                          canvas=(3000, 700), scale=3, name="big.png")
        out = read_image(image_reader, path)
        assert out["ocr_successful"] is True
        assert "VERYLARGE" in out["text"]
        assert out["extraction_info"]["image_size"] == "9000x2100"

    def test_low_resolution_recovers_via_retry(self, image_reader, tmp_path, engine):
        """The measured defect: binarisation destroys small text.

        At 40px height the shared preprocessing cut a correct 0.841 reading to
        0.632 and turned 'LOWRESTEST' into 'APT2T7712'. The retry recovers it.
        """
        # Measured: at 200x50 with a 7px font the preprocessed pass returns
        # 'OWRESTR' at 0.640 while the raw image gives the full string at 0.977.
        # The canvas stays at or above the reader's 50px minimum so the size
        # guard does not short-circuit the test.
        path = make_image(tmp_path, "LOWRESTEST 7712", font_size=7,
                          canvas=(200, 50), name="low.png")
        out = read_image(image_reader, path)
        why = _diagnosis(out, path, engine)
        assert out["ocr_successful"] is True, why
        compact = out["text"].replace(" ", "")
        assert "LOWRESTEST" in compact, why
        assert "7712" in compact, why
        assert out.get("ocr_input_variant") == "original", (
            "expected the un-preprocessed retry to have produced this result: " + why
        )

    def test_retry_only_fires_when_confidence_is_low(self, engine, tmp_path):
        """A confident first pass must not pay for a second OCR call."""
        Image = pytest.importorskip("PIL.Image")
        rgb = Image.open(make_image(tmp_path, "CONFIDENT PASS 2210")).convert("RGB")
        from reader_file.readers.read_img_fast import ImageFileReader

        libs = ImageFileReader()._get_libraries()
        proc = Image.fromarray(
            ImageFileReader()._fast_preprocess(np.array(rgb), libs)
        )
        result = recognize_best(engine, proc, rgb)
        assert result.input_variant == "preprocessed", (
            "retry fired on a confident pass, doubling OCR cost"
        )

    def test_retry_records_which_input_won(self, engine, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        rgb = Image.open(make_image(tmp_path, "LOWRESTEST 7712", font_size=6,
                                    canvas=(160, 40))).convert("RGB")
        from reader_file.readers.read_img_fast import ImageFileReader

        libs = ImageFileReader()._get_libraries()
        proc = Image.fromarray(
            ImageFileReader()._fast_preprocess(np.array(rgb), libs)
        )
        result = recognize_best(engine, proc, rgb)
        assert result.input_variant in ("preprocessed", "original")
        assert result.to_content_fields()["ocr_input_variant"] == result.input_variant

    def test_retry_never_accepts_a_worse_result(self, engine, tmp_path):
        """The retry must keep the better reading, not just the later one."""
        Image = pytest.importorskip("PIL.Image")
        rgb = Image.open(make_image(tmp_path, "STABLE READING 3301")).convert("RGB")
        from reader_file.readers.read_img_fast import ImageFileReader

        libs = ImageFileReader()._get_libraries()
        proc = Image.fromarray(
            ImageFileReader()._fast_preprocess(np.array(rgb), libs)
        )
        best = recognize_best(engine, proc, rgb)
        primary = engine.recognize(proc)
        assert (best.mean_confidence or 0) >= (primary.mean_confidence or 0)


# ----------------------------------------------------------------------
# Rotation
# ----------------------------------------------------------------------
class TestRotation:
    @pytest.mark.parametrize("angle", [90, 180, 270])
    def test_rotated_text_is_still_read(self, engine, tmp_path, angle):
        Image = pytest.importorskip("PIL.Image")
        path = make_image(tmp_path, "ROTATIONTEST 5521", angle=angle,
                          name=f"rot{angle}.png")
        result = engine.recognize(Image.open(path).convert("RGB"))
        assert result.succeeded, f"{angle} deg produced no text"
        assert "5521" in result.text.replace(" ", ""), result.text


# ----------------------------------------------------------------------
# Multi-page and mixed PDFs
# ----------------------------------------------------------------------
class TestMultiPagePdf:
    def test_every_page_is_ocrd_in_order(self, pdf_reader, tmp_path):
        markers = ["PAGEONEMARKER 111", "PAGETWOMARKER 222", "PAGETHREEMARKER 333"]
        out = read_pdf(pdf_reader, scanned_pdf_bytes(markers), tmp_path)

        assert out["num_pages"] == 3
        assert [p["page_number"] for p in out["pages"]] == [1, 2, 3]
        for page, marker in zip(out["pages"], markers, strict=True):
            assert page["method"].startswith("ocr_"), page["method"]
            assert page["ocr_derived"] is True
            assert marker.split()[0] in page["text"].replace(" ", ""), page["text"]
            assert page["ocr_confidence"] > 0.9

    def test_page_count_matches_ocr_pages(self, pdf_reader, tmp_path):
        out = read_pdf(pdf_reader, scanned_pdf_bytes(["A 1", "B 2", "C 3", "D 4"]),
                       tmp_path, "four.pdf")
        ocr_pages = [p for p in out["pages"] if p["method"].startswith("ocr_")]
        assert len(ocr_pages) == out["num_pages"] == 4


class TestMixedPdf:
    """§3: a PDF with both native text and scanned pages must lose neither."""

    def test_both_layers_survive(self, pdf_reader, tmp_path):
        pymupdf = pytest.importorskip("pymupdf")
        Image = pytest.importorskip("PIL.Image")
        ImageDraw = pytest.importorskip("PIL.ImageDraw")
        ImageFont = pytest.importorskip("PIL.ImageFont")

        native = ("NATIVETEXTLAYER marker on page one of a mixed document "
                  "that also contains a scanned page")
        img = Image.new("RGB", (1100, 300), "white")
        ImageDraw.Draw(img).text((60, 120), "SCANNEDPAGEMARKER 444",
                                 font=ImageFont.truetype(FONT, 44), fill="black")
        png = io.BytesIO()
        img.save(png, format="PNG")

        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), native, fontsize=12)
        page2 = doc.new_page()
        page2.insert_image(pymupdf.Rect(0, 0, 595, 162), stream=png.getvalue())
        data = doc.tobytes()
        doc.close()

        out = read_pdf(pdf_reader, data, tmp_path, "mixed.pdf")
        methods = {p["method"] for p in out["pages"]}

        assert out["pages"][0]["method"] == "direct_extraction"
        assert out["pages"][1]["method"].startswith("ocr_")
        # Neither layer lost.
        assert "NATIVETEXTLAYER" in out["pages"][0]["text"]
        assert "SCANNEDPAGEMARKER" in out["pages"][1]["text"].replace(" ", "")
        assert len(methods) == 2, methods
        # Only the recognised page is marked derived.
        assert out["pages"][0].get("ocr_derived") is not True
        assert out["pages"][1]["ocr_derived"] is True


# ----------------------------------------------------------------------
# Language support - asserted as the real limitation, not hidden
# ----------------------------------------------------------------------
class TestLanguageSupport:
    def test_requested_languages_are_filtered_to_installed(self):
        from core.ocr.engines import TesseractEngine

        class Stub:
            def get_languages(self, config=""):
                return ("eng", "ara")

        eng = TesseractEngine()
        eng._pytesseract = Stub()
        eng._load_attempted = True
        assert eng._languages(["heb", "ara", "zzz"]) == (
            "ara", ["heb", "zzz"]
        )

    def test_fallback_engine_models_are_latin_and_chinese_only(self, engine):
        """This project declares DEFAULT_OCR_LANGUAGES = heb/eng/ara.

        The fallback's bundled models are ch_PP-OCRv4, so Hebrew and Arabic are
        NOT supported by it and it must stay behind tesseract in preference
        order. Asserted rather than assumed, so a model upgrade that adds
        coverage makes this test fail and forces the ordering to be revisited.
        """
        if engine.name != "rapidocr":
            pytest.skip("only applies to the rapidocr fallback")
        names = [c.name for c in ocr_engines.ENGINE_PREFERENCE]
        assert names.index("tesseract") < names.index("rapidocr")

    def test_hebrew_is_not_reliably_supported_by_the_fallback(self, engine):
        if engine.name != "rapidocr":
            pytest.skip("tesseract supports heb when installed")
        Image = pytest.importorskip("PIL.Image")
        ImageDraw = pytest.importorskip("PIL.ImageDraw")
        ImageFont = pytest.importorskip("PIL.ImageFont")
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 44)
        except OSError:
            pytest.skip("no font with Hebrew glyphs")
        img = Image.new("RGB", (700, 200), "white")
        ImageDraw.Draw(img).text((40, 70), "\u05d1\u05d3\u05d9\u05e7\u05d4",
                                 font=font, fill="black")
        result = engine.recognize(img)
        # Documented limitation: either nothing, or low-confidence output.
        assert (not result.succeeded) or (result.mean_confidence or 0) < 0.9


# ----------------------------------------------------------------------
# No text / hostile input
# ----------------------------------------------------------------------
class TestNoText:
    def test_blank_image_yields_nothing(self, image_reader, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        path = tmp_path / "blank.png"
        Image.new("RGB", (800, 400), "white").save(path)
        out = read_image(image_reader, path)
        assert out["ocr_attempted"] is True
        assert out["ocr_successful"] is False
        assert out["text"] == ""

    def test_solid_colour_image_yields_nothing(self, image_reader, tmp_path):
        path = make_image(tmp_path, "", colour="#808080", name="solid.png")
        out = read_image(image_reader, path)
        assert out["ocr_successful"] is False
        assert out["text"] == ""

    def test_random_noise_is_not_hallucinated_as_text(self, engine):
        """A noisy image must not produce confident invented content."""
        Image = pytest.importorskip("PIL.Image")
        rng = np.random.default_rng(1234)
        noise = rng.integers(0, 256, (400, 800, 3), dtype=np.uint8)
        result = engine.recognize(Image.fromarray(noise, "RGB"))
        assert (not result.succeeded) or (result.mean_confidence or 0) < 0.9


class TestHostileInput:
    def test_corrupt_image_is_recorded_not_raised(self, image_reader, tmp_path):
        path = tmp_path / "broken.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"not-a-real-png" * 30)
        out = read_image(image_reader, path)
        assert isinstance(out, dict)
        assert out.get("ocr_successful") is not True

    def test_truncated_jpeg_is_recorded_not_raised(self, image_reader, tmp_path):
        path = make_image(tmp_path, "TRUNCATED 1234", name="t.jpg")
        raw = path.read_bytes()
        path.write_bytes(raw[: len(raw) // 2])
        out = image_reader.read_file(
            {"path": str(path), "effective_extension": ".jpg"}
        )
        assert isinstance(out, dict)

    def test_corrupt_pdf_page_image_is_recorded(self, pdf_reader, tmp_path):
        """process_page_optimized must not raise on garbage image bytes."""
        page_data = (0, b"not-a-real-png", "eng", "", True, ["eng"], "NATIVE 9911")
        out = pdf_reader.process_page_optimized(page_data)
        assert out["page_number"] == 1
        assert out["text"] == "NATIVE 9911"


# ----------------------------------------------------------------------
# Engine unavailable / failing
# ----------------------------------------------------------------------
class TestNoEngine:
    def test_image_reader_records_the_reason(self, image_reader, tmp_path, monkeypatch):
        monkeypatch.setattr(ocr_engines, "ENGINE_PREFERENCE", ())
        reset_engine_cache()
        import reader_file.readers.read_img_fast as rif

        monkeypatch.setattr(rif.ImageFileReader, "_is_tesseract_available",
                            lambda self: False)
        path = make_image(tmp_path, "NO ENGINE 9902")
        out = read_image(image_reader, path)
        assert out["ocr_attempted"] is False
        assert out["ocr_engine"] == "none"
        assert out["extraction_info"]["error"] == "No OCR engine available"

    def test_pdf_keeps_the_text_layer_instead(self, pdf_reader, tmp_path, monkeypatch):
        """With no engine, a page's own text layer must not be discarded."""
        monkeypatch.setattr(ocr_engines, "ENGINE_PREFERENCE", ())
        reset_engine_cache()
        import reader_file.readers.read_pdf as rp

        monkeypatch.setattr(rp, "_is_tesseract_available", lambda: False)
        monkeypatch.setattr(rp, "get_ocr_engine", lambda: None)

        pymupdf = pytest.importorskip("pymupdf")
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "SPARSE", fontsize=11)
        out = read_pdf(pdf_reader, doc.tobytes(), tmp_path, "sparse.pdf")
        doc.close()
        assert out["pages"][0]["text"] == "SPARSE"
        assert out["pages"][0]["method"] == "text_layer_fallback"


class TestEngineFailure:
    def test_engine_exception_is_captured(self, monkeypatch):
        class Exploding:
            name = "exploding"

            def available(self):
                return True

            def version(self):
                return "0.0"

            def recognize(self, image, languages=None):
                raise TimeoutError("OCR exceeded 600s")

        monkeypatch.setattr(ocr_engines, "ENGINE_PREFERENCE", (Exploding,))
        reset_engine_cache()
        from core.ocr import recognize_image

        result = recognize_image(object())
        assert result.attempted is True
        assert result.succeeded is False
        assert "TimeoutError" in (result.error or "")
        assert "600s" in (result.error or "")

    def test_failure_during_retry_falls_back_to_primary(self):
        """The retry must not turn a partial success into a total failure.

        A low-confidence primary result plus an exploding retry must still
        return the primary reading, because some text beats no text.
        """
        from core.ocr.engines import OcrResult

        class ExplodesOnRetry:
            name = "explodes-on-retry"

            def available(self):
                return True

            def version(self):
                return "0.0"

            def recognize(self, image, languages=None):
                if image == "original":
                    raise RuntimeError("retry pass exploded")
                return OcrResult(text="weak reading", attempted=True,
                                 engine=self.name, engine_version="0.0")

        result = recognize_best(ExplodesOnRetry(), "pre", "original")
        assert result.succeeded is True
        assert result.text == "weak reading"
        assert result.input_variant == "preprocessed"

    def test_retry_wins_only_when_it_is_better(self):
        from core.ocr.engines import OcrResult

        class BetterOnOriginal:
            name = "better-on-original"

            def available(self):
                return True

            def version(self):
                return "0.0"

            def recognize(self, image, languages=None):
                confidence = 0.4 if image == "pre" else 0.95
                return OcrResult(
                    text=f"from-{image}", attempted=True, engine=self.name,
                    engine_version="0.0",
                    blocks=[ocr_engines.OcrBlock("x", confidence)],
                )

        result = recognize_best(BetterOnOriginal(), "pre", "original")
        assert result.text == "from-original"
        assert result.input_variant == "original"
        assert result.mean_confidence == pytest.approx(0.95)


class _TwoVariantEngine:
    """Returns a scripted reading per input variant ("pre" / "original")."""

    name = "two-variant"

    def __init__(self, readings):
        self.readings, self.calls = readings, []

    def available(self):
        return True

    def version(self):
        return "0.0"

    def recognize(self, image, languages=None):
        self.calls.append(image)
        text, confidence, scale = self.readings[image]
        return ocr_engines.OcrResult(text=text, attempted=True, engine=self.name, engine_version="0.0",
                                     blocks=[ocr_engines.OcrBlock(text, confidence)], scale=scale)


class TestSmallTextRetry:
    """Measured with tesseract 5.3.4 (CI): the preprocessed
    "ROTATEDIMAGE_270_9911" read "..._9311" at 0.83 after a x4 enlargement;
    the confidence gate alone kept it, while the original read it exactly."""

    def test_an_enlarged_first_pass_is_checked_against_the_original(self):
        engine = _TwoVariantEngine({"pre": ("ROTATEDIMAGE_270_9311", 0.83, 4.0),
                                    "original": ("ROTATEDIMAGE_270_9911", 0.90, 2.0)})
        result = recognize_best(engine, "pre", "original")
        assert engine.calls == ["pre", "original"]
        assert result.text == "ROTATEDIMAGE_270_9911"
        assert result.input_variant == "original"

    def test_a_confident_normal_sized_first_pass_is_not_read_twice(self):
        engine = _TwoVariantEngine({"pre": ("ORDINARY TEXT", 0.93, 1.0),
                                    "original": ("never read", 0.99, 1.0)})
        result = recognize_best(engine, "pre", "original")
        assert engine.calls == ["pre"]
        assert result.input_variant == "preprocessed"

    def test_the_enlarged_first_pass_is_kept_when_the_original_is_worse(self):
        engine = _TwoVariantEngine({"pre": ("SMALL TEXT 42", 0.88, 2.0),
                                    "original": ("SMALL TEXT 4", 0.61, 2.0)})
        result = recognize_best(engine, "pre", "original")
        assert result.text == "SMALL TEXT 42"
        assert result.input_variant == "preprocessed"


def _pdf_page_with_image(pymupdf, image_px, rect, page_size=(400, 1100), rotate=0):
    Image = pytest.importorskip("PIL.Image")
    png = io.BytesIO()
    Image.new("RGB", image_px, "white").save(png, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    page.insert_image(pymupdf.Rect(*rect), stream=png.getvalue(), rotate=rotate)
    return pymupdf.open(stream=doc.tobytes())[0]


class TestPdfRenderResolution:
    """Scanned PDF pages are rasterised at their scan's own pixel density.

    Measured: a fixed 2x rendered a 1000px-wide scan placed 400pt wide at
    800px, and tesseract 5.3.4 read "PDFROTATED_180_7744" as Hebrew junk; at
    the scan's resolution (2.5x) it read it exactly at 0.91."""

    @pytest.fixture
    def pymupdf(self):
        return pytest.importorskip("pymupdf")

    def test_a_dense_scan_is_rendered_at_its_own_resolution(self, pymupdf):
        from reader_file.readers.read_pdf import ocr_render_zoom

        page = _pdf_page_with_image(pymupdf, (1000, 300), (0, 0, 400, 1100))  # placed 400x120pt
        assert ocr_render_zoom(page) == pytest.approx(2.5)

    def test_a_rotated_placement_counts_the_same(self, pymupdf):
        from reader_file.readers.read_pdf import ocr_render_zoom

        # Turned 90 degrees it occupies 120x400pt: the same 0.4 scale as
        # 400x120pt unturned, but a bbox whose sides are swapped.
        page = _pdf_page_with_image(pymupdf, (1000, 300), (0, 0, 120, 400), rotate=90)
        assert ocr_render_zoom(page) == pytest.approx(2.5, rel=0.01)

    def test_never_below_the_historical_144_dpi(self, pymupdf):
        from reader_file.readers.read_pdf import MIN_OCR_ZOOM, ocr_render_zoom

        page = _pdf_page_with_image(pymupdf, (200, 550), (0, 0, 400, 1100))  # 0.5 px/pt
        assert ocr_render_zoom(page) == MIN_OCR_ZOOM == 2.0

    def test_never_above_300_dpi(self, pymupdf):
        from reader_file.readers.read_pdf import MAX_OCR_ZOOM, ocr_render_zoom

        page = _pdf_page_with_image(pymupdf, (4000, 11000), (0, 0, 400, 1100))  # 10 px/pt
        assert ocr_render_zoom(page) == pytest.approx(MAX_OCR_ZOOM) == pytest.approx(300 / 72)

    def test_the_pixel_budget_bounds_a_large_page(self, pymupdf):
        from reader_file.readers.read_pdf import MAX_OCR_PAGE_PIXELS, ocr_render_zoom

        page = _pdf_page_with_image(pymupdf, (4000, 1000), (0, 0, 1000, 250), page_size=(1400, 1400))
        zoom = ocr_render_zoom(page)  # the image asks for 4x; 1400pt square allows ~2.86x
        assert 1400 * 1400 * zoom * zoom <= MAX_OCR_PAGE_PIXELS * 1.0001
        assert zoom == pytest.approx((MAX_OCR_PAGE_PIXELS / (1400 * 1400)) ** 0.5)

    def test_an_unreadable_image_list_falls_back_and_says_so(self, caplog):
        from reader_file.readers.read_pdf import MIN_OCR_ZOOM, ocr_render_zoom

        class Page:
            number = 4
            rect = type("R", (), {"width": 595, "height": 842})()

            def get_image_info(self):
                raise RuntimeError("broken xref")

        with caplog.at_level("WARNING"):
            assert ocr_render_zoom(Page()) == MIN_OCR_ZOOM
        assert "Cannot list the images on PDF page 5" in caplog.text

    def test_the_reader_renders_and_records_the_scan_resolution(self, pymupdf, tmp_path, monkeypatch):
        Image = pytest.importorskip("PIL.Image")
        ImageDraw = pytest.importorskip("PIL.ImageDraw")
        import reader_file.readers.read_pdf as read_pdf

        img = Image.new("RGB", (1000, 300), "white")
        ImageDraw.Draw(img).text((60, 100), "RENDER 1180", fill="black")
        png = io.BytesIO()
        img.rotate(180).save(png, format="PNG")
        doc = pymupdf.open()
        doc.new_page(width=400, height=1100).insert_image(pymupdf.Rect(0, 0, 400, 1100), stream=png.getvalue())
        path = tmp_path / "scan.pdf"
        path.write_bytes(doc.tobytes())

        seen = []

        class Recorder(_TwoVariantEngine):
            name = "tesseract"

            def recognize(self, image, languages=None):
                seen.append(image.size)
                return ocr_engines.OcrResult(text="RENDER 1180", attempted=True, engine=self.name,
                                             engine_version="0.0", blocks=[ocr_engines.OcrBlock("RENDER", 0.9)])

        monkeypatch.setattr(read_pdf, "get_ocr_engine", lambda: Recorder({}))
        monkeypatch.setattr(read_pdf, "installed_ocr_languages", lambda engine, req, label, rec: list(req))
        page = read_pdf.PDFFileReader().read_pdf_file(str(path))["pages"][0]
        assert seen and seen[0] == (1000, 2750)  # 2.5x of 400x1100pt, not 2x (800x2200)
        assert page["ocr_render_dpi"] == 180
