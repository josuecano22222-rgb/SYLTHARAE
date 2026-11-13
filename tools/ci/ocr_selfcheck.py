"""OCR self-check: what the installed OCR engines actually do on this host.

    python tools/ci/ocr_selfcheck.py                       # report only
    python tools/ci/ocr_selfcheck.py --require tesseract   # fail early (CI)
    python tools/ci/ocr_selfcheck.py --json report.json

Tesseract is a system package (see docs/CONFIGURATION.md), so OCR behaviour depends
on the host rather than on requirements.txt. This reports, stage by stage:

``engines``
    each engine: available or why not, version, languages (tesseract) or
    model family (RapidOCR); which one ingestion will select.
``languages``
    requested tesseract languages that are not installed.
``samples``
    generated images read through the production reader
    (``ImageFileReader.read_file``, the path ingestion uses), so the
    confidence reported is the one stored with ingested files: upright,
    turned 90/180/270 degrees, and small text.

Each sample is classified as one of

``success``            expected text read, confidence at or above the
                       retry threshold
``low_confidence``     expected text read, confidence below the threshold
``wrong_text``         text read, but not the expected text
``no_text``            the engine ran and read nothing
``invocation_failed``  the engine ran and failed, or its confidence could
                       not be read (``confidence_error`` says why)
``unavailable``        no engine could run

with a remediation for anything other than ``success``.

Exit status: 0 without ``--require``. With ``--require ENGINE`` it is 1
when that engine is unavailable or not the one ingestion selects, when a
requested language is missing, or when the upright sample is not
``success``; turned and small samples are reported (the test suite asserts
them). Output is plain text, plus GitHub annotations under GitHub Actions.
"""
import argparse
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

EXPECTED = "OCR SELF CHECK 2026"
SAMPLE_FONT = ROOT / "static" / "fonts" / "inter-400.ttf"  # shipped, so every host draws the same image

STATUSES = ("success", "low_confidence", "wrong_text", "no_text", "invocation_failed", "unavailable")
FAILING = {"wrong_text", "no_text", "invocation_failed", "unavailable"}

REMEDIATION = {
    "unavailable": (
        "Install an OCR engine: the tesseract-ocr system package with the "
        "tesseract-ocr-heb and tesseract-ocr-ara language packs (docs/CONFIGURATION.md), "
        "or the rapidocr_onnxruntime Python package."
    ),
    "invocation_failed": (
        "The engine is installed but failed when run: check the error on the sample line, "
        "the tesseract binary on PATH (or TESSERACT_CMD), and its tessdata directory."
    ),
    "no_text": (
        "The engine ran but read nothing from a clean sample: check the "
        "language data (tesseract --list-langs) and that the binary matches its tessdata."
    ),
    "wrong_text": (
        "The engine misread a clean sample: check the tessdata models "
        "(the standard tesseract-ocr-* packages) and the engine version."
    ),
    "low_confidence": (
        "Text was read with low confidence; ingestion keeps such text and stores "
        "the confidence with the file (extraction_provenance.ocr.confidence). "
        "Check the tessdata models if clean samples read this way."
    ),
    "missing_languages": (
        "Install the missing tesseract language packs, e.g. "
        "apt-get install tesseract-ocr-heb tesseract-ocr-ara."
    ),
    "not_selected": (
        "Another engine is selected ahead of the required one: ingestion uses the "
        "first available engine in a fixed order (tesseract, then rapidocr), so "
        "rapidocr is used only where no tesseract binary is found (PATH, TESSERACT_CMD)."
    ),
}


def _collapsed(text):
    return re.sub(r"[^0-9A-Z]", "", str(text or "").upper())


def classify(out, expected=EXPECTED):
    """Classify one ``ImageFileReader.read_file`` result: (status, detail)."""
    info = out.get("extraction_info") or {}
    text = out.get("text") or ""
    if info.get("skipped"):
        return "invocation_failed", f"the reader skipped OCR ({info.get('skip_reason')})"
    if not out.get("ocr_attempted") or out.get("ocr_engine") in (None, "none"):
        return "unavailable", info.get("error") or "no OCR engine ran"
    error = info.get("engine_error") or info.get("error")
    if not text.strip():
        return ("invocation_failed", error) if error else ("no_text", "empty reading")
    confidence = out.get("ocr_confidence")
    if confidence is None:
        return "invocation_failed", "text read but no confidence: " + str(
            info.get("ocr_blocks_error") or "the engine returned no word confidences")
    if _collapsed(expected) not in _collapsed(text):
        return "wrong_text", f"expected {expected!r}"
    from core.ocr.engines import LOW_CONFIDENCE_RETRY_THRESHOLD

    if confidence < LOW_CONFIDENCE_RETRY_THRESHOLD:
        return "low_confidence", f"{confidence:.2f} < {LOW_CONFIDENCE_RETRY_THRESHOLD}"
    return "success", ""


def _sample(size=40, canvas=(1100, 200)):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", canvas, "white")
    ImageDraw.Draw(img).text((40, (canvas[1] - size) // 2), EXPECTED, fill="black",
                             font=ImageFont.truetype(str(SAMPLE_FONT), size))
    return img


def _samples():
    upright = _sample()
    yield "upright", upright
    for angle in (90, 180, 270):
        yield f"turned {angle}", upright.rotate(angle, expand=True)
    # Above the reader's MIN_OCR_DIMENSION floor, below tesseract's legible size.
    yield "small text (12px)", _sample(size=12, canvas=(320, 60))


def _engines():
    from core.ocr import engines as ocr_engines

    rows = []
    for engine_class in ocr_engines.ENGINE_PREFERENCE:
        engine = engine_class()
        row = {"name": engine.name, "available": bool(engine.available())}
        if row["available"]:
            row["version"] = engine.version()
            row["handles_orientation"] = engine.handles_orientation
            if engine.name == "tesseract":
                try:
                    row["languages"] = sorted(engine._module().get_languages(config=""))
                except Exception as exc:
                    row["languages_error"] = f"{type(exc).__name__}: {exc}"
        else:
            row["reason"] = engine.unavailable_reason or "unknown"
        rows.append(row)
    selected = ocr_engines.get_ocr_engine()
    return rows, (selected.name if selected else "none")


def _read(image, workdir, name):
    from reader_file.readers.read_img_fast import ImageFileReader

    path = Path(workdir) / (re.sub(r"\W+", "_", name) + ".png")
    image.save(path)
    return ImageFileReader().read_file({"path": str(path), "effective_extension": ".png"})


def run(require=None, languages=("heb", "eng", "ara")):
    """Run every stage; return the report (a JSON-serialisable dict)."""
    report = {"stages": {}, "problems": []}
    try:
        rows, selected = _engines()
    except Exception as exc:
        report["stages"]["engines"] = {"error": f"{type(exc).__name__}: {exc}"}
        report["problems"].append(("engines", "invocation_failed", str(exc)))
        return report
    report["stages"]["engines"] = {"engines": rows, "selected": selected}

    tesseract = next((r for r in rows if r["name"] == "tesseract"), {})
    # Only tesseract has installable language models; RapidOCR's are fixed.
    missing = ([code for code in languages if code not in tesseract.get("languages", [])]
               if tesseract.get("available") else None)
    report["stages"]["languages"] = {"requested": list(languages), "missing": missing}
    if require:
        wanted = next((r for r in rows if r["name"] == require), None)
        if wanted is None or not wanted["available"]:
            report["problems"].append(("engines", "unavailable",
                                       f"{require}: {(wanted or {}).get('reason', 'unknown engine')}"))
        elif selected != require:
            report["problems"].append(("engines", "not_selected", f"selected {selected}, required {require}"))
        if require == "tesseract" and tesseract.get("available") and missing:
            report["problems"].append(("languages", "missing_languages", ", ".join(missing)))

    samples = []
    with tempfile.TemporaryDirectory(prefix="ocr-selfcheck-") as workdir:
        for name, image in _samples():
            try:
                out = _read(image, workdir, name)
                status, detail = classify(out)
            except Exception as exc:  # reported as a stage failure, never hidden
                out, status, detail = {}, "invocation_failed", f"{type(exc).__name__}: {exc}"
            info = out.get("extraction_info") or {}
            samples.append({
                "sample": name, "status": status, "detail": detail,
                "engine": out.get("ocr_engine"), "text": (out.get("text") or "").strip()[:80],
                "confidence": out.get("ocr_confidence"), "rotation": out.get("ocr_rotation", 0),
                "scale": info.get("ocr_scale"), "input_variant": out.get("ocr_input_variant"),
                "confidence_error": info.get("ocr_blocks_error"),
                "missing_languages": out.get("missing_ocr_languages"),
            })
    report["stages"]["samples"] = samples
    upright = samples[0] if samples else None
    if require and upright and upright["status"] != "success":
        report["problems"].append(("samples", upright["status"], f"upright: {upright['detail']}"))
    return report


def _annotate(level, title, text):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        text = str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{level} title={title}::{text[:4000]}")


def _print(report):
    engines = report["stages"].get("engines", {})
    for row in engines.get("engines", []):
        if row["available"]:
            extra = (f" languages={','.join(row['languages'])}" if "languages" in row
                     else f" languages_error={row['languages_error']}" if "languages_error" in row else "")
            line = f"{row['name']}: available, version {row['version']}{extra}"
        else:
            line = f"{row['name']}: unavailable ({row['reason']})"
        print(f"[engines] {line}")
        _annotate("notice", f"OCR engine {row['name']}", line)
    if "selected" in engines:
        print(f"[engines] selected for ingestion: {engines['selected']}")
    langs = report["stages"].get("languages")
    if langs:
        missing = ("n/a (tesseract unavailable)" if langs["missing"] is None
                   else ", ".join(langs["missing"]) or "none")
        print(f"[languages] requested {','.join(langs['requested'])}; missing: {missing}")
    for s in report["stages"].get("samples", []):
        conf = "none" if s["confidence"] is None else f"{s['confidence']:.2f}"
        line = (f"{s['sample']}: {s['status']} engine={s['engine']} text={s['text']!r} confidence={conf} "
                f"rotation={s['rotation']} scale={s['scale']} input={s['input_variant']}")
        if s["confidence_error"]:
            line += f" confidence_error={s['confidence_error']!r}"
        if s["missing_languages"]:
            line += f" missing_languages={s['missing_languages']}"
        if s["detail"]:
            line += f" ({s['detail']})"
        print(f"[samples] {line}")
        if s["status"] != "success":
            print(f"          remediation: {REMEDIATION[s['status']]}")
        _annotate("notice" if s["status"] == "success" else "warning", f"OCR sample {s['sample']}", line)
    for stage, status, detail in report["problems"]:
        message = f"{stage}: {status}: {detail}. {REMEDIATION.get(status, '')}"
        print(f"[FAIL] {message}")
        _annotate("error", "OCR self-check failed", message)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--require", choices=("tesseract", "rapidocr"),
                        help="exit 1 unless this engine is selected and reads the upright sample")
    parser.add_argument("--languages", default="heb,eng,ara",
                        help="tesseract languages that must be installed (default: heb,eng,ara)")
    parser.add_argument("--json", metavar="PATH", help="also write the report as JSON")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.CRITICAL)  # the report is the output

    report = run(args.require, tuple(c for c in args.languages.split(",") if c))
    _print(report)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
