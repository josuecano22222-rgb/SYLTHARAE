"""Portable Document Format: the parts beyond the page text.

A PDF is a container as much as a document. Page text is only one of the things
it can carry, and the rest of them are routinely where the interesting evidence
is: annotations and comments, embedded files (attachments), JavaScript in the
catalog, AcroForm and XFA form data, digital signature fields, incremental
update revisions, outlines and named destinations, and document-level metadata.

This module extracts those, with PyMuPDF as the parser, under three rules:

* **Bounded** - per-page annotation caps, a cap on embedded-file size, and a
  bounded byte scan for ``%%EOF`` markers, so a 5 GB PDF cannot turn feature
  extraction into an out-of-memory event;
* **Explicit** - an encrypted PDF, an unreadable catalog entry or a page whose
  annotations raise reports the reason in ``extraction_errors``;
* **Recursive** - embedded files are materialised into the artifact's
  extraction directory by :func:`materialise_embedded_files`, which lets the
  ordinary child pipeline process each attachment as its own object with its own
  hash, parent link and ledger state, rather than flattening it into text.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

#: Caps. Chosen so that a pathological PDF cannot exhaust memory or the disk.
MAX_ANNOTATIONS_PER_PAGE = 2000
MAX_TOTAL_ANNOTATIONS = 50_000
MAX_EMBEDDED_FILE_BYTES = 512 * 1024 * 1024
MAX_EMBEDDED_FILES = 2000
MAX_JAVASCRIPT_SNIPPET = 8192
MAX_EOF_SCAN_BYTES = 64 * 1024 * 1024
MAX_OUTLINE_ITEMS = 5000

_ANNOTATION_TEXT_KEYS = ("content", "title", "subject", "uri", "file", "action",
                         "prev_filename", "info")


@dataclass
class PdfLimits:
    """Work caps for :func:`extract_pdf_features`."""

    max_annotations_per_page: int = MAX_ANNOTATIONS_PER_PAGE
    max_total_annotations: int = MAX_TOTAL_ANNOTATIONS
    max_embedded_files: int = MAX_EMBEDDED_FILES
    max_embedded_file_bytes: int = MAX_EMBEDDED_FILE_BYTES
    max_eof_scan_bytes: int = MAX_EOF_SCAN_BYTES
    max_outline_items: int = MAX_OUTLINE_ITEMS
    max_javascript_snippet: int = MAX_JAVASCRIPT_SNIPPET


DEFAULT_PDF_LIMITS = PdfLimits()


def count_incremental_updates(path: str, limit: int = MAX_EOF_SCAN_BYTES) -> Dict[str, object]:
    """Count ``%%EOF`` markers, i.e. how many times the file was appended to.

    A PDF that has been updated incrementally keeps every previous revision in
    the file; the number of ``%%EOF`` markers is the cheapest evidence that
    earlier - possibly deleted - content is still present. The scan is bounded
    and reports whether it was truncated.
    """
    markers = 0
    scanned = 0
    truncated = False
    try:
        with open(path, "rb") as handle:
            overlap = b""
            while scanned < limit:
                chunk = handle.read(min(4 * 1024 * 1024, limit - scanned))
                if not chunk:
                    break
                scanned += len(chunk)
                markers += (overlap + chunk).count(b"%%EOF")
                overlap = (overlap + chunk)[-8:]
            truncated = bool(handle.read(1))
    except Exception as exc:
        return {"incremental_updates": None, "error": str(exc)}
    return {"incremental_updates": max(0, markers - 1), "eof_markers": markers,
            "scan_bytes": scanned, "scan_truncated": truncated}


def _catalog_keys(doc) -> Dict[str, object]:
    """Catalog-level features: what the document declares about itself."""
    features: Dict[str, object] = {}
    try:
        catalog = doc.pdf_catalog()
    except Exception as exc:
        return {"catalog_error": str(exc)}

    def _key(entry: str) -> Optional[object]:
        try:
            value = doc.xref_get_key(catalog, entry)
        except Exception:
            return None
        # PyMuPDF returns (type, value); 'null' means the entry is absent.
        if not value or value[0] == "null":
            return None
        return value[1]

    for name, entry in (("open_action", "OpenAction"), ("additional_actions", "AA"),
                        ("acroform", "AcroForm"), ("names", "Names"),
                        ("javascript_root", "JavaScript"), ("collection", "Collection"),
                        ("xfa", "XFA"), ("metadata_stream", "Metadata"),
                        ("output_intents", "OutputIntents"), ("lang", "Lang"),
                        ("page_labels", "PageLabels")):
        value = _key(entry)
        if value is not None:
            features[name] = value if not isinstance(value, str) or len(value) < 4096 \
                else value[:4096]
    names = str(features.get("names") or "")
    features["has_javascript"] = bool(
        features.get("open_action") and "JavaScript" in str(features.get("open_action"))) or \
        "JavaScript" in names or bool(features.get("javascript_root"))
    if isinstance(features.get("acroform"), str) and "/XFA" in str(features.get("acroform")):
        features["xfa"] = True
    return features


def _javascript_snippets(doc, limits: PdfLimits) -> List[Dict[str, object]]:
    """JavaScript found in the catalog or in named destinations.

    Bounded by design: the search is confined to the catalog and the name tree
    rather than walking every xref object of the file, which on a large PDF
    would cost more than the rest of the extraction put together.
    """
    snippets: List[Dict[str, object]] = []
    try:
        catalog = doc.pdf_catalog()
    except Exception:
        return snippets

    def _add(kind: str, value: object) -> None:
        text = str(value)
        if text and len(snippets) < 200:
            snippets.append({"kind": kind,
                             "source": text[: limits.max_javascript_snippet]})

    try:
        action = doc.xref_get_key(catalog, "OpenAction")
        if action and action[0] != "null" and "JavaScript" in str(action[1]):
            _add("open_action", action[1])
    except Exception:
        pass
    try:
        names = doc.xref_get_key(catalog, "Names")
        if names and "JavaScript" in str(names[1]):
            _add("names_tree", names[1])
    except Exception:
        pass
    try:
        for entry in ("AA", "JavaScript"):
            value = doc.xref_get_key(catalog, entry)
            if value and value[0] != "null":
                _add(entry, value[1])
    except Exception:
        pass
    return snippets


def _annotations(doc, limits: PdfLimits, errors: List[Dict[str, str]]) -> List[Dict[str, object]]:
    annotations: List[Dict[str, object]] = []
    try:
        page_count = len(doc)
    except Exception as exc:
        errors.append({"scope": "annotations", "error": str(exc)})
        return annotations
    for page_index in range(page_count):
        if len(annotations) >= limits.max_total_annotations:
            errors.append({"scope": "annotations",
                           "error": f"annotation cap {limits.max_total_annotations} reached"})
            break
        try:
            page = doc[page_index]
            page_annotations = list(page.annots() or [])
        except Exception as exc:
            errors.append({"scope": f"page {page_index + 1} annotations", "error": str(exc)})
            continue
        for annotation in page_annotations[: limits.max_annotations_per_page]:
            try:
                info = dict(annotation.info or {})
            except Exception as exc:
                errors.append({"scope": f"page {page_index + 1} annotation info",
                               "error": str(exc)})
                info = {}
            record: Dict[str, object] = {
                "page": page_index + 1,
                "type": str(getattr(annotation, "type", [None, None])[1]
                            if isinstance(getattr(annotation, "type", None), (list, tuple))
                            else getattr(annotation, "type", "")),
            }
            for key in _ANNOTATION_TEXT_KEYS:
                if key in info and info[key] not in (None, ""):
                    record[key] = str(info[key])[: limits.max_javascript_snippet]
            for key, value in info.items():
                record.setdefault(key, str(value)[:1024] if value is not None else None)
            try:
                rect = annotation.rect
                record["rect"] = [round(float(rect.x0), 2), round(float(rect.y0), 2),
                                  round(float(rect.x1), 2), round(float(rect.y1), 2)]
            except Exception:
                pass
            if getattr(annotation, "has_js", None):
                try:
                    js = annotation.info.get("js") if annotation.info else None
                    if js:
                        record["javascript"] = str(js)[: limits.max_javascript_snippet]
                except Exception:
                    pass
            # File attachments carried by an annotation (FileAttachment, Screen).
            try:
                embedded = annotation.fileGet() if hasattr(annotation, "fileGet") else None
            except Exception:
                embedded = None
            if embedded:
                record["embedded_file"] = {
                    "filename": embedded.get("filename") or embedded.get("name"),
                    "length": embedded.get("length"),
                    "desc": embedded.get("desc"),
                    "size_bytes": len(embedded.get("content") or b""),
                }
            annotations.append(record)
        if len(page_annotations) > limits.max_annotations_per_page:
            errors.append({"scope": f"page {page_index + 1}",
                           "error": f"page has more than {limits.max_annotations_per_page} "
                                    f"annotations; the remainder were not recorded"})
    return annotations


def _form_fields(doc, limits: PdfLimits, errors: List[Dict[str, str]]) -> List[Dict[str, object]]:
    fields: List[Dict[str, object]] = []
    try:
        page_count = len(doc)
    except Exception:
        return fields
    for page_index in range(page_count):
        if len(fields) >= 20_000:
            errors.append({"scope": "form fields", "error": "field cap reached"})
            break
        try:
            page = doc[page_index]
            widgets = list(page.widgets() or [])
        except Exception as exc:
            errors.append({"scope": f"page {page_index + 1} widgets", "error": str(exc)})
            continue
        for widget in widgets:
            try:
                record = {
                    "page": page_index + 1,
                    "name": widget.field_name,
                    "type": widget.field_type_string,
                    "value": str(widget.field_value)[:4096] if widget.field_value is not None else None,
                    "default": str(widget.field_value_default)[:4096]
                    if getattr(widget, "field_value_default", None) is not None else None,
                    "flags": widget.field_flags,
                    "read_only": bool(widget.field_flags & 1) if widget.field_flags else False,
                    "required": bool(widget.field_flags & 2) if widget.field_flags else False,
                }
                if widget.field_type_string in ("Signature", "Sig"):
                    record["signature_field"] = True
                fields.append(record)
            except Exception as exc:
                errors.append({"scope": f"page {page_index + 1} widget", "error": str(exc)})
    return fields


def _embedded_files(doc, limits: PdfLimits, errors: List[Dict[str, str]]) -> List[Dict[str, object]]:
    """PDF-embedded files (attachments), with hashes and payload for extraction.

    Wholly embedded in the container as they are, the payload is returned so the
    caller can decide whether to materialise it as a child object. Content is
    included only for files below :attr:`PdfLimits.max_embedded_file_bytes`; a
    larger attachment is still recorded with its name and declared size.
    """
    files: List[Dict[str, object]] = []
    try:
        names = list(doc.embfile_names() or [])
    except Exception as exc:
        errors.append({"scope": "embedded files", "error": str(exc)})
        return files
    for index, name in enumerate(names[: limits.max_embedded_files]):
        try:
            info = doc.embfile_info(index)
        except Exception as exc:
            errors.append({"scope": f"embedded file {name!r}", "error": str(exc)})
            info = {}
        record: Dict[str, object] = {
            "name": name,
            "filename": info.get("filename"),
            "description": info.get("desc"),
            "declared_size": info.get("size"),
            "creation_date": info.get("creationDate"),
            "mod_date": info.get("modDate"),
            "checksum": info.get("checksum"),
        }
        try:
            declared = info.get("size") or 0
            if isinstance(declared, (int, float)) and declared > limits.max_embedded_file_bytes:
                record["content_skipped"] = (
                    f"attachment is larger than {limits.max_embedded_file_bytes} bytes")
            else:
                content = doc.embfile_get(name)
                if content is not None:
                    record["size_bytes"] = len(content)
                    record["md5"] = hashlib.md5(content, usedforsecurity=False).hexdigest()
                    record["sha256"] = hashlib.sha256(content).hexdigest()
                    record["content"] = content
        except Exception as exc:
            errors.append({"scope": f"embedded file {name!r} content", "error": str(exc)})
        files.append(record)
    if len(names) > limits.max_embedded_files:
        errors.append({"scope": "embedded files",
                       "error": f"more than {limits.max_embedded_files} attachments; "
                                f"the remainder were not recorded"})
    return files


def _outline(doc, limits: PdfLimits) -> List[Dict[str, object]]:
    try:
        toc = doc.get_toc(simple=False) or []
    except Exception:
        return []
    outline: List[Dict[str, object]] = []
    for entry in toc[: limits.max_outline_items]:
        try:
            level, title, page = entry[0], entry[1], entry[2]
            record = {"level": level, "title": str(title)[:1024], "page": page}
            if len(entry) > 3 and isinstance(entry[3], dict):
                dest = {k: v for k, v in entry[3].items()
                        if k in ("kind", "uri", "file", "name", "to")}
                if dest:
                    record["destination"] = dest
            outline.append(record)
        except Exception:
            continue
    return outline


def extract_pdf_features(path: str, doc=None, limits: PdfLimits = DEFAULT_PDF_LIMITS,
                         include_content: bool = True) -> Dict[str, object]:
    """Extract container-level PDF evidence.

    Args:
        path: Path to the PDF.
        doc: An already-open PyMuPDF document. Reusing the caller's handle
            avoids a second parse and guarantees the features describe the same
            revision the pages were read from.
        limits: Work caps (:class:`PdfLimits`).
        include_content: When False, embedded-file payloads are hashed but not
            returned (used by callers that only want the record).

    Returns:
        ``{"catalog": ..., "annotations": [...], "javascript": [...],
        "form_fields": [...], "embedded_files": [...], "outline": [...],
        "revisions": {...}, "extraction_errors": [...]}``.
    """
    features: Dict[str, object] = {"extraction_errors": []}
    errors: List[Dict[str, str]] = features["extraction_errors"]  # type: ignore[assignment]

    close_after = False
    if doc is None:
        try:
            import pymupdf as fitz  # type: ignore

            doc = fitz.open(path)
            close_after = True
        except Exception as exc:
            errors.append({"scope": "open", "error": str(exc)})
            return features

    try:
        features["revisions"] = count_incremental_updates(path, limits.max_eof_scan_bytes)
        try:
            features["catalog"] = _catalog_keys(doc)
        except Exception as exc:
            errors.append({"scope": "catalog", "error": str(exc)})
        try:
            features["javascript"] = _javascript_snippets(doc, limits)
        except Exception as exc:
            errors.append({"scope": "javascript", "error": str(exc)})
        try:
            features["annotations"] = _annotations(doc, limits, errors)
        except Exception as exc:
            errors.append({"scope": "annotations", "error": str(exc)})
        try:
            features["form_fields"] = _form_fields(doc, limits, errors)
        except Exception as exc:
            errors.append({"scope": "form_fields", "error": str(exc)})
        try:
            embedded = _embedded_files(doc, limits, errors)
            if not include_content:
                for record in embedded:
                    record.pop("content", None)
            features["embedded_files"] = embedded
        except Exception as exc:
            errors.append({"scope": "embedded_files", "error": str(exc)})
        try:
            features["outline"] = _outline(doc, limits)
        except Exception as exc:
            errors.append({"scope": "outline", "error": str(exc)})
        try:
            features["signature_flags"] = doc.get_sigflags()
            features["has_signatures"] = bool(features["signature_flags"] and features["signature_flags"] > 0)
        except Exception:
            pass
        try:
            features["is_repaired"] = bool(doc.is_repaired)
        except Exception:
            pass
        try:
            features["xref_count"] = doc.xref_length()
        except Exception:
            pass
    finally:
        if close_after:
            try:
                doc.close()
            except Exception:
                pass
    return features


def materialise_embedded_files(features: Dict[str, object], source_path: str,
                               result: Optional[Dict[str, object]] = None) -> List[str]:
    """Write a PDF's embedded files to disk so they become child objects.

    Returns the list of written paths. The destination directory follows the
    same convention the Office and archive readers use, and the parent result is
    stamped with ``extraction_path`` so the router recurses into the children
    through the ordinary pipeline - each attachment then gets its own row,
    hash, parent link and ledger state.
    """
    written: List[str] = []
    embedded = features.get("embedded_files") or []
    if not embedded:
        return written
    from core.archive_safety import windows_safe_component

    try:
        from core.path_utils import get_extraction_name_file

        extract_dir = get_extraction_name_file(source_path, "")
        os.makedirs(extract_dir, exist_ok=True)
    except Exception:
        return written

    used: Dict[str, int] = {}
    for record in embedded:  # type: ignore[union-attr]
        content = record.get("content") if isinstance(record, dict) else None
        if not content:
            continue
        name = str(record.get("filename") or record.get("name") or "attachment")
        safe = windows_safe_component(os.path.basename(name)) or "attachment"
        if safe in used:
            used[safe] += 1
            stem, extension = os.path.splitext(safe)
            safe = f"{stem}_{used[safe]}{extension}"
        else:
            used[safe] = 0
        destination = os.path.join(extract_dir, safe)
        try:
            with open(destination, "wb") as handle:
                handle.write(content)
        except Exception:
            continue
        record.pop("content", None)
        record["materialised_as"] = destination
        written.append(destination)
    if written and result is not None:
        result["extraction_path"] = extract_dir
    return written


def pdf_feature_text(features: Dict[str, object]) -> str:
    """Flatten the searchable parts of a PDF feature block into text."""
    chunks: List[str] = []
    for annotation in features.get("annotations") or []:  # type: ignore[union-attr]
        if not isinstance(annotation, dict):
            continue
        for key in ("content", "title", "subject", "uri", "file", "javascript"):
            value = annotation.get(key)
            if value:
                chunks.append(str(value))
    for field in features.get("form_fields") or []:  # type: ignore[union-attr]
        if isinstance(field, dict):
            if field.get("name"):
                chunks.append(str(field["name"]))
            if field.get("value"):
                chunks.append(str(field["value"]))
    for snippet in features.get("javascript") or []:  # type: ignore[union-attr]
        if isinstance(snippet, dict) and snippet.get("source"):
            chunks.append(str(snippet["source"]))
    for item in features.get("outline") or []:  # type: ignore[union-attr]
        if isinstance(item, dict) and item.get("title"):
            chunks.append(str(item["title"]))
    seen = set()
    unique = []
    for chunk in chunks:
        if chunk not in seen:
            seen.add(chunk)
            unique.append(chunk)
    return "\n".join(unique)
