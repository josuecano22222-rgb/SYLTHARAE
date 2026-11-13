"""OCR engine layer and image-reader integration (PHASE 2A).

Before this work the reader hard-wired a single backend:

    if pytesseract and self._is_tesseract_available():

so on a host without the tesseract binary no OCR happened at all and scanned
documents produced nothing - the exact gap the Phase 1 audit recorded as its
largest evidence hole.

These tests exercise the engine abstraction with stubs (so the tesseract code
path is covered even where the binary is absent) and the real reader against
real images using the engine that is actually installed here.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.ocr.engines as ocr_engines  # noqa: E402
from core.ocr import (  # noqa: E402
    OcrBlock,
    OcrResult,
    RapidOcrEngine,
    TesseractEngine,
    ocr_available,
    ocr_engine_name,
    recognize_image,
    reset_engine_cache,
)

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


@pytest.fixture(autouse=True)
def _fresh_engine_cache():
    reset_engine_cache()
    yield
    reset_engine_cache()


def make_text_image(tmp_path, text, size=44, canvas=(1100, 260), name="scan.png"):
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    img = Image.new("RGB", canvas, "white")
    ImageDraw.Draw(img).text((40, 90), text, font=ImageFont.truetype(FONT, size),
                             fill="black")
    path = tmp_path / name
    img.save(path)
    return path


# ----------------------------------------------------------------------
# OcrResult / OcrBlock value semantics
# ----------------------------------------------------------------------
class TestOcrResult:
    def test_empty_result_did_not_succeed(self):
        assert OcrResult(attempted=True).succeeded is False

    def test_whitespace_only_did_not_succeed(self):
        assert OcrResult(text="   \n ", attempted=True).succeeded is False

    def test_text_succeeds(self):
        assert OcrResult(text="real words", attempted=True).succeeded is True

    def test_mean_confidence_ignores_unknown(self):
        """A block that reports no confidence must not drag the mean to zero."""
        result = OcrResult(
            blocks=[OcrBlock("a", 0.8), OcrBlock("b", None), OcrBlock("c", 0.6)]
        )
        assert result.mean_confidence == pytest.approx(0.7)

    def test_mean_confidence_none_when_nothing_reported(self):
        assert OcrResult(blocks=[OcrBlock("a", None)]).mean_confidence is None

    def test_unknown_confidence_is_not_zero(self):
        """'unknown' must stay distinguishable from 'certainly wrong'."""
        assert OcrBlock("a", None).confidence is None
        assert OcrBlock("b", 0.0).confidence == 0.0

    def test_to_content_fields_marks_text_as_derived(self):
        """OCR output must never be presented as native document text."""
        fields = OcrResult(text="hi", blocks=[OcrBlock("hi", 0.9)],
                           engine="rapidocr", engine_version="1.4.4",
                           attempted=True).to_content_fields()
        assert fields["ocr_derived"] is True
        assert fields["ocr_successful"] is True
        assert fields["ocr_engine"] == "rapidocr"
        assert fields["ocr_confidence"] == pytest.approx(0.9)

    def test_to_content_fields_not_derived_when_empty(self):
        fields = OcrResult(text="", attempted=True).to_content_fields()
        assert fields["ocr_derived"] is False
        assert fields["ocr_successful"] is False


# ----------------------------------------------------------------------
# Tesseract engine, driven by a stub so the path is covered without the binary
# ----------------------------------------------------------------------
class _StubOutput:
    DICT = "dict"


class _StubTesseract:
    """Records calls and replays canned text/data per PSM mode."""

    Output = _StubOutput

    def __init__(self, text_by_psm=None, data=None, languages=("eng", "heb", "ara"),
                 version="5.3.0"):
        self.text_by_psm = text_by_psm or {}
        self.data = data
        self.languages = list(languages)
        self._version = version
        self.calls = []

    def get_tesseract_version(self):
        return self._version

    def get_languages(self, config=""):
        return self.languages

    def image_to_string(self, image, lang=None, config=None):
        self.calls.append(("string", lang, config))
        psm = int(config.split("--psm")[1].strip()) if config and "--psm" in config else 3
        return self.text_by_psm.get(psm, "")

    def image_to_data(self, image, lang=None, config=None, output_type=None):
        self.calls.append(("data", lang, config))
        return self.data or {
            "text": ["", "SCAN", "MARKER"],
            "conf": ["-1", "92", "-1"],
            "left": [0, 10, 0], "top": [0, 20, 0],
            "width": [0, 100, 0], "height": [0, 30, 0],
        }


def tesseract_with_stub(stub):
    engine = TesseractEngine()
    engine._pytesseract = stub
    engine._load_attempted = True
    engine._checked = True
    engine._available = True
    engine._version = stub._version
    return engine


class TestTesseractEngine:
    def test_uses_first_psm_mode_that_yields_text(self):
        stub = _StubTesseract(text_by_psm={11: "", 6: "from psm six"})
        result = tesseract_with_stub(stub).recognize(object())
        assert result.text == "from psm six"
        assert result.engine == "tesseract"
        assert result.engine_version == "5.3.0"

    def test_confidence_parsed_from_image_to_data(self):
        stub = _StubTesseract(text_by_psm={11: "SCAN MARKER"})
        result = tesseract_with_stub(stub).recognize(object())
        # "MARKER" has conf -1 -> unknown, must not become 0.0
        confidences = [b.confidence for b in result.blocks]
        assert 0.92 in confidences
        assert None in confidences

    def test_word_with_negative_confidence_has_no_bbox_for_zero_size(self):
        stub = _StubTesseract(text_by_psm={11: "SCAN MARKER"})
        result = tesseract_with_stub(stub).recognize(object())
        bboxes = [b.bbox for b in result.blocks]
        assert (10, 20, 110, 50) in bboxes
        assert None in bboxes  # zero-width rows are not real boxes

    def test_requested_languages_filtered_to_installed(self):
        stub = _StubTesseract(text_by_psm={11: "x"}, languages=("eng", "ara"))
        result = tesseract_with_stub(stub).recognize(object(), ["heb", "ara", "zzz"])
        assert result.language == "ara"

    def test_missing_requested_language_is_not_silently_replaced(self):
        """A missing script is reported, never hidden by English fallback."""
        stub = _StubTesseract(text_by_psm={11: "x"}, languages=("deu",))
        result = tesseract_with_stub(stub).recognize(object(), ["heb"])
        assert result.language == ""
        assert result.text == ""
        assert result.error == "Missing Tesseract language data: heb"
        assert result.attempted is True

    def test_no_text_in_any_psm_is_a_clean_empty_result(self):
        stub = _StubTesseract(text_by_psm={})
        result = tesseract_with_stub(stub).recognize(object())
        assert result.succeeded is False
        assert result.attempted is True
        assert result.error is None

    def test_exception_in_one_psm_continues_to_the_next(self):
        class Exploding(_StubTesseract):
            def image_to_string(self, image, lang=None, config=None):
                if "--psm 11" in (config or ""):
                    raise RuntimeError("boom")
                return super().image_to_string(image, lang, config)

        result = tesseract_with_stub(Exploding(text_by_psm={6: "recovered"})).recognize(object())
        assert result.text == "recovered"

    def test_unavailable_engine_reports_error_not_exception(self):
        engine = TesseractEngine()
        engine._load_attempted = True
        engine._pytesseract = None
        engine._checked = True
        engine._available = False
        result = engine.recognize(object())
        assert result.attempted is True
        assert "unavailable" in (result.error or "")


# ----------------------------------------------------------------------
# RapidOCR engine, driven by a stub
# ----------------------------------------------------------------------
class _StubRapid:
    def __init__(self, payload):
        self.payload = payload

    def __call__(self, image):
        return self.payload, None


def rapid_with_stub(payload):
    engine = RapidOcrEngine()
    engine._engine = _StubRapid(payload)
    engine._load_attempted = True
    engine._checked = True
    engine._available = True
    return engine


def quad(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class TestRapidOcrEngine:
    def test_blocks_text_confidence_and_bbox(self):
        payload = [[quad(10, 20, 110, 50), "INVOICE 4471", 0.97]]
        result = rapid_with_stub(payload).recognize(_pil_white())
        assert result.text == "INVOICE 4471"
        assert result.blocks[0].confidence == pytest.approx(0.97)
        assert result.blocks[0].bbox == (10, 20, 110, 50)
        assert result.mean_confidence == pytest.approx(0.97)

    def test_multiple_blocks_join_with_newlines(self):
        payload = [
            [quad(0, 0, 10, 10), "line one", 0.9],
            [quad(0, 20, 10, 30), "line two", 0.8],
        ]
        result = rapid_with_stub(payload).recognize(_pil_white())
        assert result.text == "line one\nline two"
        assert result.block_count == 2

    def test_empty_payload_is_a_clean_empty_result(self):
        result = rapid_with_stub([]).recognize(_pil_white())
        assert result.succeeded is False
        assert result.error is None

    def test_none_payload_is_a_clean_empty_result(self):
        result = rapid_with_stub(None).recognize(_pil_white())
        assert result.succeeded is False

    def test_blank_text_blocks_are_dropped(self):
        payload = [[quad(0, 0, 10, 10), "   ", 0.9], [quad(0, 20, 10, 30), "real", 0.9]]
        result = rapid_with_stub(payload).recognize(_pil_white())
        assert result.block_count == 1

    def test_out_of_range_confidence_becomes_unknown(self):
        payload = [[quad(0, 0, 10, 10), "x", 5.0]]
        assert rapid_with_stub(payload).recognize(_pil_white()).blocks[0].confidence is None

    def test_malformed_block_is_skipped_not_fatal(self):
        payload = ["not-a-block", [quad(0, 0, 10, 10), "good", 0.9]]
        result = rapid_with_stub(payload).recognize(_pil_white())
        assert result.text == "good"

    def test_string_payload_never_fabricates_characters(self):
        """Regression: indexing a str yields chars, which read as OCR text.

        'not-a-block'[1] == 'o' previously surfaced as recognised text, so a
        malformed engine payload invented content.
        """
        payload = ["not-a-block", b"binary-blob"]
        result = rapid_with_stub(payload).recognize(_pil_white())
        assert result.text == ""
        assert result.block_count == 0

    def test_non_string_text_is_rejected(self):
        payload = [[quad(0, 0, 10, 10), 12345, 0.9]]
        assert rapid_with_stub(payload).recognize(_pil_white()).block_count == 0

    def test_short_block_is_rejected(self):
        payload = [[quad(0, 0, 10, 10), "x"]]
        assert rapid_with_stub(payload).recognize(_pil_white()).block_count == 0

    def test_engine_failure_is_reported_not_raised(self):
        class Boom:
            def __call__(self, image):
                raise RuntimeError("model exploded")

        engine = RapidOcrEngine()
        engine._engine = Boom()
        engine._load_attempted = True
        engine._checked = True
        engine._available = True
        result = engine.recognize(_pil_white())
        assert result.succeeded is False
        assert "model exploded" in (result.error or "")


def _pil_white():
    Image = pytest.importorskip("PIL.Image")
    return Image.new("RGB", (120, 60), "white")


# ----------------------------------------------------------------------
# Engine selection
# ----------------------------------------------------------------------
class TestEngineSelection:
    def test_tesseract_is_preferred_when_available(self, monkeypatch):
        """Tesseract covers this project's declared heb/eng/ara defaults."""
        monkeypatch.setattr(TesseractEngine, "available", lambda self: True)
        monkeypatch.setattr(TesseractEngine, "version", lambda self: "5.3.0")
        reset_engine_cache()
        assert ocr_engine_name() == "tesseract"

    def test_falls_back_when_tesseract_unavailable(self, monkeypatch):
        monkeypatch.setattr(TesseractEngine, "available", lambda self: False)
        monkeypatch.setattr(RapidOcrEngine, "available", lambda self: True)
        reset_engine_cache()
        assert ocr_engine_name() == "rapidocr"

    def test_no_engine_is_reported_cleanly(self, monkeypatch):
        monkeypatch.setattr(ocr_engines, "ENGINE_PREFERENCE", ())
        reset_engine_cache()
        assert ocr_available() is False
        assert ocr_engine_name() == "none"

    def test_recognize_image_without_engine_returns_error_result(self, monkeypatch):
        monkeypatch.setattr(ocr_engines, "ENGINE_PREFERENCE", ())
        reset_engine_cache()
        result = recognize_image(_pil_white())
        assert result.attempted is False
        assert result.engine == "none"
        assert "no OCR engine" in (result.error or "")

    def test_recognize_image_swallows_engine_exceptions(self, monkeypatch):
        class Bad:
            name = "bad"

            def available(self):
                return True

            def version(self):
                return "0"

            def recognize(self, image, languages=None):
                raise ValueError("unexpected")

        monkeypatch.setattr(ocr_engines, "ENGINE_PREFERENCE", (Bad,))
        reset_engine_cache()
        result = recognize_image(_pil_white())
        assert result.attempted is True
        assert "unexpected" in (result.error or "")

    def test_preference_order_is_declared_not_incidental(self):
        names = [c.name for c in ocr_engines.ENGINE_PREFERENCE]
        assert names[0] == "tesseract"
        assert "rapidocr" in names


# ----------------------------------------------------------------------
# Image reader integration (real engine, real images)
# ----------------------------------------------------------------------
class TestImageReaderOcr:
    @pytest.fixture
    def reader(self):
        from reader_file.readers.read_img_fast import ImageFileReader

        return ImageFileReader()

    def test_printed_text_is_extracted_with_provenance(self, reader, tmp_path):
        path = make_text_image(tmp_path, "SCAN PROVENANCE MARKER 5521")
        out = reader.read_file({"path": str(path), "effective_extension": ".png"})

        assert out["ocr_attempted"] is True
        assert out["ocr_successful"] is True
        assert "SCAN PROVENANCE MARKER 5521" in out["text"]
        # Provenance: engine, version, derived flag, confidence.
        assert out["ocr_engine"] in ("tesseract", "rapidocr")
        assert out["ocr_engine_version"]
        assert out["ocr_derived"] is True
        assert out["ocr_confidence"] is not None
        assert 0.0 <= out["ocr_confidence"] <= 1.0

    def test_blocks_carry_confidence_and_bbox(self, reader, tmp_path):
        path = make_text_image(tmp_path, "BLOCK COORDINATES 7781")
        out = reader.read_file({"path": str(path), "effective_extension": ".png"})
        blocks = out.get("ocr_coordinates") or []
        assert blocks, "expected per-block results"
        first = blocks[0]
        assert first["confidence"] is not None
        assert len(first["bbox"]) == 4

    def test_blank_image_yields_no_text_and_no_hallucination(self, reader, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        path = tmp_path / "blank.png"
        Image.new("RGB", (800, 400), "white").save(path)
        out = reader.read_file({"path": str(path), "effective_extension": ".png"})
        assert out["ocr_attempted"] is True
        assert out["ocr_successful"] is False
        assert out["text"] == ""
        assert out["extraction_info"]["reason"] == "no_text_extracted"

    def test_tiny_image_is_skipped_without_ocr(self, reader, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        path = tmp_path / "tiny.png"
        Image.new("RGB", (10, 10), "white").save(path)
        out = reader.read_file({"path": str(path), "effective_extension": ".png"})
        assert out["ocr_attempted"] is False
        assert out["extraction_info"]["skipped"] is True
        assert out["extraction_info"]["skip_reason"] == "too_small"

    def test_corrupt_image_is_recorded_not_raised(self, reader, tmp_path):
        path = tmp_path / "broken.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage-not-a-real-png" * 20)
        out = reader.read_file({"path": str(path), "effective_extension": ".png"})
        assert isinstance(out, dict)
        assert out.get("ocr_successful") is not True

    def test_missing_file_is_recorded(self, reader, tmp_path):
        """`read_file` reports the base-reader error shape for an absent file."""
        absent = tmp_path / "absent.png"
        out = reader.read_file({"path": str(absent), "effective_extension": ".png"})
        assert out["error"].startswith("File not found")
        assert out["path"] == str(absent)

    def test_missing_file_via_image_entry_point_keeps_status_fields(self, reader, tmp_path):
        """`read_image_file_fast` documents the richer status shape."""
        out = reader.read_image_file_fast(str(tmp_path / "absent.png"))
        assert out["ocr_attempted"] is False
        assert out["ocr_successful"] is False
        assert out["extraction_info"]["error"] == "File not found"

    def test_ocr_never_modifies_the_source_file(self, reader, tmp_path):
        """Hard requirement: OCR must not overwrite original data."""
        from core.hashing import hash_file

        path = make_text_image(tmp_path, "IMMUTABLE SOURCE 3312")
        before = hash_file(str(path))
        mtime_before = path.stat().st_mtime
        reader.read_file({"path": str(path), "effective_extension": ".png"})
        assert hash_file(str(path)) == before
        assert path.stat().st_mtime == mtime_before

    def test_no_engine_available_records_the_reason(self, reader, tmp_path, monkeypatch):
        monkeypatch.setattr(ocr_engines, "ENGINE_PREFERENCE", ())
        reset_engine_cache()
        import reader_file.readers.read_img_fast as rif

        monkeypatch.setattr(rif.ImageFileReader, "_is_tesseract_available",
                            lambda self: False)
        path = make_text_image(tmp_path, "NO ENGINE HERE 9902")
        out = reader.read_file({"path": str(path), "effective_extension": ".png"})
        assert out["ocr_attempted"] is False
        assert out["ocr_engine"] == "none"
        assert out["extraction_info"]["error"] == "No OCR engine available"


# ----------------------------------------------------------------------
# Tesseract: failures are reported, never turned into an empty result
# ----------------------------------------------------------------------
class TestTesseractFailuresAreVisible:
    """``OCR failed`` and ``OCR found nothing`` are different outcomes.

    Before v2.2.0 an ``image_to_data`` failure was swallowed by a bare
    ``except Exception: return []``: the text survived but its confidence
    became ``None`` with nothing saying why, which reads exactly like a page
    whose words carry no confidence. Every PSM mode raising produced a clean
    empty result, indistinguishable from a blank page.
    """

    def test_word_data_failure_keeps_text_and_says_why(self, caplog):
        class NoWordData(_StubTesseract):
            def image_to_data(self, image, lang=None, config=None, output_type=None):
                raise RuntimeError("tsv renderer crashed")

        with caplog.at_level("WARNING", logger="core.ocr.engines"):
            result = tesseract_with_stub(NoWordData(text_by_psm={11: "SCAN MARKER"})).recognize(object())
        assert result.text == "SCAN MARKER"
        assert result.blocks == []
        assert result.mean_confidence is None
        assert result.blocks_error == "image_to_data failed: RuntimeError: tsv renderer crashed"
        assert result.error is None  # the text itself was recognised
        assert "image_to_data failed" in caplog.text

    def test_word_data_available_leaves_no_blocks_error(self):
        result = tesseract_with_stub(_StubTesseract(text_by_psm={11: "SCAN MARKER"})).recognize(object())
        assert result.blocks_error is None
        assert result.mean_confidence == pytest.approx(0.92)

    def test_every_mode_failing_is_an_error_not_an_empty_page(self):
        class Broken(_StubTesseract):
            def image_to_string(self, image, lang=None, config=None):
                raise OSError("tesseract crashed")

        result = tesseract_with_stub(Broken()).recognize(object())
        assert result.succeeded is False
        assert result.error == "tesseract failed: psm 3: OSError: tesseract crashed"

    def test_a_page_with_no_text_is_not_an_error(self):
        result = tesseract_with_stub(_StubTesseract(text_by_psm={})).recognize(object())
        assert result.error is None and result.blocks_error is None
        assert result.succeeded is False

    @pytest.mark.parametrize("raw, expected", [
        ("96.5", 0.965), (100, 1.0), (0, 0.0), ("-1", None),
        ("250", None), ("nan", None), ("abc", None), (None, None),
    ])
    def test_confidence_values_are_scaled_or_rejected(self, raw, expected):
        value = TesseractEngine._confidence_at({"conf": [raw]}, 0)
        assert value == (pytest.approx(expected) if expected is not None else None)

    def test_missing_confidence_column_is_no_measurement(self):
        assert TesseractEngine._confidence_at({"text": ["x"]}, 0) is None


# ----------------------------------------------------------------------
# Tesseract: orientation, size and script selection (stub-driven)
# ----------------------------------------------------------------------
def _block(text, conf, bbox=(0, 0, 10, 30)):
    return ocr_engines.OcrBlock(text, conf, bbox)


class TestReadingScore:
    def test_fragments_at_high_confidence_are_not_confident(self):
        """Measured: a turned line read with heb+eng+ara came back as
        one-glyph fragments averaging 0.80."""
        fragments = OcrResult(text="5 = 5 a", attempted=True,
                              blocks=[_block(t, 0.8) for t in ("5", "=", "5", "a")])
        assert fragments.mean_confidence >= ocr_engines.LOW_CONFIDENCE_RETRY_THRESHOLD
        assert ocr_engines._is_confident(fragments) is False
        assert ocr_engines._reading_score(fragments) == 0.0

    def test_a_whole_word_at_low_confidence_beats_fragments(self):
        word = OcrResult(text="ROTATEDIMAGE_0_9911", attempted=True,
                         blocks=[_block("ROTATEDIMAGE_0_9911", 0.42)])
        fragments = OcrResult(text="2 2 8", attempted=True,
                              blocks=[_block(t, 0.59) for t in ("2", "2", "8")])
        assert ocr_engines._reading_score(word) > ocr_engines._reading_score(fragments)

    def test_words_in_any_script_count(self):
        hebrew = OcrResult(text="שלום עולם", attempted=True,
                           blocks=[_block("שלום", 0.9), _block("עולם", 0.9)])
        assert ocr_engines._is_confident(hebrew)
        assert ocr_engines._reading_score(hebrew) == pytest.approx(8 * 0.81)

    def test_failed_or_empty_readings_score_zero(self):
        assert ocr_engines._reading_score(OcrResult(text="", attempted=True)) == 0.0
        assert ocr_engines._reading_score(
            OcrResult(text="WORDS HERE", attempted=True, error="boom")) == 0.0


class TestBoxesAreInTheCallersCoordinates:
    """A box read on a turned or enlarged copy must describe the caller's image."""

    @pytest.mark.parametrize("angle", [90, 180, 270])
    def test_boxes_from_a_turned_copy_map_back(self, angle):
        np = pytest.importorskip("numpy")
        Image = pytest.importorskip("PIL.Image")
        original = Image.new("L", (300, 120), 255)
        original.paste(0, (40, 30, 90, 50))  # dark box: x 40-89, y 30-49
        turned = np.array(original.rotate(angle, expand=True))
        ys, xs = np.nonzero(turned < 128)
        seen = ocr_engines.OcrBlock("w", 0.9, (int(xs.min()), int(ys.min()),
                                               int(xs.max()) + 1, int(ys.max()) + 1))
        (mapped,) = ocr_engines._unrotate_blocks([seen], angle, original.size)
        assert mapped.bbox == (40, 30, 90, 50)

    def test_boxes_from_an_enlarged_copy_are_scaled_back(self):
        (block,) = ocr_engines._scale_blocks([_block("w", 0.9, (20, 40, 60, 100))], 0.5)
        assert block.bbox == (10, 20, 30, 50)


class _ScriptedTesseract(_StubTesseract):
    """Replays word tables chosen by (language, image size); counts readings."""

    def __init__(self, tables, **kwargs):
        super().__init__(**kwargs)
        self.tables = tables  # callable(lang, size) -> list of (word, conf, height)
        self.sizes = []

    def _table(self, image, lang):
        self.sizes.append(image.size)
        return self.tables(lang, image.size)

    def image_to_string(self, image, lang=None, config=None):
        self.calls.append(("string", lang, config))
        return " ".join(w for w, _c, _h in self._table(image, lang))

    def image_to_data(self, image, lang=None, config=None, output_type=None):
        words = self.tables(lang, image.size)
        return {
            "text": [w for w, _c, _h in words],
            "conf": [c for _w, c, _h in words],
            "left": [10 * i for i in range(len(words))],
            "top": [0] * len(words),
            "width": [8] * len(words),
            "height": [h for _w, _c, h in words],
        }


def _strings(stub):
    return [c for c in stub.calls if c[0] == "string"]


class TestSearchCost:
    def test_a_blank_image_is_read_once_without_a_search(self):
        Image = pytest.importorskip("PIL.Image")
        stub = _ScriptedTesseract(lambda lang, size: [])
        result = tesseract_with_stub(stub).recognize(Image.new("RGB", (400, 200), "white"), ["eng"])
        assert result.succeeded is False and result.error is None
        # One pass of the PSM ladder (11, 6, 3) and nothing else.
        assert len(_strings(stub)) == len(TesseractEngine.PSM_ORDER)

    def test_large_noise_is_never_enlarged(self):
        """Random noise reads as fragments; enlarging it is the slowest thing
        tesseract can be asked (measured, tesseract 5.3.4: 480k px 107 s)."""
        np = pytest.importorskip("numpy")
        Image = pytest.importorskip("PIL.Image")
        noise = Image.fromarray(np.random.default_rng(0).integers(0, 255, (300, 400, 3), dtype=np.uint8))
        assert 400 * 300 > ocr_engines.SMALL_IMAGE_PIXELS
        stub = _ScriptedTesseract(lambda lang, size: [("5", 80, 9), ("=", 80, 9), ("a", 80, 9)])
        tesseract_with_stub(stub).recognize(noise, ["eng"])
        largest = max(w * h for w, h in stub.sizes)
        assert largest == 400 * 300, "noise was enlarged"
        # Upright plus the three other orientations; no script pass (fragments).
        assert len(_strings(stub)) == 4

    def test_a_small_image_is_enlarged_even_when_its_marks_measure_tiny(self, monkeypatch):
        """CI (Ubuntu's DejaVu): a 7px line measured 5.0, below MIN_GLYPH_SIZE,
        and was never enlarged; tesseract reads it exactly at x2."""
        Image = pytest.importorskip("PIL.Image")
        monkeypatch.setattr(ocr_engines, "_glyph_size", lambda image: 5.0)

        def tables(lang, size):
            return [("LOWRESTEST", 92, 12), ("7712", 92, 12)] if size == (400, 100) else []

        stub = _ScriptedTesseract(tables)
        result = tesseract_with_stub(stub).recognize(Image.new("RGB", (200, 50), "white"), ["eng"])
        assert result.text == "LOWRESTEST 7712"
        assert result.scale == 2.0

    def test_a_large_image_with_tiny_marks_is_not_enlarged(self, monkeypatch):
        Image = pytest.importorskip("PIL.Image")
        monkeypatch.setattr(ocr_engines, "_glyph_size", lambda image: 5.0)
        stub = _ScriptedTesseract(lambda lang, size: [])
        tesseract_with_stub(stub).recognize(Image.new("RGB", (400, 300), "white"), ["eng"])
        assert set(stub.sizes) == {(400, 300)}

    def test_a_confident_upright_page_is_read_once_per_model(self):
        Image = pytest.importorskip("PIL.Image")
        stub = _ScriptedTesseract(lambda lang, size: [("ORDINARY", 96, 30), ("PRINTED", 95, 30)])
        tesseract_with_stub(stub).recognize(Image.new("RGB", (400, 200), "white"), ["heb", "eng", "ara"])
        # Combined model upright and upside down, then each language once:
        # no orientation or size search.
        assert [c[1] for c in _strings(stub)] == ["heb+eng+ara", "heb+eng+ara", "heb", "eng", "ara"]


class _UpsideDownAware(_ScriptedTesseract):
    """Reads the page by where its dark marker pixel is: top-left means
    upright, bottom-right means upside down (as after rotate(180))."""

    def __init__(self, upright, inverted):
        super().__init__(tables=None)
        self.upright, self.inverted = upright, inverted

    def _words(self, image):
        w, h = image.size
        return self.upright if image.convert("L").getpixel((1, 1)) < 128 else (
            self.inverted if image.convert("L").getpixel((w - 2, h - 2)) < 128 else [("5", 60, 9)])

    def image_to_string(self, image, lang=None, config=None):
        self.calls.append(("string", lang, config))
        return " ".join(word for word, _c, _h in self._words(image))

    def image_to_data(self, image, lang=None, config=None, output_type=None):
        self.tables = lambda _lang, _size, words=self._words(image): words
        return super().image_to_data(image, lang, config, output_type)


class TestUpsideDown:
    """Measured with tesseract 5.5: "OCR SELF CHECK 2026" upside down read as
    "9606 MOSHO 3135 YOO" at 0.82-0.85, above the 0.75 confidence bar, and
    was accepted before v2.2.0 (the self-check found it)."""

    CORRECT = [("OCR", 96, 30), ("SELF", 96, 30), ("CHECK", 96, 30), ("2026", 95, 30)]
    INVERTED = [("9606", 85, 30), ("MOSHO", 84, 30), ("3135", 83, 30), ("YOO", 82, 30)]

    @staticmethod
    def _page(upside_down):
        Image = pytest.importorskip("PIL.Image")
        page = Image.new("RGB", (400, 200), "white")
        page.putpixel((1, 1), (0, 0, 0))
        return page.rotate(180) if upside_down else page

    def test_an_upside_down_page_is_read_the_right_way_up(self):
        stub = _UpsideDownAware(upright=self.CORRECT, inverted=self.INVERTED)
        result = tesseract_with_stub(stub).recognize(self._page(upside_down=True), ["eng"])
        assert result.text == "OCR SELF CHECK 2026"
        assert result.rotation == 180

    def test_an_upright_page_stays_upright(self):
        stub = _UpsideDownAware(upright=self.CORRECT, inverted=self.INVERTED)
        result = tesseract_with_stub(stub).recognize(self._page(upside_down=False), ["eng"])
        assert result.text == "OCR SELF CHECK 2026"
        assert result.rotation == 0


class TestScriptSelection:
    def test_a_single_language_reading_beats_a_combined_one_that_lost_digits(self):
        """Measured: heb+eng+ara read "ROTATIONTEST 5521" as "ROTATIONTEST 1"."""
        Image = pytest.importorskip("PIL.Image")

        def tables(lang, size):
            if lang == "eng":
                return [("ROTATIONTEST", 94, 30), ("5521", 93, 30)]
            if lang == "heb+eng+ara":
                return [("ROTATIONTEST", 93, 30), ("1", 90, 30)]
            return [("דאסודה", 48, 30)]

        result = tesseract_with_stub(_ScriptedTesseract(tables)).recognize(
            Image.new("RGB", (400, 200), "white"), ["heb", "eng", "ara"])
        assert result.text == "ROTATIONTEST 5521"
        assert result.language == "eng"

    def test_the_turned_orientation_is_recorded_with_boxes_mapped_back(self):
        Image = pytest.importorskip("PIL.Image")
        image = Image.new("RGB", (300, 100), "white")

        def tables(lang, size):
            # Readable only when turned to portrait, i.e. rotate(90 or 270).
            return [("MARKER", 95, 30), ("4412", 94, 30)] if size == (100, 300) else [("5", 60, 9)]

        result = tesseract_with_stub(_ScriptedTesseract(tables)).recognize(image, ["eng"])
        assert result.text == "MARKER 4412"
        assert result.rotation == 90
        for block in result.blocks:
            x0, y0, x1, y1 = block.bbox
            assert 0 <= x0 < x1 <= 300 and 0 <= y0 < y1 <= 100


# ----------------------------------------------------------------------
# Stored provenance says how the text was read and why confidence is missing
# ----------------------------------------------------------------------
class TestStoredOcrProvenance:
    @staticmethod
    def _provenance(content):
        from pipeline.storage_pipeline import StoragePipeline

        return StoragePipeline._ocr_provenance(content)

    def test_rotation_and_confidence_error_are_recorded_for_an_image(self):
        prov = self._provenance({
            "ocr_attempted": True, "ocr_successful": True, "ocr_engine": "tesseract",
            "ocr_rotation": 90,
            "extraction_info": {"ocr_blocks_error": "image_to_data failed: OSError: x"},
        })
        assert prov["rotation"] == 90
        assert prov["confidence"] is None
        assert prov["confidence_error"] == "image_to_data failed: OSError: x"

    def test_missing_languages_are_recorded_for_an_image(self):
        """A file read without a requested language model says so after
        ingestion, not only in the log of the run."""
        prov = self._provenance({
            "ocr_attempted": True, "ocr_successful": True, "ocr_engine": "tesseract",
            "ocr_confidence": 0.9,
            "extraction_info": {"missing_ocr_languages": ["heb", "ara"]},
        })
        assert prov["missing_languages"] == ["heb", "ara"]

    def test_missing_languages_are_collected_over_a_pdfs_ocr_pages(self):
        prov = self._provenance({"pages": [
            {"page_number": 1, "method": "ocr_tesseract", "ocr_engine": "tesseract",
             "ocr_confidence": 0.9, "missing_ocr_languages": ["heb"]},
            {"page_number": 2, "method": "ocr_tesseract", "ocr_engine": "tesseract",
             "ocr_confidence": 0.8, "missing_ocr_languages": ["ara", "heb"]},
        ]})
        assert prov["missing_languages"] == ["ara", "heb"]

    def test_no_missing_languages_key_when_every_model_is_installed(self):
        prov = self._provenance({
            "ocr_attempted": True, "ocr_successful": True, "ocr_engine": "tesseract",
            "ocr_confidence": 0.9, "extraction_info": {},
        })
        assert "missing_languages" not in prov

    def test_an_upright_image_with_confidence_keeps_the_existing_shape(self):
        prov = self._provenance({
            "ocr_attempted": True, "ocr_successful": True, "ocr_engine": "tesseract",
            "ocr_confidence": 0.93, "extraction_info": {},
        })
        assert "rotation" not in prov and "confidence_error" not in prov
        assert prov["confidence"] == 0.93

    def test_rotated_pdf_pages_are_listed(self):
        prov = self._provenance({"pages": [
            {"page_number": 1, "method": "ocr_tesseract", "ocr_confidence": 0.9},
            {"page_number": 2, "method": "ocr_tesseract", "ocr_confidence": 0.8, "ocr_rotation": 270},
        ]})
        assert prov["rotated_pages"] == [2]
