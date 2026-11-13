"""
Fast Image Reader - Comprehensive OCR Text Extraction
Extracts complete textual content from images with coordinate tracking
Stores only OCR text content, preserving formatting
Optimized for speed and reliability
"""

import os
import re
import logging
from typing import Dict, Any, Optional, Set
import threading
import warnings

from core.ocr import get_ocr_engine, recognize_best
from core.optional_imports import import_opencv

from .base_reader import BaseReader

logger = logging.getLogger(__name__)

# Suppress PIL warning about palette images with transparency
warnings.filterwarnings('ignore', message='.*Palette images with Transparency.*', category=UserWarning, module='PIL')

#: Minimum width/height, in pixels, for OCR to be attempted at all.
#:
#: Below this floor a raster cannot hold a readable text line: the recogniser
#: returns noise or nothing while still costing a full tesseract launch, and on
#: a corpus of icons and thumbnails that cost dominates the run. The outcome is
#: an *explicit* skip - ``extraction_info["skipped"] = True`` with
#: ``skip_reason = "too_small"`` - which the storage layer records as the
#: first-class ``skipped`` processing state and the ledger counts as skipped.
#: That is deliberately not the same as discarding the file: it is stored, its
#: metadata is kept, and the reason travels with the row.
#:
#: The value is the reader's documented 50px floor (see the contract tests in
#: tests/unit/test_ocr_engines.py and the corpus description in
#: tests/integration/test_status_persisted.py).
MIN_OCR_DIMENSION = 50

# Global cache for library imports
_LIBS_CACHE = {}
_LIBS_LOCK = threading.Lock()

# Cache for tesseract availability check
_TESSERACT_AVAILABLE = None
_TESSERACT_CHECKED = False

# Installed tesseract language packs, keyed by the tesseract binary in use.
# ``pytesseract.get_languages`` spawns a tesseract process; the reader used to
# ask for the list once per file, which is pure overhead on a corpus of images.
_TESSERACT_LANGUAGE_CACHE = {}
_TESSERACT_LANGUAGE_LOCK = threading.Lock()


def _installed_tesseract_languages(pytesseract):
    """Return the set of installed tesseract language packs (cached).

    Raises like ``get_languages`` does when tesseract cannot be queried, so the
    caller keeps deciding availability exactly as before.
    """
    binary = getattr(pytesseract, "tesseract_cmd", None) or "tesseract"
    with _TESSERACT_LANGUAGE_LOCK:
        cached = _TESSERACT_LANGUAGE_CACHE.get(binary)
    if cached is None:
        cached = set(pytesseract.get_languages(config=""))
        with _TESSERACT_LANGUAGE_LOCK:
            _TESSERACT_LANGUAGE_CACHE[binary] = cached
    return cached


def installed_ocr_languages(engine, requested, label, record):
    """The requested OCR languages ``engine`` can read; the rest are recorded.

    A missing language model is not hidden: it is logged and listed in
    ``record["missing_ocr_languages"]``, and the page is read with the
    languages that are installed. When none is installed the request is
    returned unchanged so the engine reports the error itself.
    """
    requested = list(requested)
    if engine is None or getattr(engine, "name", "") != "tesseract":
        return requested  # other engines have fixed models and ignore this
    try:
        installed = _installed_tesseract_languages(_get_libraries().get("pytesseract"))
    except Exception as exc:
        logger.warning(
            "Cannot list the installed Tesseract languages (%s: %s); requesting %s",
            type(exc).__name__, exc, "+".join(requested),
        )
        return requested
    missing = [code for code in requested if code not in installed]
    usable = [code for code in requested if code in installed]
    if missing:
        logger.error(
            "Missing Tesseract language data for %s: %s (reading with %s)",
            label, ", ".join(missing), "+".join(usable) or "none",
        )
        record["missing_ocr_languages"] = missing
    return usable or requested


_TESSERACT_LOCK = threading.Lock()

# Default OCR languages - Hebrew prioritized for RTL text
DEFAULT_OCR_LANGUAGES = ["heb", "eng", "ara"]


class ImageFileReader(BaseReader):
    """
    Image file reader with comprehensive OCR extraction.
    Extracts complete textual content with bounding box coordinates.
    Stores only OCR text - no metadata in content.
    
    Returns structure:
    {
        "text": str - Extracted OCR text (formatting preserved),
        "ocr_coordinates": list - Bounding boxes for each word (optional),
        "ocr_language": str - Language used for OCR,
        "ocr_attempted": bool - Whether OCR was attempted,
        "ocr_successful": bool - Whether text was successfully extracted,
        "extraction_info": dict - Detailed extraction indicators,
        "location": dict - GPS coordinates if available (for paths.coordinates field, not content)
    }
    """
    
    def get_supported_extensions(self) -> Set[str]:
        """Return set of supported image extensions"""
        return {
            '.png', '.jpg', '.jpeg', '.gif', '.bmp', 
            '.tiff', '.tif', '.webp', '.ico', '.svg', '.heic', '.heif', '.avif'
        }
    
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read image file and extract OCR text content
        
        Args:
            file_info: Dictionary with 'path' key and optional 'languages' key
        
        Returns:
            Dictionary with extracted text content and extraction indicators
        """
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))
        
        file_path = str(file_info.get("path"))
        languages = file_info.get("languages")
        # DETECT-01: dispatch on the content-verified type, not the filename.
        ext = self.effective_extension(file_info)
        
        try:
            if ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp',
                       '.tiff', '.tif', '.webp', '.ico', '.heic', '.heif'):
                result = self.read_image_file_fast(file_path, languages=languages)
                if result is None:
                    return self.create_error_result("Failed to read image file", file_path)
                return result
            elif ext == '.svg':
                return self.read_svg_file(file_path)
            else:
                error_msg = f"Unsupported image file type: {ext or file_path}"
                return self.handle_read_error(ValueError(error_msg), file_path, "read_file")
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")
    
    def read_image_file_fast(self, filepath, languages=None):
        """
        Extract OCR text content from image file.
        
        Returns:
            dict: {
                "text": str - Extracted OCR text (formatting preserved),
                "ocr_coordinates": list - Bounding boxes for each word (optional),
                "ocr_language": str - Language used for OCR,
                "ocr_attempted": bool - Whether OCR was attempted,
                "ocr_successful": bool - Whether text was successfully extracted,
                "extraction_info": dict - Detailed extraction indicators,
                "location": dict - GPS coordinates if available (for paths.coordinates field, not content)
            }
        """
        if not os.path.exists(filepath):
            logger.warning(f"[EXTRACTION] File not found: {filepath}")
            return {
                "text": "",
                "ocr_attempted": False,
                "ocr_successful": False,
                "extraction_info": {
                    "error": "File not found",
                    "extracted": False,
                    "stored": False
                }
            }
        
        libs = self._get_libraries()
        Image = libs.get('Image')
        TAGS = libs.get('TAGS')
        GPSTAGS = libs.get('GPSTAGS')
        cv2 = libs.get('cv2')
        np = libs.get('np')
        
        if not Image:
            logger.warning(f"[EXTRACTION] PIL/Image library not available for: {filepath}")
            return {
                "text": "",
                "ocr_attempted": False,
                "ocr_successful": False,
                "retryable": True,
                "extraction_info": {
                    "error": "PIL/Image library not available",
                    "reason": "required_dependency_unavailable",
                    "extracted": False,
                    "stored": False
                }
            }
        
        result = {
            "text": "",
            "ocr_attempted": False,
            "ocr_successful": False,
            "extraction_info": {
                "extracted": False,
                "stored": False,
                "text_length": 0,
                "word_count": 0,
                "coordinate_count": 0
            }
        }
        
        try:
            with Image.open(filepath) as img:
                width, height = img.size
                img_format = img.format
                
                result["extraction_info"].update({
                    "image_size": f"{width}x{height}",
                    "image_format": img_format
                })

                # Size floor (see MIN_OCR_DIMENSION). Small images are not
                # discarded: they are recorded as an explicit, terminal skip
                # with the reason, so a downstream reader can always tell
                # "deliberately not attempted" from "attempted and failed".
                if width < MIN_OCR_DIMENSION or height < MIN_OCR_DIMENSION:
                    result["extraction_info"].update({
                        "skipped": True,
                        "skip_reason": "too_small",
                        "reason": "too_small",
                        "min_ocr_dimension": MIN_OCR_DIMENSION,
                    })
                    logger.info(
                        "[EXTRACTION] Skipping OCR for %s: %dx%d is below the "
                        "%dpx floor (stored as an explicit skip, not a failure)",
                        os.path.basename(filepath), width, height, MIN_OCR_DIMENSION,
                    )
                    return result
                
                # Extract GPS location (for paths.coordinates field, not content)
                location_info = None
                try:
                    exif_data = img._getexif()
                    if exif_data and TAGS and GPSTAGS:
                        gps_ifd = exif_data.get(34853)
                        if gps_ifd:
                            location_info = self._extract_gps_location(gps_ifd, GPSTAGS)
                            if location_info:
                                result["location"] = location_info
                                result["extraction_info"]["gps_extracted"] = True
                except Exception:
                    pass
                
                # Perform OCR extraction.
                # PHASE 2A: OCR is no longer tied to the tesseract binary. The
                # engine layer picks tesseract when present (it covers this
                # project's declared heb/eng/ara defaults) and otherwise falls
                # back to a pure-pip engine, so a host without tesseract still
                # gets OCR instead of silently producing nothing.
                # Every engine goes through the engine layer (core.ocr), so the
                # result carries the same provenance whichever one runs:
                # confidence, word boxes, rotation, scale, the input variant.
                # Tesseract is preferred when installed (it covers this
                # project's heb/eng/ara defaults); otherwise a pure-pip engine.
                engine = get_ocr_engine()
                requested_languages = list(languages or DEFAULT_OCR_LANGUAGES)
                ocr_languages = installed_ocr_languages(
                    engine, requested_languages, os.path.basename(filepath),
                    result["extraction_info"],
                )

                if engine is not None:
                    result["ocr_attempted"] = True
                    rgb_img = img.convert("RGB")

                    # Preprocess image (shared by every engine)
                    if cv2 and np:
                        arr = np.array(rgb_img)
                        processed = self._fast_preprocess(arr, libs)
                        ocr_target = Image.fromarray(processed)
                        result["extraction_info"]["preprocessing"] = "enhanced"
                    else:
                        ocr_target = rgb_img.convert("L")
                        result["extraction_info"]["preprocessing"] = "grayscale"

                    # Confidence-gated retry on the un-preprocessed image: the
                    # shared binarisation measurably harms small text.
                    engine_result = recognize_best(engine, ocr_target, rgb_img, ocr_languages)
                    text = engine_result.text or ""
                    ocr_coordinates = [
                        {
                            "word": block.text,
                            "confidence": block.confidence,
                            "bbox": list(block.bbox) if block.bbox else None
                        }
                        for block in engine_result.blocks
                    ]
                    lang = engine_result.language
                    result["ocr_language"] = lang
                    result["extraction_info"]["used_language"] = lang
                    if engine_result.error:
                        result["extraction_info"]["engine_error"] = engine_result.error
                    if engine_result.blocks_error:
                        # Text without word confidences: say why, so a missing
                        # confidence is never read as a low one.
                        result["extraction_info"]["ocr_blocks_error"] = engine_result.blocks_error
                    if engine_result.rotation:
                        result["ocr_rotation"] = engine_result.rotation
                    if engine_result.scale and engine_result.scale != 1:
                        result["extraction_info"]["ocr_scale"] = engine_result.scale

                    ocr_engine_name_used = engine_result.engine
                    ocr_engine_version_used = engine_result.engine_version
                    ocr_confidence_used = engine_result.mean_confidence
                    # Which input produced this reading - part of provenance.
                    result["ocr_input_variant"] = engine_result.input_variant

                    # Provenance: state how this text was derived so it is
                    # never mistaken for native document text.
                    result["ocr_engine"] = ocr_engine_name_used
                    result["ocr_engine_version"] = ocr_engine_version_used
                    result["ocr_derived"] = bool(text and text.strip())
                    if ocr_confidence_used is not None:
                        result["ocr_confidence"] = ocr_confidence_used

                    # Store extraction results
                    if text and text.strip():
                        result["text"] = text.rstrip()
                        result["ocr_successful"] = True
                        
                        text_length = len(text.strip())
                        word_count = len(text.strip().split())
                        coord_count = len(ocr_coordinates) if ocr_coordinates else 0
                        
                        result["extraction_info"].update({
                            "extracted": True,
                            "stored": True,
                            "text_length": text_length,
                            "word_count": word_count,
                            "coordinate_count": coord_count,
                            "image_size": f"{width}x{height}",
                            "image_format": img_format
                        })
                        
                        if ocr_coordinates:
                            result["ocr_coordinates"] = ocr_coordinates
                        
                        logger.info(
                            f"[EXTRACTION] ✅ {os.path.basename(filepath)} - "
                            f"Extracted: {text_length} chars, {word_count} words | "
                            f"Coordinates: {coord_count} words | "
                            f"Language: {lang}"
                        )
                    else:
                        result["ocr_successful"] = False
                        result["extraction_info"].update({
                            "extracted": False,
                            "stored": False,
                            "text_length": 0,
                            "word_count": 0,
                            "coordinate_count": len(ocr_coordinates) if ocr_coordinates else 0,
                            "image_size": f"{width}x{height}",
                            "image_format": img_format,
                            "reason": "no_text_extracted",
                            "empty_result": True,
                        })
                        result["empty_result"] = True
                        
                        if ocr_coordinates:
                            result["ocr_coordinates"] = ocr_coordinates
                            result["extraction_info"]["coordinate_count"] = len(ocr_coordinates)
                        
                        logger.info(
                            f"[EXTRACTION] ⚠️  {os.path.basename(filepath)} - "
                            f"No text extracted | "
                            f"Coordinates: {len(ocr_coordinates) if ocr_coordinates else 0} words | "
                            f"Language: {lang}"
                        )
                else:
                    # OCR was required but no backend could run. That is not
                    # an OCR attempt: preserve the original, mark the content
                    # retryable, and report the missing capability explicitly.
                    result["ocr_attempted"] = False
                    result["ocr_engine"] = "none"
                    result["retryable"] = True
                    result["extraction_info"].update({
                        "extracted": False,
                        "stored": False,
                        "error": "No OCR engine available",
                        "reason": "ocr_required_engine_unavailable",
                        "image_size": f"{width}x{height}",
                        "image_format": img_format
                    })
                    logger.error(
                        "[EXTRACTION] OCR required but no engine is available for: %s",
                        os.path.basename(filepath),
                    )
                
                return result
        
        except Exception as e:
            logger.error(f"[EXTRACTION] ❌ Error processing {os.path.basename(filepath)}: {e}")
            return {
                "text": "",
                "ocr_attempted": False,
                "ocr_successful": False,
                "extraction_info": {
                    "error": str(e),
                    "extracted": False,
                    "stored": False
                }
            }
    
    #: Elements whose text is part of an SVG document. SVG 1.1/2 defines
    #: <text>/<tspan>/<textPath> and <title>/<desc>; editors add flow trees
    #: (Inkscape), which are collected too.
    SVG_TEXT_TAGS = frozenset({
        "text", "tspan", "textPath", "tref", "title", "desc",
        "flowRoot", "flowDiv", "flowPara", "flowSpan",
    })

    #: Cap on text taken from a single SVG (a chart exported with tens of
    #: thousands of labels must not become an unbounded content blob).
    SVG_TEXT_LIMIT = 2_000_000

    @classmethod
    def _collect_svg_text(cls, xml_bytes):
        """Return ``(lines, element_count, parse_failure)`` for an SVG document.

        Text is read with an XML parser instead of a regex, so nested
        ``<tspan>`` runs, entity references and non-ASCII characters come out as
        text and never as markup. Each top-level text element becomes one line,
        which is how the document lays text out; whitespace runs inside an
        element are layout, so they collapse to single spaces. ``parse_failure``
        is None when the document parsed.
        """
        import xml.etree.ElementTree as ET

        def local_name(tag):
            if isinstance(tag, str) and tag.startswith("{"):
                return tag.split("}", 1)[1]
            return tag if isinstance(tag, str) else ""

        def gather(node, pieces):
            """Depth-first text of ``node`` in document order."""
            if node.text:
                pieces.append(node.text)
            for child in list(node):
                gather(child, pieces)
                if child.tail:
                    pieces.append(child.tail)

        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            return [], 0, f"svg_malformed_xml: {exc}"

        lines = []
        element_count = 0

        def visit(node):
            """Collect one line per top-level text element, in document order."""
            nonlocal element_count
            if local_name(node.tag) in cls.SVG_TEXT_TAGS:
                # A text element starts a line; text nested inside it (a
                # <tspan>, or an <a> wrapper) belongs to that same line, so
                # this branch does not descend further.
                pieces = []
                gather(node, pieces)
                joined = re.sub(r"\s+", " ", "".join(pieces)).strip()
                if joined:
                    lines.append(joined)
                    element_count += 1
                return
            for child in list(node):
                visit(child)

        visit(root)
        return lines, element_count, None

    @staticmethod
    def _svg_text_by_stripping(content):
        """Recover text from markup that the XML pass could not use.

        Used for a malformed document, and for a well-formed one whose text is
        not inside text elements. Comments, script and style blocks are removed
        first so their code is not mistaken for document text.
        """
        body = re.sub(r"<!--.*?-->", " ", content, flags=re.DOTALL)
        body = re.sub(
            r"<(script|style)\b.*?</\1>", " ", body,
            flags=re.DOTALL | re.IGNORECASE,
        )
        body = re.sub(r"<[^>]*>", " ", body)
        return re.sub(r"\s+", " ", body).strip()

    def read_svg_file(self, filepath):
        """Read an SVG (XML vector) file.

        SVG stores its text in the markup, so no OCR is involved: the text is
        parsed out of the document. The previous implementation regexed only
        ``<text>`` (missing ``<title>``/``<desc>``, Inkscape flow text and
        multi-line runs) and returned no ``reason`` field at all, which the
        storage layer logged as "Reason: unknown" for a real ingest - a
        condition with no explanation is indistinguishable from a defect.

        Every outcome carries an explicit reason now, and content this reader
        does not turn into text (embedded raster images, vector paths, embedded
        script/style) is counted and named rather than left implicit.
        """
        try:
            if not os.path.exists(filepath):
                return {
                    "text": "",
                    "ocr_attempted": False,
                    "ocr_successful": False,
                    "extraction_info": {
                        "error": "File not found",
                        "reason": "file_not_found",
                        "extracted": False,
                        "stored": False,
                    },
                }

            with open(filepath, "rb") as handle:
                raw = handle.read()
            content = raw.decode("utf-8", "replace")

            lines, text_elements, parse_failure = self._collect_svg_text(raw)
            reason = None
            recovered = False
            if not lines:
                stripped = self._svg_text_by_stripping(content)
                if stripped:
                    recovered = True
                    lines = [stripped]
                    text_elements = 1
                    reason = (
                        f"{parse_failure}; text recovered by stripping markup"
                        if parse_failure else
                        "svg_text_recovered_from_markup"
                    )
                else:
                    reason = parse_failure

            extracted_text = "\n".join(lines)[:self.SVG_TEXT_LIMIT]
            has_text = bool(extracted_text.strip())

            # Content that does not become text: counted, never silently
            # dropped. An SVG is vector, so there is no raster to recognise;
            # an embedded <image> would have to be rasterised first, which this
            # reader does not do.
            image_count = len(re.findall(r"<image\b", content, re.IGNORECASE))
            path_count = len(re.findall(r"<path\b", content, re.IGNORECASE))
            code_chars = sum(
                len(match.group(0)) for match in re.finditer(
                    r"<(?:script|style)\b.*?</(?:script|style)>", content,
                    flags=re.DOTALL | re.IGNORECASE,
                )
            )

            if has_text:
                reason = reason or "svg_text_extracted"
            elif reason is None:
                reason = "svg_has_no_text_elements"
                if image_count:
                    reason += (
                        f"; {image_count} embedded raster image(s) are not OCR'd"
                        " (an SVG is vector text, not a scanned image)"
                    )
                elif path_count:
                    reason += (
                        f"; {path_count} vector path element(s) found, which may"
                        " be outlined (converted) text that no extractor can read"
                    )
            if has_text and code_chars:
                reason += f"; {code_chars} chars of embedded script/style not indexed"

            result = {
                "text": extracted_text,
                "ocr_attempted": False,
                "ocr_successful": has_text,
                "extraction_info": {
                    "extracted": has_text,
                    "stored": has_text,
                    # The document was read and the conclusion is that it holds
                    # no extractable text (vector-only artwork, outlined paths).
                    # Saying so explicitly keeps the run's accounting from
                    # reporting a successfully read file as a broken one: the
                    # database records this outcome as processed, with the
                    # reason in status_detail.
                    "empty_result": not has_text,
                    "reason": reason,
                    "text_length": len(extracted_text.strip()),
                    "word_count": len(extracted_text.split()),
                    "coordinate_count": 0,
                    "file_type": "SVG",
                    "text_elements": text_elements,
                    "embedded_images": image_count,
                    "vector_paths": path_count,
                    "embedded_code_chars": code_chars,
                    "recovered_from_markup": recovered,
                },
            }

            width_match = re.search(r'width=["\'](\d+(?:\.\d+)?)["\']', content)
            height_match = re.search(r'height=["\'](\d+(?:\.\d+)?)["\']', content)
            viewbox_match = re.search(r'viewBox=["\']([^"\']+)["\']', content)

            if width_match:
                result["extraction_info"]["image_width"] = width_match.group(1)
            if height_match:
                result["extraction_info"]["image_height"] = height_match.group(1)
            if viewbox_match:
                result["extraction_info"]["viewBox"] = viewbox_match.group(1)

            if has_text:
                logger.info(
                    "[EXTRACTION] SVG text: %d chars, %d word(s) from %d text "
                    "element(s) in %s",
                    result["extraction_info"]["text_length"],
                    result["extraction_info"]["word_count"],
                    text_elements, os.path.basename(str(filepath)),
                )
            else:
                logger.info(
                    "[EXTRACTION] SVG has no extractable text (%s): %s",
                    os.path.basename(str(filepath)), reason,
                )
            return result

        except Exception as e:
            logger.error(
                "[EXTRACTION] Error reading SVG %s: %s",
                os.path.basename(str(filepath)), e,
            )
            return {
                "text": "",
                "ocr_attempted": False,
                "ocr_successful": False,
                "extraction_info": {
                    "error": str(e),
                    "reason": "svg_read_error",
                    "extracted": False,
                    "stored": False,
                },
            }

    def _is_tesseract_available(self):
        """Check if tesseract is installed and available (cached)"""
        global _TESSERACT_AVAILABLE, _TESSERACT_CHECKED
        
        with _TESSERACT_LOCK:
            if _TESSERACT_CHECKED:
                return _TESSERACT_AVAILABLE
            
            _TESSERACT_CHECKED = True
            try:
                import pytesseract
                pytesseract.get_tesseract_version()
                _TESSERACT_AVAILABLE = True
                return True
            except Exception:
                _TESSERACT_AVAILABLE = False
                logger.warning("Tesseract OCR not installed or not in PATH. Image OCR will be skipped.")
                return False
    
    def _get_libraries(self):
        """Get image processing libraries (cached)"""
        
        with _LIBS_LOCK:
            if _LIBS_CACHE:
                return _LIBS_CACHE
            
            try:
                from PIL import Image
                from PIL.ExifTags import TAGS, GPSTAGS
                _LIBS_CACHE['Image'] = Image
                _LIBS_CACHE['TAGS'] = TAGS
                _LIBS_CACHE['GPSTAGS'] = GPSTAGS
            except ImportError:
                _LIBS_CACHE['Image'] = None
                _LIBS_CACHE['TAGS'] = None
                _LIBS_CACHE['GPSTAGS'] = None
            
            try:
                import pytesseract
                _LIBS_CACHE['pytesseract'] = pytesseract
            except ImportError:
                _LIBS_CACHE['pytesseract'] = None
            
            # Through the guard: a failed cv2 load leaves its own directory
            # on sys.path (round 22), where it shadows later imports.
            try:
                import numpy as np
                _LIBS_CACHE['np'] = np
            except ImportError:
                _LIBS_CACHE['np'] = None
            _LIBS_CACHE['cv2'] = import_opencv()
            
            return _LIBS_CACHE
    
    def _get_optimized_tesseract_config(self, detected_lang=None, languages=None, psm_mode=None):
        """
        Get optimized Tesseract configuration for maximum text extraction
        
        Args:
            detected_lang: Auto-detected language
            languages: Fallback language list
            psm_mode: PSM mode (None = auto-select PSM 11)
        
        Returns:
            tuple: (language_string, config_string)
        """
        libs = self._get_libraries()
        pytesseract = libs.get('pytesseract')
        
        if not pytesseract:
            return "eng", "--oem 3 --psm 11"
        
        try:
            available_langs = pytesseract.get_languages(config="")
        except:
            available_langs = ["eng"]
        
        # Priority: detected language first, then fallback languages
        if detected_lang and detected_lang in available_langs:
            lang_str = detected_lang
        else:
            selected_langs = []
            fallback_langs = languages if languages else DEFAULT_OCR_LANGUAGES
            for lang in fallback_langs:
                if lang in available_langs:
                    selected_langs.append(lang)
            
            if not selected_langs:
                selected_langs = ["eng"]
            
            lang_str = "+".join(selected_langs)
        
        # PSM 11: Sparse text - finds ALL text regardless of layout
        if psm_mode is None:
            psm_mode = 11
        
        config = f"--oem 3 --psm {psm_mode}"
        
        return lang_str, config
    
    def _extract_gps_location(self, gps_info, GPSTAGS):
        """Extract GPS location from EXIF data"""
        if not gps_info:
            return None

        gps = {GPSTAGS.get(k, k): v for k, v in gps_info.items()}

        def to_deg(value):
            try:
                if isinstance(value[0], tuple):
                    d = value[0][0] / value[0][1]
                    m = value[1][0] / value[1][1]
                    s = value[2][0] / value[2][1]
                else:
                    d, m, s = value
                return d + (m / 60.0) + (s / 3600.0)
            except Exception:
                return None

        lat = lon = None

        if "GPSLatitude" in gps and "GPSLatitudeRef" in gps:
            lat = to_deg(gps["GPSLatitude"])
            if gps["GPSLatitudeRef"] == "S":
                lat = -lat

        if "GPSLongitude" in gps and "GPSLongitudeRef" in gps:
            lon = to_deg(gps["GPSLongitude"])
            if gps["GPSLongitudeRef"] == "W":
                lon = -lon

        if lat is None or lon is None:
            return None

        result = {
            "latitude": lat,
            "longitude": lon,
            "coordinates": f"{lat:.6f}, {lon:.6f}",
            "google_maps_url": f"https://www.google.com/maps?q={lat},{lon}"
        }

        if "GPSAltitude" in gps:
            result["altitude_meters"] = float(gps["GPSAltitude"])

        if "GPSTimeStamp" in gps:
            h, m, s = gps["GPSTimeStamp"]
            result["gps_time_utc"] = f"{int(h):02d}:{int(m):02d}:{int(s):02d}"

        return result
    
    def _fast_preprocess(self, img_array, libs):
        """
        Enhanced image preprocessing for better OCR results
        
        Args:
            img_array: numpy array of image
            libs: cached libraries dict
        
        Returns:
            Preprocessed image ready for OCR
        """
        cv2 = libs.get('cv2')
        np = libs.get('np')
        
        if not cv2 or not np:
            return img_array
        
        # Convert to grayscale
        if len(img_array.shape) == 3:
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_array
        
        # Denoise
        denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
        
        # Enhance contrast with CLAHE
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(denoised)
        
        # Adaptive thresholding
        binary_adaptive = cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
            cv2.THRESH_BINARY, 11, 2
        )
        
        return binary_adaptive


# Module-level wrapper functions for cross-module use
_shared_reader_instance = None

def _get_shared_instance():
    """Get or create shared ImageFileReader instance"""
    global _shared_reader_instance
    if _shared_reader_instance is None:
        _shared_reader_instance = ImageFileReader()
    return _shared_reader_instance

def _get_libraries():
    """Module-level wrapper for _get_libraries"""
    return _get_shared_instance()._get_libraries()

def _get_optimized_tesseract_config(detected_lang=None, languages=None):
    """Module-level wrapper for _get_optimized_tesseract_config"""
    return _get_shared_instance()._get_optimized_tesseract_config(detected_lang, languages)

def _fast_preprocess(img_array, libs):
    """Module-level wrapper for _fast_preprocess"""
    return _get_shared_instance()._fast_preprocess(img_array, libs)

def _is_tesseract_available():
    """Module-level wrapper for _is_tesseract_available"""
    return _get_shared_instance()._is_tesseract_available()
