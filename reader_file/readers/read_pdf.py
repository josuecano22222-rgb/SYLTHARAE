"""
Optimized PDF Reader - 10x Faster Performance
Aligned with database design principles - all functions within class
Improvements:
1. Parallel page processing (multiple pages at once)
2. Smart OCR skipping (detect text-based PDFs early)
3. Reuses OCR functions from read_img_fast.py (no duplication)
4. Adaptive strategy based on PDF characteristics
5. Memory-efficient streaming
6. Early exit on text detection
"""

import os
import io
import math
from pathlib import Path
from typing import Dict, Any, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing
import logging
import time

# Suppress PIL warning about palette images with transparency
# We handle this by converting to RGBA when needed

# Import shared OCR functions from read_img_fast (avoid duplication)
# These are module-level functions exported from ImageFileReader for cross-module use
from reader_file.readers.read_img_fast import (
    _get_libraries,
    _get_optimized_tesseract_config,
    _fast_preprocess,
    _is_tesseract_available,
    installed_ocr_languages,
    DEFAULT_OCR_LANGUAGES
)

# PHASE 2A: OCR engine selection. Imported from its owning module rather than
# re-exported through read_img_fast.
from core.ocr import get_ocr_engine, recognize_best

# The page model: reading order, ligatures, hyphenation, tables, geometry.
from .pdf_layout import page_model, text_quality

from .base_reader import BaseReader

logger = logging.getLogger(__name__)

#: Scanned pages are rasterised for OCR at no less than this zoom (144 dpi),
#: the reader's historical fixed resolution ...
MIN_OCR_ZOOM = 2.0
#: ... and no more than 300 dpi, the resolution tesseract's documentation
#: recommends for OCR.
MAX_OCR_ZOOM = 300 / 72
#: Pixels per rendered page (the engine's own enlargement budget).
MAX_OCR_PAGE_PIXELS = 16_000_000

#: A page of a "text" PDF with fewer characters than this is treated as a page
#: that may be a scan even though the document as a whole is text. A whole
#: document sampled from its first three pages used to decide for every page,
#: so a report with text on pages 1-3 and scans after them was never OCR'd.
MIN_PAGE_TEXT_CHARS = 32

#: Below this share of lexical words a text layer is not worth keeping when the
#: page also has an image: it is the residue of a failed recognition, not text.
MIN_TEXT_LAYER_QUALITY = 0.5


def choose_reading(native_text, ocr_text, layer_quality: float,
                   min_quality: float = MIN_TEXT_LAYER_QUALITY) -> dict:
    """Decide which reading of a page to store: its text layer, OCR, or neither.

    OCR exists to recover pages that have no usable text of their own. It is not
    a second opinion on a page that already reads: a rendered page loses
    characters to hinting, column layout and table rules, so a recognition of
    the *same* page is usually shorter than the layer it was rendered from. When
    the two were compared by lexical quality alone the shorter OCR reading won
    (it has a higher ratio of real words - it simply has fewer of them), and
    testPDF_childAttachments.pdf stored 1 566 characters for a page of 2 503.

    The rule, in order:
    1. no usable text layer  -> OCR is the page;
    2. no OCR text           -> the layer is the page;
    3. the layer reads as language (quality >= ``min_quality``) -> keep it;
    4. otherwise the page's layer is recognition residue -> OCR, but only when
       it is both better and not shorter than what it replaces.

    Returns ``{text, source, reason, native_quality, ocr_quality}`` so the
    decision is auditable per page rather than implied by a stored string.
    """
    native = str(native_text or "")
    ocr = str(ocr_text or "")
    native_quality = round(float(layer_quality or 0.0), 4)
    ocr_quality = round(text_quality(ocr), 4) if ocr else 0.0
    decision = {"native_quality": native_quality, "ocr_quality": ocr_quality}

    if not native.strip():
        if ocr.strip():
            return {**decision, "text": ocr, "source": "ocr", "reason": "no_text_layer"}
        return {**decision, "text": "", "source": "none", "reason": "no_text_and_no_ocr"}

    if not ocr.strip():
        return {**decision, "text": native, "source": "text_layer", "reason": "no_ocr_text"}

    if native_quality >= min_quality:
        return {**decision, "text": native, "source": "text_layer",
                "reason": "text_layer_reads_as_language"}

    if ocr_quality <= native_quality:
        return {**decision, "text": native, "source": "text_layer",
                "reason": "ocr_not_better_than_residue"}

    if len(ocr.strip()) < 0.9 * len(native.strip()):
        return {**decision, "text": native, "source": "text_layer",
                "reason": "ocr_shorter_than_residue"}

    return {**decision, "text": ocr, "source": "ocr",
            "reason": "text_layer_is_recognition_residue"}


def ocr_render_zoom(page) -> float:
    """Zoom at which to rasterise ``page`` for OCR.

    A fixed 2x (144 dpi) threw away pixels whenever a page's scan was denser:
    measured, a 1000px-wide scan placed 400pt wide was rendered 800px wide,
    and tesseract 5.3.4 then read "PDFROTATED_180_7744" as Hebrew junk while
    at the scan's own resolution (2.5x) it read it exactly (0.91; 5.5.1 went
    from 0.76 with a misread to exact at 0.90). So the page is rendered at
    the pixel density of its densest embedded image, within MIN_OCR_ZOOM,
    MAX_OCR_ZOOM and MAX_OCR_PAGE_PIXELS. Density is measured by area
    (pixels over points squared), so a rotated placement counts the same.
    """
    zoom = MIN_OCR_ZOOM
    try:
        images = page.get_image_info()
    except Exception as exc:
        logger.warning("Cannot list the images on PDF page %s (%s: %s); rendering at %d dpi",
                       getattr(page, "number", -1) + 1, type(exc).__name__, exc, round(72 * zoom))
        images = []
    for info in images:
        x0, y0, x1, y1 = info.get("bbox") or (0, 0, 0, 0)
        placed = (x1 - x0) * (y1 - y0)
        pixels = (info.get("width") or 0) * (info.get("height") or 0)
        if placed > 0 and pixels > 0:
            zoom = max(zoom, math.sqrt(pixels / placed))
    area = page.rect.width * page.rect.height
    budget = math.sqrt(MAX_OCR_PAGE_PIXELS / area) if area > 0 else MIN_OCR_ZOOM
    return max(MIN_OCR_ZOOM, min(zoom, MAX_OCR_ZOOM, budget))

from Hdg_Err_Ex_Log import (
    handle_error,
    ErrorCategory,
    ErrorSeverity,
    format_validation_error
)


class PDFFileReader(BaseReader):
    """
    Reader for PDF files.
    
    Follows database design principles:
    - All functions are within the class
    - Inherits from BaseReader
    - Consistent error handling
    - Proper resource management
    """
    
    def get_supported_extensions(self) -> Set[str]:
        """Return set of supported PDF extensions"""
        return {'.pdf'}
    
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read PDF file and extract content with improved error handling
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with PDF content or error information
        """
        # Validate file info using base class method
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))
        
        file_path = str(file_info.get("path"))
        # DETECT-01: dispatch on the content-verified type, not the filename.
        ext = self.effective_extension(file_info)
        
        try:
            if ext == '.pdf':
                return self.read_pdf_file(file_path)
            else:
                error_msg = f"Unsupported file type: {ext or file_path}"
                return self.handle_read_error(ValueError(error_msg), file_path, "read_file")
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")
    
    def _check_pdf_libraries(self):
        """Check PDF-specific library (fitz/pymupdf) + get shared libs"""
        libs = _get_libraries()  # Get shared libs from read_img_fast
        
        # Ensure we always have a "missing" key so callers can safely check it
        # Without this, accessing libs["missing"] raises KeyError and bubbles up
        # as an error like "'missing'" for every PDF file.
        if "missing" not in libs or libs["missing"] is None:
            libs["missing"] = []
        
        # Add fitz (PDF-specific)
        if 'fitz' not in libs:
            try:
                import pymupdf as fitz
                libs['fitz'] = fitz
            except ImportError:
                libs['fitz'] = None
                # Track that the PDF library is missing
                if 'pymupdf' not in libs.get('missing', []):
                    libs['missing'] = libs.get('missing', []) + ['pymupdf']
        
        return libs
    
    def detect_pdf_type_early(self, doc, sample_pages=3):
        """
        Quickly detect if PDF has extractable text by sampling first few pages
        Returns: "text" if text-based, "image" if image-based
        """
        total_pages = len(doc)
        pages_to_check = min(sample_pages, total_pages)

        # PDF-01: a PDF with no pages has nothing to sample. Dividing by
        # pages_to_check raised ZeroDivisionError, which surfaced as an opaque
        # "division by zero" failure for the whole document.
        if pages_to_check <= 0:
            return "image"

        text_chars = 0

        for page_num in range(pages_to_check):
            page = doc[page_num]
            text = page.get_text("text")
            if text:
                text_chars += len(str(text).strip())

        # If we found substantial text in sample, it's a text-based PDF
        avg_chars_per_page = text_chars / pages_to_check

        if avg_chars_per_page > 50:  # Threshold: 50 chars per page
            return "text"
        else:
            return "image"
    
    def _page_ocr_fallback(self, page, page_num, fitz, filepath,
                           native_text, ocr_languages, ocr_available):
        """OCR one page of a document that is otherwise text.

        The document-level decision to skip OCR is made from a three-page
        sample. That is fine for a born-digital report and wrong for the very
        common mixed document - a cover sheet, then scans - where pages 4+ hold
        no text at all and are stored as empty strings with no error.

        A page qualifies when it carries an image and either almost no text or
        a text layer that does not look like language. The page is rasterised,
        recognised, and the better of the two readings is kept, so this can only
        add text: a page whose own layer is better keeps its layer.
        """
        if not ocr_available:
            return None
        try:
            images = page.get_images(full=True)
        except Exception:
            return None
        if not images:
            return None
        zoom = ocr_render_zoom(page)
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            img_bytes = pix.tobytes("png")
        except Exception as exc:
            return {"ocr_trigger": "rasterise_failed", "error": str(exc)}
        if not img_bytes:
            return None
        tesseract_lang, tesseract_config = _get_optimized_tesseract_config(
            None, tuple(ocr_languages))
        try:
            recognised = self.process_page_optimized((
                page_num, img_bytes, tesseract_lang, tesseract_config,
                True, ocr_languages, native_text, str(filepath)))
        except Exception as exc:
            return {"ocr_trigger": "ocr_failed", "error": str(exc)}
        if not recognised or not str(recognised.get("text") or "").strip():
            return None
        recognised["ocr_trigger"] = "page_scan_or_broken_text_layer"
        recognised["ocr_render_dpi"] = round(72 * zoom)
        return recognised

    def _structure_page(self, page, want_tables=True):
        """The page model, or a minimal fallback when the model itself fails."""
        try:
            return page_model(page, want_tables=want_tables)
        except Exception as exc:
            text = page.get_text("text") or ""
            return {
                "text": text, "text_raw": text, "blocks": [], "columns": 0,
                "tables": [], "rotation": int(getattr(page, "rotation", 0) or 0),
                "width": round(page.rect.width, 2), "height": round(page.rect.height, 2),
                "stats": {"chars": len(text), "quality": 0.0},
                "model_error": f"{type(exc).__name__}: {exc}",
            }

    def process_page_optimized(self, page_data):
        """
        Optimized page processing - uses shared OCR functions from read_img_fast
        """
        # PDF-02: page_data carries the page's own text layer so a page whose
        # text is too sparse to skip OCR can still fall back to it. Optional
        # for backward compatibility with the previous 6-element tuple. The
        # 8th field (source path) is likewise optional and used only for
        # diagnostics.
        unpacked = tuple(page_data)
        if len(unpacked) >= 8:
            (page_num, page_bytes, tesseract_lang, tesseract_config,
             needs_ocr, ocr_languages, native_text, source_path) = unpacked[:8]
        elif len(unpacked) >= 7:
            (page_num, page_bytes, tesseract_lang, tesseract_config,
             needs_ocr, ocr_languages, native_text) = unpacked[:7]
            source_path = None
        else:
            (page_num, page_bytes, tesseract_lang, tesseract_config,
             needs_ocr, ocr_languages) = unpacked[:6]
            native_text = ""
            source_path = None
        native_text = native_text or ""
        source_label = os.path.basename(source_path) if source_path \
            else f"page {page_num + 1}"
        
        libs = _get_libraries()  # Shared function
        Image = libs.get('Image')
        cv2 = libs.get('cv2')
        np = libs.get('np')
        
        result = {
            "page_number": page_num + 1,
            "text": "",
            "text_length": 0,
            "method": "unknown"
        }

        def _fallback_to_text_layer(reason):
            """Keep the page's own text layer instead of discarding it."""
            result["ocr_status"] = reason
            stripped = native_text.strip()
            if stripped:
                result["text"] = stripped
                result["text_length"] = len(stripped)
                result["method"] = "text_layer_fallback"
            else:
                result["text"] = ""
                result["text_length"] = 0
                result["method"] = reason
            return result

        if not needs_ocr:
            result["method"] = "skipped_text_based_pdf"
            return result

        # Every engine goes through the engine layer (core.ocr), so a page
        # carries the same provenance whichever one runs: confidence,
        # rotation, the input variant. Tesseract is preferred when installed
        # (it covers this project's heb/eng/ara defaults).
        engine = get_ocr_engine()
        if engine is None:
            return _fallback_to_text_layer("ocr_required_engine_unavailable")
        requested = list(ocr_languages) if ocr_languages else list(DEFAULT_OCR_LANGUAGES)
        usable_languages = installed_ocr_languages(engine, requested, source_label, result)

        try:
            engine_result = None
            pil_image = Image.open(io.BytesIO(page_bytes))
            # Convert palette images with transparency to RGBA to avoid PIL warnings
            if pil_image.mode == 'P' and 'transparency' in pil_image.info:
                pil_image = pil_image.convert('RGBA')
            rgb_image = pil_image.convert("RGB")

            # Use shared preprocessing from read_img_fast (shared by every engine)
            if cv2 and np:
                img_array = np.array(rgb_image)
                processed = _fast_preprocess(img_array, libs)
                pil_processed = Image.fromarray(processed)
            else:
                pil_processed = pil_image.convert("L")

            # Confidence-gated retry on the un-preprocessed page image.
            engine_result = recognize_best(engine, pil_processed, rgb_image, usable_languages)
            text = engine_result.text or ""
            result["ocr_language"] = engine_result.language
            if engine_result.error:
                result["engine_error"] = engine_result.error
            if engine_result.blocks_error:
                result["ocr_blocks_error"] = engine_result.blocks_error
            if engine_result.rotation:
                result["ocr_rotation"] = engine_result.rotation

            engine_name = engine_result.engine
            engine_version = engine_result.engine_version
            confidence = engine_result.mean_confidence
            input_variant = engine_result.input_variant

            if text and len(text.strip()) > 0:
                result["text"] = text.strip()
                result["text_length"] = len(text.strip())
                result["method"] = f"ocr_{engine_name}"
                # Provenance: this text was recognised, not authored.
                result["ocr_engine"] = engine_name
                result["ocr_engine_version"] = engine_version
                result["ocr_derived"] = True
                if confidence is not None:
                    result["ocr_confidence"] = confidence
                if input_variant:
                    result["ocr_input_variant"] = input_variant
            else:
                # OCR found nothing (or could not run). Preserve the native
                # layer, while retaining the actual diagnostic for retry.
                reason = "ocr_failed" if (engine_result and engine_result.error) else "ocr_no_text"
                if engine_result and engine_result.error:
                    result["error"] = engine_result.error
                return _fallback_to_text_layer(reason)

        except Exception as e:
            error_str = str(e).lower()
            # Suppress verbose tesseract errors - we already checked availability
            if 'tesseract' in error_str and ('not installed' in error_str or 'not in your path' in error_str):
                reason = "ocr_required_engine_unavailable"
            else:
                reason = "ocr_failed"
                result["error"] = str(e)
            return _fallback_to_text_layer(reason)

        return result
    
    def read_pdf_file(self, filepath, max_workers=None, languages=None):
        """
        Highly optimized PDF reader with parallel processing and multi-language OCR
        
        Args:
            filepath: Path to PDF file
            max_workers: Number of parallel OCR workers (default: auto)
            languages: List of language codes for OCR (default: ["eng", "ara", "heb"])
                       Supports all Tesseract languages installed on system
        
        Features:
        1. Early detection of PDF type (text vs image)
        2. Auto-detection of page language/script
        3. Multi-language OCR (Hebrew, Arabic, English, etc.)
        4. Parallel OCR processing for image-based PDFs
        5. Skip OCR for text-based PDFs entirely
        6. Adaptive worker count based on CPU cores
        """
        
        # Check libraries (uses shared + PDF-specific)
        libs = self._check_pdf_libraries()
        fitz = libs.get('fitz')
        pytesseract = libs.get('pytesseract')

        # Return error if any required libraries are missing
        missing_libs = libs.get('missing') or []
        if missing_libs:
            return {
                "error": f"Missing required libraries: {', '.join(missing_libs)}",
                "install_command": f"pip install {' '.join(missing_libs)}",
                "filepath": str(filepath),
                "pages": [],
                "total_characters": 0
            }
        
        # Determine optimal worker count
        if max_workers is None:
            # Use more workers for I/O-bound OCR tasks
            max_workers = min(multiprocessing.cpu_count() * 2, 8)
        
        filepath = Path(filepath)
        if not filepath.exists():
            return {
                "error": "File not found", 
                "filepath": str(filepath),
                "pages": [],
                "total_characters": 0
            }
        
        start_time = time.time()
        
        try:
            result = {
                "filepath": str(filepath),
                "num_pages": 0,
                "is_encrypted": False,
                "metadata": {},
                "pages": [],
                "ocr_used": False,
                "processing_time": 0
            }
            
            # Open PDF
            doc = fitz.open(filepath)
            result["num_pages"] = len(doc)
            result["is_encrypted"] = doc.is_encrypted
            
            # Handle encrypted PDFs - try to decrypt or return error
            if doc.is_encrypted:
                # Try to decrypt with empty password (many PDFs are encrypted but allow empty password)
                if not doc.authenticate(""):
                    logger.warning(f"PDF is encrypted and cannot be decrypted: {filepath.name}")
                    doc.close()
                    return {
                        "error": "PDF is encrypted and cannot be decrypted",
                        "filepath": str(filepath),
                        "is_encrypted": True,
                        "pages": [],
                        "total_characters": 0
                    }
            
            result["metadata"] = doc.metadata

            # PDF-02: a PDF is a container. Annotations, comments, embedded
            # files, JavaScript, form fields, signature fields and incremental
            # revisions live in it whether or not they are ever rendered on a
            # page, and none of them are visible to a page-text extraction. The
            # feature pass below records them, materialises the embedded files
            # as child objects (so they are hashed, stored and processed like
            # any other artifact) and never lets a failure there lose the page
            # text that was already extracted.
            try:
                from core.forensics.pdf import (
                    extract_pdf_features,
                    materialise_embedded_files,
                    pdf_feature_text,
                )

                pdf_features = extract_pdf_features(filepath, doc=doc)
                # Materialise the attachments first, so the records that reach
                # the result already name the file each one was written to.
                written = materialise_embedded_files(pdf_features, str(filepath), result)
                result["forensic_features"] = pdf_features
                result["annotations"] = pdf_features.get("annotations", [])
                result["embedded_files"] = [
                    {key: value for key, value in record.items() if key != "content"}
                    for record in pdf_features.get("embedded_files", [])
                ]
                result["javascript"] = pdf_features.get("javascript", [])
                result["form_fields"] = pdf_features.get("form_fields", [])
                result["revisions"] = pdf_features.get("revisions", {})
                result["has_signatures"] = pdf_features.get("has_signatures", False)
                if written:
                    logger.info("Materialised %d PDF embedded file(s) for %s",
                                len(written), filepath.name)
                feature_text = pdf_feature_text(pdf_features)
                if feature_text:
                    result["forensic_text"] = feature_text
                if pdf_features.get("extraction_errors"):
                    result["extraction_errors"] = pdf_features["extraction_errors"]
            except Exception as exc:
                # Feature extraction is additive: record the failure and keep
                # the page text rather than failing the artifact.
                result.setdefault("extraction_errors", []).append(
                    {"scope": "pdf_features", "error": str(exc)})
            
            # ===== CRITICAL OPTIMIZATION: Detect PDF type early =====
            logger.info(f"Detecting PDF type for {filepath.name}...")
            pdf_type = self.detect_pdf_type_early(doc, sample_pages=3)
            logger.info(f"PDF type detected: {pdf_type}")
            
            if pdf_type == "text":
                # ===== TEXT-BASED PDF: Direct extraction (VERY FAST) =====
                # PRODUCTION: Streaming extraction for very large PDFs
                num_pages = len(doc)
                file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
                is_very_large = file_size > 100 * 1024 * 1024 or num_pages > 1000  # > 100MB or > 1000 pages
                
                if is_very_large:
                    logger.info(f"Large PDF detected ({file_size / (1024*1024):.2f}MB, {num_pages} pages), using streaming extraction")
                
                logger.info(f"Using direct text extraction for {filepath.name}")
                
                try:
                    # PRODUCTION: Process pages incrementally to prevent memory buildup
                    max_text_length = 0
                    total_text_length = 0

                    # Decided lazily: a document that needs no page fallback
                    # must not pay for an OCR engine probe.
                    ocr_available = None
                    ocr_languages = languages if languages else DEFAULT_OCR_LANGUAGES

                    for page_num in range(num_pages):
                        # Yield periodically to prevent CPU spikes
                        self.yield_periodically(page_num, interval=5, aggressive=False)
                        
                        # PRODUCTION: For very large PDFs, yield more frequently
                        if is_very_large and page_num % 10 == 0:
                            self.yield_cpu_if_needed(aggressive=True)
                        
                        page = doc[page_num]
                        # The page model, not get_text("text"): reading order
                        # recovered by an X-Y cut, ligatures resolved, words
                        # rejoined across line breaks, tables located. The old
                        # string is kept beside it as text_raw so the change is
                        # auditable.
                        model = self._structure_page(page)
                        text = str(model.get("text") or "")

                        # A page of a "text" document can still be a scan (mixed
                        # documents) or carry a text layer that is recognition
                        # residue. OCR it and keep whichever reading is better.
                        layer_quality = float((model.get("stats") or {}).get("quality") or 0.0)
                        if (len(text.strip()) < MIN_PAGE_TEXT_CHARS
                                or layer_quality < MIN_TEXT_LAYER_QUALITY):
                            if ocr_available is None:
                                ocr_available = bool(
                                    (_is_tesseract_available() and pytesseract is not None)
                                    or get_ocr_engine() is not None)
                            if ocr_available and page.get_images(full=True):
                                fallback = self._page_ocr_fallback(
                                    page, page_num, fitz, filepath, text,
                                    ocr_languages, ocr_available)
                                if fallback and str(fallback.get("text") or "").strip():
                                    ocr_text = str(fallback.get("text") or "")
                                    choice = choose_reading(text, ocr_text, layer_quality)
                                    if choice["source"] == "ocr":
                                        result["ocr_used"] = True
                                        text = choice["text"]
                                        model["text"] = text
                                        model["ocr_fallback"] = {
                                            "trigger": fallback.get("ocr_trigger"),
                                            "engine": fallback.get("ocr_engine"),
                                            "confidence": fallback.get("ocr_confidence"),
                                            "render_dpi": fallback.get("ocr_render_dpi"),
                                            "replaced_chars": len(model.get("text_raw") or ""),
                                            "decision": choice["reason"],
                                        }
                                    else:
                                        # The page keeps its own layer: record that OCR was
                                        # considered and why it was not used, so the
                                        # choice is evidence rather than an absence.
                                        model["ocr_rejected"] = {
                                            "reason": choice["reason"],
                                            "native_chars": len(text),
                                            "ocr_chars": len(ocr_text.strip()),
                                            "native_quality": choice["native_quality"],
                                            "ocr_quality": choice["ocr_quality"],
                                        }

                        text_len = len(text)
                        total_text_length += text_len
                        max_text_length = max(max_text_length, text_len)
                        
                        # PRODUCTION: For very large PDFs, limit stored text per page to prevent memory exhaustion
                        # Store full text for smaller PDFs, but limit for very large ones
                        stored_text = text
                        if is_very_large and text_len > 10 * 1024 * 1024:  # > 10MB per page
                            # Store first 5MB and last 5MB, with indicator
                            stored_text = text[:5 * 1024 * 1024] + "\n\n[... TRUNCATED FOR LARGE FILE ...]\n\n" + text[-5 * 1024 * 1024:]
                            logger.debug(f"Page {page_num + 1} text truncated ({text_len / (1024*1024):.2f}MB -> {len(stored_text) / (1024*1024):.2f}MB)")
                        
                        result["pages"].append({
                            "page_number": page_num + 1,
                            "text": stored_text,
                            "text_length": text_len,  # Store original length
                            "stored_text_length": len(stored_text),  # Store actual stored length
                            "method": ("ocr_layout_fallback" if model.get("ocr_fallback")
                                       else "direct_extraction"),
                            # Where the text is: size, rotation, blocks with their
                            # bounding boxes and column, and any grids found.
                            "width": model.get("width"),
                            "height": model.get("height"),
                            "rotation": model.get("rotation"),
                            "columns": model.get("columns"),
                            "blocks": [
                                {"index": b.get("index"), "type": b.get("type"),
                                 "bbox": b.get("bbox"), "column": b.get("column"),
                                 "dir": b.get("dir"), "lines": b.get("lines"),
                                 "font_size": b.get("font_size"),
                                 "chars": len(b.get("text") or "")}
                                for b in (model.get("blocks") or [])
                            ],
                            "tables": model.get("tables") or [],
                            "text_raw": model.get("text_raw"),
                            "layout": model.get("stats") or {},
                            **({"ocr_fallback": model["ocr_fallback"]}
                               if model.get("ocr_fallback") else {}),
                            **({"model_error": model["model_error"]}
                               if model.get("model_error") else {}),
                        })
                        
                        # PRODUCTION: Log progress for very large PDFs
                        if is_very_large and (page_num + 1) % 100 == 0:
                            progress_pct = ((page_num + 1) / num_pages) * 100
                            logger.info(f"Extracted {page_num + 1}/{num_pages} pages ({progress_pct:.1f}%) - {total_text_length / (1024*1024):.2f}MB total text")
                    
                    # Store summary statistics
                    result["total_text_length"] = total_text_length
                    result["max_page_text_length"] = max_text_length
                    result["is_very_large"] = is_very_large
                    
                finally:
                    # Always close the document to prevent resource leaks
                    doc.close()
                
                # A text document may still have OCR'd individual pages
                # (mixed documents); never overwrite that back to False.
                result["ocr_used"] = bool(result.get("ocr_used"))

            else:
                # ===== IMAGE-BASED PDF: OCR required (SLOWER) =====
                logger.info(f"Using parallel OCR for {filepath.name} ({len(doc)} pages, {max_workers} workers)")
                result["ocr_used"] = True
                
                # Get Tesseract config with multi-language support (shared function)
                ocr_languages = languages if languages else DEFAULT_OCR_LANGUAGES
                tesseract_lang, tesseract_config = _get_optimized_tesseract_config(None, tuple(ocr_languages))
                result["ocr_languages"] = ocr_languages

                # PDF-02: if no OCR engine is present, rasterizing every page is
                # pure waste and previously ended with the text layer thrown
                # away. Decide once, up front. PHASE 2A: "available" now means
                # any engine, not specifically the tesseract binary.
                ocr_available = (bool(pytesseract) and _is_tesseract_available()) \
                    or get_ocr_engine() is not None
                if not ocr_available:
                    # The missing backend is a visible caveat, recorded on the
                    # document (and per page) as ``ocr_status``. It must NOT
                    # fail the document up front: whether any content was
                    # actually lost is decided after the page loop, from the
                    # pages that could not be read at all (see the
                    # ``unavailable_pages`` block below). A PDF whose pages all
                    # carry native text is a completed extraction whether or
                    # not an OCR backend exists.
                    result["ocr_status"] = "ocr_required_engine_unavailable"
                    logger.error(
                        "OCR required but no engine is available for %s; native text layers will be preserved",
                        filepath.name,
                    )
                
                # Prepare page data for parallel processing
                page_data_list = []
                render_dpi = {}  # page index -> resolution it was rasterised at for OCR
                native_texts = {}  # page index -> the page's own text layer
                
                try:
                    for page_num in range(len(doc)):
                        # Yield periodically during preparation
                        self.yield_periodically(page_num, interval=10, aggressive=False)
                        
                        try:
                            page = doc[page_num]
                            
                            # Quick check: does this page have text? The
                            # model gives the same string a reader would see
                            # (reading order, no ligatures, no broken words)
                            # plus the geometry and grids that go with it.
                            model = self._structure_page(page)
                            text = str(model.get("text") or "")
                            
                            if len(text.strip()) > 30:
                                # Page has text, no OCR needed
                                result["pages"].append({
                                    "page_number": page_num + 1,
                                    "text": text,
                                    "text_length": len(text),
                                    "method": "direct_extraction",
                                    "width": model.get("width"),
                                    "height": model.get("height"),
                                    "rotation": model.get("rotation"),
                                    "columns": model.get("columns"),
                                    "blocks": [
                                        {"index": b.get("index"), "type": b.get("type"),
                                         "bbox": b.get("bbox"), "column": b.get("column"),
                                         "dir": b.get("dir"), "lines": b.get("lines"),
                                         "font_size": b.get("font_size"),
                                         "chars": len(b.get("text") or "")}
                                        for b in (model.get("blocks") or [])
                                    ],
                                    "tables": model.get("tables") or [],
                                    "text_raw": model.get("text_raw"),
                                    "layout": model.get("stats") or {},
                                })
                            elif not ocr_available:
                                # Do not silently classify a scanned page as
                                # processed. Preserve any native text layer,
                                # but expose the missing OCR dependency so the
                                # job cannot claim that the page was read.
                                stripped = text.strip()
                                result["pages"].append({
                                    "page_number": page_num + 1,
                                    "text": stripped,
                                    "text_length": len(stripped),
                                    "method": ("text_layer_fallback" if stripped
                                               else "ocr_required_engine_unavailable"),
                                    "ocr_status": "ocr_required_engine_unavailable",
                                    "error": (None if stripped else
                                              "OCR is required but no OCR engine is available")
                                })
                            else:
                                # Page needs OCR - ensure we still track the page number
                                try:
                                    zoom = ocr_render_zoom(page)
                                    render_dpi[page_num] = round(72 * zoom)
                                    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                                    img_bytes = pix.tobytes("png")
                                    
                                    native_texts[page_num] = text
                                    page_data_list.append((
                                        page_num,
                                        img_bytes,
                                        tesseract_lang,
                                        tesseract_config,
                                        True,  # needs_ocr
                                        ocr_languages,  # languages for per-page detection
                                        text,  # native text layer, kept as fallback
                                        # PDF-02: the reader instance is shared
                                        # (one per repository) and these tuples are
                                        # consumed on executor threads, so neither
                                        # ``self`` nor a thread-local can carry the
                                        # file identity here. Pass it explicitly.
                                        str(filepath)
                                    ))
                                except Exception as page_error:
                                    # If we can't convert page to image, store empty page
                                    logger.warning(f"Failed to convert page {page_num + 1} to image: {page_error}")
                                    result["pages"].append({
                                        "page_number": page_num + 1,
                                        "text": "",
                                        "text_length": 0,
                                        "method": "conversion_failed",
                                        "error": str(page_error)
                                    })
                        except Exception as page_error:
                            # If we can't process a page, store empty page to preserve order
                            logger.warning(f"Failed to process page {page_num + 1}: {page_error}")
                            result["pages"].append({
                                "page_number": page_num + 1,
                                "text": "",
                                "text_length": 0,
                                "method": "processing_failed",
                                "error": str(page_error)
                            })
                finally:
                    # Always close the document to prevent resource leaks
                    doc.close()
                
                # ===== PARALLEL OCR PROCESSING =====
                result["ocr_used"] = bool(page_data_list)
                if page_data_list:
                    logger.info(f"Processing {len(page_data_list)} pages with OCR...")
                    
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = {
                            executor.submit(self.process_page_optimized, page_data): page_data[0]
                            for page_data in page_data_list
                        }
                        
                        completed_count = 0
                        for future in as_completed(futures):
                            try:
                                # Yield periodically during OCR processing
                                completed_count += 1
                                self.yield_periodically(completed_count, interval=3, aggressive=True)
                                
                                page_result = future.result()
                                if page_result.get("ocr_derived") and futures[future] in render_dpi:
                                    page_result["ocr_render_dpi"] = render_dpi[futures[future]]

                                # A document typed "image" from a three-page
                                # sample can still hold pages with a short, real
                                # text layer: a cover page with a title and a
                                # page number, or a form with printed labels.
                                # The recognition of the rendered page is
                                # shorter than that layer (rendering loses
                                # hinting, rules and column order), and before
                                # this the layer was simply discarded.
                                native_text = native_texts.get(futures[future], "")
                                if (page_result.get("ocr_derived") and native_text.strip()):
                                    choice = choose_reading(
                                        native_text, page_result.get("text"),
                                        text_quality(native_text))
                                    if choice["source"] == "text_layer":
                                        page_result["ocr_text"] = page_result.get("text")
                                        page_result["text"] = choice["text"]
                                        page_result["text_length"] = len(choice["text"])
                                        page_result["method"] = "text_layer_preferred"
                                        page_result["ocr_rejected"] = {
                                            "reason": choice["reason"],
                                            "native_chars": len(native_text.strip()),
                                            "ocr_chars": len(str(
                                                page_result.get("ocr_text") or "").strip()),
                                            "native_quality": choice["native_quality"],
                                            "ocr_quality": choice["ocr_quality"],
                                        }
                                result["pages"].append(page_result)
                            except Exception as ocr_error:
                                # If OCR fails for a page, we should still have the page number
                                # from the page_data, but we need to extract it
                                logger.warning(f"OCR processing failed for a page: {ocr_error}")
                                # The page_result should still have page_number from process_page_optimized
                                # If not, we can't recover it here, so we'll skip it
                                # The page will be missing, but that's better than crashing
            
            # Sort pages by page number
            result["pages"].sort(key=lambda x: x["page_number"])
            
            # Calculate statistics
            result["total_characters"] = sum(p.get("text_length", 0) for p in result["pages"])
            result["avg_chars_per_page"] = (
                result["total_characters"] / len(result["pages"]) 
                if result["pages"] else 0
            )
            
            # Success rate
            successful_pages = sum(1 for p in result["pages"] if p.get("text_length", 0) > 0)
            result["success_rate"] = f"{successful_pages / len(result['pages']) * 100:.1f}%" if result["pages"] else "0%"
            
            # Method breakdown
            methods = {}
            for page in result["pages"]:
                method = page.get("method", "unknown")
                methods[method] = methods.get(method, 0) + 1
            result["methods_used"] = methods
            
            # A missing OCR backend must remain visible to the job runner even
            # when a PDF also contains a native text layer.
            unavailable_pages = [
                p for p in result["pages"]
                if p.get("method") == "ocr_required_engine_unavailable"
            ]
            if unavailable_pages:
                result["extraction_failed"] = True
                result["retryable"] = True
                result["error"] = "OCR required but unavailable for one or more pages"
                result["ocr_status"] = "ocr_required_engine_unavailable"

            # Processing time
            result["processing_time"] = time.time() - start_time
            
            logger.info(f"PDF processed in {result['processing_time']:.2f}s: {filepath.name}")
            logger.info(f"  Methods: {methods}")
            logger.info(f"  Success rate: {result['success_rate']}")
            
            return result
        
        except Exception as e:
            return {
                "error": str(e),
                "filepath": str(filepath),
                "error_type": type(e).__name__,
                "processing_time": time.time() - start_time,
                "pages": [],
                "total_characters": 0
            }
    
    def batch_process_pdfs(self, pdf_paths, max_workers=4):
        """
        Process multiple PDFs in parallel
        
        Args:
            pdf_paths: List of PDF file paths
            max_workers: Number of parallel PDF processors
        
        Returns:
            List of results
        """
        results = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Create file_info for each PDF
            file_infos = [{"path": path} for path in pdf_paths]
            
            futures = {
                executor.submit(self.read_file, file_info): file_info
                for file_info in file_infos
            }
            
            for future in as_completed(futures):
                file_info = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    logger.info(f"Completed: {file_info['path']}")
                except Exception as e:
                    logger.error(f"Failed: {file_info['path']} - {e}")
                    results.append({
                        "error": str(e),
                        "filepath": file_info['path']
                    })
        
        return results

