"""tools/ci/ocr_selfcheck.py is an operational diagnostic and a CI gate.

It must tell apart: no engine, engine failing, nothing read, wrong text,
low confidence and success, and report confidence from the same path
ingestion uses (``ImageFileReader.read_file``).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.ci import ocr_selfcheck  # noqa: E402
import core.ocr.engines as ocr_engines  # noqa: E402


def _out(text="OCR SELF CHECK 2026", confidence=0.95, engine="tesseract", attempted=True, **info):
    return {"text": text, "ocr_confidence": confidence, "ocr_engine": engine,
            "ocr_attempted": attempted, "extraction_info": info}


class TestClassify:
    def test_success(self):
        assert ocr_selfcheck.classify(_out()) == ("success", "")

    def test_spacing_differences_are_not_wrong_text(self):
        # RapidOCR reads the 12px sample as "OCRSELFCHECK2026".
        assert ocr_selfcheck.classify(_out(text="OCRSELFCHECK2026"))[0] == "success"

    def test_low_confidence(self):
        status, detail = ocr_selfcheck.classify(_out(confidence=0.5))
        assert status == "low_confidence" and "0.50" in detail

    def test_wrong_text(self):
        # Measured: the sample upside down, as tesseract read it before v2.2.0.
        assert ocr_selfcheck.classify(_out(text="9606 MOSHO 3135 YOO", confidence=0.85))[0] == "wrong_text"

    def test_no_text(self):
        assert ocr_selfcheck.classify(_out(text="", confidence=None))[0] == "no_text"

    def test_engine_error_is_invocation_failed(self):
        status, detail = ocr_selfcheck.classify(
            _out(text="", confidence=None, engine_error="tesseract failed: psm 3: OSError: x"))
        assert status == "invocation_failed" and "OSError" in detail

    def test_text_without_confidence_is_invocation_failed_with_the_reason(self):
        status, detail = ocr_selfcheck.classify(
            _out(confidence=None, ocr_blocks_error="image_to_data failed: RuntimeError: tsv"))
        assert status == "invocation_failed"
        assert "image_to_data failed" in detail

    def test_no_engine_is_unavailable(self):
        status, _ = ocr_selfcheck.classify(
            _out(text="", confidence=None, engine="none", attempted=False, error="No OCR engine available"))
        assert status == "unavailable"

    def test_a_reader_skip_is_not_reported_as_a_missing_engine(self):
        status, detail = ocr_selfcheck.classify(
            _out(text="", confidence=None, attempted=False, skipped=True, skip_reason="too_small"))
        assert status == "invocation_failed" and "too_small" in detail

    def test_every_status_has_a_remediation(self):
        for status in ocr_selfcheck.STATUSES:
            if status != "success":
                assert ocr_selfcheck.REMEDIATION[status]


@pytest.fixture
def fresh_selection():
    ocr_engines.reset_engine_cache()
    yield
    ocr_engines.reset_engine_cache()


@pytest.fixture
def without_tesseract(monkeypatch, fresh_selection):
    def unavailable(self):
        self.unavailable_reason = "tesseract binary not usable: forced by the test"
        return False

    monkeypatch.setattr(ocr_engines.TesseractEngine, "available", unavailable)


class TestRequire:
    def test_a_required_engine_that_is_missing_fails_with_the_reason(self, without_tesseract, capsys):
        assert ocr_selfcheck.main(["--require", "tesseract"]) == 1
        out = capsys.readouterr().out
        assert "tesseract: unavailable (tesseract binary not usable: forced by the test)" in out
        assert "[FAIL] engines: unavailable" in out
        assert "tesseract-ocr" in out  # the remediation names the package
        assert "missing: n/a (tesseract unavailable)" in out

    def test_the_fallback_engine_passes_when_it_is_the_one_required(self, without_tesseract, tmp_path):
        report_path = tmp_path / "report.json"
        assert ocr_selfcheck.main(["--require", "rapidocr", "--json", str(report_path)]) == 0
        report = json.loads(report_path.read_text())
        assert report["stages"]["engines"]["selected"] == "rapidocr"
        upright = report["stages"]["samples"][0]
        assert upright["sample"] == "upright" and upright["status"] == "success"
        assert 0.0 <= upright["confidence"] <= 1.0

    def test_a_missing_language_pack_fails_when_tesseract_is_required(self, monkeypatch, fresh_selection):
        monkeypatch.setattr(ocr_engines.TesseractEngine, "available", lambda self: True)
        monkeypatch.setattr(ocr_engines.TesseractEngine, "version", lambda self: "5.3.4")
        monkeypatch.setattr(ocr_selfcheck, "_samples", lambda: iter(()))  # engines and languages only

        class Mod:
            @staticmethod
            def get_languages(config=""):
                return ["eng", "osd"]

        monkeypatch.setattr(ocr_engines.TesseractEngine, "_module", lambda self: Mod)
        report = ocr_selfcheck.run("tesseract", ("heb", "eng", "ara"))
        assert report["stages"]["languages"]["missing"] == ["heb", "ara"]
        assert ("languages", "missing_languages", "heb, ara") in report["problems"]


def test_the_host_engine_reads_every_sample(fresh_selection):
    """End to end on whatever engine this host selects (tesseract in CI):
    the same samples, the same reader and the same confidence as ingestion."""
    report = ocr_selfcheck.run()
    samples = {s["sample"]: s for s in report["stages"]["samples"]}
    assert set(samples) == {"upright", "turned 90", "turned 180", "turned 270", "small text (12px)"}
    for name, sample in samples.items():
        assert sample["status"] == "success", (name, sample)
        assert 0.0 <= sample["confidence"] <= 1.0
    assert samples["upright"]["engine"] == report["stages"]["engines"]["selected"]


def test_remediations_name_only_real_settings():
    """A remediation that names a setting the code does not read sends the
    operator nowhere (one named ``OCR_ENGINE``, which never existed)."""
    import re
    from pathlib import Path

    configuration = (Path(__file__).resolve().parents[2] / "docs" / "CONFIGURATION.md").read_text()
    for status, text in ocr_selfcheck.REMEDIATION.items():
        for name in re.findall(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b", text):
            assert f"`{name}`" in configuration, f"{status}: {name} is not a documented setting"
