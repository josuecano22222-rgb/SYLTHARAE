"""Content-based format identification with evidence and discrepancies.

This module is the pipeline's answer to "what is this file?". It never trusts
the filename - the filename is *evidence*, recorded as what the artifact was
declared to be, and every disagreement with the content is preserved in
:attr:`DetectionResult.discrepancies`.

The signature work is delegated to :mod:`core.detect_binanry_utils`, the
sniffer already used in production. What this module adds is the part a
forensic record needs on top of "which magic bytes matched":

* **format identity** - ``.docx`` vs ``.docm`` vs ``.dotx`` are three different
  formats, and the difference is forensic (one of them can carry executable
  macro code). Container entry names and content types decide, not extension.
* **container features** - macros, digital signatures, encryption, templates,
  slideshows, hidden/embedded parts, incremental PDF revisions - reported as
  facts about the artifact.
* **declared vs detected** - the discrepancy list, with severities, so that
  "the file called report.doc is actually a ZIP of images" is visible in the
  record instead of being silently normalised away.
* **bounded cost** - identification reads a header, a central directory and at
  most a few kilobytes of small metadata parts. It never loads a file body, so
  it is safe on multi-gigabyte artifacts and on millions of files.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from core.detect_binanry_utils import (
    CONFIDENCE_NONE,
    CONFIDENCE_STRONG,
    CONFIDENCE_WEAK,
    SNIFF_HEADER_SIZE,
    detect_file_type_with_confidence,
    read_file_header,
)

from .catalog import (
    FormatFamily,
    FormatSpec,
    READER_BY_FAMILY,
    canonical_extension,
    lookup_by_extension,
    lookup_format,
)

DEFAULT_DECLARED_NAME = "unknown"

#: Prefix used when a container is identified but its exact member variant
#: cannot be proven from the available evidence.
_UNPROVEN = "unproven"

#: Severities used for discrepancies. ``high`` means a reader chosen from the
#: declared extension would process the wrong bytes; ``notable`` means the
#: record should show it but handling is the same; ``info`` is cosmetic.
SEVERITY_HIGH = "high"
SEVERITY_NOTABLE = "notable"
SEVERITY_INFO = "info"

_MACRO_ENTRY_RE = re.compile(r"(?:^|/)(?:vbaProject\.bin|vbaData\.xml|"
                             r"vbaProjectSignature\.bin)$", re.IGNORECASE)
_SIGNATURE_ENTRY_RE = re.compile(r"^_xmlsignatures/", re.IGNORECASE)

#: Sizes of the small container parts this module is willing to read.
_METADATA_PART_LIMIT = 256 * 1024

#: Marker bytes for the ZIP local/central records, used to tell a ZIP that
#: merely *contains* PK data from a real archive.
_ZIP_MARKERS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

#: Extensions the signature sniffer reports for ZIP-based packages. Their
#: verdict is a *guess* made from the first 4 KiB; the container's own part
#: list and content types are authoritative, so these always get reopened.
_ZIP_PACKAGE_EXTENSIONS = frozenset({
    ".zip", ".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm", ".dotx",
    ".xltx", ".potx", ".dotm", ".xltm", ".potm", ".ppsm", ".ppsx", ".xlsb",
    ".odt", ".ods", ".odp", ".ott", ".ots", ".otp", ".odg", ".epub",
})


@dataclass(frozen=True)
class Discrepancy:
    """A difference between what a file claims to be and what it is.

    Attributes:
        kind: Machine-readable category (see the ``DISC_*`` constants below).
        declared: The extension or type the filename claimed.
        detected: What the content proved.
        severity: ``info`` / ``notable`` / ``high``.
        detail: Human-readable explanation for the audit record.
    """

    kind: str
    declared: str
    detected: str
    severity: str
    detail: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "kind": self.kind,
            "declared": self.declared,
            "detected": self.detected,
            "severity": self.severity,
            "detail": self.detail,
        }


# Discrepancy kinds -----------------------------------------------------------
DISC_EXTENSION_ABSENT = "extension_absent"
DISC_EXTENSION_UNKNOWN = "extension_unknown"
DISC_FORMAT_MISMATCH = "format_mismatch"
DISC_VARIANT_MISMATCH = "variant_mismatch"
DISC_ALIAS = "extension_alias"
DISC_ENCRYPTED = "encrypted_content"
DISC_CONTAINER_SUBTYPE = "container_subtype"
DISC_BINARY_TEXT = "binary_declared_as_text"
DISC_UNREADABLE = "unreadable"
DISC_UNSUPPORTED = "unsupported_format"


@dataclass
class DetectionResult:
    """What a file is, what it claimed to be, and how to process it.

    Attributes:
        declared_name: Filename as discovered (extension preserved verbatim).
        declared_extension: Lower-cased extension from the filename.
        extension: Canonical extension of the *detected* format.
        format_id: Catalogue identifier of the detected format.
        family: Coarse family, which selects the reader.
        mime: MIME type for the detected format.
        version: Format version when the container states one (``PDF-1.7``).
        confidence: ``strong`` / ``weak`` / ``none`` - the strength of the
            content evidence behind the detection.
        evidence: What matched, in order, for auditability.
        features: Container facts (macros, encryption, entry counts, ...).
        discrepancies: Declared-vs-detected differences, with severities.
        reader: Reader module the router should dispatch to.
        parse_notes: Capabilities the owning format is required to extract.
    """

    declared_name: str
    declared_extension: str
    extension: str
    format_id: str
    family: FormatFamily
    mime: str
    version: Optional[str] = None
    confidence: str = CONFIDENCE_NONE
    evidence: List[str] = field(default_factory=list)
    features: Dict[str, Any] = field(default_factory=dict)
    discrepancies: List[Discrepancy] = field(default_factory=list)
    reader: Optional[str] = None
    description: str = ""
    parse_notes: Tuple[str, ...] = ()

    # ------------------------------------------------------------------ API
    @property
    def macro_present(self) -> bool:
        """True when the artifact carries macro code (declared, not inferred)."""
        return bool(self.features.get("macros_present"))

    @property
    def encrypted(self) -> bool:
        """True when the payload is encrypted and cannot be parsed as-is."""
        return bool(self.features.get("encrypted"))

    @property
    def high_severity_discrepancies(self) -> List[Discrepancy]:
        return [d for d in self.discrepancies if d.severity == SEVERITY_HIGH]

    @property
    def extension_matches_content(self) -> bool:
        """True when the declared extension names the detected format.

        Aliases count as a match (``.jpeg`` for ``.jpg``); a different but
        valid spelling of a different format does not.
        """
        if not self.declared_extension:
            return False
        declared = lookup_by_extension(self.declared_extension)
        if declared is None:
            return False
        return canonical_extension(self.declared_extension) == self.extension

    def as_dict(self) -> Dict[str, Any]:
        return {
            "declared_name": self.declared_name,
            "declared_extension": self.declared_extension,
            "detected_extension": self.extension,
            "format_id": self.format_id,
            "format_family": self.family.value,
            "mime_type": self.mime,
            "format_version": self.version,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "features": dict(self.features),
            "discrepancies": [d.as_dict() for d in self.discrepancies],
            "extension_matches_content": self.extension_matches_content,
            "reader": self.reader,
            "description": self.description,
            "parse_notes": list(self.parse_notes),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _declared(declared_name: Optional[str], path: Optional[str]) -> Tuple[str, str]:
    name = declared_name or (os.path.basename(path) if path else DEFAULT_DECLARED_NAME)
    return name or DEFAULT_DECLARED_NAME, os.path.splitext(name or "")[1].lower()


def _result_for(spec: FormatSpec, *, declared_name: str, declared_extension: str,
                extension: Optional[str] = None, confidence: str = CONFIDENCE_STRONG,
                evidence: Optional[List[str]] = None,
                features: Optional[Dict[str, Any]] = None,
                version: Optional[str] = None) -> DetectionResult:
    return DetectionResult(
        declared_name=declared_name,
        declared_extension=declared_extension,
        extension=extension or spec.extension,
        format_id=spec.format_id,
        family=spec.family,
        mime=spec.mime,
        version=version,
        confidence=confidence,
        evidence=list(evidence or []),
        features=dict(features or {}),
        reader=READER_BY_FAMILY.get(spec.family),
        description=spec.description,
        parse_notes=spec.parse_notes,
    )


def _spec_or_binary(extension: str) -> FormatSpec:
    spec = lookup_by_extension(extension)
    if spec is not None:
        return spec
    return lookup_format("binary.unknown") or lookup_format("unknown")


def _zip_entries(path: Optional[str], data: bytes) -> Tuple[Optional[List[str]], Optional[zipfile.ZipFile], Optional[io.BytesIO]]:
    """Enumerate a ZIP's entry names.

    Prefers the file on disk (the central directory is authoritative); falls
    back to an in-memory open of the sniffed bytes. Returns ``(names, zip_file,
    buffer)``; the caller must close ``zip_file`` when it is not ``None``. The
    in-memory buffer is returned only to keep it alive for the ZipFile.
    """
    if path:
        try:
            zf = zipfile.ZipFile(path)
            return zf.namelist(), zf, None
        except Exception:
            pass
    buffer = io.BytesIO(data)
    try:
        zf = zipfile.ZipFile(buffer)
        return zf.namelist(), zf, buffer
    except Exception:
        return None, None, None


def _read_part(zf: Optional[zipfile.ZipFile], name: str, limit: int = _METADATA_PART_LIMIT) -> Optional[bytes]:
    """Read one small container part, never more than ``limit`` bytes."""
    if zf is None:
        return None
    try:
        info = zf.getinfo(name)
        if info.file_size > limit:
            return None
        return zf.read(name)
    except Exception:
        return None


def _zip_features(zf: Optional[zipfile.ZipFile], names: List[str], path: Optional[str]) -> Dict[str, Any]:
    """Container facts for a ZIP-family package (bounded: no decompression)."""
    features: Dict[str, Any] = {
        "container": "zip",
        "entry_count": len(names),
    }
    try:
        infos = zf.infolist() if zf is not None else []
    except Exception:
        infos = []
    if infos:
        features["uncompressed_bytes"] = sum(i.file_size for i in infos)
        features["compressed_bytes"] = sum(i.compress_size for i in infos)
        features["encrypted_entries"] = sum(1 for i in infos if i.flag_bits & 0x1)
        features["encrypted"] = bool(features["encrypted_entries"])
        methods = sorted({i.compress_type for i in infos})
        features["compression_methods"] = methods
        entries_with_data_descriptor = sum(1 for i in infos if i.flag_bits & 0x8)
        features["entries_with_data_descriptor"] = entries_with_data_descriptor
    features["has_macros"] = features["macros_present"] = any(
        _MACRO_ENTRY_RE.search(n) for n in names)
    features["has_xml_signature"] = any(_SIGNATURE_ENTRY_RE.match(n) for n in names)
    features["skipped_by_zipfile"] = _unreadable_entries(zf, names)
    return features


def _unreadable_entries(zf: Optional[zipfile.ZipFile], names: List[str]) -> List[str]:
    """Entries ``zipfile`` would refuse to read (encrypted/unsupported).

    Recorded, never skipped silently: an entry that cannot be read is evidence
    about the container and must appear in the artifact record.
    """
    if zf is None:
        return []
    unreadable: List[str] = []
    try:
        for info in zf.infolist():
            if info.flag_bits & 0x1:
                unreadable.append(info.filename)
    except Exception:
        return []
    return unreadable[:1000]


def _classify_ooxml(zf: Optional[zipfile.ZipFile], names: List[str],
                    features: Dict[str, Any]) -> Tuple[FormatSpec, Optional[str]]:
    """Decide which OOXML variant a package is, from its parts.

    The variant is decided by the *content types* the package declares for its
    main part, which is what the format standard uses; entry names are used
    only to find the application area. This distinguishes a document from a
    template from a slideshow, and each of those from its macro-enabled twin -
    a distinction that changes what has to be extracted.
    """
    lowered = {n.lower() for n in names}
    content_types = _read_part(zf, "[Content_Types].xml") or b""
    ct = content_types.decode("utf-8", "replace").lower()
    features["content_types_present"] = bool(content_types)
    features["declared_content_types"] = sorted(
        set(re.findall(r'contenttype="([^"]+)"', ct)))[:32]

    has_word_main = "word/document.xml" in lowered
    has_xl_main = "xl/workbook.xml" in lowered or "xl/workbook.bin" in lowered
    has_ppt_main = "ppt/presentation.xml" in lowered
    # Content-type flags can be true for *embedded* parts (a Word doc that
    # embeds an Excel chart declares spreadsheetML in [Content_Types].xml).
    # Main-part paths decide the package variant.
    is_word = has_word_main or ("/word/" in ct or "wordprocessingml.main" in ct)
    is_excel = has_xl_main or (
        not has_word_main and not has_ppt_main and "spreadsheetml.main" in ct
    )
    is_ppt = has_ppt_main or (
        not has_word_main and not has_xl_main and "presentationml.main" in ct
    )
    # The content type is the format's own statement about the variant; a part
    # named vbaProject.bin is the artifact that makes it executable. Either is
    # sufficient evidence of macro capability, so both are consulted.
    if "macroenabled" in ct:
        features["macros_present"] = True
        features["macro_evidence"] = "macroEnabled content type"
    macro = bool(features.get("macros_present"))

    if has_word_main or (is_word and not has_xl_main and not has_ppt_main):
        features["application"] = "word"
        if "template" in ct:
            features["variant_evidence"] = "template content type"
            return lookup_format("ooxml.doc.template.macro" if macro else "ooxml.doc.template"), None
        features["variant_evidence"] = "word/document.xml main part"
        return lookup_format("ooxml.doc.macro" if macro else "ooxml.doc"), None
    if is_excel:
        binary_workbook = "xl/workbook.bin" in lowered
        features["application"] = "excel"
        if binary_workbook:
            features["variant_evidence"] = "xl/workbook.bin part"
            return lookup_format("ooxml.xls.binary"), None
        if "template" in ct:
            features["variant_evidence"] = "template content type"
            return lookup_format("ooxml.xls.template.macro" if macro else "ooxml.xls.template"), None
        features["variant_evidence"] = "workbook content type"
        return lookup_format("ooxml.xls.macro" if macro else "ooxml.xls"), None
    if is_ppt:
        features["application"] = "powerpoint"
        if "slideshow" in ct:
            features["variant_evidence"] = "slideshow content type"
            return lookup_format("ooxml.ppt.slideshow.macro" if macro else "ooxml.ppt.slideshow"), None
        if "template" in ct:
            features["variant_evidence"] = "template content type"
            return lookup_format("ooxml.ppt.template.macro" if macro else "ooxml.ppt.template"), None
        features["variant_evidence"] = "presentation content type"
        return lookup_format("ooxml.ppt.macro" if macro else "ooxml.ppt"), None

    # An OOXML-marked package whose application area is unreadable: name the
    # container, not a guess, so the router does not send it to a reader that
    # cannot open it.
    features.setdefault("variant_evidence", "unresolved application area")
    return lookup_format("archive.zip"), _UNPROVEN


def _zip_detection(names: List[str], zf: Optional[zipfile.ZipFile],
                   features: Dict[str, Any]) -> Tuple[FormatSpec, Optional[str], List[str]]:
    """Classify a ZIP container: OOXML / ODF / EPUB / plain archive."""
    evidence: List[str] = [f"ZIP container ({len(names)} entries)"]
    lowered = [n.lower() for n in names]
    mimetype = _read_part(zf, "mimetype") or b""
    mimetype_text = mimetype.decode("ascii", "replace").strip() if mimetype else ""
    if mimetype_text:
        features["mimetype_entry"] = mimetype_text

    if names and names[0] == "mimetype" and mimetype_text:
        odf_map = {
            "application/vnd.oasis.opendocument.text": "odf.text",
            "application/vnd.oasis.opendocument.text-template": "odf.text.template",
            "application/vnd.oasis.opendocument.spreadsheet": "odf.spreadsheet",
            "application/vnd.oasis.opendocument.spreadsheet-template": "odf.spreadsheet.template",
            "application/vnd.oasis.opendocument.presentation": "odf.presentation",
            "application/vnd.oasis.opendocument.presentation-template": "odf.presentation.template",
            "application/vnd.oasis.opendocument.graphics": "odf.graphics",
            "application/epub+zip": "ebook.epub",
        }
        spec = lookup_format(odf_map.get(mimetype_text, ""))
        if spec is not None:
            evidence.append(f"uncompressed 'mimetype' part declares {mimetype_text}")
            # Membership against the lower-cased list; the read below uses the
            # exact part name, because ZIP entry names are case-sensitive.
            features["odf_manifest"] = "meta-inf/manifest.xml" in lowered
            if "meta-inf/manifest.xml" in lowered:
                manifest = _read_part(zf, "META-INF/manifest.xml") or b""
                if b"encryption-data" in manifest.lower():
                    features["encrypted"] = True
                    features["encryption_evidence"] = "META-INF/manifest.xml encryption-data"
            return spec, None, evidence

    if "[content_types].xml" in lowered:
        evidence.append("[Content_Types].xml part present (OOXML package)")
        spec, unproven = _classify_ooxml(zf, names, features)
        if unproven:
            evidence.append("application area not resolvable from the part list")
        return spec, unproven, evidence

    # draw.io / diagrams.net: the ZIP form of a .drawio holds the mxfile
    # document as file.xml next to metadata.xml. Name it by payload so the
    # diagram reader - not the archive reader - receives it.
    if "file.xml" in lowered and "metadata.xml" in lowered:
        evidence.append("file.xml + metadata.xml parts (draw.io ZIP container)")
        spec = lookup_format("diagram.drawio")
        if spec is not None:
            return spec, None, evidence

    index_xml_name = next((n for n in names if n.lower() == "index.xml"), None)
    if index_xml_name:
        idx_bytes = _read_part(zf, index_xml_name) or b""
        head = idx_bytes[:512]
        if b"sl:document" in head or b"/namespaces/sl" in head:
            spec = lookup_format("iwork.pages")
            if spec is not None:
                evidence.append("index.xml declares an Apple Pages (sl) document")
                features["container"] = "iwork-pages"
                return spec, None, evidence
        if b"ls:document" in head or b"/namespaces/ls" in head:
            spec = lookup_format("iwork.numbers")
            if spec is not None:
                evidence.append("index.xml declares an Apple Numbers (ls) document")
                features["container"] = "iwork-numbers"
                return spec, None, evidence

    if any(
        n.lower().startswith("index/") and n.lower().endswith(".iwa")
        for n in names
    ):
        spec = lookup_format("iwork.pages")
        if spec is not None:
            evidence.append("Index/*.iwa parts (modern iWork bundle)")
            features["container"] = "iwork-iwa"
            return spec, None, evidence

    # A plain archive. Note whether it is only a wrapper around documents, and
    # whether members are themselves processable: the recursion is the loader's
    # job, but the inventory belongs in the record.
    from core.detect_binanry_utils import _NESTED_DOCUMENT_EXTENSIONS  # local: shared list

    nested_documents = sum(1 for n in lowered
                           if n.endswith(tuple(_NESTED_DOCUMENT_EXTENSIONS)))
    nested_archives = sum(1 for n in lowered
                          if n.endswith((".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
                                         ".xz", ".cab")))
    features["nested_documents"] = nested_documents
    features["nested_archives"] = nested_archives
    features["encrypted"] = bool(features.get("encrypted_entries"))
    return lookup_format("archive.zip"), None, evidence


def _ole_streams(path: Optional[str], data: bytes) -> Tuple[List[str], Optional[Any]]:
    """Enumerate OLE compound-file stream names via :mod:`olefile`."""
    try:
        import olefile
    except Exception:
        return [], None
    if path and os.path.exists(path):
        try:
            ole = olefile.OleFileIO(path)
            return ole.listdir(), ole
        except Exception:
            return [], None
    try:
        buffer = io.BytesIO(data)
        ole = olefile.OleFileIO(buffer)
        return ole.listdir(), ole
    except Exception:
        return [], None


def _fib_flags(stream_bytes: bytes) -> Optional[int]:
    """Word FIB ``flags`` word (offset 0x0A) from a WordDocument stream."""
    if len(stream_bytes) < 12:
        return None
    return int.from_bytes(stream_bytes[10:12], "little")


def _biff_document_type(stream_bytes: bytes) -> Optional[int]:
    """Excel BIFF BOF ``dt`` field: 0x10 workbook, 0x20 template."""
    if len(stream_bytes) < 8:
        return None
    for offset in (0, 0x200, 0x400, 0x800, 0x1000):
        if len(stream_bytes) < offset + 4:
            break
        if stream_bytes[offset:offset + 2] in (b"\x09\x08", b"\x09\x04", b"\x09\x02"):
            return int.from_bytes(stream_bytes[offset + 2:offset + 4], "little")
    return None


def _ole_detection(path: Optional[str], data: bytes,
                   declared_extension: str,
                   features: Dict[str, Any]) -> Tuple[FormatSpec, Optional[str], List[str], Dict[str, Any]]:
    """Classify an OLE compound file: legacy Office, MSG, encrypted package."""
    evidence: List[str] = ["OLE compound file signature (D0 CF 11 E0 ...)"]
    streams, ole = _ole_streams(path, data)
    names = ["/".join(entry) for entry in streams]
    lowered = [n.lower() for n in names]
    features["container"] = "ole"
    features["stream_count"] = len(names)
    features["stream_names"] = names[:64]
    macro_roots = ("_vba_project_cur", "macros", "vba", "vba_project")
    macro_streams = [n for n in lowered
                     if any(part in n for part in macro_roots)
                     or n.endswith("vbaproject.bin")]
    features["macros_present"] = bool(macro_streams)
    features["macro_streams"] = names[:0] + [
        original for original, lower in zip(names, lowered)
        if any(part in lower for part in macro_roots)
        or lower.endswith("vbaproject.bin")][:32]

    def _stream(prefix: str, limit: int = 1 << 20) -> Optional[bytes]:
        if ole is None:
            return None
        try:
            for entry in ole.listdir():
                joined = "/".join(entry)
                if joined.lower().startswith(prefix.lower()):
                    with ole.openstream(entry) as handle:
                        return handle.read(limit)
        except Exception:
            return None
        return None

    if any(n.lower().startswith("encryptioninfo") for n in names) or \
            any(n.lower().startswith("encryptedpackage") for n in names):
        features["encrypted"] = True
        features["encryption_evidence"] = "EncryptionInfo/EncryptedPackage streams (CFB encryption)"
        evidence.append("EncryptionInfo + EncryptedPackage streams present")
        spec = lookup_format("ooxml.doc")  # placeholder replaced below
        declared = lookup_by_extension(declared_extension)
        if declared is not None and declared.family in (
                FormatFamily.OFFICE_OOXML, FormatFamily.OFFICE_OOXML_MACRO):
            spec = declared
        features["variant_source"] = "declared extension (encrypted payload is opaque)"
        return spec, "office.encrypted", evidence, features

    if any(n.lower().startswith("worddocument") for n in names):
        flags = _fib_flags(_stream("worddocument") or b"") or 0
        features["fib_flags"] = flags
        features["fencrypted"] = bool(flags & 0x0100)
        features["fdot"] = bool(flags & 0x0001)
        if flags & 0x0100:
            features["encrypted"] = True
            features["encryption_evidence"] = "Word FIB fEncrypted flag"
        template = bool(flags & 0x0001)
        if template:
            evidence.append("FIB fDot flag set (template)")
            spec = lookup_format("ole.doc.template")
        else:
            evidence.append("WordDocument stream present")
            spec = lookup_format("ole.doc.macro" if features["macros_present"] else "ole.doc")
        return spec, None, evidence, features

    if any(n.lower() in ("workbook", "book") for n in names):
        dt = _biff_document_type(_stream("workbook", 1 << 16) or _stream("book", 1 << 16) or b"")
        features["biff_document_type"] = dt
        template = dt == 0x20
        if template:
            evidence.append("BIFF BOF dt=0x20 (template)")
            spec = lookup_format("ole.xls.template")
        else:
            evidence.append("Workbook stream present")
            spec = lookup_format("ole.xls.macro" if features["macros_present"] else "ole.xls")
        return spec, None, evidence, features

    if any("powerpoint document" in n.lower() for n in names):
        evidence.append("PowerPoint Document stream present")
        # PowerPoint 97-2003 stores the presentation/slideshow/template variant
        # in the document record; the stream names are identical for all three.
        # Use the declared extension when it names one of the family (and say so
        # in the record) rather than asserting a variant that was not proven.
        variant = {".pot": "ole.ppt.template", ".pps": "ole.ppt.slideshow"}.get(
            declared_extension, "ole.ppt")
        features["variant_source"] = (
            f"declared extension {declared_extension}" if declared_extension in (".pot", ".pps")
            else "default (variant not encoded in stream names)"
        )
        return lookup_format(variant), None, evidence, features

    if any(n.lower().startswith("__substg1.0_") or "properties_version1.0" in n.lower()
           for n in names):
        evidence.append("MAPI property streams (__substg1.0_*) present")
        # Every MAPI property stream counts, both the fixed-size property
        # blocks (__properties_version1.0) and the variable-length ones
        # (__substg1.0_XXXXYYYY); a message is described by both.
        features["mapi_streams"] = sum(
            1 for n in lowered
            if n.startswith("__substg1.0_") or n.startswith("__properties_version1.0"))
        return lookup_format("ole.msg"), None, evidence, features

    evidence.append("no recognised Office/MAPI storage")
    return lookup_format("ole.unknown"), None, evidence, features


_PDF_FEATURE_PATTERNS: Tuple[Tuple[str, bytes], ...] = (
    ("encrypted", b"/Encrypt"),
    ("javascript", b"/JavaScript"),
    ("javascript", b"/JS"),
    ("embedded_files", b"/EmbeddedFile"),
    ("xfa", b"/XFA"),
    ("acroform", b"/AcroForm"),
    ("linearized", b"/Linearized"),
    ("object_streams", b"/ObjStm"),
    ("signature", b"/ByteRange"),
    ("outline", b"/Outlines"),
    ("named_destinations", b"/Dests"),
)


def _pdf_detection(data: bytes, features: Dict[str, Any]) -> Tuple[FormatSpec, List[str], Dict[str, Any], Optional[str]]:
    evidence: List[str] = ["%PDF header"]
    version = None
    match = re.match(rb"%PDF-(\d\.\d)", data[:16])
    if match:
        version = "PDF-" + match.group(1).decode("ascii")
        evidence.append(f"declared version {version}")
    tail = data[-2048:] if len(data) > 2048 else data
    scan = data[: SNIFF_HEADER_SIZE] + b"\n" + tail
    for name, marker in _PDF_FEATURE_PATTERNS:
        if marker in scan:
            features[name] = True
    features["incremental_updates"] = max(0, data.count(b"%%EOF") - 1)
    features["trailing_eof"] = data.rstrip().endswith(b"%%EOF")
    if features.get("encrypted"):
        evidence.append("/Encrypt dictionary present (payload is encrypted)")
    for key in ("javascript", "embedded_files", "xfa", "signature"):
        if features.get(key):
            evidence.append(f"/{key} present")
    return lookup_format("pdf"), evidence, features, version


_EMAIL_HEADER_RE = re.compile(rb"^(Received|Return-Path|From|Date|Message-ID|"
                              rb"MIME-Version|Content-Type|Subject|To):", re.IGNORECASE | re.MULTILINE)


def _email_or_archive_detection(extension: str, data: bytes,
                                features: Dict[str, Any]) -> Tuple[Optional[FormatSpec], List[str]]:
    """Refine text/binary sniffing for email and stream containers."""
    if extension in (".eml", ".emlx"):
        return lookup_format("email.eml"), ["declared RFC 822 message"]
    if data.startswith(b"From ") and b"\nReceived:" in data[:4096]:
        return lookup_format("email.mbox"), ["mbox 'From ' separator"]
    header_hits = _EMAIL_HEADER_RE.findall(data[:4096])
    if len(header_hits) >= 3:
        features["email_headers"] = [h.decode("ascii", "replace") for h in header_hits[:16]]
        if extension == ".mbox":
            return lookup_format("email.mbox"), ["RFC 822 headers in an .mbox container"]
        return lookup_format("email.eml"), [
            f"{len(header_hits)} RFC 822 header fields at the start of the file"]
    return None, []


def _spec_can_hold_text(spec) -> bool:
    """Whether a format can legitimately contain plain text.

    A ``.png`` whose bytes are text is not a PNG with unusual content: the
    name is wrong and the content decides - the same rule the signature table
    applies in the other direction. The catalogue's own MIME statement is what
    this asks, so a text-capable image format (``image/svg+xml``) is still
    honoured while ``image/png`` is not.
    """
    mime = (getattr(spec, "mime", "") or "").lower()
    if mime.startswith("text/"):
        return True
    if mime.endswith("+xml") or mime.endswith("+json"):
        return True
    # draw.io's registered type has no +xml suffix, yet the plain form of a
    # .drawio IS an XML text document (the ZIP form is identified separately).
    return mime in ("application/json", "application/xml", "application/x-yaml",
                    "application/vnd.jgraph.mxfile")


def _apply_declared_extension_disambiguation(extension: str, declared_extension: str,
                                             features: Dict[str, Any]) -> str:
    """Handle formats whose content signature cannot separate their variants.

    PST and OST share the ``!BDN`` client signature, and a legacy .doc/.xls is
    the same OLE container whether or not it carries macros. Where the *content*
    cannot decide, the declared extension is used - and the record states that
    it was used, so the evidence chain stays explicit.
    """
    if extension == ".pst" and declared_extension in (".pst", ".ost"):
        features["store_variant_source"] = (
            "declared extension (PST and OST share the !BDN signature)")
        features["store_signature"] = "!BDN"
        return declared_extension
    return extension


def identify_bytes(data: Union[bytes, bytearray, memoryview],
                   declared_name: Optional[str] = None,
                   path: Optional[str] = None,
                   deep: bool = True) -> DetectionResult:
    """Identify a byte string, with content evidence taking precedence.

    Args:
        data: Leading bytes of the artifact (a header is enough; containers are
            opened from ``path`` when available).
        declared_name: The filename as discovered. Preserved as evidence.
        path: Filesystem path, when the artifact exists on disk. Enables
            central-directory and OLE stream inspection.
        deep: When False, only the signature table is consulted (no container
            parsing). Used in hot loops where the container was already parsed.

    Returns:
        A :class:`DetectionResult`. Unreadable input yields ``binary.unknown``
        with an ``unreadable`` discrepancy rather than an exception.
    """
    if isinstance(data, (bytearray, memoryview)):
        data = bytes(data)
    declared_name, declared_extension = _declared(declared_name, path)
    features: Dict[str, Any] = {}
    evidence: List[str] = []
    version: Optional[str] = None
    unproven: Optional[str] = None

    if not data:
        result = _result_for(lookup_format("binary.unknown"), declared_name=declared_name,
                             declared_extension=declared_extension,
                             confidence=CONFIDENCE_NONE, evidence=["no readable bytes"])
        result.discrepancies.append(Discrepancy(
            DISC_UNREADABLE, declared_extension or "", "unknown", SEVERITY_HIGH,
            "The file could not be read (empty, locked or zero-length): "
            "identification is impossible, so the artifact is recorded as unknown "
            "rather than assumed from its name."))
        _finish(result)
        return result

    sniffed_extension, confidence = detect_file_type_with_confidence(data, path)
    evidence.append(f"content signature -> {sniffed_extension} ({confidence})")
    sniffed_extension = _apply_declared_extension_disambiguation(
        sniffed_extension, declared_extension, features)

    # -------------------------------------------------------------- ZIP
    # A ZIP-based package is opened whenever the bytes are a ZIP, even if the
    # signature sniffer already named a package type from its first 4 KiB: only
    # the container knows whether it is a plain document, a macro-enabled twin,
    # a template or a slideshow, and the macro distinction is forensic.
    zip_like = data[:4] in _ZIP_MARKERS or sniffed_extension in _ZIP_PACKAGE_EXTENSIONS
    if zip_like and deep:
        names, zf, _buffer = _zip_entries(path, data)
        try:
            if names:
                features.update(_zip_features(zf, names, path))
                spec, unproven, zip_evidence = _zip_detection(names, zf, features)
                evidence.extend(zip_evidence)
                result = _result_for(
                    spec, declared_name=declared_name, declared_extension=declared_extension,
                    confidence=CONFIDENCE_STRONG, evidence=evidence, features=features,
                    version=version)
                if unproven:
                    result.format_id = unproven if unproven != _UNPROVEN else spec.format_id
                    result.description = "OOXML-family package with an unresolvable application area"
                    result.discrepancies.append(Discrepancy(
                        DISC_UNSUPPORTED, declared_extension or "", "zip package",
                        SEVERITY_NOTABLE,
                        "The ZIP container looks like an Office package but its "
                        "application area could not be resolved; it is recorded as "
                        "a ZIP archive so its entries are still inventoried."))
                _finish(result)
                return result
            features["central_directory_readable"] = False
            if sniffed_extension == ".zip":
                features["container"] = "zip"
                result = _result_for(lookup_format("archive.zip"), declared_name=declared_name,
                                     declared_extension=declared_extension,
                                     confidence=CONFIDENCE_WEAK, evidence=evidence,
                                     features=features)
                result.discrepancies.append(Discrepancy(
                    DISC_UNSUPPORTED, declared_extension or "", ".zip", SEVERITY_NOTABLE,
                    "ZIP signature matched but the central directory is unreadable "
                    "(truncated, split or corrupt archive); the archive reader will "
                    "attempt a local-header scan and record per-entry failures."))
                _finish(result)
                return result
            # A package extension without a readable container: report what the
            # signature said (weakly) rather than claiming an archive that was
            # never proven. The reader will resolve it or fail visibly.
            evidence.append("package container could not be enumerated "
                            "(header-only sniff or damaged central directory)")
        finally:
            if zf is not None:
                try:
                    zf.close()
                except Exception:
                    pass

    # -------------------------------------------------------------- OLE
    # Any compound file is opened by its streams. The signature sniffer names a
    # legacy Office document from the presence of one stream name
    # (``WordDocument`` etc.), which cannot tell a document from a template,
    # cannot see an encrypted FIB, and cannot see a VBA project at all. Those
    # distinctions are exactly what the legacy branch adds, so it runs whenever
    # the bytes are a compound file - not only when the sniffer said ``.ole``.
    is_compound_file = data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    if is_compound_file and deep:
        spec, override_id, ole_evidence, ole_features = _ole_detection(
            path, data, declared_extension, features)
        evidence.extend(ole_evidence)
        features.update(ole_features)
        result = _result_for(spec, declared_name=declared_name,
                             declared_extension=declared_extension,
                             confidence=CONFIDENCE_STRONG, evidence=evidence,
                             features=features, version=version)
        if override_id:
            # Encrypted packages have no readable application area; the format
            # id names the container and the declared extension names the family.
            result.format_id = override_id
            result.description = "Encrypted Office document (CFB container)"
            result.extension = spec.extension or result.extension
        _finish(result)
        return result

    # -------------------------------------------------------------- PDF
    if sniffed_extension == ".pdf":
        spec, pdf_evidence, features, version = _pdf_detection(data, features)
        evidence.extend(pdf_evidence)
        return _finish(_result_for(spec, declared_name=declared_name,
                                   declared_extension=declared_extension,
                                   confidence=CONFIDENCE_STRONG, evidence=evidence,
                                   features=features, version=version))

    # ------------------------------------------------------------ email
    if sniffed_extension in (".pst", ".ost"):
        spec = lookup_format("email.ost" if sniffed_extension == ".ost" else "email.pst")
        features["container"] = "pst-store"
        return _finish(_result_for(spec, declared_name=declared_name,
                                   declared_extension=declared_extension,
                                   confidence=CONFIDENCE_STRONG, evidence=evidence,
                                   features=features))
    if sniffed_extension in (".eml", ".mbox"):
        spec = lookup_format("email.eml" if sniffed_extension == ".eml" else "email.mbox")
        return _finish(_result_for(spec, declared_name=declared_name,
                                   declared_extension=declared_extension,
                                   confidence=CONFIDENCE_STRONG, evidence=evidence,
                                   features=features))
    if sniffed_extension == ".bin":
        spec, email_evidence = _email_or_archive_detection(
            declared_extension, data, features)
        if spec is not None:
            evidence.extend(email_evidence)
            confidence = CONFIDENCE_WEAK
            return _finish(_result_for(spec, declared_name=declared_name,
                                       declared_extension=declared_extension,
                                       confidence=confidence, evidence=evidence,
                                       features=features))

    # --------------------------------------------------- plain text / scripts
    if sniffed_extension == ".bin":
        text_score = _text_likeness(data)
        if text_score is not None:
            features["text_likeness"] = round(text_score, 3)
            features["line_count_sampled"] = data[: SNIFF_HEADER_SIZE].count(b"\n") + 1
            evidence.append(f"textual content (likeness {text_score:.2f})")
            declared_spec = lookup_by_extension(declared_extension)
            if declared_spec is not None and not _spec_can_hold_text(declared_spec):
                # The name says a format that cannot hold these bytes (a text
                # file called .png, for instance). Keeping the declared type
                # sent the file to the image reader, which failed with "cannot
                # identify image file" - the content is what decides.
                features["declared_type_rejected_for_content"] = declared_spec.format_id
                evidence.append(
                    f"declared extension names {declared_spec.description or declared_spec.format_id}"
                    f" ({declared_spec.mime}), which cannot hold text content"
                )
            spec = declared_spec or lookup_format("text.plain")
            # A declared type that can hold text is kept: it is more specific
            # than "plain text" and it is what the artifact claims to be. One
            # that cannot hold text (image/png, application/zip, a video) is
            # not, however loudly it is declared - the bytes are text, so the
            # content decides and the mismatch is recorded below.
            if (not _spec_can_hold_text(spec)
                    or spec.family in (FormatFamily.BINARY, FormatFamily.UNKNOWN)):
                spec = lookup_format("text.plain")
            return _finish(_result_for(spec, declared_name=declared_name,
                                       declared_extension=declared_extension,
                                       confidence=CONFIDENCE_WEAK, evidence=evidence,
                                       features=features))

    # --------------------------------------------------------- everything else
    spec = _spec_or_binary(sniffed_extension)
    if spec.extension and spec.extension != sniffed_extension:
        features["reported_extension"] = sniffed_extension
    result = _result_for(spec, declared_name=declared_name,
                         declared_extension=declared_extension,
                         extension=sniffed_extension if sniffed_extension != ".bin" else None,
                         confidence=confidence, evidence=evidence, features=features,
                         version=version)
    return _finish(result)


def identify(path: str, declared_name: Optional[str] = None, deep: bool = True,
             size: int = SNIFF_HEADER_SIZE) -> DetectionResult:
    """Identify a file on disk from its content.

    Args:
        path: Path to the artifact.
        declared_name: Overrides the filename used as declared evidence (used
            when an embedded artifact's original name differs from the
            temporary file it was materialised as).
        deep: Passed to :func:`identify_bytes`.
        size: Header window to read.

    Returns:
        A :class:`DetectionResult`; the declared name defaults to the file's
        basename so nothing about the original naming is lost.
    """
    header = read_file_header(path, size)
    result = identify_bytes(header, declared_name=declared_name or os.path.basename(path),
                            path=path, deep=deep)
    try:
        result.features.setdefault("size_bytes", os.path.getsize(path))
    except Exception:
        pass
    return result


#: Control bytes that never appear in ordinary text (tab, newline, form feed
#: and carriage return excluded).  DEL counts too.  These are the same signal
#: the discrepancy analysis calls "NUL/control bytes with no textual
#: structure".
_NON_TEXT_CONTROL_BYTES = frozenset(
    byte for byte in range(0x20) if byte not in (0x09, 0x0A, 0x0C, 0x0D)
) | {0x7F}

#: Share of control bytes above which a sample is not treated as text at all.
_CONTROL_BYTE_TOLERANCE = 0.02

#: Minimum text-likeness for the weak text detection to fire.
_TEXT_LIKENESS_THRESHOLD = 0.85


def _text_likeness(data: bytes) -> Optional[float]:
    """Score how much a byte string looks like text (``None`` if binary).

    A NUL byte is the decisive tell for a binary payload, and *other* control
    bytes count against a payload rather than being ignored: text that is worth
    reading as text does not carry ``0x01``-``0x08`` runs. Ignoring them made a
    payload like ``b"\x01\x02\x03\x04 bytes with no known magic"`` score 0.87
    and get promoted to ``text/plain``, so an unidentifiable binary file was
    stored as a successfully processed text document instead of being recorded
    as an unsupported type - the "degraded extraction reported as success" case
    this pipeline has to avoid, and a change in stored classification rather
    than in capability.

    The score still weighs printable ASCII, whitespace and UTF-8-multi-byte
    structure, so accented or non-Latin text is unaffected.
    """
    sample = data[: SNIFF_HEADER_SIZE]
    if not sample:
        return None
    if b"\x00" in sample:
        return None
    control = sum(1 for b in sample if b in _NON_TEXT_CONTROL_BYTES)
    if control and (control / len(sample)) > _CONTROL_BYTE_TOLERANCE:
        # More than an accidental stray control byte: no textual structure to
        # parse, whatever the extension claims.
        return None
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    high = sum(1 for b in sample if b >= 128)
    # Non-ASCII bytes are text when they form valid UTF-8 (an accented or
    # non-Latin document), and ambiguous when they do not (a legacy codepage, or
    # a binary payload that happens to be high-bit). The previous fixed 0.5
    # weight rejected perfectly ordinary ``.txt`` content such as
    # "café naïve résumé": the bytes are text, but the score said otherwise.
    high_weight = 1.0 if _decodes_as_utf8(sample) else 0.5
    score = (printable + high * high_weight) / len(sample)
    if high and score < 0.6:
        return None
    return score if score >= _TEXT_LIKENESS_THRESHOLD else None


def _decodes_as_utf8(sample: bytes) -> bool:
    """Whether ``sample`` is valid UTF-8, tolerating a truncated final char.

    The sample is a fixed-size prefix, so it can end mid-sequence; an
    incremental decoder with ``final=False`` treats that tail as incomplete
    rather than invalid.
    """
    try:
        import codecs

        codecs.getincrementaldecoder("utf-8")(errors="strict").decode(sample, final=False)
        return True
    except Exception:
        return False


def _finish(result: DetectionResult) -> DetectionResult:
    """Attach the declared-vs-detected discrepancy analysis."""
    _add_discrepancies(result)
    if result.reader is None:
        result.reader = READER_BY_FAMILY.get(result.family)
    return result


def _add_discrepancies(result: DetectionResult) -> None:
    declared = result.declared_extension
    if not declared:
        result.discrepancies.append(Discrepancy(
            DISC_EXTENSION_ABSENT, "", result.extension or result.format_id, SEVERITY_INFO,
            "The artifact has no filename extension; the format was identified "
            "entirely from its content."))
    else:
        declared_spec = lookup_by_extension(declared)
        if declared_spec is None:
            result.discrepancies.append(Discrepancy(
                DISC_EXTENSION_UNKNOWN, declared, result.extension or result.format_id,
                SEVERITY_NOTABLE,
                f"Extension {declared} is not in the format catalogue; the content "
                f"was used to select the reader."))
        elif declared_spec.format_id != result.format_id:
            if canonical_extension(declared) == result.extension:
                result.discrepancies.append(Discrepancy(
                    DISC_ALIAS, declared, result.extension, SEVERITY_INFO,
                    f"{declared} is an accepted spelling of {result.extension}; "
                    f"the format is the same."))
            elif (declared_spec.family is result.family
                  or {declared_spec.family, result.family} <= {
                      FormatFamily.OFFICE_OOXML, FormatFamily.OFFICE_OOXML_MACRO,
                      FormatFamily.OFFICE_LEGACY, FormatFamily.OFFICE_ODF}):
                severity = SEVERITY_HIGH if (
                    declared_spec.macro_capable != _result_macro_capable(result)) else SEVERITY_NOTABLE
                result.discrepancies.append(Discrepancy(
                    DISC_VARIANT_MISMATCH, declared, result.extension or result.format_id,
                    severity,
                    f"The file is named {declared} ({declared_spec.description}) but its "
                    f"content is {result.description or result.format_id}. "
                    f"Content decides the reader; the declared name is retained."))
            elif (declared_spec.family in (FormatFamily.TEXT, FormatFamily.DATA)
                  and result.family in (FormatFamily.BINARY, FormatFamily.UNKNOWN)):
                result.discrepancies.append(Discrepancy(
                    DISC_BINARY_TEXT, declared, result.extension or result.format_id,
                    SEVERITY_HIGH,
                    f"The file is named {declared} (text) but its bytes are binary "
                    f"(NUL/control bytes with no textual structure). It is recorded "
                    f"as binary so no text parser is applied to it."))
            else:
                result.discrepancies.append(Discrepancy(
                    DISC_FORMAT_MISMATCH, declared, result.extension or result.format_id,
                    SEVERITY_HIGH,
                    f"The file is named {declared} but its content is "
                    f"{result.description or result.format_id}. Content decides the "
                    f"reader; the declared name is retained."))

    if result.encrypted:
        result.discrepancies.append(Discrepancy(
            DISC_ENCRYPTED, declared or "", result.extension or result.format_id,
            SEVERITY_NOTABLE,
            "The payload is encrypted; only container-level metadata can be "
            "extracted unless a key is supplied. The artifact is recorded as "
            "encrypted rather than failed."))

    if result.features.get("nested_documents"):
        result.discrepancies.append(Discrepancy(
            DISC_CONTAINER_SUBTYPE, declared or "", result.extension or result.format_id,
            SEVERITY_INFO,
            f"The container holds {result.features['nested_documents']} document "
            f"member(s) that must be extracted recursively."))


def _result_macro_capable(result: DetectionResult) -> bool:
    if result.macro_present:
        return True
    spec = lookup_format(result.format_id)
    return bool(spec and spec.macro_capable)


def digest_of(data: bytes, algorithm: str = "sha256") -> str:
    """Hex digest of a byte string (helper for tests and records)."""
    return hashlib.new(algorithm, data).hexdigest()
