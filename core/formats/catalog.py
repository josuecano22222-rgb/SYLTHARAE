"""The known-format catalogue.

A single declarative table of the formats this pipeline can identify *and*
process. The rule that keeps the pipeline honest: a format appears here only
when a reader in :mod:`reader_file.readers` actually handles it. Identification
and processing cannot drift apart, because both sides are looked up from the
same entry.

The catalogue deliberately separates:

* **format identity** - ``format_id``, the canonical extension and the MIME
  type. ``.docx`` and ``.docm`` are different formats, not aliases: one can
  carry executable macro content and one cannot, and a forensic record that
  conflates them loses that fact.
* **family** - the coarse grouping the router dispatches on (OOXML, legacy OLE,
  ODF, PDF, email, archive, ...). Readers are selected by family.
* **declared spellings** - every extension that legitimately names the format.
  A ``.jpeg`` file is a JPEG; reporting it as a contradiction with ``.jpg`` is
  noise, and noise is what makes real contradictions invisible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Optional, Tuple


class FormatFamily(str, Enum):
    """Coarse format grouping; this is what the router dispatches on."""

    OFFICE_LEGACY = "office-legacy"
    """OLE Compound File based Office (Word/Excel/PowerPoint 97-2003)."""

    OFFICE_OOXML = "office-ooxml"
    """ECMA-376 / ISO-29500 packages whose parts are XML."""

    OFFICE_OOXML_MACRO = "office-ooxml-macro"
    """OOXML packages carrying a VBA project - the macro-enabled variants."""

    OFFICE_ODF = "office-odf"
    """OpenDocument packages (.odt/.ods/.odp and their template variants)."""

    OFFICE_OTHER = "office-other"
    """RTF, CSV and other flat office interchange formats."""

    PDF = "pdf"
    EMAIL = "email"
    EMAIL_STORE = "email-store"
    ARCHIVE = "archive"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DATABASE = "database"
    EBOOK = "ebook"
    DIAGRAM = "diagram"
    """draw.io / diagrams.net documents (plain mxfile XML or its ZIP form)."""

    TEXT = "text"
    DATA = "data"
    EXECUTABLE = "executable"
    BINARY = "binary"
    UNKNOWN = "unknown"


#: Family -> the reader module responsible for it. The router uses this instead
#: of an extension if/elif chain, so adding a format to the catalogue is enough
#: for it to be processed.
READER_BY_FAMILY: Dict[FormatFamily, str] = {
    FormatFamily.OFFICE_LEGACY: "read_office",
    FormatFamily.OFFICE_OOXML: "read_office",
    FormatFamily.OFFICE_OOXML_MACRO: "read_office",
    FormatFamily.OFFICE_ODF: "read_office",
    FormatFamily.OFFICE_OTHER: "read_office",
    FormatFamily.PDF: "read_pdf",
    FormatFamily.EMAIL: "read_email",
    FormatFamily.EMAIL_STORE: "read_email",
    FormatFamily.ARCHIVE: "read_archive",
    FormatFamily.IMAGE: "read_img_fast",
    FormatFamily.AUDIO: "read_audio",
    FormatFamily.VIDEO: "read_video",
    FormatFamily.DATABASE: "read_database",
    FormatFamily.EBOOK: "read_ebook",
    FormatFamily.DIAGRAM: "read_diagram",
    FormatFamily.TEXT: "read_remaining",
    FormatFamily.DATA: "read_remaining",
    FormatFamily.EXECUTABLE: "read_remaining",
    FormatFamily.BINARY: "read_remaining",
    FormatFamily.UNKNOWN: "read_remaining",
}


@dataclass(frozen=True)
class FormatSpec:
    """One format the pipeline can identify and process.

    Attributes:
        format_id: Stable identifier used in records and reports.
        family: Coarse grouping; selects the reader.
        extension: Canonical extension (what ``detected_format`` reports).
        extensions: Every spelling that legitimately names this format *and*
            that the owning reader accepts.
        aliases: Accepted spellings that the router canonicalises before a
            reader sees them (``.jpeg`` -> ``.jpg``). Kept separate so
            "the reader handles this extension" stays a checkable claim.
        mime: MIME type recorded with the artifact.
        description: Human-readable name, for reports.
        macro_capable: Whether the format can carry executable macro code.
        parse_notes: What the reader guarantees to extract, for auditability.
        identified_only: True when the pipeline identifies the format but has
            no parser for its payload. Such an artifact is recorded with the
            explicit ``unsupported`` outcome and this reason - never skipped,
            and never handed to a reader that would mis-parse it.
        identified_only_reason: Why no parser exists (required when the flag is
            set; the catalogue test enforces it).
    """

    format_id: str
    family: FormatFamily
    extension: str
    extensions: FrozenSet[str]
    mime: str
    description: str
    aliases: FrozenSet[str] = field(default=frozenset())
    macro_capable: bool = False
    parse_notes: Tuple[str, ...] = field(default=())
    identified_only: bool = False
    identified_only_reason: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "format_id": self.format_id,
            "family": self.family.value,
            "extension": self.extension,
            "extensions": sorted(self.extensions),
            "aliases": sorted(self.aliases),
            "mime": self.mime,
            "description": self.description,
            "macro_capable": self.macro_capable,
            "reader": READER_BY_FAMILY.get(self.family),
            "identified_only": self.identified_only,
            "identified_only_reason": self.identified_only_reason,
        }


def _spec(format_id: str, family: FormatFamily, extension: str, mime: str,
          description: str, extensions: Tuple[str, ...] = (), *,
          macro_capable: bool = False,
          parse_notes: Tuple[str, ...] = (),
          identified_only: bool = False,
          identified_only_reason: str = "",
          aliases: Tuple[str, ...] = ()) -> FormatSpec:
    declared = frozenset({extension} | set(extensions)) if extension else frozenset(extensions)
    return FormatSpec(format_id=format_id, family=family, extension=extension,
                      extensions=declared, mime=mime, description=description,
                      aliases=frozenset(aliases),
                      macro_capable=macro_capable, parse_notes=parse_notes,
                      identified_only=identified_only,
                      identified_only_reason=identified_only_reason)


#: Reason recorded for formats the pipeline identifies but cannot parse. Every
#: entry is a deliberate decision with a stated boundary, not an oversight:
#: the artifact keeps its identity, its MIME type and its discrepancies, and
#: is recorded as UNSUPPORTED with this text.
IDENTIFIED_ONLY_REASONS = {
    "no_parser": "Identified from its signature; no parser for this payload is "
                 "implemented, so the artifact is recorded UNSUPPORTED with its "
                 "identity rather than processed as though it were text.",
    "needs_external_tool": "Identified from its signature; parsing requires an "
                           "external tool that is not part of this installation.",
}


_SPECS: Tuple[FormatSpec, ...] = (
    # ---------------------------------------------------------------- OOXML
    _spec(
        "ooxml.doc", FormatFamily.OFFICE_OOXML, ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Word document (OOXML)",
        parse_notes=("text", "tables", "properties", "revisions", "comments",
                     "hyperlinks", "embedded media", "headers/footers", "fields"),
    ),
    _spec(
        "ooxml.doc.template", FormatFamily.OFFICE_OOXML, ".dotx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "Word template (OOXML)",
    ),
    _spec(
        "ooxml.xls", FormatFamily.OFFICE_OOXML, ".xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Excel workbook (OOXML)",
        parse_notes=("cells", "formulas", "sheet state", "defined names",
                     "properties", "embedded media"),
    ),
    _spec(
        "ooxml.xls.template", FormatFamily.OFFICE_OOXML, ".xltx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
        "Excel template (OOXML)",
    ),
    _spec(
        "ooxml.xls.binary", FormatFamily.OFFICE_OOXML, ".xlsb",
        "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
        "Excel binary workbook (OOXML container, BIFF12 parts)",
    ),
    _spec(
        "ooxml.ppt", FormatFamily.OFFICE_OOXML, ".pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "PowerPoint presentation (OOXML)",
    ),
    _spec(
        "ooxml.ppt.template", FormatFamily.OFFICE_OOXML, ".potx",
        "application/vnd.openxmlformats-officedocument.presentationml.template",
        "PowerPoint template (OOXML)",
    ),
    _spec(
        "ooxml.ppt.slideshow", FormatFamily.OFFICE_OOXML, ".ppsx",
        "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
        "PowerPoint slideshow (OOXML)",
    ),
    # ------------------------------------------------- macro-enabled OOXML
    _spec(
        "ooxml.doc.macro", FormatFamily.OFFICE_OOXML_MACRO, ".docm",
        "application/vnd.ms-word.document.macroEnabled.12",
        "Word macro-enabled document",
        macro_capable=True,
        parse_notes=("VBA modules", "macro metadata", "all ooxml.doc content"),
    ),
    _spec(
        "ooxml.doc.template.macro", FormatFamily.OFFICE_OOXML_MACRO, ".dotm",
        "application/vnd.ms-word.template.macroEnabled.12",
        "Word macro-enabled template",
        macro_capable=True,
    ),
    _spec(
        "ooxml.xls.macro", FormatFamily.OFFICE_OOXML_MACRO, ".xlsm",
        "application/vnd.ms-excel.sheet.macroEnabled.12",
        "Excel macro-enabled workbook",
        macro_capable=True,
    ),
    _spec(
        "ooxml.xls.template.macro", FormatFamily.OFFICE_OOXML_MACRO, ".xltm",
        "application/vnd.ms-excel.template.macroEnabled.12",
        "Excel macro-enabled template",
        macro_capable=True,
    ),
    _spec(
        "ooxml.ppt.macro", FormatFamily.OFFICE_OOXML_MACRO, ".pptm",
        "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
        "PowerPoint macro-enabled presentation",
        macro_capable=True,
    ),
    _spec(
        "ooxml.ppt.template.macro", FormatFamily.OFFICE_OOXML_MACRO, ".potm",
        "application/vnd.ms-powerpoint.template.macroEnabled.12",
        "PowerPoint macro-enabled template",
        macro_capable=True,
    ),
    _spec(
        "ooxml.ppt.slideshow.macro", FormatFamily.OFFICE_OOXML_MACRO, ".ppsm",
        "application/vnd.ms-powerpoint.slideshow.macroEnabled.12",
        "PowerPoint macro-enabled slideshow",
        macro_capable=True,
    ),
    # ----------------------------------------------------- legacy OLE Office
    _spec("ole.doc", FormatFamily.OFFICE_LEGACY, ".doc",
          "application/msword", "Word 97-2003 document",
          parse_notes=("text", "properties", "embedded objects")),
    _spec("ole.doc.template", FormatFamily.OFFICE_LEGACY, ".dot",
          "application/msword", "Word 97-2003 template", (".dot",)),
    _spec("ole.doc.macro", FormatFamily.OFFICE_LEGACY, ".doc",
          "application/msword", "Word 97-2003 document with VBA project",
          macro_capable=True),
    _spec("ole.xls", FormatFamily.OFFICE_LEGACY, ".xls",
          "application/vnd.ms-excel", "Excel 97-2003 workbook",
          parse_notes=("cells", "formulas", "sheet state")),
    _spec("ole.xls.template", FormatFamily.OFFICE_LEGACY, ".xlt",
          "application/vnd.ms-excel", "Excel 97-2003 template"),
    _spec("ole.xls.macro", FormatFamily.OFFICE_LEGACY, ".xls",
          "application/vnd.ms-excel", "Excel 97-2003 workbook with VBA project",
          macro_capable=True),
    _spec("ole.ppt", FormatFamily.OFFICE_LEGACY, ".ppt",
          "application/vnd.ms-powerpoint", "PowerPoint 97-2003 presentation"),
    _spec("ole.ppt.template", FormatFamily.OFFICE_LEGACY, ".pot",
          "application/vnd.ms-powerpoint", "PowerPoint 97-2003 template"),
    _spec("ole.ppt.slideshow", FormatFamily.OFFICE_LEGACY, ".pps",
          "application/vnd.ms-powerpoint", "PowerPoint 97-2003 slideshow"),
    _spec("ole.msg", FormatFamily.EMAIL, ".msg",
          "application/vnd.ms-outlook", "Outlook message (OLE/MAPI)"),
    _spec("ole.unknown", FormatFamily.OFFICE_LEGACY, ".ole",
          "application/x-ole-storage", "OLE compound file (unrecognised)"),
    # ------------------------------------------------------ OpenDocument/ODF
    _spec("odf.text", FormatFamily.OFFICE_ODF, ".odt",
          "application/vnd.oasis.opendocument.text", "OpenDocument text"),
    _spec("odf.text.template", FormatFamily.OFFICE_ODF, ".ott",
          "application/vnd.oasis.opendocument.text-template",
          "OpenDocument text template"),
    _spec("odf.spreadsheet", FormatFamily.OFFICE_ODF, ".ods",
          "application/vnd.oasis.opendocument.spreadsheet",
          "OpenDocument spreadsheet"),
    _spec("odf.spreadsheet.template", FormatFamily.OFFICE_ODF, ".ots",
          "application/vnd.oasis.opendocument.spreadsheet-template",
          "OpenDocument spreadsheet template"),
    _spec("odf.presentation", FormatFamily.OFFICE_ODF, ".odp",
          "application/vnd.oasis.opendocument.presentation",
          "OpenDocument presentation"),
    _spec("odf.presentation.template", FormatFamily.OFFICE_ODF, ".otp",
          "application/vnd.oasis.opendocument.presentation-template",
          "OpenDocument presentation template"),
    _spec("odf.graphics", FormatFamily.OFFICE_ODF, ".odg",
          "application/vnd.oasis.opendocument.graphics",
          "OpenDocument graphics"),
    # --------------------------------------------------------- office other
    _spec("office.rtf", FormatFamily.OFFICE_OTHER, ".rtf",
          "application/rtf", "Rich Text Format"),
    _spec("office.csv", FormatFamily.OFFICE_OTHER, ".csv",
          "text/csv", "Comma-separated values", (".tsv",)),
    _spec("iwork.pages", FormatFamily.OFFICE_OTHER, ".pages",
          "application/vnd.apple.pages", "Apple Pages document"),
    _spec("iwork.numbers", FormatFamily.OFFICE_OTHER, ".numbers",
          "application/vnd.apple.numbers", "Apple Numbers spreadsheet"),
    # ------------------------------------------------------------------ PDF
    _spec("pdf", FormatFamily.PDF, ".pdf", "application/pdf",
          "Portable Document Format",
          parse_notes=("text layer classification", "annotations", "embedded files",
                       "JavaScript", "form fields", "incremental updates", "OCR")),
    # --------------------------------------------------------------- email
    _spec("email.eml", FormatFamily.EMAIL, ".eml", "message/rfc822",
          "RFC 822 / MIME message", (".emlx",),
          parse_notes=("headers", "bodies", "MIME parts", "attachments", "recursion")),
    _spec("email.msg", FormatFamily.EMAIL, ".msg", "application/vnd.ms-outlook",
          "Outlook message (OLE/MAPI)"),
    _spec("email.mbox", FormatFamily.EMAIL, ".mbox", "application/mbox",
          "Unix mbox mailbox", (".mbx",)),
    _spec("email.pst", FormatFamily.EMAIL_STORE, ".pst",
          "application/vnd.ms-outlook-pst", "Outlook personal store",
          parse_notes=("folders", "messages", "attachments", "MAPI properties")),
    _spec("email.ost", FormatFamily.EMAIL_STORE, ".ost",
          "application/vnd.ms-outlook-ost", "Outlook offline store"),
    # ------------------------------------------------------------- archives
    _spec("archive.zip", FormatFamily.ARCHIVE, ".zip", "application/zip",
          "ZIP archive",
          parse_notes=("entry inventory", "per-entry metadata", "nesting", "recursion")),
    _spec("archive.rar", FormatFamily.ARCHIVE, ".rar", "application/vnd.rar",
          "RAR archive"),
    _spec("archive.7z", FormatFamily.ARCHIVE, ".7z",
          "application/x-7z-compressed", "7-Zip archive"),
    _spec("archive.tar", FormatFamily.ARCHIVE, ".tar", "application/x-tar",
          "TAR archive"),
    _spec("archive.gz", FormatFamily.ARCHIVE, ".gz",
          "application/gzip", "GZIP stream", (".tgz",)),
    _spec("archive.bz2", FormatFamily.ARCHIVE, ".bz2",
          "application/x-bzip2", "BZIP2 stream", (".tbz2",)),
    _spec("archive.xz", FormatFamily.ARCHIVE, ".xz",
          "application/x-xz", "XZ stream", (".txz",)),
    _spec("archive.compress", FormatFamily.ARCHIVE, ".Z",
          "application/x-compress", "LZW compress (.Z)",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("archive.cab", FormatFamily.ARCHIVE, ".cab",
          "application/vnd.ms-cab-compressed", "Microsoft Cabinet",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("archive.iso", FormatFamily.ARCHIVE, ".iso",
          "application/x-iso9660-image", "ISO 9660 image",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    # --------------------------------------------------------------- images
    _spec("image.png", FormatFamily.IMAGE, ".png", "image/png", "PNG image"),
    _spec("image.jpeg", FormatFamily.IMAGE, ".jpg", "image/jpeg", "JPEG image",
          (".jpeg",), aliases=(".jfif",)),
    _spec("image.gif", FormatFamily.IMAGE, ".gif", "image/gif", "GIF image"),
    _spec("image.bmp", FormatFamily.IMAGE, ".bmp", "image/bmp", "BMP image"),
    _spec("image.tiff", FormatFamily.IMAGE, ".tiff", "image/tiff", "TIFF image",
          (".tif",)),
    _spec("image.webp", FormatFamily.IMAGE, ".webp", "image/webp", "WebP image"),
    _spec("image.svg", FormatFamily.IMAGE, ".svg", "image/svg+xml", "SVG image"),
    _spec("image.heic", FormatFamily.IMAGE, ".heic", "image/heic", "HEIC image",
          (".heif",)),
    _spec("image.ico", FormatFamily.IMAGE, ".ico", "image/x-icon", "Windows icon"),
    _spec("image.psd", FormatFamily.IMAGE, ".psd", "image/vnd.adobe.photoshop",
          "Photoshop document",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("image.dicom", FormatFamily.IMAGE, ".dcm", "application/dicom",
          "DICOM image",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("image.wmf", FormatFamily.IMAGE, ".wmf", "image/wmf", "Windows metafile",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    # ---------------------------------------------------------------- audio
    _spec("audio.mp3", FormatFamily.AUDIO, ".mp3", "audio/mpeg", "MPEG audio"),
    _spec("audio.wav", FormatFamily.AUDIO, ".wav", "audio/wav", "WAVE audio"),
    _spec("audio.ogg", FormatFamily.AUDIO, ".ogg", "audio/ogg", "Ogg audio"),
    _spec("audio.flac", FormatFamily.AUDIO, ".flac", "audio/flac", "FLAC audio"),
    _spec("audio.midi", FormatFamily.AUDIO, ".midi", "audio/midi", "MIDI sequence",
          (".mid",),
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    # ---------------------------------------------------------------- video
    _spec("video.mp4", FormatFamily.VIDEO, ".mp4", "video/mp4", "MPEG-4 video",
          (".m4v",)),
    _spec("audio.mp4", FormatFamily.AUDIO, ".m4a", "audio/mp4", "MPEG-4 audio",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS["no_parser"]),
    _spec("video.mkv", FormatFamily.VIDEO, ".mkv", "video/x-matroska",
          "Matroska video", (".webm",)),
    _spec("video.avi", FormatFamily.VIDEO, ".avi", "video/x-msvideo", "AVI video"),
    _spec("video.wmv", FormatFamily.VIDEO, ".wmv", "video/x-ms-wmv", "WMV video"),
    _spec("video.mpeg", FormatFamily.VIDEO, ".mpeg", "video/mpeg", "MPEG video",
          (".mpg",)),
    _spec("video.flv", FormatFamily.VIDEO, ".flv", "video/x-flv", "Flash video"),
    # ------------------------------------------------------------- database
    _spec("db.sqlite", FormatFamily.DATABASE, ".sqlite", "application/vnd.sqlite3",
          "SQLite database", (".sqlite3", ".db", ".db3", ".s3db", ".sdb")),
    _spec("db.mdb", FormatFamily.DATABASE, ".mdb",
          "application/x-msaccess", "Microsoft Access 97-2003 database",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['needs_external_tool']),
    _spec("db.accdb", FormatFamily.DATABASE, ".accdb",
          "application/x-msaccess", "Microsoft Access database",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['needs_external_tool']),
    _spec("db.registry", FormatFamily.DATABASE, ".hiv",
          "application/x-windows-registry", "Windows registry hive",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("db.evt", FormatFamily.DATABASE, ".evt",
          "application/x-ms-evtx", "Windows event log (legacy)",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("db.evtx", FormatFamily.DATABASE, ".evtx",
          "application/x-ms-evtx", "Windows event log",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("db.chm", FormatFamily.DATABASE, ".chm",
          "application/vnd.ms-htmlhelp", "Compiled HTML help",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("db.wim", FormatFamily.DATABASE, ".wim",
          "application/x-ms-wim", "Windows imaging format",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("db.vhd", FormatFamily.DATABASE, ".vhd", "application/x-vhd",
          "Virtual hard disk", (".vhdx", ".vmdk"),
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("db.one", FormatFamily.DATABASE, ".one",
          "application/onenote", "OneNote document",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("db.lnk", FormatFamily.DATA, ".lnk",
          "application/x-ms-shortcut", "Windows shortcut",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    # ---------------------------------------------------------------- ebook
    _spec("ebook.epub", FormatFamily.EBOOK, ".epub", "application/epub+zip",
          "EPUB book"),
    _spec("ebook.mobi", FormatFamily.EBOOK, ".mobi", "application/x-mobipocket-ebook",
          "Mobipocket book", (".azw", ".azw3")),
    _spec("ebook.fb2", FormatFamily.EBOOK, ".fb2", "application/x-fictionbook+xml",
          "FictionBook"),
    _spec("ebook.lit", FormatFamily.EBOOK, ".lit", "application/x-ms-reader",
          "Microsoft Reader book"),
    _spec("ebook.pdb", FormatFamily.EBOOK, ".pdb", "application/x-palm-database",
          "Palm database / e-book"),
    # -------------------------------------------------------------- diagram
    _spec("diagram.drawio", FormatFamily.DIAGRAM, ".drawio",
          "application/vnd.jgraph.mxfile", "draw.io / diagrams.net diagram",
          (".dio",)),
    # ----------------------------------------------------------------- text
    _spec("text.plain", FormatFamily.TEXT, ".txt", "text/plain", "Plain text",
          (".log", ".md", ".srt", ".vtt", ".ics", ".ini", ".cfg", ".conf",
           ".properties", ".toml", ".env")),
    _spec("text.xml", FormatFamily.DATA, ".xml", "application/xml", "XML document"),
    _spec("text.json", FormatFamily.DATA, ".json", "application/json", "JSON document"),
    _spec("text.yaml", FormatFamily.DATA, ".yaml", "application/yaml", "YAML document",
          (".yml",)),
    _spec("text.html", FormatFamily.TEXT, ".html", "text/html", "HTML document",
          (".htm",)),
    _spec("text.script", FormatFamily.TEXT, ".sh", "text/x-shellscript",
          "Script or source code",
          (".bash", ".bat", ".cmd", ".ps1", ".js", ".ts", ".py", ".java", ".c",
           ".cpp", ".cs", ".rb", ".go", ".rs", ".php", ".sql")),
    _spec("binary.exe", FormatFamily.EXECUTABLE, ".exe",
          "application/vnd.microsoft.portable-executable",
          "Windows PE executable", (".dll", ".sys", ".mach-o"),
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("binary.elf", FormatFamily.EXECUTABLE, ".elf",
          "application/x-executable", "ELF executable",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("binary.unknown", FormatFamily.BINARY, ".bin",
          "application/octet-stream", "Unrecognised binary",
          identified_only=True,
          identified_only_reason=IDENTIFIED_ONLY_REASONS['no_parser']),
    _spec("unknown", FormatFamily.UNKNOWN, "", "application/octet-stream",
          "Unidentified file"),
)


#: format_id -> FormatSpec
CATALOG: Dict[str, FormatSpec] = {spec.format_id: spec for spec in _SPECS}

#: extension -> the spec that owns it. First writer wins, so the canonical
#: variants are listed before the ambiguous ones.
_BY_EXTENSION: Dict[str, FormatSpec] = {}

#: Spellings whose reader registration is *not* required because the router
#: canonicalises them before dispatch (``.jpeg`` -> ``.jpg``).
_ALIASES: Dict[str, str] = {}


def _register() -> None:
    # Keys are lower-cased: extensions arrive from filenames in any case
    # (``.Z`` for LZW compress is the canonical spelling, ``.z`` the common
    # one) and a lookup must never depend on which was used.
    for spec in _SPECS:
        for extension in spec.extensions:
            _BY_EXTENSION.setdefault(extension.lower(), spec)
        for alias in spec.aliases:
            _BY_EXTENSION.setdefault(alias.lower(), spec)
            _ALIASES[alias.lower()] = spec.extension


_register()

#: Extensions that resolve to more than one format (resolved by content).
AMBIGUOUS_EXTENSIONS: Dict[str, Tuple[str, ...]] = {
    ".doc": ("ole.doc", "ole.doc.macro"),
    ".xls": ("ole.xls", "ole.xls.macro"),
    ".msg": ("ole.msg", "email.msg"),
    ".dot": ("ole.doc.template",),
    ".ppt": ("ole.ppt",),
}


def lookup_format(format_id: str) -> Optional[FormatSpec]:
    """Return the spec for ``format_id``, or ``None`` when unknown."""
    return CATALOG.get(format_id)


def lookup_by_extension(extension: str) -> Optional[FormatSpec]:
    """Return the canonical spec for a declared extension (``.docx``)."""
    if not extension:
        return None
    extension = extension.lower()
    if not extension.startswith("."):
        extension = "." + extension
    return _BY_EXTENSION.get(extension)


def canonical_extension(extension: str) -> str:
    """Resolve a declared extension to the canonical spelling.

    ``.jpeg`` -> ``.jpg``, ``.tgz`` -> ``.gz``. Unknown extensions are returned
    unchanged (lower-cased) so a caller never loses what the file declared.
    """
    spec = lookup_by_extension(extension)
    if spec is None:
        return (extension or "").lower()
    return spec.extension


def catalogue_extensions() -> Dict[str, str]:
    """``extension -> format_id`` for every known spelling (aliases included)."""
    return {extension: spec.format_id for extension, spec in sorted(_BY_EXTENSION.items())}


def reader_extensions() -> Dict[str, str]:
    """``extension -> format_id`` for spellings a reader must actually accept.

    Formats marked ``identified_only`` are excluded: the pipeline identifies
    them and records them as unsupported, and claiming a reader for them would
    be exactly the drift this function exists to detect.
    """
    return {extension.lower(): spec.format_id for spec in _SPECS
            if not spec.identified_only
            for extension in sorted(spec.extensions)}


def alias_extensions() -> Dict[str, str]:
    """``alias -> canonical extension`` for spellings resolved before dispatch."""
    return dict(_ALIASES)


def formats_for_family(family: FormatFamily) -> Tuple[FormatSpec, ...]:
    """Every format in ``family`` (used by reports and capability listings)."""
    return tuple(spec for spec in _SPECS if spec.family is family)
