"""Pluggable OCR engine layer (PHASE 2A).

The reader layer previously hard-wired a single OCR backend::

    if pytesseract and self._is_tesseract_available():
        ...

so on any host without the ``tesseract`` system binary, **no** OCR happened at
all and scanned documents produced nothing. This module makes OCR a first-class,
substitutable capability.

Engine preference is deliberate, not incidental:

1. **tesseract** - supports the languages this project declares as defaults
   (``heb``, ``eng``, ``ara``; see ``DEFAULT_OCR_LANGUAGES``) and has an OSD
   script detector. Preferred whenever present.
2. **rapidocr** - PaddleOCR PP-OCRv4 models running under ONNX Runtime. Pure
   pip, no system binary, so it works in environments where tesseract cannot be
   installed. Latin + Chinese; it does **not** cover Hebrew or Arabic.

Every result carries explicit provenance - ``engine``, ``engine_version``,
``language`` - so downstream storage and display can state *how* text was
derived rather than presenting OCR output as if it were source text.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from core.optional_imports import import_opencv

logger = logging.getLogger(__name__)

BBox = Tuple[int, int, int, int]


@dataclass(frozen=True)
class OcrBlock:
    """One recognised text region.

    Attributes:
        text: The recognised text.
        confidence: 0.0-1.0 as reported by the engine, or ``None`` when the
            engine does not report confidence. Kept distinct from 0.0 so
            "unknown" is never mistaken for "certainly wrong".
        bbox: ``(x0, y0, x1, y1)`` in source-image pixels, or ``None``.
    """

    text: str
    confidence: Optional[float] = None
    bbox: Optional[BBox] = None


@dataclass
class OcrResult:
    """Engine-agnostic OCR outcome with provenance."""

    text: str = ""
    blocks: List[OcrBlock] = field(default_factory=list)
    engine: str = "none"
    engine_version: str = ""
    language: str = ""
    attempted: bool = False
    error: Optional[str] = None
    #: Which input variant produced this result: "preprocessed", "original",
    #: or "" when the caller passed a single image. Part of provenance - it
    #: records that the text came from a retry, not the primary pass.
    input_variant: str = ""
    #: Degrees (counter-clockwise, as ``PIL.Image.rotate``) the input was
    #: turned before this reading. Block boxes are always in the coordinates
    #: of the image the caller passed, whatever the rotation.
    rotation: int = 0
    #: Factor the input was enlarged by before this reading (small text).
    scale: float = 1.0
    #: Text was recognised but its word boxes and confidences could not be
    #: obtained; says why. ``mean_confidence`` is then ``None`` because the
    #: engine produced no measurement, not because it measured nothing.
    blocks_error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return bool(self.text and self.text.strip())

    @property
    def mean_confidence(self) -> Optional[float]:
        """Mean confidence (0.0-1.0) over blocks that reported one.

        ``None`` means no block carried a confidence: there was no text, the
        engine reports none for these words, or ``blocks_error`` says why the
        word data was unavailable. It never stands for a low score.
        """
        values = [b.confidence for b in self.blocks if b.confidence is not None]
        return sum(values) / len(values) if values else None

    @property
    def block_count(self) -> int:
        return len(self.blocks)

    def to_content_fields(self) -> dict:
        """Flatten into the keys the storage layer consumes.

        OCR output is always labelled as derived: ``ocr_derived`` is True so a
        consumer can never mistake recognised text for native document text.
        """
        return {
            "ocr_attempted": self.attempted,
            "ocr_successful": self.succeeded,
            "ocr_engine": self.engine,
            "ocr_engine_version": self.engine_version,
            "ocr_derived": self.succeeded,
            "ocr_confidence": self.mean_confidence,
            "ocr_block_count": self.block_count,
            "ocr_input_variant": self.input_variant,
        }


def _is_image(image: Any) -> bool:
    """True for an image that can be measured, rotated and resized (PIL)."""
    return all(hasattr(image, name) for name in ("rotate", "resize", "size"))


#: A word with fewer letters or digits than this is not counted as evidence
#: of a correct reading (see :func:`_reading_score`).
SUBSTANTIVE_WORD_LENGTH = 3

#: Confidence assumed for scoring a reading whose engine reported none.
UNMEASURED_CONFIDENCE = 0.25


def _alnum_length(text: str) -> int:
    """Letters and digits in ``text``, in any script."""
    return sum(1 for ch in text if ch.isalnum())


def _is_confident(result: OcrResult) -> bool:
    """A reading good enough that no orientation or size retry is needed.

    High mean confidence is not enough on its own: a turned line read with
    Hebrew and Arabic models loaded came back as one-glyph fragments at 0.80.
    Most of the text must also be in words (see :func:`_reading_score`).
    """
    confidence = result.mean_confidence
    if not result.succeeded or confidence is None:
        return False
    return confidence >= LOW_CONFIDENCE_RETRY_THRESHOLD and _is_mostly_words(result)


def _is_mostly_words(result: OcrResult) -> bool:
    """At least half the letters and digits read are in words.

    Measured: real text (any size, correct orientation) reads at 1.00, a
    turned line at 0.00, 1200x800 random noise at 0.16.
    """
    total = sum(_alnum_length(b.text) for b in result.blocks)
    words = sum(
        _alnum_length(b.text) for b in result.blocks
        if _alnum_length(b.text) >= SUBSTANTIVE_WORD_LENGTH
    )
    return total > 0 and words * 2 >= total


def _reading_score(result: OcrResult) -> float:
    """How much text a reading recognised confidently, to compare readings
    of the same image (turned, enlarged, or with another language model).

    The sum, over words of at least ``SUBSTANTIVE_WORD_LENGTH`` letters or
    digits, of that count times the word's confidence squared.

    Mean confidence alone is the wrong measure. Text read in the wrong
    orientation comes back as one- and two-glyph fragments ("5|=|5",
    "N|wo|Ww") that can average 0.6-0.8, while the correct orientation of a
    small font reads the whole marker as one word at ~0.40. Counting only
    words, weighted by length, rewards reading real text; squaring keeps
    doubtful words from outweighing confident ones. Measured on the 11px and
    44px fixtures in all four orientations, each read at a legible size, the
    correct orientation scored 4.2-7.3x the best wrong one.
    A word without a confidence counts at ``UNMEASURED_CONFIDENCE``.
    Ties keep the earlier reading, so the upright one wins when nothing
    reads better.
    """
    if result.error or not result.succeeded:
        return 0.0
    blocks = result.blocks or [OcrBlock(w, None) for w in result.text.split()]
    score = 0.0
    for block in blocks:
        length = _alnum_length(block.text)
        if length < SUBSTANTIVE_WORD_LENGTH:
            continue
        confidence = UNMEASURED_CONFIDENCE if block.confidence is None else block.confidence
        score += length * confidence * confidence
    return score


def _median_block_height(blocks: Sequence[OcrBlock]) -> Optional[float]:
    """Median box height of the words (fragments only when there are none)."""
    words = [b for b in blocks if b.bbox and _alnum_length(b.text) >= SUBSTANTIVE_WORD_LENGTH]
    heights = sorted(b.bbox[3] - b.bbox[1] for b in (words or [b for b in blocks if b.bbox]))
    if not heights:
        return None
    return float(heights[len(heights) // 2])


def _scale_blocks(blocks: List[OcrBlock], factor: float) -> List[OcrBlock]:
    """Boxes read on an enlarged copy, expressed in the original's pixels."""
    return [
        OcrBlock(b.text, b.confidence,
                 tuple(int(round(v * factor)) for v in b.bbox) if b.bbox else None)
        for b in blocks
    ]


def _unrotate_point(x: int, y: int, angle: int, size: Tuple[int, int]) -> Tuple[int, int]:
    """Map a point of ``image.rotate(angle, expand=True)`` back to ``image``.

    ``PIL.Image.rotate`` turns counter-clockwise; ``size`` is the original's.
    """
    width, height = size
    if angle == 90:
        return width - y, x
    if angle == 180:
        return width - x, height - y
    if angle == 270:
        return y, height - x
    return x, y


def _unrotate_blocks(blocks: List[OcrBlock], angle: int, size: Tuple[int, int]) -> List[OcrBlock]:
    """Boxes read on a turned copy, expressed in the original's pixels."""
    out = []
    for b in blocks:
        bbox = None
        if b.bbox:
            x0, y0 = _unrotate_point(b.bbox[0], b.bbox[1], angle, size)
            x1, y1 = _unrotate_point(b.bbox[2], b.bbox[3], angle, size)
            bbox = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        out.append(OcrBlock(b.text, b.confidence, bbox))
    return out


def _in_caller_coordinates(result: OcrResult, angle: int, turned_from: Tuple[int, int],
                           factor: int) -> OcrResult:
    """Record how ``result`` was read and express its boxes for the caller.

    ``result`` was read on ``work.rotate(angle, expand=True)`` where ``work``
    (size ``turned_from``) is the caller's image enlarged ``factor`` times.
    Its boxes are in the turned image's pixels (any enlargement made while
    reading it is already undone); ``result.scale`` then records the total.
    """
    blocks = _unrotate_blocks(result.blocks, angle, turned_from) if angle else result.blocks
    result.blocks = _scale_blocks(blocks, 1.0 / factor) if factor != 1 else blocks
    result.rotation = angle
    result.scale = float(factor) * (result.scale or 1.0)
    return result


#: Median glyph size (px, see :func:`_glyph_size`) below which marks are not
#: treated as text. Measured: 1200x800 random noise 4.0; the smallest real
#: text in the test matrix (a 7px font) 7.5 and PIL's default font 8.0.
MIN_GLYPH_SIZE = 6

#: Images up to this many pixels get the size search even when their marks
#: measure below MIN_GLYPH_SIZE: there, tesseract decides what is legible.
#: The glyph gate exists for cost, and its measurement depends on the
#: renderer: the 7px line measured 7.5 on Debian's DejaVu 2.37-6 but 5.0 on
#: Ubuntu 24.04's (CI), where letters do not merge, so a fixed floor refused
#: real text. Measured worst case (random noise, tesseract 5.3.4, search to
#: x4): 38k px 6.2 s, 120k px 15.8 s, 240k px 33 s, 480k px 107 s.
SMALL_IMAGE_PIXELS = 40_000


def _glyph_size(image: Any) -> Optional[float]:
    """Median size of the dark marks in ``image``, whatever its orientation.

    Otsu threshold, then connected components; each component's larger side
    is roughly a glyph's height in any of the four orientations. Components
    under 4 pixels of area are specks. ``None`` when nothing is measured or
    OpenCV is unavailable (the search then runs at the given size).
    """
    try:
        import numpy as np
    except ImportError:
        return None
    cv2 = import_opencv()
    if cv2 is None:
        return None
    try:
        gray = np.array(image.convert("L"))
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    except Exception as exc:
        logger.debug("glyph measurement failed: %s", exc)
        return None
    if count <= 1:
        return None
    stats = stats[1:]
    sizes = np.maximum(stats[:, cv2.CC_STAT_HEIGHT], stats[:, cv2.CC_STAT_WIDTH])
    sizes = sizes[stats[:, cv2.CC_STAT_AREA] >= 4]
    return float(np.median(sizes)) if sizes.size else None


def _lanczos():
    try:
        from PIL import Image

        return Image.Resampling.LANCZOS
    except (ImportError, AttributeError):  # pragma: no cover - Pillow < 9.1
        from PIL import Image

        return Image.LANCZOS


class BaseOcrEngine(ABC):
    """Interface every OCR backend must satisfy."""

    #: Stable identifier recorded as provenance.
    name: str = "base"

    #: True when ``recognize`` itself reads turned text (so callers need not
    #: try rotations): tesseract through its orientation search, RapidOCR
    #: through its text-direction classifier.
    handles_orientation: bool = False

    #: Why ``available()`` returned False, for diagnostics; None otherwise.
    unavailable_reason: Optional[str] = None

    @abstractmethod
    def available(self) -> bool:
        """Return True if this engine can actually run on this host."""

    @abstractmethod
    def version(self) -> str:
        """Return a human-readable engine/model version string."""

    @abstractmethod
    def recognize(
        self, image: Any, languages: Optional[Sequence[str]] = None
    ) -> OcrResult:
        """Recognise text in a PIL image. Must not raise for bad input."""

    def _result(self, **kwargs) -> OcrResult:
        kwargs.setdefault("engine", self.name)
        kwargs.setdefault("engine_version", self.version())
        kwargs.setdefault("attempted", True)
        return OcrResult(**kwargs)


def _tesseract_candidates() -> List[str]:
    """Plausible tesseract executable locations, in preference order.

    pytesseract resolves the binary through PATH. On Windows the standard
    installer writes ``C:\\Program Files\\Tesseract-OCR\\tesseract.exe`` and
    does not necessarily extend PATH, so a correct installation would otherwise
    be invisible and OCR would silently degrade to whatever fallback exists -
    with no indication that a working engine was present. An explicit
    TESSERACT_CMD always wins.
    """
    explicit = os.environ.get("TESSERACT_CMD")
    found: List[str] = []
    if explicit:
        found.append(explicit)
    if os.name == "nt":
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base:
                found.append(
                    os.path.join(base, "Tesseract-OCR", "tesseract.exe")
                )
    else:
        found.extend((
            "/usr/bin/tesseract",
            "/usr/local/bin/tesseract",
            "/opt/homebrew/bin/tesseract",
        ))
    return found


def _configure_tesseract_cmd(mod) -> None:
    """Point pytesseract at a binary that is installed but not on PATH."""
    try:
        current = getattr(mod.pytesseract, "tesseract_cmd", "")
    except Exception:
        return
    if current and current != "tesseract":
        return  # already configured explicitly by the caller
    for candidate in _tesseract_candidates():
        if candidate and os.path.isfile(candidate):
            try:
                mod.pytesseract.tesseract_cmd = candidate
                logger.info("Using tesseract binary at %s", candidate)
            except Exception:
                logger.debug("Could not set tesseract_cmd", exc_info=True)
            return


class TesseractEngine(BaseOcrEngine):
    """pytesseract-backed engine. Preferred when the binary is installed."""

    name = "tesseract"
    handles_orientation = True

    #: Page-segmentation modes tried in order. PSM 11 (sparse) finds text in
    #: any layout; PSM 6 (uniform block) preserves paragraph structure.
    PSM_ORDER = (11, 6, 3)

    def __init__(self):
        self._pytesseract = None
        self._load_attempted = False
        self._checked = False
        self._available = False
        self._version = ""
        self._lock = threading.Lock()

    def _module(self):
        """Import pytesseract once. Guarded by its own flag so that the
        availability probe can set ``_checked`` without disabling the load."""
        if not self._load_attempted:
            self._load_attempted = True
            try:
                import pytesseract

                _configure_tesseract_cmd(pytesseract)
                self._pytesseract = pytesseract
            except ImportError:
                self._pytesseract = None
        return self._pytesseract

    def available(self) -> bool:
        with self._lock:
            if self._checked:
                return self._available
            self._checked = True
            mod = self._module()
            if mod is None:
                self.unavailable_reason = "the pytesseract package is not installed"
                return False
            try:
                self._version = str(mod.get_tesseract_version())
                self._available = True
            except Exception as exc:
                self._available = False
                self.unavailable_reason = f"tesseract binary not usable: {type(exc).__name__}: {exc}"
                logger.debug("tesseract binary not usable", exc_info=True)
            return self._available

    def version(self) -> str:
        if not self._version:
            self.available()
        return self._version or "unknown"

    def _languages(self, languages: Optional[Sequence[str]]) -> Tuple[str, List[str]]:
        mod = self._module()
        requested = [code for code in (languages or []) if code]
        if not requested:
            return "eng", []
        try:
            installed = set(mod.get_languages(config=""))
        except Exception:
            installed = set()
        missing = [code for code in requested if code not in installed]
        usable = [code for code in requested if code in installed]
        return "+".join(usable), missing

    def recognize(
        self, image: Any, languages: Optional[Sequence[str]] = None
    ) -> OcrResult:
        if not self.available():
            return self._result(error="tesseract unavailable")

        mod = self._module()
        lang, missing_languages = self._languages(languages)
        if missing_languages:
            # Do not silently downgrade heb+eng+ara to whichever one happened
            # to be installed. That would discard scripts without reporting it.
            return self._result(
                language=lang,
                error="Missing Tesseract language data: " + ", ".join(missing_languages),
            )
        if not lang:
            return self._result(error="No requested Tesseract language data is installed")

        if not _is_image(image):
            # Nothing to rotate or measure (a stub, or a caller-built object):
            # one reading, as the engine always did.
            return self._read(mod, image, lang)

        # 1. The upright reading, with every requested language loaded. Most
        # pages end here: confident, so no orientation or size search, only
        # the check against the page read upside down.
        best = self._read_at_legible_size(mod, image, lang)
        if best.error:
            return best
        if _is_confident(best):
            best = self._against_inverted(mod, image, lang, best, {(1, 0)})
        else:
            best = self._search(mod, image, lang, best)

        # 2. Script. With several languages loaded at once tesseract lets each
        # word pick a model, which is where it goes wrong: measured, heb+eng+ara
        # read "ROTATIONTEST 5521" as "ROTATIONTEST 1", while eng alone was
        # exact (and on Hebrew the combined model kept digits heb alone lost).
        # So each language also reads the page on its own and the best-scoring
        # reading wins. Skipped when what was read is mostly fragments
        # (noise, a photo): there is no script to choose.
        codes = lang.split("+")
        if len(codes) > 1 and best.succeeded and _is_mostly_words(best):
            work, factor = self._working_copy(image, best.scale)
            source = work.rotate(best.rotation, expand=True) if best.rotation else work
            for code in codes:
                candidate = (self._read(mod, source, code) if factor > 1
                             else self._read_at_legible_size(mod, source, code))
                if _reading_score(candidate) > _reading_score(best):
                    best = _in_caller_coordinates(candidate, best.rotation, work.size, factor)
        return best

    def _search(self, mod, image, lang: str, upright: OcrResult) -> OcrResult:
        """Orientation (and, for small glyphs, size) search for a doubtful page.

        Tesseract's own orientation detector (OSD) declines anything shorter
        than a few lines ("Too few characters"): most labels, photos and
        one-line scans. So each orientation is read and scored instead.

        Small text reads as fragments in every orientation with all three
        language models loaded, so it cannot be told from noise by what was
        read. The glyphs themselves are measured instead (:func:`_glyph_size`)
        and, when they are small (or the image is too small for the
        measurement to matter, SMALL_IMAGE_PIXELS), enlarged copies compete with the original
        size: an enlargement is kept only when it reads better (on an already
        blurred scan it can read worse). Readings: three more orientations;
        for small glyphs, four at x2 and the best orientation at each larger
        factor, stopping at the first confident reading, which is then checked
        against its upside-down counterpart (:meth:`_against_inverted`).
        """
        glyph = _glyph_size(image)
        small_image = image.size[0] * image.size[1] <= SMALL_IMAGE_PIXELS
        factors = (self._upscale_factors(image, glyph)
                   if glyph is not None and glyph < self.MIN_TEXT_HEIGHT
                   and (glyph >= MIN_GLYPH_SIZE or small_image)
                   else [])
        if not upright.succeeded and not factors:
            # Nothing read and no small glyphs: blank, a photo, a gradient.
            # Turned text reads as fragments, not as nothing, so there is no
            # rotation to look for.
            return upright

        best = upright
        seen = {(1, 0)}
        plan = [(1, angle) for angle in self.ORIENTATIONS]
        if factors:
            plan += [(factors[0], angle) for angle in (0,) + self.ORIENTATIONS]
        for factor, angle in plan:
            if (factor, angle) in seen:
                continue
            seen.add((factor, angle))
            best = self._consider(mod, image, lang, best, factor, angle)
            if _is_confident(best):
                return self._against_inverted(mod, image, lang, best, seen)
        for factor in factors[1:]:
            seen.add((factor, best.rotation))
            best = self._consider(mod, image, lang, best, factor, best.rotation)
            if _is_confident(best):
                return self._against_inverted(mod, image, lang, best, seen)
        return best

    def _against_inverted(self, mod, image, lang, best, seen):
        """Compare a confident reading with the same page turned 180 degrees.

        Confidence cannot tell a line from itself upside down: measured on a
        sans-serif "OCR SELF CHECK 2026", the inverted page read
        "9606 MOSHO 3135 YOO" at 0.82-0.85 (O, S, H, X, Z, 0 are symmetric;
        6 and 9 swap) against 0.96 upright, while PIL's default font reads
        correctly at only 0.86. No threshold separates the two, so the
        counterpart is read (unless already read) and the higher score kept.
        """
        factor = max(1, int(round(best.scale or 1.0)))
        angle = (best.rotation + 180) % 360
        if (factor, angle) in seen:
            return best
        seen.add((factor, angle))
        return self._consider(mod, image, lang, best, factor, angle)

    def _consider(self, mod, image, lang, best, factor, angle):
        """Read ``image`` enlarged ``factor`` times and turned ``angle``; return
        whichever of that reading and ``best`` scores higher."""
        work, factor = self._working_copy(image, factor)
        try:
            turned = work.rotate(angle, expand=True) if angle else work
        except Exception as exc:
            logger.warning("tesseract: cannot rotate the image by %s: %s", angle, exc)
            return best
        candidate = self._read(mod, turned, lang)
        if _reading_score(candidate) > _reading_score(best):
            return _in_caller_coordinates(candidate, angle, work.size, factor)
        return best

    def _upscale_factors(self, image, text_height: float) -> List[int]:
        """Enlargements to try for text ``text_height`` px tall, smallest first:
        x2 up to the one reaching TARGET_TEXT_HEIGHT, within MAX_UPSCALE and the
        pixel budget. Measured on a 7px font: exact at x2 and x3 (0.92), a
        stray character from x4, so the smallest confident one is kept."""
        ceiling = max(2, min(self.MAX_UPSCALE, math.ceil(self.TARGET_TEXT_HEIGHT / text_height)))
        width, height = image.size
        return [f for f in range(2, ceiling + 1)
                if width * height * f * f <= self.MAX_UPSCALED_PIXELS]

    @staticmethod
    def _working_copy(image, factor):
        factor = int(factor) if factor and factor > 1 else 1
        if factor == 1:
            return image, 1
        width, height = image.size
        try:
            return image.resize((width * factor, height * factor), _lanczos()), factor
        except Exception as exc:
            logger.warning("tesseract: cannot enlarge the image x%s: %s", factor, exc)
            return image, 1

    #: Orientations tried when the upright reading is empty or doubtful.
    ORIENTATIONS = (90, 180, 270)

    #: Median word height (px) below which the image is enlarged and read
    #: again. Tesseract's guidance is text of roughly 20px or more; measured on
    #: PIL's default 11px font, the reading went from "POTATEDIMAGE.0.9911" at
    #: 0.42 to the exact marker at 0.86 when doubled.
    MIN_TEXT_HEIGHT = 20
    #: Height the enlargement aims for, and its bounds.
    TARGET_TEXT_HEIGHT = 32
    MAX_UPSCALE = 4
    #: Pixel budget for an enlarged copy (memory and time stay bounded): a
    #: 12 MP photo or a 300 dpi A4 scan is never enlarged.
    MAX_UPSCALED_PIXELS = 16_000_000

    def _read_at_legible_size(self, mod, image, lang: str) -> OcrResult:
        """Read ``image``; read enlarged copies too when its words are small.

        Enlargements are tried smallest first and one is kept only when it
        scores higher; its boxes are scaled back so they describe ``image``.
        """
        result = self._read(mod, image, lang)
        if result.error or not result.succeeded or not _is_mostly_words(result):
            return result
        # Only words are evidence of small text here. Fragments (a turned
        # page, noise, a photo) are not: enlarging those multiplies the work
        # for nothing. The orientation search measures glyphs instead.
        words = [b for b in result.blocks
                 if b.bbox and _alnum_length(b.text) >= SUBSTANTIVE_WORD_LENGTH]
        height = _median_block_height(words)
        if height is None or height >= self.MIN_TEXT_HEIGHT:
            return result
        best = result
        for factor in self._upscale_factors(image, height):
            enlarged, factor = self._working_copy(image, factor)
            if factor == 1:
                break
            candidate = self._read(mod, enlarged, lang)
            if _reading_score(candidate) > _reading_score(best):
                candidate.scale = float(factor)
                candidate.blocks = _scale_blocks(candidate.blocks, 1.0 / factor)
                best = candidate
                if _is_confident(best):
                    break
        return best

    def _read(self, mod, image, lang: str) -> OcrResult:
        """One reading: the first PSM mode that yields text wins.

        A mode that raises moves on to the next. If every mode raised, the
        result carries the error: "tesseract could not run" and "tesseract
        found no text" are different outcomes and are reported differently.
        """
        failures: List[str] = []
        for psm in self.PSM_ORDER:
            config = f"--oem 3 --psm {psm}"
            try:
                text, data, data_error = self._recognize_once(mod, image, lang, config)
            except Exception as exc:
                logger.debug("tesseract psm %s failed: %s", psm, exc)
                failures.append(f"psm {psm}: {type(exc).__name__}: {exc}")
                continue
            if text and text.strip():
                return self._result(
                    text=text.rstrip(),
                    blocks=self._blocks_from_data(data) if data is not None else [],
                    language=lang,
                    blocks_error=data_error,
                )
        if failures and len(failures) == len(self.PSM_ORDER):
            logger.warning("tesseract failed in every mode: %s", "; ".join(failures))
            return self._result(
                text="", blocks=[], language=lang,
                error="tesseract failed: " + failures[-1],
            )
        return self._result(text="", blocks=[], language=lang)

    def _recognize_once(self, mod, image, lang: str, config: str):
        """Return ``(text, image_to_data dict or None, why data is missing)``.

        Tesseract writes the text and the word table (TSV) from a single
        recognition when asked for both, so that is tried first; it halves
        the work of the orientation and size retries. With a pytesseract that
        does not expose those primitives, the two-call sequence is used.
        Raises when the text itself cannot be obtained.
        """
        # These primitives live in the ``pytesseract.pytesseract`` submodule;
        # the package itself does not re-export them.
        primitives = getattr(mod, "pytesseract", mod)
        run_tesseract = getattr(primitives, "run_tesseract", None)
        file_to_dict = getattr(primitives, "file_to_dict", None)
        save_image = getattr(primitives, "save", None)
        if run_tesseract is not None and file_to_dict is not None and save_image is not None:
            try:
                with save_image(image) as (base, input_filename):
                    run_tesseract(
                        input_filename=input_filename,
                        output_filename_base=base,
                        extension="txt",
                        lang=lang,
                        config=f"-c tessedit_create_tsv=1 {config}",
                        nice=0,
                        timeout=0,
                    )
                    with open(f"{base}.txt", encoding="utf-8") as handle:
                        text = handle.read()
                    with open(f"{base}.tsv", encoding="utf-8") as handle:
                        tsv = handle.read()
                return text, file_to_dict(tsv, "\t", -1), None
            except Exception as exc:
                logger.debug(
                    "tesseract single-run txt+tsv unavailable (%s: %s); "
                    "using separate text and word-data calls", type(exc).__name__, exc,
                )

        text = mod.image_to_string(image, lang=lang, config=config)
        if not text or not text.strip():
            return text, None, None
        try:
            data = mod.image_to_data(
                image, lang=lang, config=config, output_type=mod.Output.DICT
            )
        except Exception as exc:
            # The text stands; without the word table there are no boxes and
            # no confidence, and the result says why instead of looking like
            # a reading that simply had no words.
            logger.warning(
                "tesseract image_to_data failed; no word confidences: %s: %s",
                type(exc).__name__, exc,
            )
            return text, None, f"image_to_data failed: {type(exc).__name__}: {exc}"
        return text, data, None

    def _blocks_from_data(self, data: dict) -> List[OcrBlock]:
        """Per-word blocks with confidence from an ``image_to_data`` table."""
        blocks: List[OcrBlock] = []
        words = data.get("text") or []
        for i, word in enumerate(words):
            if not word or not str(word).strip():
                continue
            confidence = self._confidence_at(data, i)
            bbox = self._bbox_at(data, i)
            blocks.append(OcrBlock(str(word).strip(), confidence, bbox))
        return blocks

    @staticmethod
    def _confidence_at(data: dict, index: int) -> Optional[float]:
        """Tesseract's 0-100 word confidence as 0.0-1.0.

        ``-1`` is tesseract's "not a recognised word" and yields ``None``.
        A missing, non-numeric or out-of-range value is malformed output: it
        also yields ``None`` (no measurement), and is logged, never guessed.
        """
        try:
            raw = float(data["conf"][index])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.debug("tesseract: no usable confidence for word %s: %s", index, exc)
            return None
        if raw < 0:
            return None
        if raw > 100 or raw != raw:  # NaN compares unequal to itself
            logger.debug("tesseract: confidence %r for word %s is out of range", raw, index)
            return None
        return raw / 100.0

    @staticmethod
    def _bbox_at(data: dict, index: int) -> Optional[BBox]:
        try:
            left = int(data["left"][index])
            top = int(data["top"][index])
            width = int(data["width"][index])
            height = int(data["height"][index])
        except Exception:
            return None
        if width <= 0 or height <= 0:
            return None
        return (left, top, left + width, top + height)


class RapidOcrEngine(BaseOcrEngine):
    """PaddleOCR PP-OCRv4 under ONNX Runtime. Pure pip, no system binary.

    Covers Latin and Chinese. It does not cover Hebrew or Arabic, so it is a
    fallback rather than a replacement for tesseract in this project.
    """

    name = "rapidocr"
    handles_orientation = True

    def __init__(self):
        self._engine = None
        self._load_attempted = False
        self._checked = False
        self._available = False
        self._lock = threading.Lock()

    def _instance(self):
        """Build the ONNX engine once. Guarded by its own flag so that the
        availability probe can set ``_checked`` without disabling the load."""
        if not self._load_attempted:
            self._load_attempted = True
            try:
                from rapidocr_onnxruntime import RapidOCR

                self._engine = RapidOCR()
            except Exception as exc:
                self._engine = None
                self.unavailable_reason = f"rapidocr_onnxruntime not usable: {type(exc).__name__}: {exc}"
                logger.debug("rapidocr not importable", exc_info=True)
        return self._engine

    def available(self) -> bool:
        with self._lock:
            if self._checked:
                return self._available
            self._checked = True
            self._available = self._instance() is not None
            return self._available

    def version(self) -> str:
        """Distribution version plus the bundled model family.

        ``rapidocr_onnxruntime`` exposes no ``__version__`` attribute, so the
        installed distribution metadata is authoritative.
        """
        try:
            from importlib.metadata import version as _dist_version

            dist = _dist_version("rapidocr_onnxruntime")
        except Exception:
            dist = "unknown"
        return f"{dist} (PP-OCRv4 onnx)"

    def recognize(
        self, image: Any, languages: Optional[Sequence[str]] = None
    ) -> OcrResult:
        engine = self._instance()
        if engine is None:
            return self._result(error="rapidocr unavailable")

        try:
            import numpy as np

            array = np.array(image.convert("RGB"))
            # The engine expects BGR channel order.
            bgr = array[:, :, ::-1]
            raw, _elapse = engine(bgr)
        except Exception as exc:
            logger.warning("rapidocr failed: %s", exc)
            return self._result(error=f"{type(exc).__name__}: {exc}")

        blocks: List[OcrBlock] = []
        for item in raw or []:
            # A block must be a (quad, text, confidence) sequence. Rejecting
            # str/bytes explicitly matters: indexing a string succeeds and
            # silently yields single characters as "recognised text", which
            # would fabricate content out of a malformed payload.
            if isinstance(item, (str, bytes)) or not isinstance(item, (list, tuple)):
                continue
            if len(item) < 3:
                continue
            try:
                quad, text, confidence = item[0], item[1], item[2]
            except Exception:
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            blocks.append(
                OcrBlock(str(text).strip(), self._as_confidence(confidence),
                         self._quad_to_bbox(quad))
            )

        # Engine order is detection order, not reading order: the boxes have
        # to be assembled into lines. See core.ocr.layout for the measured
        # failures (a curved title split mid-word, table cells separated).
        from .layout import reconstruct_lines

        text = reconstruct_lines(blocks)
        return self._result(text=text, blocks=blocks, language="latn+chi")

    @staticmethod
    def _as_confidence(value) -> Optional[float]:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None
        return confidence if 0.0 <= confidence <= 1.0 else None

    @staticmethod
    def _quad_to_bbox(quad) -> Optional[BBox]:
        try:
            xs = [float(point[0]) for point in quad]
            ys = [float(point[1]) for point in quad]
        except Exception:
            return None
        return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))


#: Engine preference order. Tesseract first because it covers this project's
#: declared default languages; RapidOCR second because it needs no system
#: binary. Extend this tuple to add a backend.
ENGINE_PREFERENCE: Tuple[type, ...] = (TesseractEngine, RapidOcrEngine)

_SELECTED_LOCK = threading.Lock()
_SELECTED: Optional[BaseOcrEngine] = None
_SELECTION_DONE = False


def _select_engine() -> Optional[BaseOcrEngine]:
    """Pick the first available engine in preference order."""
    global _SELECTED, _SELECTION_DONE
    with _SELECTED_LOCK:
        if _SELECTION_DONE:
            return _SELECTED
        _SELECTION_DONE = True
        # Device policy comes from the compute gateway: with no verified
        # accelerator backend present it reports the CPU, which is exactly what
        # every engine below runs on.  Accelerated OCR is only selected when the
        # gateway can point at a real accelerator path, and the engines that
        # implement it are preferred in that case - the logging makes the
        # decision visible instead of leaving it implicit.
        try:
            from core.compute import WorkloadKind, get_compute_gateway

            decision = get_compute_gateway().select_device(WorkloadKind.OCR)
            logger.info(
                "OCR device selection: %s (%s; %s)",
                decision.device, decision.backend, decision.reason,
            )
        except Exception as exc:  # pragma: no cover - policy is advisory
            logger.debug("Compute gateway unavailable for OCR selection: %s", exc)

        for engine_class in ENGINE_PREFERENCE:
            engine = engine_class()
            if engine.available():
                _SELECTED = engine
                logger.info(
                    "OCR engine selected: %s (%s)", engine.name, engine.version()
                )
                return _SELECTED
        logger.error(
            "No OCR engine available; image and scanned-PDF processing will be marked retryable"
        )
        return None


def get_ocr_engine() -> Optional[BaseOcrEngine]:
    """Return the selected OCR engine, or ``None`` if none can run."""
    return _select_engine()


def ocr_available() -> bool:
    """True if some OCR engine is usable on this host."""
    return get_ocr_engine() is not None


def ocr_engine_name() -> str:
    """Name of the selected engine, or ``'none'``."""
    engine = get_ocr_engine()
    return engine.name if engine else "none"


def reset_engine_cache() -> None:
    """Clear the cached selection. Intended for tests."""
    global _SELECTED, _SELECTION_DONE
    with _SELECTED_LOCK:
        _SELECTED = None
        _SELECTION_DONE = False


#: Confidence below which the primary (preprocessed) pass is retried on the
#: un-preprocessed image. Not arbitrary: measured across six image heights the
#: fallback engine returned 0.92-1.00 on text it read correctly and 0.55-0.65
#: on text it mangled, so this sits in the observed gap. At 40px height the
#: shared binarisation step cut a correct 0.841 reading down to 0.632 and
#: turned "LOWRESTEST" into "APT2T7712", which is what the retry recovers.
LOW_CONFIDENCE_RETRY_THRESHOLD = 0.75


def recognize_best(
    engine: BaseOcrEngine,
    preprocessed: Any,
    original: Optional[Any] = None,
    languages: Optional[Sequence[str]] = None,
    threshold: float = LOW_CONFIDENCE_RETRY_THRESHOLD,
) -> OcrResult:
    """Recognise, retrying on the un-preprocessed image when confidence is low.

    The shared preprocessing is a hard binarisation tuned for tesseract. For
    small images it discards the anti-aliasing that helps a neural engine, so
    the retry is gated rather than applied blindly: a confident first pass on
    normal-sized text costs nothing extra.

    The gate is low confidence, or a first pass the engine had to enlarge
    (``scale > 1``: its evidence that the text is small, which is where
    binarisation destroys strokes). Confidence alone is not enough there:
    measured with tesseract 5.3.4, the preprocessed "ROTATEDIMAGE_270_9911"
    read "..._9311" at 0.83, while the original read it exactly at 0.90; a
    7px line read nothing preprocessed and exactly from the original.

    ``original`` is optional; when omitted this behaves exactly like
    ``engine.recognize``.
    """
    result = engine.recognize(preprocessed, languages)
    result.input_variant = "preprocessed"

    if original is None:
        return result

    confidence = result.mean_confidence
    small_text = (result.scale or 1.0) > 1.0
    if result.succeeded and confidence is not None and confidence >= threshold and not small_text:
        return result

    # The retry must never turn a partial success into a total failure: some
    # text beats no text, so a retry that raises or finds nothing keeps the
    # primary reading.
    try:
        retry = engine.recognize(original, languages)
    except Exception as exc:
        logger.warning("OCR retry on un-preprocessed image failed: %s", exc)
        return result
    retry.input_variant = "original"
    if not retry.succeeded:
        return result

    retry_confidence = retry.mean_confidence
    if confidence is None or (
        retry_confidence is not None and retry_confidence > confidence
    ):
        logger.info(
            "OCR retry on un-preprocessed image improved confidence %s -> %s",
            confidence, retry_confidence,
        )
        return retry
    return result


def recognize_image(
    image: Any, languages: Optional[Iterable[str]] = None
) -> OcrResult:
    """Recognise text in a PIL image with the selected engine, in any orientation.

    Engines that read turned text themselves (``handles_orientation``) are
    trusted with it; for others the four orientations are tried when the
    upright reading is empty or doubtful, and the best-scoring one is kept.

    Never raises: an unavailable engine or a recognition failure yields an
    ``OcrResult`` whose ``error`` explains what happened, so a caller can record
    the attempt rather than silently dropping the file.
    """
    engine = get_ocr_engine()
    if engine is None:
        return OcrResult(
            attempted=False, engine="none", error="no OCR engine available"
        )
    try:
        lang_list = list(languages) if languages else None
        res = engine.recognize(image, lang_list)
        if engine.handles_orientation or _is_confident(res) or not _is_image(image):
            return res

        # An engine that cannot read turned text: try the other orientations
        # and keep the best by the same score the engines use (length alone
        # rewards the fragments turned text produces).
        best = res
        for angle in (90, 180, 270):
            try:
                candidate = engine.recognize(image.rotate(angle, expand=True), lang_list)
            except Exception as exc:
                logger.warning("OCR of the image turned %s degrees failed: %s", angle, exc)
                continue
            if _reading_score(candidate) > _reading_score(best):
                candidate.rotation = angle
                candidate.blocks = _unrotate_blocks(candidate.blocks, angle, image.size)
                best = candidate
        return best
    except Exception as exc:  # engines should not raise, but never trust that
        logger.warning("OCR engine %s raised: %s", engine.name, exc)
        return OcrResult(
            attempted=True,
            engine=engine.name,
            engine_version=engine.version(),
            error=f"{type(exc).__name__}: {exc}",
        )
