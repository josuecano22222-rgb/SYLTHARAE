"""
Office file reader - Extract content from office documents
Aligned with database design principles - all functions within class
Supports: Word, Excel, PowerPoint, OpenDocument formats
"""

import os
import re
import tempfile
import logging
from typing import Dict, Any, List, Optional, Set

from core.file_utils import (
    OPENPYXL_PATH_SUFFIXES, load_spreadsheet_workbook,
)

from .base_reader import BaseReader
from core.optional_imports import import_clean


def _odf(dotted: str):
    """An ``odf`` submodule, imported without leaving its directory on sys.path.

    ``odfpy`` appends ``site-packages/odf`` to ``sys.path`` from every one of its
    modules, so reading a document left a package directory on the path for the
    rest of the process. ``import_clean`` puts the path back; the module stays
    cached, so the next read costs nothing.
    """
    return import_clean(dotted)




logger = logging.getLogger(__name__)

#: Upper bound on retained CSV data rows. Materialising every row of a
#: multi-gigabyte CSV as Python lists costs several times the file size and
#: turns ingestion into a memory-exhaustion failure; rows past this point are
#: counted and reported as truncated instead.
MAX_CSV_ROWS = 200_000


def _docx_text_boxes(paragraph) -> List[str]:
    """Text of the text boxes anchored in one paragraph, one entry per box.

    A text box is not part of the paragraph's ``w:r``/``w:t`` stream: Word keeps
    it in a ``w:txbxContent`` subtree inside the drawing (DrawingML) or the
    ``v:textbox`` (VML) that anchors it. ``paragraph.text`` therefore never sees
    it, and a caption living in a box was stored as an empty paragraph.

    ``mc:AlternateContent`` carries the same box twice - once in ``mc:Choice``
    for new readers, once in ``mc:Fallback`` for old ones. Only the ``Choice``
    copy is taken, otherwise every boxed caption is stored twice.
    """
    from docx.oxml.ns import qn

    # python-docx's namespace map has no "mc" prefix, so spell the URI out
    fallback = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
    out = []
    for box in paragraph._p.iter(qn("w:txbxContent")):
        ancestor, in_fallback = box.getparent(), False
        while ancestor is not None:
            if ancestor.tag == fallback:
                in_fallback = True
                break
            ancestor = ancestor.getparent()
        if in_fallback:
            continue
        text = " ".join(" ".join(t.text or "" for t in box.iter(qn("w:t"))).split())
        if text:
            out.append(text)
    return out


def _docx_cell_text(cell, depth: int = 0) -> str:
    """All the text a Word table cell displays, nested tables included.

    ``python-docx``'s ``cell.text`` walks the cell's own ``w:p`` children only.
    A cell whose content is another table - a form layout, a nested grid, the
    whole body of ``test-doc/Documents/testInstrLink.docx`` - therefore reads as
    ``""`` even though Word shows its text. This walks the ``tc`` children in
    document order, so a paragraph, the nested table and a trailing paragraph
    keep their order.
    """
    from docx.oxml.ns import qn
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph as DocxParagraph

    if depth > 20:  # a malformed file cannot make this recurse forever
        return ""

    parts = []
    for child in cell._tc.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = DocxParagraph(child, cell)
            parts.append(paragraph.text)
            parts.extend(_docx_text_boxes(paragraph))
        elif child.tag == qn("w:tbl"):
            for row in _docx_table_rows(DocxTable(child, cell), depth + 1):
                parts.append(" | ".join(value for value in row if value))
    return "\n".join(part for part in parts if part)


def _docx_table_rows(table, depth: int = 0):
    """Rows of cell strings, with merged-cell text kept once (as before)."""
    rows = []
    emitted_cells: list = []
    for row in table.rows:
        row_cells = []
        for cell in row.cells:
            # A merged cell is the SAME tc element at several grid positions.
            # The text is kept once, at the first position it covers; the rest
            # stay empty so the grid keeps its shape and the reading order.
            if any(cell._tc is seen for seen in emitted_cells):
                row_cells.append("")
                continue
            emitted_cells.append(cell._tc)
            row_cells.append(_docx_cell_text(cell, depth))
        rows.append(row_cells)
    return rows


def _docx_iter_blocks(container, parent):
    """Yield the paragraphs and tables of a block container, in document order.

    ``python-docx``'s ``iter_inner_content()`` yields only direct ``w:p`` and
    ``w:tbl`` children. A block wrapped in ``w:sdt`` - a content control: form
    field, date picker, repeating section, reusable snippet - is not a direct
    child, so its paragraphs and tables were never visited, and Word documents
    built out of content controls stored almost none of their text
    (``testWORD_template.docx``: 13% of document tokens, and its CV body is
    entirely controlled paragraphs).

    Descends through ``w:sdtContent`` instead of treating the control as opaque,
    keeping document order and visiting each block exactly once.
    """
    from docx.oxml.ns import qn
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph as DocxParagraph

    p_tag, tbl_tag, sdt_tag = qn("w:p"), qn("w:tbl"), qn("w:sdt")
    for child in container.iterchildren():
        if child.tag == p_tag:
            yield DocxParagraph(child, parent)
        elif child.tag == tbl_tag:
            yield DocxTable(child, parent)
        elif child.tag == sdt_tag:
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                yield from _docx_iter_blocks(content, parent)


def _pptx_openable_copy(filepath: str) -> Optional[str]:
    """A path python-pptx accepts, for packages with a non-deck main part.

    ``.ppsx`` (slideshow) and ``.potx`` (template) are the same container as
    ``.pptx`` with a different content type. python-pptx validates that string
    and refuses the file with "is not a PowerPoint file", so a slideshow could
    not be read at all - testPPT.ppsx scored 0.75 on whatever the error path
    happened to leave behind. The copy changes only that declaration and is
    deleted as soon as the package is open; nothing about the artifact changes.
    """
    import tempfile
    import zipfile

    variants = (b"presentationml.slideshow.main+xml",
                b"presentationml.template.main+xml",
                b"presentationml.macroEnabled.main+xml")
    try:
        with zipfile.ZipFile(filepath) as archive:
            content_types = archive.read('[Content_Types].xml')
            if not any(variant in content_types for variant in variants):
                return None
            replacement = content_types
            for variant in variants:
                replacement = replacement.replace(
                    variant, b"presentationml.presentation.main+xml")
            members = [(info.filename, archive.read(info.filename))
                       for info in archive.infolist()]
    except Exception:
        return None

    handle, temp_path = tempfile.mkstemp(suffix='.pptx')
    os.close(handle)
    try:
        with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as copy:
            for name, data in members:
                if name == '[Content_Types].xml':
                    data = replacement
                copy.writestr(name, data)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return None
    return temp_path


def _drawingml_paragraph_texts(xml: str) -> List[str]:
    """Every ``a:p``'s text in a DrawingML part, runs concatenated.

    python-pptx models shapes it understands. A WordArt, a diagram or a shape
    inside a structure the library does not describe keeps its text in
    ``a:t`` runs that no shape object exposes - testPPT_various2.pptx has notes
    placeholders holding "NotesMasterHeader" and "NotesWordArtPage2" which
    python-pptx reports as empty text frames. Reading the part is the only
    reliable way to be sure the text is not there.
    """
    import xml.etree.ElementTree as ET

    a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    if not xml or "<a:p" not in xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    paragraphs: List[str] = []
    for paragraph in root.iter(a + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(a + "t"))
        text = " ".join(text.split())
        if text:
            paragraphs.append(text)
    return paragraphs


def _pptx_cover_remaining(slides: List[Dict[str, Any]], filepath: str) -> Dict[str, Any]:
    """Add the DrawingML paragraphs the shape walk did not reach.

    Slides, notes pages and the notes master are read part by part. A paragraph
    already present in the slide's stored text is not repeated; one that is not
    is appended with ``source: xml_backfill`` and the part it came from, so
    recovered text is distinguishable from parsed text.
    """
    import zipfile

    try:
        with zipfile.ZipFile(filepath) as archive:
            parts = [name for name in archive.namelist()
                     if name.endswith('.xml')
                     and (name.startswith('ppt/slides/slide')
                          or name.startswith('ppt/notesSlides/notesSlide')
                          or name.startswith('ppt/notesMasters/'))]
            payload = {name: archive.read(name).decode('utf-8', 'replace')
                       for name in parts}
            # A notes page is not slide N just because its part is called
            # notesSlideN: the number is the notes page's own, and a deck that
            # gained or lost a notes page while editing has them out of step.
            # notesSlide2.xml in testPPT_various2.pptx holds "Notes for slide3".
            # The relationship file is the authority.
            notes_targets = {}
            for name in parts:
                if not name.startswith('ppt/notesSlides/notesSlide'):
                    continue
                rels_name = name.replace('notesSlides/', 'notesSlides/_rels/') + '.rels'
                rels = payload.get(rels_name)
                if rels is None:
                    try:
                        rels = archive.read(rels_name).decode('utf-8', 'replace')
                    except KeyError:
                        continue
                match = re.search(r'Target="[^"]*slides/(slide\d+)\.xml"', rels)
                if match:
                    notes_targets[name] = int(match.group(1)[len('slide'):])
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "added": 0}

    by_slide = {}
    for slide in slides:
        haystack = " ".join(str(text) for text in (slide.get('texts') or []))
        haystack += " " + " ".join(
            str(element.get('text') or '') for element in (slide.get('elements') or []))
        haystack += " " + str(slide.get('notes') or '')
        by_slide[int(slide.get('slide_number') or 0)] = haystack

    added, per_part, leftovers = 0, {}, []
    for name in sorted(payload):
        number = 0
        if name in notes_targets:
            number = notes_targets[name]
        else:
            match = re.search(r'(slide|notesSlide)(\d+)\.xml$', name)
            if match:
                number = int(match.group(2))
        paragraphs = _drawingml_paragraph_texts(payload[name])
        kept = 0
        for text in paragraphs:
            haystack = by_slide.get(number)
            if haystack is None or text in haystack:
                if haystack is None:
                    leftovers.append(f"{name}: {text}")
                continue
            slide = next((s for s in slides if int(s.get('slide_number') or 0) == number), None)
            if slide is None:
                leftovers.append(f"{name}: {text}")
                continue
            slide.setdefault('elements', []).append({
                "type": "text",
                "text": text,
                "source": "xml_backfill",
                "location": name,
            })
            slide.setdefault('texts', []).append(text)
            by_slide[number] = haystack + " " + text
            kept += 1
            added += 1
        per_part[name] = {"paragraphs": len(paragraphs), "recovered": kept}

    result = {"added": added, "parts": per_part}
    if leftovers:
        # Text in a part that belongs to no slide - the notes master's header
        # and footer placeholders, for instance - is still furniture of this
        # deck. It goes to the forensic channel, which the pipeline indexes.
        result["unattributed"] = leftovers
        result["forensic_text"] = "\n".join(leftovers)
    return result


def _ods_repeat(node, attribute: str, *, default: int = 1, cap: int = 16384) -> int:
    """The repeat/span count an ODF cell or row declares, within bounds.

    ODF writes a run of identical nodes once and records the length:
    ``number-rows-repeated="2"`` is two rows, ``number-columns-repeated="3"``
    is three cells. A reader that takes the node as one row moves every address
    below it upwards - a value in B4 was reported in B3 and the *columns* of a
    row shifted the same way. Capped because writers pad sheets to the sheet
    limit with enormous empty repeats.
    """
    raw = node.getAttribute(attribute)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < 1:
        return 1
    return min(value, cap)


def _ods_row_cells(row) -> list:
    """One ODF table row as a list of cell texts, repeats and spans expanded.

    Trailing empty runs are not materialised: a padded sheet declares thousands
    of empty cells per row and none of them carry an address that matters. They
    are held back and flushed only when a cell with text follows, which keeps
    every populated cell in its true column.
    """
    _odf_mod_1 = _odf("odf.table")
    TableCell = _odf_mod_1.TableCell

    cells = []
    pending_empty = 0
    for cell in row.getElementsByType(TableCell):
        text = ""
        for para in cell.childNodes:
            if hasattr(para, "data"):
                text += para.data
            elif hasattr(para, "childNodes") and para.childNodes:
                text += _odf_node_text(para)
        text = text.strip()
        width = max(_ods_repeat(cell, "numbercolumnsrepeated"),
                    _ods_repeat(cell, "numbercolumnsspanned"))
        if not text:
            pending_empty += width
            continue
        if pending_empty:
            cells.extend([""] * pending_empty)
            pending_empty = 0
        cells.extend([text] * min(width, 100))
    return cells


def _odf_node_text(node) -> str:
    """All text under an ODF node, in document order.

    ``para.childNodes[0].data`` reads one run, and in ODF a run is usually a
    ``text:span`` carrying a style - its own text sits one level deeper, so the
    attribute is absent and the paragraph looks empty. Every bullet in
    testODP_NPE.odp is shaped exactly like that, and the presentation reader
    stored 3% of it. ``odf.teletype.extractText`` walks the whole subtree; the
    hand-rolled walk is only for the case where odfpy cannot be imported.
    """
    if node is None:
        return ''
    try:
        _odf_mod_2 = _odf("odf.teletype")
        extractText = _odf_mod_2.extractText
        text = extractText(node)
        if text:
            return ' '.join(text.split())
    except Exception:
        pass
    parts = []

    def walk(current):
        for child in getattr(current, 'childNodes', ()):
            data = getattr(child, 'data', None)
            if isinstance(data, str):
                parts.append(data)
            if hasattr(child, 'childNodes'):
                walk(child)

    walk(node)
    return ' '.join(''.join(parts).split())


#: Ceilings on how much of a spreadsheet is materialised in memory.
#:
#: There is no bound in the file format: ``testRecordSizeExceeded.xlsx`` is 12 MB
#: on disk and 200 000 rows x 15 columns in memory, which produced a 181 MB text
#: body from a single artifact and took the read from seconds to a minute. A
#: cap is not a silent loss: the sheet records ``truncated``, how many cells were
#: kept, and how many the sheet actually holds, so the shortfall is visible in
#: the result and in storage.
MAX_SHEET_CELLS = 250_000
MAX_WORKBOOK_CELLS = 1_000_000

#: Work ceiling on the *padded* rectangle, which is what actually costs time.
#: The cap above counts populated cells; a sheet whose used range spans all
#: 16384 columns still has to be walked, so iteration is bounded separately.
MAX_SHEET_GRID_CELLS = 4_000_000


def _cap_from_env(name: str, default: int) -> int:
    """A configured cap, falling back to the module default.

    The caps are deployment facts: a workstation and a 512 MB container have
    different right answers, and a ceiling that can only be changed by editing
    a reader is a ceiling that silently decides what is recoverable.
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        logger.warning("Ignoring %s=%r: not an integer", name, raw)
        return default
    if value <= 0:
        logger.warning("Ignoring %s=%r: must be positive", name, raw)
        return default
    return value


def sheet_cell_cap() -> int:
    return _cap_from_env("SYLTHARAE_MAX_SHEET_CELLS", MAX_SHEET_CELLS)


def workbook_cell_cap() -> int:
    return _cap_from_env("SYLTHARAE_MAX_WORKBOOK_CELLS", MAX_WORKBOOK_CELLS)


def sheet_grid_cap() -> int:
    return _cap_from_env("SYLTHARAE_MAX_SHEET_GRID_CELLS", MAX_SHEET_GRID_CELLS)

#: Every part of a WordprocessingML package that can hold visible text.
_DOCX_TEXT_PARTS = re.compile(
    r"word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml$")
_W_T_RE = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)
_W_P_RE = re.compile(r"<w:p[\s>].*?</w:p>", re.S)
_MC_FALLBACK_RE = re.compile(r"<mc:Fallback>.*?</mc:Fallback>", re.S)


def _docx_unescape(text: str) -> str:
    """XML entities a text run can contain."""
    return (text.replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&apos;", "'")
                .replace("&amp;", "&"))


def _docx_paragraph_texts(xml: str) -> List[str]:
    """Every paragraph's text in one part, nested paragraphs included.

    A regex over ``<w:p>...</w:p>`` stops at the first closing tag, and Word
    nests paragraphs inside paragraphs whenever a text box, a shape or a nested
    table sits in a run - which is exactly the content a structured walk is
    most likely to miss. ElementTree walks the real tree, so nesting is handled
    by construction. A part that is not well-formed is reported, not guessed at.
    """
    import xml.etree.ElementTree as ET

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []

    texts: List[str] = []

    def paragraph_text(paragraph) -> str:
        """The paragraph's own runs.

        ``paragraph.iter(w:t)`` also collects the runs of a text box anchored in
        the paragraph, so the box's caption appeared twice: once as part of the
        anchoring paragraph and once as its own paragraph. A ``w:txbxContent``
        subtree holds its own ``w:p`` elements and the walk below visits them,
        so it is skipped here.
        """
        collected: List[str] = []

        def collect(node) -> None:
            for child in list(node):
                if child.tag == w + "txbxContent":
                    continue
                if child.tag == w + "t":
                    collected.append(child.text or "")
                collect(child)

        collect(paragraph)
        return "".join(collected)

    def walk(node, inside_paragraph: bool = False) -> None:
        for child in list(node):
            if child.tag == w + "p":
                if not inside_paragraph:
                    texts.append(paragraph_text(child))
                walk(child, True)
            elif child.tag == w + "txbxContent":
                # The box's paragraphs are blocks of their own: visit them, but
                # outside the anchoring paragraph's text.
                walk(child, False)
            else:
                walk(child, inside_paragraph)

    walk(root)
    return texts


def _docx_cover_remaining(elements: List[Dict[str, Any]], filepath) -> Dict[str, Any]:
    """Add the text runs in the package that the structured walk did not reach.

    The structured walk describes the document the way python-docx models it.
    Whatever that model omits is text in the file that never reaches storage: a
    table nested inside a nested table (``017097.docx``: 437 tables, the body is
    nothing but tables), a shape in an ``mc:AlternateContent`` branch, or a part
    python-docx never opens at all - ``header2.xml``, ``footnotes.xml``,
    ``comments.xml``.

    This pass is the guarantee rather than another model: it reads every
    ``<w:t>`` run in every text-bearing part, groups by paragraph, and adds the
    paragraphs whose text is not already present, each labelled with the part it
    came from. It cannot invent text, so it can only make the extraction more
    complete.
    """
    import zipfile

    try:
        with zipfile.ZipFile(filepath) as zf:
            parts = {name: zf.read(name).decode("utf-8", "replace")
                     for name in zf.namelist() if _DOCX_TEXT_PARTS.match(name)}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "added": 0}

    def normalise(text: str) -> str:
        return " ".join(text.split())

    # Coverage is counted, per line, over what the structured walk produced.
    #
    # Two defects lived here, both measured on `Other/testWORD_template.dotx`:
    # the whole haystack was normalised after joining, so every newline became
    # a space and nothing ever matched (the backfill re-added paragraphs that
    # had already been extracted), and coverage was a plain membership test, so
    # the five identical "Click here to enter text" placeholders the package
    # contains were stored once and the file scored 0.839.
    #
    # Matching is by whole line rather than by substring: "Click here to enter
    # text" is a substring of "Click here to enter text.", and a substring test
    # declared three real paragraphs covered by one that differs by a full
    # stop. A cell holding several paragraphs contributes each of its lines, so
    # a paragraph inside a table cell still matches rather than being stored
    # twice.
    covered: Dict[str, int] = {}

    def add_lines(text: Any, counts: Dict[str, int]) -> None:
        for line in str(text or "").split("\n"):
            line = normalise(line)
            if line:
                counts[line] = counts.get(line, 0) + 1

    for element in elements:
        if not isinstance(element, dict):
            continue
        add_lines(element.get("text"), covered)
        for row in element.get("rows") or []:
            add_lines(" | ".join(str(c) for c in row), covered)
            for cell in row:
                add_lines(cell, covered)

    def present_in_document(text: str) -> int:
        return covered.get(text, 0)

    added: List[Dict[str, Any]] = []
    per_part: Dict[str, Dict[str, int]] = {}

    for name in sorted(parts):
        xml = _MC_FALLBACK_RE.sub("", parts[name])  # never count a fallback twice
        part_added = part_seen = 0
        seen_per_text: Dict[str, int] = {}
        for paragraph in _docx_paragraph_texts(xml):
            text = normalise(_docx_unescape(paragraph))
            if not text:
                continue
            part_seen += 1
            seen_per_text[text] = seen_per_text.get(text, 0) + 1
            if seen_per_text[text] <= present_in_document(text):
                continue
            covered[text] = covered.get(text, 0) + 1
            part_added += 1
            added.append({
                "type": "paragraph",
                "position": len(elements) + len(added),
                "index": len(elements) + len(added),
                "text": text,
                "style": "Recovered",
                "source": "xml_backfill",
                "location": name,
            })
        per_part[name] = {"paragraphs": part_seen, "recovered": part_added}

    return {"added": len(added), "parts": per_part, "elements": added}


#: ODF namespaces, by prefix. ElementTree reports tags as ``{uri}local``;
#: these let a reader ask for an attribute by the prefix the spec uses.
_ODF_NS = {
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
}

#: Inline ODF elements: they continue the word around them, so no separator is
#: written at their boundaries. Everything else inside a paragraph is a field or
#: a nested block, and gets one.
_ODF_INLINE = frozenset({
    "span", "a", "s", "tab", "line-break", "soft-page-break", "bookmark",
    "bookmark-start", "bookmark-end", "reference-mark",
    "reference-mark-start", "reference-mark-end", "change-start", "change-end",
    "change", "note-citation", "note-ref", "ruby-base", "ruby-text",
})


#: OOXML extensions whose encrypted form is a CFB container rather than a ZIP.
_ENCRYPTABLE_OOXML = (
    '.docx', '.docm', '.dotx', '.dotm',
    '.xlsx', '.xlsm', '.xltx', '.xltm',
    '.pptx', '.pptm', '.potx', '.potm', '.ppsx', '.ppsm',
)


def _ooxml_encryption_report(filepath: str) -> Optional[dict]:
    """Describe an encrypted OOXML package, or return None if it is not one.

    Password-protected OOXML is not a ZIP: the whole package is a CFB container
    holding ``EncryptionInfo`` and ``EncryptedPackage``, which is why openpyxl
    answers "File is not a zip file" and python-pptx "Package not found" for
    files that are intact. Measured on the round-17 corpus: five such files were
    reported as unreadable rather than protected. The evidence is two stream
    names, so it can be checked before any parser is asked to open the file.
    """
    try:
        import olefile
    except Exception:
        return None
    try:
        if not olefile.isOleFile(filepath):
            return None
        with olefile.OleFileIO(filepath) as ole:
            names = ["/".join(entry) for entry in ole.listdir()]
    except Exception:
        return None

    lowered = [name.lower() for name in names]
    encrypted_parts = [name for name, low in zip(names, lowered)
                       if low.startswith('encryptioninfo')
                       or low.startswith('encryptedpackage')]
    if not encrypted_parts:
        return None
    return {
        'error': ('Encrypted Office package (CFB encryption): the payload is '
                  'ciphertext and needs the password; EncryptionInfo and '
                  'EncryptedPackage are present, so the file is protected, '
                  'not corrupt'),
        'encrypted': True,
        'encrypted_parts': sorted(encrypted_parts),
        'extraction_info': {
            'skipped': True,
            'skip_reason': 'encrypted_without_password',
            'encryption': 'cfb',
        },
    }


def _odf_annotations(filepath: str) -> List[Dict[str, Any]]:
    """Anchored comments in ``content.xml``: author, timestamp and note.

    odfpy's ``extractText`` flattens ``office:annotation`` into the paragraph it
    is anchored to, with no separator, so the note, its timestamp and the word
    before it became one token in testODTStyles3.odt ("Rolf2017-01-31T00:00:00").
    A comment is metadata about a paragraph, not a run of it: it is extracted
    here on its own so it can be stored as a labelled line and removed from the
    sentence.
    """
    import zipfile
    import xml.etree.ElementTree as ET

    try:
        with zipfile.ZipFile(filepath) as zf:
            if "content.xml" not in zf.namelist():
                return []
            root = ET.fromstring(zf.read("content.xml"))
    except Exception:
        return []

    records: List[Dict[str, Any]] = []
    for element in root.iter():
        if _et_local(element.tag) != "annotation":
            continue
        author = date = ""
        bodies: List[str] = []
        for child in element:
            local = _et_local(child.tag)
            if local == "creator" and not author:
                author = _collapse(child.text or "")
            elif local == "date" and not date:
                date = _collapse(child.text or "")
            elif local in ("p", "h"):
                text = _collapse(_odf_block_text(child))
                if text:
                    bodies.append(text)
        text = " ".join(bodies)
        if not (text or author or date):
            continue
        records.append({"author": author, "date": date, "text": text})
    return records


def _odf_annotation_line(record: Dict[str, Any]) -> str:
    head = "[comment"
    if record.get("author"):
        head += f": {record['author']}"
    if record.get("date"):
        head += f" on {record['date']}"
    head += "]"
    return f"{head} {record['text']}".strip()


def _apply_odf_annotations(result: Dict[str, Any], filepath: str) -> None:
    """Detach comments from paragraph text and add them as their own lines."""
    annotations = _odf_annotations(filepath)
    if not annotations:
        return
    paragraphs = result.get("paragraphs")
    if not isinstance(paragraphs, list):
        return
    for paragraph in paragraphs:
        text = paragraph.get("text") or ""
        if not text:
            continue
        changed = False
        for record in annotations:
            for fragment in (record.get("date"), record.get("text")):
                if fragment and fragment in text:
                    text = text.replace(fragment, " ")
                    changed = True
        if changed:
            # Re-collapse: the removal leaves a double space where the note was.
            paragraph["text"] = " ".join(text.split())
    for record in annotations:
        paragraphs.append({
            "text": _odf_annotation_line(record),
            "style": "Comment",
            "location": "office:annotation",
            "kind": "comment",
        })
    result["total_paragraphs"] = len(paragraphs)
    result["annotations"] = annotations


def _odf_tracked_changes(filepath: str) -> List[Dict[str, Any]]:
    """Every tracked change in ``content.xml``, with its author and timestamp.

    Two parsers can read an ODT and they answered this question differently:
    the content.xml fallback read ``office:change-info`` and the odfpy path did
    not look at it at all, so whether an author survived depended on which
    parser accepted the package. Both paths now call this one function, which
    walks the real tree and returns one record per ``text:insertion`` /
    ``text:deletion`` / ``text:format-change`` / ``text:change`` region:

    ``{kind, author, date, text, location}``

    Repeated records are kept. Two insertions by the same person in the same
    second are two entries in the redline, and collapsing them by text loses a
    change an examiner may need to count - which is exactly what happened to
    ``testODFwithOOo3.odt`` (three regions, one stored).
    """
    import zipfile
    import xml.etree.ElementTree as ET

    try:
        with zipfile.ZipFile(filepath) as zf:
            if "content.xml" not in zf.namelist():
                return []
            root = ET.fromstring(zf.read("content.xml"))
    except Exception:
        return []

    records: List[Dict[str, Any]] = []
    for element in root.iter():
        if _et_local(element.tag) not in ("insertion", "deletion",
                                          "format-change", "change"):
            continue
        author = date = ""
        bodies: List[str] = []
        for child in element.iter():
            local = _et_local(child.tag)
            if local == "creator" and not author:
                author = _collapse(child.text or "")
            elif local == "date" and not date:
                date = _collapse(child.text or "")
            elif local in ("p", "h"):
                text = _collapse(_odf_block_text(child))
                if text:
                    bodies.append(text)
        kind = _et_local(element.tag)
        records.append({
            "kind": kind,
            "author": author,
            "date": date,
            "text": " ".join(bodies),
            "location": f"text:{kind}",
        })
    return records


def _odf_change_line(record: Dict[str, Any]) -> str:
    """The one-line form of a tracked change, as it reaches the text body."""
    parts = [f"[{record['kind']}"]
    if record.get("author"):
        parts.append(f": {record['author']}")
    if record.get("date"):
        parts.append(f" on {record['date']}")
    head = "".join(parts) + "]"
    return f"{head} {record['text']}".strip() if record.get("text") else head


def _odf_encryption_report(filepath):
    """A structured, honest answer for an encrypted ODF package.

    An ODF package hides nothing: ``META-INF/manifest.xml`` declares
    ``manifest:encryption-data`` for every stream it encrypted, ``content.xml``
    included. Without that check the reader tried to decode ciphertext as UTF-8
    and surfaced ``'utf-8' codec can't decode byte 0x85 in position 3`` - a
    message that names the symptom and hides the cause. The text of a
    password-protected document is genuinely unavailable; saying so is the
    correct result, and it lets the auditor mark the file EMPTY-AGREED instead
    of counting a real file as a total extraction loss.
    """
    import zipfile

    try:
        with zipfile.ZipFile(filepath) as archive:
            names = archive.namelist()
            if 'META-INF/manifest.xml' not in names:
                return None
            manifest = archive.read('META-INF/manifest.xml')
    except Exception:
        return None
    if b'encryption-data' not in manifest:
        return None
    encrypted = []
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(manifest)
        for entry in root.iter():
            if any(key.endswith('encryption-data') for key in entry.attrib):
                path = entry.get('{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}full-path')
                encrypted.append(path) if path else None
    except Exception:
        pass
    return {
        'error': ('Encrypted ODF package: content.xml is encrypted and cannot '
                  'be read without the password'),
        'encrypted': True,
        'encryption': 'ODF package encryption (manifest:encryption-data)',
        'encrypted_parts': sorted(set(encrypted)),
    }


def _et_local(tag) -> str:
    """Local name of an ElementTree tag (namespace stripped)."""
    return tag.split("}", 1)[1] if isinstance(tag, str) and "}" in tag else str(tag)


def _et_ancestor_map(root):
    """``{id(child): parent}`` for every element under ``root``."""
    cached = getattr(root, "_et_parents", None)
    if cached is not None:
        return cached
    parents = {}
    for parent in root.iter():
        for child in parent:
            parents[id(child)] = parent
    try:
        root._et_parents = parents
    except AttributeError:  # pragma: no cover - C-accelerated elements allow it
        pass
    return parents


def _collapse(text: str) -> str:
    """Collapse all runs of whitespace to single spaces and trim."""
    return ' '.join(str(text or '').split())


def _odf_block_text(element) -> str:
    """The text of an ODF block, with ODF-aware separators.

    ``''.join(element.itertext())`` welds adjacent elements together: a creator
    and a date, a paragraph and the annotation hanging off it. ``text:s``
    carries a repeat count that itertext() also ignores. Both matter, because
    the token stream is what gets stored and searched.
    """
    parts: List[str] = []

    def ensure_space():
        if parts and not parts[-1].endswith((" ", "\t", "\n")):
            parts.append(" ")

    def walk(node, top=False):
        if node.text:
            parts.append(node.text)
        for child in node:
            tag = _et_local(child.tag)
            if tag == "s":
                parts.append(" " * int(child.get(_ODF_NS["text"] + "c") or 1))
            elif tag == "tab":
                parts.append("\t")
            elif tag == "line-break":
                parts.append("\n")
            elif tag in _ODF_INLINE:
                walk(child)
            else:
                ensure_space()
                walk(child)
                ensure_space()
            if child.tail:
                parts.append(child.tail)

    walk(element)
    text = "".join(parts)
    return ' '.join(text.split())


class OfficeFileReader(BaseReader):
    """
    Reader for office document files.
    
    Follows database design principles:
    - All functions are within the class
    - Inherits from BaseReader
    - Consistent error handling
    - Proper resource management
    """
    
    def _embedded_extraction_dir(self, source_path):
        """The directory where this document's embedded objects are materialised.

        Computed once per source document and cached, because
        get_extraction_name_file allocates a NEW numbered folder whenever the
        name already exists - calling it per image would scatter one document's
        embedded objects across image-count directories.
        """
        cached = getattr(self, '_embedded_dir_cache', None)
        if cached and cached[0] == source_path:
            return cached[1]
        from core.path_utils import get_extraction_name_file

        extract_dir = get_extraction_name_file(source_path, '')
        os.makedirs(extract_dir, exist_ok=True)
        self._embedded_dir_cache = (source_path, extract_dir)
        return extract_dir

    def _materialise_embedded_image(self, image_bytes, image_name, source_path,
                                    result=None):
        """Write one embedded image to disk so it can become a child object.

        Returns the written path.

        EMBED-02: each embedded image used to be written to a
        NamedTemporaryFile and unlinked straight after OCR, so the bytes never
        survived long enough to be an object in their own right. Their text was
        folded into the parent document and the image itself was discarded - no
        row, no hash, no parent, no hierarchy. Writing into the same kind of
        directory that archives and email attachments use lets the router run
        the image through the ordinary child pipeline instead.

        The name goes through the same Windows-safe component normalisation
        used for archive members: package part names are attacker-influenced and
        must not become reserved device names or over-long components.
        """
        from core.archive_safety import windows_safe_component

        extract_dir = self._embedded_extraction_dir(source_path)
        safe_name = windows_safe_component(os.path.basename(image_name)) or 'embedded_image'
        destination = os.path.join(extract_dir, safe_name)
        with open(destination, 'wb') as handle:
            handle.write(image_bytes)
        # Stamp the result so the router knows this document has materialised
        # children to recurse into. Callers that build their result elsewhere
        # (extract_images_from_pptx returns a bare list) pass None and stamp it
        # themselves once the list is attached.
        if result is not None:
            result["extraction_path"] = extract_dir
        return destination

    def _image_reader(self):
        """A cached ImageFileReader used to OCR embedded images.

        Created once per reader rather than once per image: a document with
        fifty embedded pictures must not construct fifty readers.
        """
        cached = getattr(self, '_image_reader_cache', None)
        if cached is None:
            from .read_img_fast import ImageFileReader

            cached = ImageFileReader()
            self._image_reader_cache = cached
        return cached

    def _ocr_embedded_image(self, image_path):
        """Run the standard image reader over one embedded image.

        EMBED-01: this used to be a module-level import of
        read_image_file_fast at six call sites (DOCX, XLSX, PPTX, ODT, ODS,
        ODP). But read_image_file_fast is a METHOD on ImageFileReader, so the
        import raised ImportError every time. Each site was wrapped in
        except Exception with a logger.debug, so the failure was invisible and
        embedded image extraction silently produced nothing for every Office
        and OpenDocument format.
        """
        return self._image_reader().read_image_file_fast(image_path)

    def get_supported_extensions(self) -> Set[str]:
        """Every Office/OpenDocument format this reader processes.

        The list is deliberately complete rather than convenient: templates and
        macro-enabled variants are the formats most often *missed* by
        extension-driven routing, and a document that reaches no reader is a
        document whose evidence silently disappears. Each variant below is
        routed to a real parse path in :meth:`read_file` - see the dispatch
        table there - and the macro-enabled ones additionally have their VBA
        project extracted and hashed.
        """
        return {
            # Microsoft Word formats
            '.docx',   # Word 2007+
            '.doc',    # Word 97-2003
            '.docm',   # Word Macro-Enabled Document
            '.dotx',   # Word Template (OOXML)
            '.dotm',   # Word Macro-Enabled Template (OOXML)
            '.dot',    # Word 97-2003 Template
            # Microsoft Excel formats
            '.xlsx',   # Excel 2007+
            '.xls',    # Excel 97-2003
            '.xlsm',   # Excel Macro-Enabled Workbook
            '.xlsb',   # Excel Binary Workbook
            '.xltx',   # Excel Template (OOXML)
            '.xltm',   # Excel Macro-Enabled Template (OOXML)
            '.xlt',    # Excel 97-2003 Template
            # Microsoft PowerPoint formats
            '.pptx',   # PowerPoint 2007+
            '.pptm',   # PowerPoint Macro-Enabled Presentation
            '.ppt',    # PowerPoint 97-2003
            '.potx',   # PowerPoint Template (OOXML)
            '.potm',   # PowerPoint Macro-Enabled Template (OOXML)
            '.pot',    # PowerPoint 97-2003 Template
            '.ppsx',   # PowerPoint Slideshow (OOXML)
            '.ppsm',   # PowerPoint Macro-Enabled Slideshow (OOXML)
            '.pps',    # PowerPoint 97-2003 Slideshow
            # OpenDocument formats (documents, spreadsheets, presentations,
            # their templates and drawings - all ODF packages)
            '.odt',    # OpenDocument Text
            '.ott',    # OpenDocument Text Template
            '.ods',    # OpenDocument Spreadsheet
            '.ots',    # OpenDocument Spreadsheet Template
            '.odp',    # OpenDocument Presentation
            '.otp',    # OpenDocument Presentation Template
            '.odg',    # OpenDocument Drawing
            # Other formats
            '.csv',    # Comma-Separated Values
            '.tsv',    # Tab-Separated Values
            '.rtf',    # Rich Text Format
            '.ole',    # OLE compound file whose payload is an unparsable document
            '.pages',  # Apple Pages (ZIP / iWork bundle)
            '.numbers',  # Apple Numbers (ZIP / iWork bundle)
        }
    
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read office file and extract content with improved error handling
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with office document content or error information
        """
        # Validate file info using base class method
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))
        
        file_path = str(file_info.get("path"))
        # DETECT-01: dispatch on the content-verified type, not the filename.
        ext = self.effective_extension(file_info)

        # An encrypted OOXML package is not a broken ZIP. Answer before any
        # parser gets the chance to describe the ciphertext as corruption.
        if ext in _ENCRYPTABLE_OOXML:
            encrypted = _ooxml_encryption_report(file_path)
            if encrypted:
                encrypted["filepath"] = file_path
                return encrypted
        
        try:
            result = None
            # Microsoft Word formats. Templates and macro-enabled variants are
            # the same package structure with a different content type, so they
            # share the document parse path; the forensic pass below adds the
            # macro project and the variant details.
            if ext in ('.docx', '.docm', '.dotx', '.dotm'):
                result = self.read_docx_file(file_path)
            elif ext in ('.doc', '.dot'):
                result = self._promote_doc_text_to_elements(self.read_doc_file(file_path))
            # Microsoft Excel formats
            elif ext in ('.xlsx', '.xlsm', '.xltx', '.xltm'):
                result = self.read_xlsx_file(file_path)
            elif ext == '.xlsb':
                result = self.read_xlsb_file(file_path)
            elif ext in ('.xls', '.xlt'):
                result = self.read_xls_file(file_path)
            # Microsoft PowerPoint formats
            elif ext in ('.pptx', '.pptm', '.potx', '.potm', '.ppsx', '.ppsm'):
                result = self.read_pptx_file(file_path)
            elif ext in ('.ppt', '.pot', '.pps'):
                result = self.read_ppt_file(file_path)
            # OpenDocument formats. Drawings use the presentation parser: both
            # are ODF packages whose pages are draw:page elements.
            elif ext in ('.odt', '.ott'):
                result = self.read_odt_file(file_path)
            elif ext in ('.ods', '.ots'):
                result = self.read_ods_file(file_path)
            elif ext in ('.odp', '.otp', '.odg'):
                result = self.read_odp_file(file_path)
            # Other formats
            elif ext in ('.csv', '.tsv'):
                delimiter = '\t' if ext == '.tsv' else ','
                result = self.read_csv_file(file_path, delimiter=delimiter)
            elif ext == '.rtf':
                result = self.read_rtf_file(file_path)
            elif ext == '.ole':
                # A compound file whose contents are not an Office document.
                # Reported with its OLE stream inventory so the artifact is
                # recorded as unsupported-but-inspected rather than failed.
                return self._read_unrecognised_ole(file_path)
            elif ext == '.pages':
                result = self.read_pages_file(file_path)
            elif ext == '.numbers':
                result = self.read_numbers_file(file_path)
            else:
                error_msg = f"Unsupported office file type: {ext or file_path}"
                return self.handle_read_error(ValueError(error_msg), file_path, "read_file")

            if ext in (
                '.xlsx', '.xlsm', '.xltx', '.xltm', '.xls', '.xlt', '.xlsb',
                '.ods', '.ots',
            ):
                result = self._normalize_spreadsheet_result(result)

            return self._attach_forensic_detail(result, file_path, ext)
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")



    def read_docx_file(self, filepath):
        """
        Independent DOCX reader function with image extraction and OCR.
        Preserves original document order by interleaving paragraphs, tables, and other elements.
        """
        try:
            from docx import Document
            from docx.table import Table as DocxTable
            from docx.text.paragraph import Paragraph as DocxParagraph
        except ImportError:
            return {
                "error": "python-docx not installed. Install with: pip install python-docx",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            result = {
                "filepath": filepath,
                "elements": [],  # Ordered list of all document elements (paragraphs, tables, etc.)
                "paragraphs": [],  # Keep for backward compatibility
                "tables": [],  # Keep for backward compatibility
                "total_paragraphs": 0,
                "total_tables": 0,
                "extracted_images": []
            }
            
            # Extract elements in document order to preserve original layout
            # Iterate through document body elements in order
            paragraph_idx = 0
            table_idx = 0
            element_position = 0
            
            # One Paragraph or Table per body element, in body order. These
            # proxies used to be looked up in doc.paragraphs / doc.tables and
            # remembered by id(); those proxies are rebuilt and freed on every
            # access, so a reused address made a different paragraph look
            # "already processed" and it was silently dropped (DOCX-DROP-01).
            # python-docx refuses a package whose main part is not
            # "document.main+xml". Macro-enabled (.docm) and template (.dotx)
            # documents are valid WordprocessingML with a different content
            # type and used to fail the whole read; fall through to the XML
            # pass below, which reads every text run whatever the content type.
            doc = None
            try:
                doc = Document(filepath)
            except Exception as exc:
                result["python_docx_error"] = f"{type(exc).__name__}: {exc}"

            for block in (_docx_iter_blocks(doc.element.body, doc)
                          if doc is not None else ()):
                if isinstance(block, DocxParagraph):
                    para = block
                    if para is not None:
                        para_text = para.text.strip()
                        # Include all paragraphs (even empty ones) to preserve spacing/formatting
                        para_data = {
                            "type": "paragraph",
                            "position": element_position,
                            "text": para_text,
                            "style": para.style.name if para.style else "Normal",
                            "index": paragraph_idx
                        }
                        result["elements"].append(para_data)

                        # A text box anchored in this paragraph is content the
                        # paragraph's own text stream does not contain. Emit it
                        # here so it keeps the paragraph's place in the reading
                        # order instead of being appended at the end or lost.
                        for box_text in _docx_text_boxes(para):
                            result["elements"].append({
                                "type": "paragraph",
                                "position": element_position,
                                "text": box_text,
                                "style": "Text Box",
                                "index": paragraph_idx,
                                "source": "text_box"
                            })
                            result["paragraphs"].append({
                                "text": box_text,
                                "style": "Text Box"
                            })
                            paragraph_idx += 1
                            element_position += 1

                        # Also add to paragraphs list for backward compatibility (only non-empty)
                        if para_text:
                            result["paragraphs"].append({
                                "text": para_text,
                                "style": para.style.name if para.style else "Normal"
                            })
                        
                        paragraph_idx += 1
                        element_position += 1
                
                elif isinstance(block, DocxTable):
                    table = block
                    if table is not None:
                        table_data = {
                            "type": "table",
                            "position": element_position,
                            "table_number": table_idx + 1,
                            "rows": []
                        }
                        
                        # Merged-cell de-duplication and nested-table
                        # recursion both live in _docx_table_rows: the text of
                        # a cell that contains a table used to be dropped
                        # because cell.text does not descend into it.
                        table_data["rows"] = _docx_table_rows(table)
                        
                        result["elements"].append(table_data)
                        result["tables"].append({
                            "table_number": table_idx + 1,
                            "rows": table_data["rows"]
                        })
                        table_idx += 1
                        element_position += 1
            
            # The completeness guarantee: every text run in the package that
            # the structured walk above did not reach.
            covered = _docx_cover_remaining(result["elements"], filepath)
            if covered.get("elements"):
                result["elements"].extend(covered["elements"])
                result["paragraphs"].extend(
                    {"text": element["text"], "style": element["style"]}
                    for element in covered["elements"])
            result["xml_backfill"] = {k: v for k, v in covered.items()
                                      if k != "elements"}

            result["total_paragraphs"] = len(result["paragraphs"])
            result["total_tables"] = len(result["tables"])
            
            # Extract and OCR images from the document
            try:
                import zipfile
                
                # DOCX files are ZIP archives - extract images from word/media/ folder
                with zipfile.ZipFile(filepath, 'r') as docx_zip:
                    # List all files in the archive
                    file_list = docx_zip.namelist()
                    
                    # Find all image files in word/media/ directory
                    image_files = [f for f in file_list if f.startswith('word/media/') and 
                                 any(f.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'])]
                    
                    # Extract and process each image
                    for image_file in image_files:
                        try:
                            image_bytes = docx_zip.read(image_file)
                            image_name = os.path.basename(image_file)
                            
                            # Process image with OCR using existing function
                            # Materialise the embedded image into the document's
                            # extraction directory, then OCR it in place. Writing
                            # it out - rather than to a temp file that is unlinked
                            # immediately - is what lets the router treat the image
                            # as a child object with its own row, hash and lineage.
                            tmp_path = self._materialise_embedded_image(
                                image_bytes, image_name, filepath, result
                            )
                            ocr_result = self._ocr_embedded_image(tmp_path)
                            if ocr_result:
                                ocr_result["name"] = image_name
                                if ocr_result.get("text") or ocr_result.get("error"):
                                    result["extracted_images"].append(ocr_result)
                        except Exception as e:
                            logger.warning(f"Failed to process embedded image {image_file}: {e}")
                            continue
                
                if result["extracted_images"]:
                    logger.info(f"Extracted and processed {len(result['extracted_images'])} images from DOCX")
            
            except Exception as e:
                logger.debug(f"Image extraction from DOCX failed (non-critical): {e}")
                # Don't fail the whole document if image extraction fails
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    # ------------------------------------------------------------------
    # Forensic detail: everything the document libraries do not return
    # ------------------------------------------------------------------
    def _attach_forensic_detail(self, result, filepath, extension):
        """Add properties, revisions, annotations, links and macros to a result.

        The body readers return the visible document. This method adds the rest
        of the evidence - document and custom properties, comments, tracked
        changes, hidden content, fields, bookmarks, headers/footers, external
        targets, embedded objects, formulas, notes, the package inventory and
        the VBA project - under four additive keys:

        ``artifact_metadata``
            Core/application/custom document properties.
        ``forensic_features``
            Relationships, package inventory and the per-application feature
            block, plus ``extraction_errors`` naming anything that could not be
            read.
        ``macros``
            Every VBA project with its digests, module list and analysis.
        ``forensic_text``
            The extracted detail flattened into searchable text, so a term
            inside a tracked deletion, a comment or a macro is indexed by the
            same pipeline that indexes the body.

        Failure never removes evidence: if a sub-extraction raises, the error is
        recorded in ``extraction_errors`` and the body result is returned
        unchanged.
        """
        if not isinstance(result, dict):
            return result
        if result.get('error') and not result.get('filepath'):
            return result

        import zipfile

        errors = []
        is_zip_package = False
        try:
            is_zip_package = zipfile.is_zipfile(filepath)
        except Exception as exc:
            errors.append({'scope': 'package', 'error': str(exc)})

        # Composition of the artifact, always recorded (cheap, and it is what
        # makes "which parts existed" answerable later).
        try:
            from core.formats import identify

            detection = identify(filepath)
            result['format_identification'] = detection.as_dict()
        except Exception as exc:
            errors.append({'scope': 'format_identification', 'error': str(exc)})

        app = None
        if is_zip_package:
            app = {'.docx': 'word', '.docm': 'word', '.dotx': 'word', '.dotm': 'word',
                   '.doc': 'word', '.dot': 'word',
                   '.xlsx': 'excel', '.xlsm': 'excel', '.xltx': 'excel',
                   '.xltm': 'excel', '.xls': 'excel', '.xlsb': 'excel',
                   '.xlt': 'excel',
                   '.pptx': 'powerpoint', '.pptm': 'powerpoint', '.potx': 'powerpoint',
                   '.potm': 'powerpoint', '.ppsx': 'powerpoint', '.ppsm': 'powerpoint',
                   '.ppt': 'powerpoint', '.pot': 'powerpoint', '.pps': 'powerpoint',
                   '.odt': 'odf', '.ott': 'odf', '.ods': 'odf', '.ots': 'odf',
                   '.odp': 'odf', '.otp': 'odf', '.odg': 'odf'}.get(extension)
            try:
                from core.forensics import extract_package_features, text_from_features

                features = extract_package_features(filepath, app=app)
                result['forensic_features'] = features
                result['artifact_metadata'] = features.get('properties', {})
                errors.extend(features.get('extraction_errors') or [])
                feature_text = text_from_features(features)
                if feature_text:
                    # Additive: the reader may already have put content here
                    # (a deck's notes-master text, for instance). Replacing it
                    # dropped that text silently.
                    existing = str(result.get('forensic_text') or '')
                    result['forensic_text'] = '\n'.join(
                        part for part in (existing, feature_text) if part)
            except Exception as exc:
                errors.append({'scope': 'package_features', 'error': str(exc)})

        # VBA: extracted for every Office container, macro-enabled or legacy.
        # ``macro_present`` is True even when decompression fails, so "no
        # macros" and "macros we could not read" cannot be confused.
        try:
            from core.forensics import extract_vba, macro_summary

            macros = extract_vba(filepath)
            if macros.get('macro_present') or macros.get('extraction_errors'):
                result['macros'] = {
                    **macro_summary(macros),
                    'modules': [
                        {'name': module.get('name'), 'stream': module.get('stream'),
                         'line_count': module.get('line_count'),
                         'md5': module.get('md5'), 'source': module.get('source')}
                        for project in macros.get('projects', [])
                        for module in (project.get('modules') or [])
                    ],
                }
                errors.extend(macros.get('extraction_errors') or [])
                source = str(macros.get('source_text') or '')
                if source:
                    result['macros']['source_text'] = source
                    existing = str(result.get('forensic_text') or '')
                    result['forensic_text'] = (existing + '\n\n' + source).strip()
        except Exception as exc:
            errors.append({'scope': 'macros', 'error': str(exc)})

        if errors:
            result.setdefault('extraction_errors', [])
            result['extraction_errors'] = list(result['extraction_errors']) + errors
        return result

    def _read_unrecognised_ole(self, filepath):
        """Record an OLE compound file that is not a recognised Office document.

        The streams are inventoried (names, sizes, digests of small streams) so
        the artifact is evidence-bearing even though no document parser claims
        it. Its outcome is ``unsupported`` with a reason, never a silent skip.
        """
        result = {
            'filepath': filepath,
            'ole_container': True,
            'streams': [],
            'extraction_errors': [],
        }
        try:
            import hashlib

            import olefile

            if not olefile.isOleFile(filepath):
                return {'error': 'Not an OLE compound file', 'filepath': filepath}
            with olefile.OleFileIO(filepath) as ole:
                for entry in ole.listdir():
                    name = '/'.join(entry)
                    try:
                        size = ole.get_size(entry)
                    except Exception:
                        size = None
                    record = {'name': name, 'size': size}
                    if size is not None and size <= 1024 * 1024:
                        try:
                            with ole.openstream(entry) as handle:
                                data = handle.read()
                            record['md5'] = hashlib.md5(data, usedforsecurity=False).hexdigest()
                            record['sha256'] = hashlib.sha256(data).hexdigest()
                        except Exception as exc:
                            record['error'] = str(exc)
                    result['streams'].append(record)
            result['stream_count'] = len(result['streams'])
        except Exception as exc:
            result['extraction_errors'].append({'scope': 'ole', 'error': str(exc)})
        return self._attach_forensic_detail(result, filepath, '.ole')

    def _iwork_collect_xml_text(self, xml_bytes: bytes) -> str:
        """Pull visible strings from an iWork index.xml payload."""
        import re
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            return ""

        chunks: List[str] = []
        for element in root.iter():
            if element.text and element.text.strip():
                chunks.append(element.text.strip())
            if element.tail and element.tail.strip():
                chunks.append(element.tail.strip())
        if chunks:
            return "\n".join(chunks)

        # Last resort: strip tags from a UTF-8 decode.
        raw = xml_bytes.decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", "\n", raw)

    def _iwork_collect_iwa_text(self, zf) -> str:
        """Best-effort text from modern .iwa protobuf/snappy streams."""
        import re
        import zipfile

        if not isinstance(zf, zipfile.ZipFile):
            return ""

        noise = re.compile(
            r"^(character-style|paragraph-style|shape-|tableCell|headerRow|"
            r"DocumentStylesheet|CalculationEngine|ViewState)[-\d]*$",
            re.I,
        )
        seen = set()
        lines: List[str] = []
        for name in zf.namelist():
            if not name.lower().endswith(".iwa"):
                continue
            try:
                blob = zf.read(name)
            except Exception:
                continue
            for match in re.finditer(rb"[\x09\x0a\x0d\x20-\x7e]{6,}", blob):
                piece = match.group().decode("ascii", errors="replace").strip()
                if len(piece) < 6 or noise.match(piece):
                    continue
                if piece in seen:
                    continue
                seen.add(piece)
                lines.append(piece)
        return "\n".join(lines)

    def _iwork_result_from_text(
        self, filepath: str, text: str, method: str, format_label: str
    ) -> Dict[str, Any]:
        text = (text or "").strip()
        elements = []
        position = 0
        for para in text.splitlines():
            line = para.strip()
            if not line:
                continue
            elements.append(
                {"type": "paragraph", "text": line, "position": position}
            )
            position += 1
        return {
            "filepath": filepath,
            "format": format_label,
            "extraction_method": method,
            "text": text,
            "elements": elements,
            "paragraphs": [e["text"] for e in elements],
            "total_paragraphs": len(elements),
        }

    def _read_iwork_zip_package(
        self, filepath: str, format_label: str, numbers: bool = False
    ) -> Dict[str, Any]:
        import zipfile

        if not os.path.isfile(filepath):
            return {"error": "File not found", "filepath": filepath}

        try:
            with zipfile.ZipFile(filepath) as zf:
                names = zf.namelist()
                lowered = {n.lower(): n for n in names}
                text = ""
                method = None

                index_name = lowered.get("index.xml")
                if index_name:
                    text = self._iwork_collect_xml_text(zf.read(index_name))
                    method = "index.xml"

                if len(text.strip()) < 40 and any(
                    n.lower().endswith(".iwa") for n in names
                ):
                    iwa_text = self._iwork_collect_iwa_text(zf)
                    if len(iwa_text.strip()) > len(text.strip()):
                        text = iwa_text
                        method = "iwa-stream-scan"

                preview_pdf = lowered.get("quicklook/preview.pdf")
                if len(text.strip()) < 40 and preview_pdf:
                    import tempfile

                    from reader_file.readers.read_pdf import PDFFileReader

                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                        tmp.write(zf.read(preview_pdf))
                        tmp_path = tmp.name
                    try:
                        pdf_result = PDFFileReader().read_file(
                            {"path": tmp_path, "extension": "pdf"}
                        )
                        if isinstance(pdf_result, dict) and not pdf_result.get("error"):
                            page_texts = [
                                str(p.get("text") or "")
                                for p in (pdf_result.get("pages") or [])
                                if isinstance(p, dict)
                            ]
                            text = "\n".join(t for t in page_texts if t.strip())
                            if not text.strip():
                                text = str(pdf_result.get("text") or "")
                            method = "quicklook-preview.pdf"
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass

                if len(text.strip()) < 40:
                    target = "ods" if numbers else "docx"
                    converted = self._convert_with_libreoffice(filepath, target)
                    if converted and os.path.isfile(converted):
                        try:
                            if numbers:
                                inner = self.read_ods_file(converted)
                            else:
                                inner = self.read_docx_file(converted)
                            if isinstance(inner, dict) and not inner.get("error"):
                                text = str(inner.get("text") or "")
                                if not text.strip() and inner.get("elements"):
                                    text = "\n".join(
                                        str(e.get("text") or "")
                                        for e in inner["elements"]
                                        if isinstance(e, dict)
                                    )
                                method = f"libreoffice->{target}"
                        finally:
                            try:
                                os.unlink(converted)
                            except OSError:
                                pass

                if not text.strip():
                    return {
                        "error": (
                            "No extractable text from iWork package "
                            "(index.xml / IWA / QuickLook / LibreOffice paths exhausted)"
                        ),
                        "filepath": filepath,
                    }

                return self._iwork_result_from_text(
                    filepath, text, method or "unknown", format_label
                )
        except zipfile.BadZipFile as exc:
            return {"error": f"Not a valid iWork ZIP package: {exc}", "filepath": filepath}
        except Exception as exc:
            return {"error": str(exc), "filepath": filepath}

    def read_pages_file(self, filepath: str) -> Dict[str, Any]:
        return self._read_iwork_zip_package(filepath, "pages", numbers=False)

    def read_numbers_file(self, filepath: str) -> Dict[str, Any]:
        result = self._read_iwork_zip_package(filepath, "numbers", numbers=True)
        if isinstance(result, dict) and not result.get("error"):
            result = self._normalize_spreadsheet_result(result)
        return result

    def _find_libreoffice_executable(self) -> Optional[str]:
        import shutil

        for cmd in ("soffice", "libreoffice"):
            path = shutil.which(cmd)
            if path:
                return path
        if os.name == "nt":
            for candidate in (
                r"C:\Program Files\LibreOffice\program\soffice.exe",
                r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
            ):
                if os.path.isfile(candidate):
                    return candidate
        return None

    def _convert_with_libreoffice(self, filepath: str, target_ext: str) -> Optional[str]:
        """Headless LibreOffice conversion; returns path to converted file or None."""
        import subprocess

        lo = self._find_libreoffice_executable()
        if not lo:
            return None
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                proc = subprocess.run(
                    [
                        lo,
                        "--headless",
                        "--convert-to",
                        target_ext,
                        "--outdir",
                        tmpdir,
                        filepath,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except Exception as exc:
                logger.debug("LibreOffice convert failed to start: %s", exc)
                return None
            if proc.returncode != 0:
                logger.debug(
                    "LibreOffice convert exit %s: %s",
                    proc.returncode,
                    (proc.stderr or proc.stdout or "")[:500],
                )
                return None
            base = os.path.splitext(os.path.basename(filepath))[0]
            out_path = os.path.join(tmpdir, f"{base}.{target_ext}")
            if not os.path.isfile(out_path):
                return None
            # Copy out of the temp dir so the caller can read after context exit.
            persistent = os.path.join(
                tempfile.gettempdir(),
                f"sylth_{os.getpid()}_{base}.{target_ext}",
            )
            try:
                with open(out_path, "rb") as src, open(persistent, "wb") as dst:
                    dst.write(src.read())
                return persistent
            except OSError:
                return None

    def _promote_doc_text_to_elements(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Legacy .doc readers return flat text; storage prefers ``elements`` like DOCX."""
        if not isinstance(result, dict) or result.get("error") or result.get("elements"):
            return result
        elements = []
        position = 0
        paragraphs = result.get("paragraphs")
        if isinstance(paragraphs, list) and paragraphs:
            if isinstance(paragraphs[0], str):
                for idx, para in enumerate(paragraphs):
                    text = str(para)
                    elements.append({
                        "type": "paragraph",
                        "position": position,
                        "text": text,
                        "style": "Normal",
                        "index": idx,
                    })
                    position += 1
            elif isinstance(paragraphs[0], dict):
                for idx, para in enumerate(paragraphs):
                    if not isinstance(para, dict):
                        continue
                    elements.append({
                        "type": "paragraph",
                        "position": position,
                        "text": str(para.get("text") or ""),
                        "style": para.get("style") or "Normal",
                        "index": idx,
                    })
                    position += 1
        elif result.get("text"):
            blocks = str(result["text"]).split("\n\n")
            for idx, block in enumerate(blocks):
                elements.append({
                    "type": "paragraph",
                    "position": position,
                    "text": block.strip(),
                    "style": "Normal",
                    "index": idx,
                })
                position += 1
        if elements:
            result["elements"] = elements
            result["total_paragraphs"] = sum(
                1 for e in elements if e.get("type") == "paragraph" and e.get("text")
            )
        return result

    def _normalize_spreadsheet_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Align XLS/XLSX/XLSB/ODS sheet payloads for storage (Excel-shaped)."""
        if not isinstance(result, dict) or not isinstance(result.get("sheets"), dict):
            return result
        for sheet_name, sheet_data in result["sheets"].items():
            if not isinstance(sheet_data, dict):
                continue
            data = sheet_data.get("data") or []
            sheet_data.setdefault("name", sheet_name)
            nrows = sheet_data.get("nrows")
            ncols = sheet_data.get("ncols")
            max_row = sheet_data.get("max_row") or nrows or len(data)
            max_col = sheet_data.get("max_column") or ncols
            if max_col is None and data:
                max_col = max((len(row) for row in data if isinstance(row, (list, tuple))), default=0)
            sheet_data["max_row"] = max_row
            sheet_data["max_column"] = max_col
            if nrows is None:
                sheet_data["nrows"] = max_row
            if ncols is None:
                sheet_data["ncols"] = max_col
        return result

    def read_doc_file(self, filepath):
        """Independent DOC reader function - tries multiple methods."""
        if not os.path.exists(filepath):
            return {"error": "File not found", "filepath": filepath}

        # Full-resolution path: convert to DOCX and reuse the structured reader
        # (elements, tables, embedded images, forensic pass).
        converted = self._convert_with_libreoffice(filepath, "docx")
        if converted:
            try:
                docx_result = self.read_docx_file(converted)
                if isinstance(docx_result, dict) and not docx_result.get("error"):
                    docx_result["filepath"] = filepath
                    docx_result["method"] = "libreoffice_docx_pipeline"
                    docx_result["source_format"] = "doc"
                    return docx_result
            finally:
                try:
                    os.unlink(converted)
                except OSError:
                    pass
        
        # Try docx2txt
        try:
            import docx2txt
            # if logger:
            #     logger.debug("Attempting to read .doc with docx2txt", file_path=filepath)
            
            text = docx2txt.process(filepath)
            
            result = {
                "filepath": filepath,
                "text": text,
                "method": "docx2txt",
                "total_characters": len(text),
                "total_lines": len(text.splitlines())
            }
            
            paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
            if paragraphs:
                result["paragraphs"] = paragraphs
                result["total_paragraphs"] = len(paragraphs)
            
            return result
        except ImportError:
            logger.debug("docx2txt not available, trying next method", extra={"file_path": filepath})
        except Exception as e:
            logger.debug(f"docx2txt failed: {str(e)}", extra={"file_path": filepath})
        
        # Try antiword
        try:
            import subprocess
            import shutil
            
            antiword_path = shutil.which('antiword')
            if antiword_path:
                # if logger:
                #     logger.debug("Attempting to read .doc with antiword", file_path=filepath)
                
                result = subprocess.run(
                    [antiword_path, filepath],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode == 0:
                    text = result.stdout
                    
                    doc_result = {
                        "filepath": filepath,
                        "text": text,
                        "method": "antiword",
                        "total_characters": len(text),
                        "total_lines": len(text.splitlines())
                    }
                    
                    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                    if paragraphs:
                        doc_result["paragraphs"] = paragraphs
                        doc_result["total_paragraphs"] = len(paragraphs)
                    
                    return doc_result
        except Exception as e:
            logger.debug(f"antiword failed: {str(e)}", extra={"file_path": filepath})
        
        # Try olefile
        try:
            import olefile
            
            if not olefile.isOleFile(filepath):
                return {"error": "File is not a valid OLE2 format .doc file", "filepath": filepath}
            
            # if logger:
            #     logger.debug("Attempting to read .doc with olefile", file_path=filepath)
            
            ole = olefile.OleFileIO(filepath)
            
            if ole.exists('WordDocument'):
                stream = ole.openstream('WordDocument')
                data = stream.read()
                
                text_parts = []
                current_text = []
                for byte in data:
                    if 32 <= byte <= 126 or byte in [9, 10, 13]:
                        current_text.append(chr(byte))
                    else:
                        if len(current_text) > 3:
                            text_parts.append(''.join(current_text))
                        current_text = []
                
                if current_text and len(current_text) > 3:
                    text_parts.append(''.join(current_text))
                
                text = ' '.join(text_parts)
                
                ole.close()
                
                if text.strip():
                    doc_result = {
                        "filepath": filepath,
                        "text": text,
                        "method": "olefile",
                        "total_characters": len(text),
                        "total_lines": len(text.splitlines()),
                        "note": "Basic text extraction - formatting may be lost"
                    }
                    
                    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                    if paragraphs:
                        doc_result["paragraphs"] = paragraphs
                        doc_result["total_paragraphs"] = len(paragraphs)
                    
                    return doc_result
                else:
                    ole.close()
                    return {"error": "Could not extract text from .doc file", "filepath": filepath}
            else:
                ole.close()
                return {"error": "WordDocument stream not found in .doc file", "filepath": filepath}
                
        except ImportError:
            logger.debug("olefile not available, trying next method", extra={"file_path": filepath})
        except Exception as e:
            logger.debug(f"olefile extraction failed: {str(e)}", extra={"file_path": filepath})
        
        # All methods failed
        error_msg = (
            "Could not read .doc file. Please install one of the following:\n"
            "  - textract: pip install textract (requires antiword or LibreOffice)\n"
            "  - python-docx2txt: pip install docx2txt\n"
            "  - olefile: pip install olefile\n"
            "Or install external tools:\n"
            "  - antiword (command-line tool)\n"
            "  - LibreOffice (soffice command)"
        )
        
        # if logger:
        #     logger.warning("All .doc reading methods failed", file_path=filepath)
        
        return {
            "error": error_msg,
            "filepath": filepath
        }


    #: Suffixes openpyxl accepts when it is handed a *path*.  Anything else has
    def _load_workbook_by_content(self, filepath):
        """Open a spreadsheet by its bytes, not by its filename.

        openpyxl refuses a *path* whose extension is not one of its supported
        formats before it looks at the content:

            openpyxl does not support .docx file format, please check you can
            open it with Excel first. Supported formats are:
            .xlsx,.xlsm,.xltx,.xltm

        That is the error a real corpus produced for an attachment named
        ``attachment_00520.docx`` whose bytes are an xlsx workbook - the router
        had already resolved the content-verified type to ``.xlsx`` and sent it
        here, and the reader then failed the file because of its *name*,
        storing it with no text at all.

        The extension check is skipped for file-like objects, so a stream is
        passed whenever the declared name is not a supported spreadsheet
        suffix. Content wins over the name, which is the whole point of
        content-based routing; files whose names are already correct keep the
        exact code path they had before.
        """
        suffix = os.path.splitext(filepath)[1].lower()
        if suffix not in OPENPYXL_PATH_SUFFIXES:
            logger.info(
                "Opening %s as a spreadsheet by content (declared suffix %r is "
                "not a supported openpyxl path suffix)",
                os.path.basename(filepath), suffix or "(none)",
            )
        # Extraction needs cell values, not external-link parts; keep_links=False
        # stops a DDE link with a missing _rels part from failing the whole file.
        return load_spreadsheet_workbook(filepath, data_only=True, keep_links=False)

    def read_xlsx_file(self, filepath):
        """Independent XLSX reader function with image extraction and OCR."""
        try:
            import openpyxl
        except ImportError:
            return {
                "error": "openpyxl not installed. Install with: pip install openpyxl",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            workbook = self._load_workbook_by_content(filepath)
            
            result = {
                "filepath": filepath,
                "sheet_names": workbook.sheetnames,
                "sheets": {},
                "total_sheets": len(workbook.sheetnames),
                "extracted_images": []
            }
            
            cells_read = 0
            for sheet_idx, sheet_name in enumerate(workbook.sheetnames):
                # Yield periodically during sheet processing
                self.yield_periodically(sheet_idx, interval=3, aggressive=False)
                sheet = workbook[sheet_name]
                sheet_data = {
                    "name": sheet_name,
                    "max_row": sheet.max_row,
                    "max_column": sheet.max_column,
                    # A hidden sheet is still part of the workbook, and is
                    # often where a spreadsheet keeps the working it does not
                    # want on screen. It is read like any other sheet; the state
                    # is stored beside the content rather than used to skip it.
                    "state": str(getattr(sheet, "sheet_state", "visible") or "visible"),
                    "hidden": str(getattr(sheet, "sheet_state", "visible")
                                  or "visible") != "visible",
                    "data": []
                }

                sheet_cells = 0
                grid_cells = 0
                padding_cells_trimmed = 0
                empty_rows_skipped = 0
                max_sheet_cells = sheet_cell_cap()
                max_workbook_cells = workbook_cell_cap()
                max_grid_cells = sheet_grid_cap()
                for row_idx, row in enumerate(sheet.iter_rows(values_only=True)):
                    # Yield periodically during row processing
                    if row_idx > 0 and row_idx % 100 == 0:
                        self.yield_cpu_if_needed(aggressive=False)
                    padded = len(row)
                    # Trailing empty cells are padding, not content. A sheet
                    # whose used range spans all 16384 columns used to burn the
                    # whole cell cap on fifteen rows of empty space - the
                    # CVLKRA KYC workbook stopped at 245,760 cells with rows
                    # still to read - so only populated cells are counted and
                    # only they are stored.
                    row = list(row)
                    trimmed = 0
                    while row and (row[-1] is None or row[-1] == ""):
                        row.pop()
                        trimmed += 1
                    padding_cells_trimmed += trimmed
                    if not row:
                        empty_rows_skipped += 1
                        continue
                    grid_cells += padded
                    if grid_cells > max_grid_cells:
                        sheet_data["truncated"] = True
                        sheet_data["truncated_reason"] = (
                            f"sheet grid cap reached: {grid_cells} padded cells "
                            f"> SYLTHARAE_MAX_SHEET_GRID_CELLS ({max_grid_cells})")
                        sheet_data["cells_read"] = sheet_cells
                        sheet_data["rows_read"] = row_idx
                        logger.warning("Sheet %s in %s: %s", sheet_name, filepath,
                                       sheet_data["truncated_reason"])
                        break
                    if (sheet_cells + len(row) > max_sheet_cells
                            or cells_read + len(row) > max_workbook_cells):
                        sheet_data["truncated"] = True
                        if cells_read + len(row) > max_workbook_cells:
                            sheet_data["truncated_reason"] = (
                                f"workbook cell cap reached: {cells_read} + {len(row)} "
                                f"> SYLTHARAE_MAX_WORKBOOK_CELLS ({max_workbook_cells})")
                        else:
                            sheet_data["truncated_reason"] = (
                                f"sheet cell cap reached: {sheet_cells} + {len(row)} "
                                f"> SYLTHARAE_MAX_SHEET_CELLS ({max_sheet_cells})")
                        sheet_data["cells_read"] = sheet_cells
                        sheet_data["rows_read"] = row_idx
                        logger.warning("Sheet %s in %s: %s", sheet_name, filepath,
                                       sheet_data["truncated_reason"])
                        break
                    sheet_data["data"].append(row)
                    sheet_cells += len(row)
                    cells_read += len(row)
                    # Row numbers are the sheet's own (1-based) so a truncation
                    # gap can be located: rows_read counts rows, row_numbers
                    # records which.
                    sheet_data.setdefault("row_numbers", []).append(row_idx + 1)

                if not sheet_data.get("truncated"):
                    sheet_data["cells_read"] = sheet_cells
                # Why a stored character count can fall without content being
                # lost: these were never content. Recorded so a smaller body is
                # a number to check rather than a mystery (testEXCEL_poi-61034
                # is formulas with no cached values - 410 declared rows, no
                # values at all - and stored 4,556 characters of separators).
                if padding_cells_trimmed:
                    sheet_data["padding_cells_trimmed"] = padding_cells_trimmed
                if empty_rows_skipped:
                    sheet_data["empty_rows_skipped"] = empty_rows_skipped
                if len(sheet_data.get("data") or []) == (sheet.max_row or 0):
                    sheet_data.pop("row_numbers", None)
                sheet_data["cells_available"] = (
                    (sheet.max_row or 0) * (sheet.max_column or 0))

                # Cell comments are content: a reviewer's note, a decision, a
                # correction. openpyxl attaches them to cells, so a value-only
                # read of the sheet never sees them and they were stored
                # nowhere. Collected per sheet with their anchor cell so the
                # note keeps its address.
                try:
                    # openpyxl keeps the comments themselves; walking the grid
                    # to find them repeats work proportional to the sheet, on
                    # the one path where the sheet may be too large to walk
                    # again. Measured on testRecordSizeExceeded.xlsx: the value
                    # pass had stored 10,000,085 characters and this second
                    # walk raised MemoryError, which the reader returned as a
                    # total failure - throwing the extracted text away.
                    comments = []
                    # The cells openpyxl has already materialised, not a second
                    # walk over the grid: reading a sheet twice costs a second
                    # pass's memory for text that has already been collected.
                    cells = getattr(sheet, "_cells", None)
                    if isinstance(cells, dict):
                        for cell in list(cells.values()):
                            note = getattr(cell, "comment", None)
                            text = " ".join(str(getattr(note, "text", "") or "").split()) \
                                if note is not None else ""
                            if not text:
                                continue
                            comments.append({
                                "cell": str(getattr(cell, "coordinate", "") or ""),
                                "author": str(getattr(note, "author", "") or ""),
                                "text": text,
                            })
                    else:  # pragma: no cover - read-only workbooks have no cell map
                        for row in sheet.iter_rows():
                            for cell in row:
                                note = getattr(cell, "comment", None)
                                if note is None or not str(getattr(note, "text", "") or "").strip():
                                    continue
                                comments.append({
                                    "cell": cell.coordinate,
                                    "author": str(getattr(note, "author", "") or ""),
                                    "text": " ".join(str(note.text).split()),
                                })
                    if comments:
                        sheet_data["comments"] = comments
                except MemoryError:
                    # A comment inventory is worth less than the cells already
                    # read: say it could not be collected and keep the sheet.
                    sheet_data["comments_error"] = "MemoryError while collecting comments"
                except Exception as comment_error:
                    sheet_data["comments_error"] = str(comment_error)[:200]

                result["sheets"][sheet_name] = sheet_data

            result["cells_read"] = cells_read
            if any((sheet or {}).get("truncated")
                   for sheet in result["sheets"].values()):
                result["truncated"] = True

            hidden_sheets = [name for name, sheet in result["sheets"].items()
                             if (sheet or {}).get("hidden")]
            if hidden_sheets:
                result["hidden_sheets"] = hidden_sheets
            comment_count = sum(len(sheet.get("comments") or [])
                                for sheet in result["sheets"].values())
            if comment_count:
                result["comment_count"] = comment_count
                result["forensic_text"] = "\n".join(
                    f"Comment on {name}!{note['cell']}"
                    + (f" ({note['author']})" if note.get("author") else "")
                    + f": {note['text']}"
                    for name, sheet in result["sheets"].items()
                    for note in (sheet.get("comments") or []))
            
            # Extract and OCR images from the workbook
            try:
                import zipfile
                
                # XLSX files are ZIP archives - extract images from xl/media/ folder
                with zipfile.ZipFile(filepath, 'r') as xlsx_zip:
                    # List all files in the archive
                    file_list = xlsx_zip.namelist()
                    
                    # Find all image files in xl/media/ directory
                    image_files = [f for f in file_list if f.startswith('xl/media/') and 
                                any(f.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'])]
                    
                    # Extract and process each image
                    for image_file in image_files:
                        try:
                            image_bytes = xlsx_zip.read(image_file)
                            image_name = os.path.basename(image_file)
                            
                            # Process image with OCR using existing function
                            # Materialise the embedded image into the document's
                            # extraction directory, then OCR it in place. Writing
                            # it out - rather than to a temp file that is unlinked
                            # immediately - is what lets the router treat the image
                            # as a child object with its own row, hash and lineage.
                            tmp_path = self._materialise_embedded_image(
                                image_bytes, image_name, filepath, result
                            )
                            ocr_result = self._ocr_embedded_image(tmp_path)
                            if ocr_result:
                                ocr_result["name"] = image_name
                                if ocr_result.get("text") or ocr_result.get("error"):
                                    result["extracted_images"].append(ocr_result)
                        except Exception as e:
                            logger.warning(f"Failed to process embedded image {image_file}: {e}")
                            continue
                
                if result["extracted_images"]:
                    logger.info(f"Extracted and processed {len(result['extracted_images'])} images from XLSX")
            
            except Exception as e:
                logger.debug(f"Image extraction from XLSX failed (non-critical): {e}")
                # Don't fail the whole document if image extraction fails
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    def read_xls_file(self, filepath):
        """Independent XLS reader function."""
        try:
            import xlrd
        except ImportError:
            return {
                "error": (
                    "xlrd not installed. Install with: pip install 'xlrd>=2.0' "
                    "(legacy .xls only; use pyxlsb or LibreOffice for .xlsb)"
                ),
                "filepath": filepath
            }

        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}

            workbook = xlrd.open_workbook(filepath)

            result = {
                "filepath": filepath,
                "sheet_names": workbook.sheet_names(),
                "sheets": {},
                "total_sheets": len(workbook.sheet_names())
            }

            for sheet_name in workbook.sheet_names():
                sheet = workbook.sheet_by_name(sheet_name)
                sheet_data = {
                    "name": sheet_name,
                    "nrows": sheet.nrows,
                    "ncols": sheet.ncols,
                    "data": []
                }

                for row_idx in range(sheet.nrows):
                    row_values = sheet.row_values(row_idx)
                    sheet_data["data"].append(row_values)

                result["sheets"][sheet_name] = sheet_data

            return self._normalize_spreadsheet_result(result)
        except Exception as e:
            return {"error": str(e), "filepath": filepath}

    def read_xlsb_file(self, filepath):
        """Excel binary workbook (.xlsb) — full sheet grid via pyxlsb or LO→XLSX."""
        if not os.path.exists(filepath):
            return {"error": "File not found", "filepath": filepath}

        try:
            import pyxlsb
        except ImportError:
            pyxlsb = None  # type: ignore

        pyxlsb_error = None
        if pyxlsb is not None:
            try:
                result = {
                    "filepath": filepath,
                    "sheet_names": [],
                    "sheets": {},
                    "total_sheets": 0,
                    "method": "pyxlsb",
                }
                with pyxlsb.open_workbook(filepath) as workbook:
                    for sheet_name in workbook.sheets:
                        rows = []
                        with workbook.get_sheet(sheet_name) as sheet:
                            for row in sheet.rows():
                                rows.append([cell.v for cell in row])
                        result["sheet_names"].append(sheet_name)
                        result["sheets"][sheet_name] = {
                            "name": sheet_name,
                            "data": rows,
                            "nrows": len(rows),
                            "ncols": max((len(r) for r in rows), default=0),
                        }
                    result["total_sheets"] = len(result["sheet_names"])
                return self._normalize_spreadsheet_result(result)
            except Exception as exc:
                # Kept as a warning with the real cause: pyxlsb is installed on
                # this machine, so "install pyxlsb" would send the user the wrong way.
                pyxlsb_error = f"{type(exc).__name__}: {exc}"
                logger.warning("pyxlsb could not read %s: %s", filepath, pyxlsb_error)

        converted = self._convert_with_libreoffice(filepath, "xlsx")
        if converted:
            try:
                xlsx_result = self.read_xlsx_file(converted)
                if isinstance(xlsx_result, dict) and not xlsx_result.get("error"):
                    xlsx_result["filepath"] = filepath
                    xlsx_result["method"] = "libreoffice_xlsx_pipeline"
                    xlsx_result["source_format"] = "xlsb"
                    return self._normalize_spreadsheet_result(xlsx_result)
            finally:
                try:
                    os.unlink(converted)
                except OSError:
                    pass

        if pyxlsb is None:
            message = (
                "Could not read .xlsb. Install pyxlsb (pip install pyxlsb) "
                "or LibreOffice (soffice) for conversion."
            )
        elif pyxlsb_error:
            message = (
                f"Could not read .xlsb: pyxlsb failed ({pyxlsb_error}) and no "
                "LibreOffice conversion was available. Install LibreOffice "
                "(soffice) to read this workbook."
            )
        else:
            message = (
                "Could not read .xlsb: LibreOffice conversion failed. Check that "
                "soffice runs headless on this machine."
            )
        return {"error": message, "filepath": filepath}


    def read_csv_file(self, filepath, delimiter=',', encoding='utf-8'):
        """Independent CSV reader function.

        Produces the structured shape (headers / rows / row_count /
        column_count) that ``pipeline.storage_pipeline`` expects for tabular
        content.

        ROUTE-01: ``.csv`` now routes here instead of the plain-text reader,
        so a row cap is applied - a list of lists costs several times the
        file size in Python objects, and an unbounded read would turn a very
        large CSV into a memory-exhaustion failure. Rows beyond
        ``MAX_CSV_ROWS`` are counted but not retained, and the truncation is
        recorded in the result rather than hidden.
        """
        try:
            import csv

            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}

            result = {
                "filepath": filepath,
                "delimiter": delimiter,
                "headers": [],
                "rows": []
            }

            encodings = [encoding, 'utf-8', 'latin-1', 'cp1252']

            for enc in encodings:
                try:
                    rows_seen = 0
                    truncated = False
                    with open(filepath, 'r', encoding=enc, newline='') as file:
                        reader = csv.reader(file, delimiter=delimiter)

                        try:
                            result["headers"] = next(reader)
                        except StopIteration:
                            return {"error": "Empty file", "filepath": filepath}

                        for row in reader:
                            rows_seen += 1
                            if rows_seen <= MAX_CSV_ROWS:
                                result["rows"].append(row)
                            else:
                                truncated = True

                    result["encoding_used"] = enc
                    result["row_count"] = rows_seen
                    result["rows_stored"] = len(result["rows"])
                    result["truncated"] = truncated
                    if truncated:
                        result["truncation_note"] = (
                            f"Only the first {MAX_CSV_ROWS} data rows were "
                            f"retained; the file holds {rows_seen}."
                        )
                        logger.warning(
                            "CSV %s truncated to %d of %d rows",
                            filepath, MAX_CSV_ROWS, rows_seen,
                        )
                    break
                except UnicodeDecodeError:
                    if enc == encodings[-1]:
                        raise
                    continue

            result.setdefault("row_count", len(result["rows"]))
            result.setdefault("rows_stored", len(result["rows"]))
            result.setdefault("truncated", False)
            result["column_count"] = len(result["headers"])

            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    def extract_images_from_pptx(self, filepath: str):
        """Extract all images from PPTX and perform OCR."""
        extracted_images = []
        
        try:
            import zipfile
            # PPTX files are ZIP archives
            with zipfile.ZipFile(filepath, 'r') as pptx_zip:
                file_list = pptx_zip.namelist()
                
                # Find all image files in ppt/media/
                image_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp']
                image_files = [f for f in file_list if f.startswith('ppt/media/') and 
                            any(f.lower().endswith(ext) for ext in image_extensions)]
                
                logger.info(f"Found {len(image_files)} images in PPTX")
                
                # Process each image
                for image_file in image_files:
                    try:
                        image_bytes = pptx_zip.read(image_file)
                        image_name = os.path.basename(image_file)
                        
                        # Try to perform OCR if function is available
                        try:
                            
                            # Materialise the embedded image into the document's
                            # extraction directory, then OCR it in place. Writing
                            # it out - rather than to a temp file that is unlinked
                            # immediately - is what lets the router treat the image
                            # as a child object with its own row, hash and lineage.
                            # This helper returns a bare list rather than a
                            # result dict, so nothing is stamped here; the caller
                            # records extraction_path once the list is attached.
                            tmp_path = self._materialise_embedded_image(
                                image_bytes, image_name, filepath
                            )
                            ocr_result = self._ocr_embedded_image(tmp_path)
                            if ocr_result:
                                ocr_result["name"] = image_name
                                if ocr_result.get("text") or ocr_result.get("error"):
                                    extracted_images.append(ocr_result)
                        except ImportError:
                            # OCR not available, just record image info
                            extracted_images.append({
                                "name": image_name,
                                "size_bytes": len(image_bytes),
                                "note": "OCR not available"
                            })
                    
                    except Exception as e:
                        logger.warning(f"Failed to process embedded image {image_file}: {e}")
                        continue
            
            if extracted_images:
                logger.info(f"Successfully extracted {len(extracted_images)} images from PPTX")
        
        except Exception as e:
            logger.debug(f"Image extraction from PPTX failed: {e}")
        
        return extracted_images


    def read_pptx_file(self, filepath):
        """
        Comprehensive PPTX reader with full content extraction.
        Extracts text, tables, images, charts, SmartArt, notes, and comments.
        Preserves spatial order of elements.
        """
        try:
            from pptx import Presentation
            from pptx.enum.shapes import MSO_SHAPE_TYPE
        except ImportError:
            return {
                "error": "python-pptx not installed. Install with: pip install python-pptx",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            openable = _pptx_openable_copy(filepath)
            try:
                prs = Presentation(openable or filepath)
            finally:
                if openable:
                    try:
                        os.unlink(openable)
                    except OSError:
                        pass

            def flatten_shapes(shapes, group: str = None):
                """Shapes with groups expanded, in document order.

                ``slide.shapes`` yields a group as one object; its children are
                reachable only through ``group.shapes``. A deck that arranges
                its content in groups therefore read as empty, and the whole
                slide was stored as no text at all - testPPT_groups.pptx scored
                0.33 because "WordArt1", "box1Text" and the link text live
                inside a group. Each expanded shape carries the name of the
                group it came from, so the result keeps the nesting.
                """
                for shape in shapes:
                    label = group
                    try:
                        is_group = (getattr(shape, 'shape_type', None) == MSO_SHAPE_TYPE.GROUP
                                    or str(type(shape).__name__) == 'GroupShape')
                        children = getattr(shape, 'shapes', None) if is_group else None
                    except Exception:
                        children = None
                    if children is not None:
                        try:
                            nested = group or str(getattr(shape, 'name', '') or 'group')
                            yield from flatten_shapes(children, nested)
                            continue
                        except Exception as exc:
                            logger.debug("Failed to expand group %s: %s",
                                         getattr(shape, "name", "?"), exc)
                    yield shape, label

            total_slides = len(prs.slides)
            
            result = {
                "filepath": filepath,
                "slides": [],
                "total_slides": total_slides,
                "extracted_images": [],
                "presentation_notes": []
            }
            
            # Log warning for very large presentations
            if total_slides > 100:
                logger.warning(f"Large presentation detected: {total_slides} slides. Processing may take longer.")
            
            # Process slides with error handling for each slide
            slides_processed = 0
            slides_failed = 0
            
            for slide_idx, slide in enumerate(prs.slides):
                # Yield periodically to prevent CPU spikes during slide processing
                self.yield_periodically(slide_idx, interval=5, aggressive=True)
                try:
                    slide_data = {
                        "slide_number": slide_idx + 1,
                        "elements": [],
                        "texts": [],  # All text content (backward compatibility)
                        "notes": None,  # Speaker notes
                        "comments": []  # Slide comments
                    }
                    
                    # Extract speaker notes. The notes page is a slide of its
                    # own with several placeholders - the body placeholder is
                    # what notes_text_frame returns, but a notes page also
                    # carries header, footer and date placeholders, and a
                    # template can put real text in them. Walking every shape
                    # on the notes page is what stores the page, not one third
                    # of it.
                    if slide.has_notes_slide:
                        try:
                            notes_slide = slide.notes_slide
                            notes_parts: List[str] = []
                            for notes_shape, _group in flatten_shapes(notes_slide.shapes):
                                text = ""
                                try:
                                    if getattr(notes_shape, 'has_text_frame', False):
                                        text = notes_shape.text_frame.text.strip()
                                    elif getattr(notes_shape, 'has_table', False):
                                        text = "\n".join(
                                            " | ".join(cell.text.strip() for cell in row.cells)
                                            for row in notes_shape.table.rows)
                                except Exception:
                                    text = ""
                                if text and text not in notes_parts:
                                    notes_parts.append(text)
                            if notes_parts:
                                notes_text = "\n".join(notes_parts)
                                slide_data["notes"] = notes_text
                                result["presentation_notes"].append({
                                    "slide_number": slide_idx + 1,
                                    "notes": notes_text
                                })
                        except Exception as e:
                            logger.debug(f"Failed to extract notes from slide {slide_idx + 1}: {e}")
                    
                    # Collect all shapes with their positions
                    shapes_with_positions = []
                    
                    for shape_idx, (shape, group_name) in enumerate(flatten_shapes(slide.shapes)):
                        # Yield periodically during shape processing
                        if shape_idx > 0 and shape_idx % 10 == 0:
                            self.yield_cpu_if_needed(aggressive=True)
                        # Yield periodically during shape processing
                        if shape_idx > 0 and shape_idx % 10 == 0:
                            self.yield_cpu_if_needed(aggressive=True)
                        # Get spatial position
                        top = getattr(shape, 'top', 0)
                        left = getattr(shape, 'left', 0)
                        width = getattr(shape, 'width', 0)
                        height = getattr(shape, 'height', 0)
                        
                        element_data = {
                            "type": None,
                            "position": shape_idx,
                            "group": group_name,
                            "spatial_order": (top, left),
                            "top": top,
                            "left": left,
                            "width": width,
                            "height": height
                        }
                        
                        # Determine shape type and extract content
                        shape_type = getattr(shape, 'shape_type', None)
                        
                        # 0. SKIP SHAPES THAT DON'T HAVE TEXT CONTENT
                        # Connector shapes (lines, arrows) don't have text
                        if shape_type == MSO_SHAPE_TYPE.LINE or shape_type == MSO_SHAPE_TYPE.FREEFORM:
                            # Skip connector shapes - they're visual elements without text
                            continue
                        
                        # GraphicFrame objects (SmartArt, embedded graphics) - handle
                        # separately, EXCEPT tables: a table shape IS a GraphicFrame
                        # and must fall through to the structured table extraction
                        # below (previously tables were silently skipped here).
                        if (shape_type == MSO_SHAPE_TYPE.GROUP or
                                str(type(shape).__name__) == 'GraphicFrame') and \
                                not getattr(shape, 'has_table', False):
                            # Try to extract text from GraphicFrame if it has text_frame
                            try:
                                if hasattr(shape, 'has_text_frame') and shape.has_text_frame:
                                    text_frame = shape.text_frame
                                    text_content = []
                                    for paragraph in text_frame.paragraphs:
                                        para_text = paragraph.text.strip()
                                        if para_text:
                                            text_content.append(para_text)
                                    if text_content:
                                        element_data["type"] = "graphic_frame"
                                        element_data["text"] = "\n".join(text_content)
                                        slide_data["texts"].extend(text_content)
                                        shapes_with_positions.append(element_data)
                            except Exception as e:
                                logger.debug(f"GraphicFrame extraction failed for shape {shape_idx}: {e}")
                            # Continue to next shape regardless
                            continue
                        
                        # 1. TABLES - Check first as they're most structured
                        if shape.has_table:
                            try:
                                table = shape.table
                                element_data["type"] = "table"
                                element_data["table_number"] = len([e for e in shapes_with_positions if e.get("type") == "table"]) + 1
                                element_data["rows"] = []
                                element_data["row_count"] = len(table.rows)
                                element_data["col_count"] = len(table.columns) if table.rows else 0
                                
                                for row_idx, row in enumerate(table.rows):
                                    row_data = []
                                    for cell_idx, cell in enumerate(row.cells):
                                        cell_text = cell.text.strip() if cell.text else ""
                                        row_data.append(cell_text)
                                        if cell_text:
                                            slide_data["texts"].append(cell_text)
                                    element_data["rows"].append(row_data)
                                
                                shapes_with_positions.append(element_data)
                                continue
                            except (AttributeError, ValueError) as e:
                                logger.debug(f"Table extraction failed for shape {shape_idx}: {e}")
                        
                        # 2. CHARTS
                        if shape.has_chart:
                            try:
                                chart = shape.chart
                                element_data["type"] = "chart"
                                element_data["chart_type"] = str(chart.chart_type)
                                element_data["chart_title"] = chart.chart_title.text_frame.text if chart.has_title else None
                                
                                # Extract chart data
                                element_data["chart_data"] = {
                                    "categories": [],
                                    "series": []
                                }
                                
                                # Extract series data
                                for series in chart.series:
                                    series_data = {
                                        "name": series.name,
                                        "values": [v for v in series.values] if hasattr(series, 'values') else []
                                    }
                                    element_data["chart_data"]["series"].append(series_data)
                                
                                if element_data["chart_title"]:
                                    slide_data["texts"].append(element_data["chart_title"])
                                
                                shapes_with_positions.append(element_data)
                                continue
                            except Exception as e:
                                logger.debug(f"Chart extraction failed for shape {shape_idx}: {e}")
                            
                        # 3. IMAGES/PICTURES
                        if shape_type == MSO_SHAPE_TYPE.PICTURE or hasattr(shape, 'image'):
                            try:
                                element_data["type"] = "image"
                                element_data["image_name"] = f"slide_{slide_idx + 1}_shape_{shape_idx + 1}"
                                
                                # Try to get image file extension/format
                                if hasattr(shape, 'image'):
                                    image = shape.image
                                    element_data["image_format"] = image.content_type
                                    element_data["image_filename"] = getattr(image, 'filename', None)
                                
                                shapes_with_positions.append(element_data)
                                continue
                            except Exception as e:
                                logger.debug(f"Image extraction failed for shape {shape_idx}: {e}")
                        
                        # 4. GROUP SHAPES - Recursively extract content
                        if shape_type == MSO_SHAPE_TYPE.GROUP:
                            try:
                                element_data["type"] = "group"
                                element_data["group_elements"] = []
                                
                                for grouped_shape in shape.shapes:
                                    if hasattr(grouped_shape, 'text') and grouped_shape.text.strip():
                                        element_data["group_elements"].append({
                                            "type": "text",
                                            "text": grouped_shape.text.strip()
                                        })
                                        slide_data["texts"].append(grouped_shape.text.strip())
                                
                                if element_data["group_elements"]:
                                    shapes_with_positions.append(element_data)
                                continue
                            except Exception as e:
                                logger.debug(f"Group shape extraction failed for shape {shape_idx}: {e}")
                            
                        # 5. TEXT FRAMES - Most common text content
                        if shape.has_text_frame:
                            try:
                                text_frame = shape.text_frame
                                text_content = []
                                
                                for paragraph in text_frame.paragraphs:
                                    para_text = paragraph.text.strip()
                                    if para_text:
                                        text_content.append(para_text)
                                
                                if text_content:
                                    element_data["type"] = "text_frame"
                                    element_data["text"] = "\n".join(text_content)
                                    element_data["paragraph_count"] = len(text_content)
                                    slide_data["texts"].extend(text_content)
                                    shapes_with_positions.append(element_data)
                                continue
                            except Exception as e:
                                logger.debug(f"Text frame extraction failed for shape {shape_idx}: {e}")
                        
                        # 6. SIMPLE TEXT (fallback - check with safe attribute access)
                        try:
                            if hasattr(shape, "text"):
                                text_value = getattr(shape, "text", None)
                                if text_value and str(text_value).strip():
                                    element_data["type"] = "text"
                                    element_data["text"] = str(text_value).strip()
                                    slide_data["texts"].append(str(text_value).strip())
                                    shapes_with_positions.append(element_data)
                                    continue
                        except (AttributeError, TypeError) as e:
                            logger.debug(f"Text extraction failed for shape {shape_idx}: {e}")
                            # Continue to next check
                        
                        # 7. PLACEHOLDER SHAPES (titles, content placeholders)
                        if hasattr(shape, 'placeholder_format'):
                            try:
                                placeholder = shape.placeholder_format
                                element_data["type"] = "placeholder"
                                element_data["placeholder_type"] = str(placeholder.type)
                                
                                if shape.has_text_frame and shape.text.strip():
                                    element_data["text"] = shape.text.strip()
                                    slide_data["texts"].append(shape.text.strip())
                                    shapes_with_positions.append(element_data)
                            except Exception as e:
                                logger.debug(f"Placeholder extraction failed for shape {shape_idx}: {e}")
                        
                    # Sort elements by spatial position (top-to-bottom, left-to-right)
                    shapes_with_positions.sort(key=lambda x: (x["spatial_order"][0], x["spatial_order"][1]))
                    
                    # Update position indices after sorting
                    for idx, element in enumerate(shapes_with_positions):
                        element["position"] = idx
                    
                    slide_data["elements"] = shapes_with_positions
                    result["slides"].append(slide_data)
                    slides_processed += 1
                    
                    # Log progress for large presentations
                    if total_slides > 50 and (slide_idx + 1) % 10 == 0:
                        logger.info(f"Processing presentation: {slides_processed}/{total_slides} slides completed...")
                    
                except Exception as slide_error:
                    # Log error but continue processing other slides
                    slides_failed += 1
                    logger.warning(
                        f"Failed to process slide {slide_idx + 1}/{total_slides}: {slide_error}. "
                        f"Continuing with remaining slides..."
                    )
                    # Add error slide data to preserve slide count
                    result["slides"].append({
                        "slide_number": slide_idx + 1,
                        "elements": [],
                        "texts": [],
                        "notes": None,
                        "comments": [],
                        "error": str(slide_error)[:200]  # Truncate long error messages
                    })
                    continue
            
            # Log summary
            if slides_failed > 0:
                logger.warning(
                    f"Presentation processing complete: {slides_processed}/{total_slides} slides processed successfully, "
                    f"{slides_failed} slides failed"
                )
            elif total_slides > 50:
                logger.info(f"Presentation processing complete: {slides_processed}/{total_slides} slides processed successfully")
            
            # Cover pass: whatever the shape walk did not reach in the slide,
            # notes-page and notes-master parts is still text of this deck.
            try:
                result["xml_backfill"] = _pptx_cover_remaining(result["slides"], filepath)
                if result["xml_backfill"].get("forensic_text"):
                    existing = str(result.get("forensic_text") or "")
                    result["forensic_text"] = "\n".join(
                        part for part in (existing, result["xml_backfill"]["forensic_text"])
                        if part)
            except Exception as backfill_error:  # never fail a read over the guarantee
                logger.debug("PPTX cover pass failed for %s: %s", filepath, backfill_error)
                result["xml_backfill"] = {"error": str(backfill_error)[:200], "added": 0}

            # Extract and OCR images from the PPTX archive (with error handling)
            try:
                result["extracted_images"] = self.extract_images_from_pptx(filepath)
                if result["extracted_images"]:
                    # The images were materialised during extraction; publish the
                    # directory so the router processes them as child objects.
                    result["extraction_path"] = self._embedded_extraction_dir(filepath)
            except Exception as img_error:
                logger.warning(f"Failed to extract images from PPTX: {img_error}. Continuing without images.")
                result["extracted_images"] = []
            
            return result
        
        except Exception as e:
            logger.error(f"Failed to read PPTX file: {e}")
            return {"error": str(e), "filepath": filepath}


    _PPT_OLE_NOISE_RE = None

    @classmethod
    def _ppt_ole_noise_pattern(cls):
        import re

        if cls._PPT_OLE_NOISE_RE is None:
            cls._PPT_OLE_NOISE_RE = re.compile(
                r"^(?:___PPT\d+|"
                r"Times New Roman|Times|Arial|Calibri|Courier|Verdana|"
                r"Blank Presentation|"
                r"Click to edit Master|Click to edit subtitle|"
                r"Second level|Third level|Fourth level|Fifth level|"
                r"\[Content_Types\]\.xml|_rels/\.rels|\.xmlPK)$",
                re.I,
            )
        return cls._PPT_OLE_NOISE_RE

    def _strings_from_ppt_ole_stream(self, data: bytes) -> List[str]:
        """Pull candidate slide strings from a PowerPoint Document stream."""
        import re

        found: List[str] = []
        seen = set()
        noise = self._ppt_ole_noise_pattern()

        i = 0
        while i + 4 < len(data):
            if data[i + 1] == 0 and 32 <= data[i] < 127:
                j = i
                chars: List[str] = []
                while j + 1 < len(data) and data[j + 1] == 0 and 32 <= data[j] < 127:
                    chars.append(chr(data[j]))
                    j += 2
                if len(chars) >= 4:
                    piece = "".join(chars).strip()
                    if piece and piece not in seen and not noise.match(piece):
                        seen.add(piece)
                        found.append(piece)
                i = j
            else:
                i += 1

        for match in re.finditer(rb"[\x20-\x7e]{6,}", data):
            piece = match.group().decode("ascii", errors="replace").strip()
            if not piece or piece in seen or noise.match(piece):
                continue
            if piece.endswith("PK") and ".xml" in piece:
                continue
            seen.add(piece)
            found.append(piece)

        return found

    def _read_legacy_ppt_via_ole(self, filepath: str) -> Optional[Dict[str, Any]]:
        """Best-effort text from binary PowerPoint 97-2003 (no LibreOffice)."""
        try:
            import olefile
        except ImportError:
            return None

        if not olefile.isOleFile(filepath):
            return None

        try:
            ole = olefile.OleFileIO(filepath)
        except Exception as exc:
            logger.debug("OLE open failed for %s: %s", filepath, exc)
            return None

        try:
            data = None
            for entry in ole.listdir():
                name = "/".join(entry)
                if name in ("PowerPoint Document", "PP97_DUALSTORAGE"):
                    data = ole.openstream(entry).read()
                    break
            if not data:
                return None
        except Exception as exc:
            logger.debug("PowerPoint stream read failed for %s: %s", filepath, exc)
            return None
        finally:
            try:
                ole.close()
            except Exception:
                pass

        lines = self._strings_from_ppt_ole_stream(data)
        if not lines:
            return None

        elements = [
            {"type": "text", "text": line, "position": idx}
            for idx, line in enumerate(lines)
        ]
        slide = {
            "slide_number": 1,
            "elements": elements,
            "texts": lines,
        }
        return {
            "filepath": filepath,
            "slides": [slide],
            "total_slides": 1,
            "method": "ole_text_extraction",
            "source_format": "ppt",
        }

    def _convert_ppt_via_powerpoint_com(self, filepath: str) -> Optional[str]:
        """Windows + Microsoft PowerPoint: export to OOXML for structured read."""
        if os.name != "nt":
            return None
        try:
            import win32com.client  # type: ignore
        except ImportError:
            return None

        out_path = os.path.join(
            tempfile.gettempdir(),
            f"sylth_{os.getpid()}_{os.path.splitext(os.path.basename(filepath))[0]}.pptx",
        )
        app = None
        presentation = None
        try:
            app = win32com.client.Dispatch("PowerPoint.Application")
            app.Visible = False
            presentation = app.Presentations.Open(
                os.path.abspath(filepath), WithWindow=False, ReadOnly=True
            )
            # ppSaveAsOpenXMLPresentation = 24
            presentation.SaveAs(out_path, 24)
            if os.path.isfile(out_path):
                return out_path
        except Exception as exc:
            logger.debug("PowerPoint COM conversion failed for %s: %s", filepath, exc)
        finally:
            if presentation is not None:
                try:
                    presentation.Close()
                except Exception:
                    pass
            if app is not None:
                try:
                    app.Quit()
                except Exception:
                    pass
        try:
            if os.path.isfile(out_path):
                os.unlink(out_path)
        except OSError:
            pass
        return None

    def read_ppt_file(self, filepath):
        """
        Read legacy .ppt / .pot / .pps files.

        Order: LibreOffice → pptx pipeline, then PowerPoint COM (Windows),
        then OLE stream text extraction (always available when olefile can
        open the compound file).
        """
        if not os.path.exists(filepath):
            return {"error": "File not found", "filepath": filepath}

        converted = self._convert_with_libreoffice(filepath, "pptx")
        if converted and os.path.isfile(converted):
            try:
                pptx_result = self.read_pptx_file(converted)
                if isinstance(pptx_result, dict) and not pptx_result.get("error"):
                    pptx_result["filepath"] = filepath
                    pptx_result["method"] = "libreoffice_pptx_pipeline"
                    pptx_result["source_format"] = "ppt"
                    return pptx_result
            finally:
                try:
                    os.unlink(converted)
                except OSError:
                    pass

        com_pptx = self._convert_ppt_via_powerpoint_com(filepath)
        if com_pptx and os.path.isfile(com_pptx):
            try:
                pptx_result = self.read_pptx_file(com_pptx)
                if isinstance(pptx_result, dict) and not pptx_result.get("error"):
                    pptx_result["filepath"] = filepath
                    pptx_result["method"] = "powerpoint_com_pptx_pipeline"
                    pptx_result["source_format"] = "ppt"
                    return pptx_result
            finally:
                try:
                    os.unlink(com_pptx)
                except OSError:
                    pass

        ole_result = self._read_legacy_ppt_via_ole(filepath)
        if ole_result:
            return ole_result

        return {
            "error": (
                "Could not read legacy PowerPoint file. Install LibreOffice "
                "(soffice), use Microsoft PowerPoint on Windows, or supply a "
                "valid OLE .ppt compound file."
            ),
            "filepath": filepath,
        }


    def _read_odt_from_content_xml(self, filepath: str) -> Optional[Dict[str, Any]]:
        """Read an ODT by walking ``content.xml`` directly.

        Used when odfpy refuses the package - LibreOffice writes MathML
        external DTD references into some documents and odfpy's defusedxml
        loader refuses them. The previous version of this fallback emitted
        every ``text:p``, ``text:h`` *and* ``text:span`` it found, joined each
        one's descendants with ``"".join(itertext())`` and dropped the rest:
        text was duplicated (a span and its parent paragraph both became
        paragraphs), the order of block content was lost, and - because no
        separator was written between sibling elements - adjacent fields were
        welded into single tokens (``dc:creator`` "Bart" + ``dc:date``
        "2009-10-05T19:10:26" became ``Hanssens2009-10-05T19:10:26``). The
        completeness oracle tokenises the same XML and sees two words where the
        reader stored one.

        What is recovered here, explicitly:
        * ``text:p`` and ``text:h`` as blocks, in document order;
        * inline runs joined by the ODF rules (``text:s`` -> spaces,
          ``text:tab`` -> tab, ``text:line-break`` -> newline, styled
          ``text:span``/``text:a`` continue the surrounding word);
        * structural boundaries get a space, because an annotation, a footnote
          marker or a changed-region is a different field, not a syllable;
        * comments (``office:annotation``), notes (``text:note``) and tracked
          changes (``text:deletion`` / ``text:insertion``) as their own records
          tagged with a location, so they are stored instead of silently
          discarded;
        * tables as rows.
        """
        import zipfile
        import xml.etree.ElementTree as ET

        try:
            with zipfile.ZipFile(filepath) as zf:
                if "content.xml" not in zf.namelist():
                    return None
                root = ET.fromstring(zf.read("content.xml"))
        except Exception:
            return None

        body = None
        for element in root.iter():
            if _et_local(element.tag) == "body":
                for child in element:
                    body = child
                    break
                break
        body = body if body is not None else root
        parents = _et_ancestor_map(body)

        def has_ancestor(element, names) -> bool:
            current = parents.get(id(element))
            while current is not None:
                if _et_local(current.tag) in names:
                    return True
                current = parents.get(id(current))
            return False

        paragraphs: List[Dict[str, Any]] = []
        tables: List[Dict[str, Any]] = []
        # De-duplication is by element, not by text. Two identical paragraphs
        # are two paragraphs - a résumé repeats a placeholder, a redline repeats
        # a change - and collapsing them by text dropped real content. What the
        # set is for is the nesting case: a paragraph inside a paragraph (a text
        # box, a nested list item) is already part of its ancestor's text, so
        # its element id is marked when the ancestor is emitted.
        emitted: set = set()

        def record(text: str, style: str, location: str, kind: str = "paragraph"):
            text = _collapse(text)
            if not text:
                return
            paragraphs.append({"text": text, "style": style, "location": location,
                               "kind": kind})

        def mark_descendants(element) -> None:
            for child in element.iter():
                if child is element:
                    continue
                if _et_local(child.tag) in ("p", "h"):
                    emitted.add(id(child))

        def add_block(element, location: str, kind: str = "paragraph"):
            if id(element) in emitted:
                return
            style = (element.get(_ODF_NS["text"] + "style-name")
                     or ("Heading" if _et_local(element.tag) == "h" else "Normal"))
            mark_descendants(element)
            record(_odf_block_text(element), style, location, kind)

        for element in body.iter():
            tag = _et_local(element.tag)
            if tag in ("p", "h"):
                if has_ancestor(element, ("annotation", "note", "changed-region")):
                    continue  # stored as comment / note / tracked-change records
                if has_ancestor(element, ("table-cell",)):
                    continue  # stored as table rows below
                add_block(element, "content.xml", "paragraph")
            elif tag == "table":
                for row_index, row in enumerate(
                        [r for r in element.iter() if _et_local(r.tag) == "table-row"]):
                    cells = [_collapse(_odf_block_text(cell))
                             for cell in row if _et_local(cell.tag) == "table-cell"]
                    if any(cells):
                        tables.append({"table_number": len(tables) + 1,
                                       "rows": [cells]})
            # Annotations are emitted once, below, by _apply_odf_annotations,
            # which also keeps the timestamp and detaches the note from the
            # sentence it is anchored to. Recording them here as well produced
            # two comment paragraphs for one comment.
            elif tag == "note":
                note_class = element.get(_ODF_NS["text"] + "note-class") or "footnote"
                for para in element.iter():
                    if _et_local(para.tag) in ("p", "h"):
                        record(_odf_block_text(para), note_class.capitalize(),
                               f"text:{note_class}", note_class)
            # Tracked changes are emitted once, below, by the shared extractor,
            # so the fallback and the odfpy path cannot disagree about them.

        _apply_odf_annotations(
            {"paragraphs": paragraphs, "tables": tables}, filepath)

        changes = _odf_tracked_changes(filepath)
        for change in changes:
            record(_odf_change_line(change), "Tracked change",
                   change["location"], "tracked_change")

        if not paragraphs and not tables:
            return None
        result = {
            "filepath": filepath,
            "paragraphs": paragraphs,
            "tables": tables,
            "total_paragraphs": len(paragraphs),
            "total_tables": len(tables),
            "method": "odt_content_xml",
        }
        if changes:
            result["tracked_changes"] = changes
        return result

    def read_odt_file(self, filepath):
        """Independent ODT (OpenDocument Text) reader function."""
        try:
            _odf_mod_3 = _odf("odf.opendocument")
            load = _odf_mod_3.load
            _odf_mod_4 = _odf("odf.text")
            P = _odf_mod_4.P
            _odf_mod_5 = _odf("odf.table")
            Table = _odf_mod_5.Table
            TableRow = _odf_mod_5.TableRow
            TableCell = _odf_mod_5.TableCell
            _odf_mod_6 = _odf("odf.teletype")
            extractText = _odf_mod_6.extractText
        except ImportError:
            return {
                "error": "odfpy not installed. Install with: pip install odfpy",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}

            encrypted = _odf_encryption_report(filepath)
            if encrypted:
                encrypted["filepath"] = filepath
                return encrypted

            doc = None
            try:
                doc = load(filepath)
            except Exception as load_exc:
                logger.debug("odfpy load failed for %s: %s", filepath, load_exc)
                fallback = self._read_odt_from_content_xml(filepath)
                if fallback:
                    return fallback
                raise
            
            result = {
                "filepath": filepath,
                "paragraphs": [],
                "tables": [],
                "total_paragraphs": 0,
                "total_tables": 0,
                "extracted_images": []
            }
            
            # Extract paragraphs (child nodes are often elements, not Text nodes)
            for paragraph in doc.getElementsByType(P):
                text = extractText(paragraph).strip()
                if text:
                    result["paragraphs"].append({
                        "text": text,
                        "style": paragraph.getAttribute("stylename") or "Normal"
                    })

            # Tracked changes. odfpy happily loads a document full of redlines
            # and never mentions them: getElementsByType(P) returns the body
            # paragraphs, and office:change-info is not one. The same package
            # handed to the content.xml fallback kept its authors, so whether
            # a change could be attributed depended on which parser accepted
            # the file. Both paths now use the shared extractor.
            changes = _odf_tracked_changes(filepath)
            if changes:
                for change in changes:
                    result["paragraphs"].append({
                        "text": _odf_change_line(change),
                        "style": "Tracked change",
                        "location": change["location"],
                        "kind": "tracked_change",
                    })
                result["tracked_changes"] = changes

            result["total_paragraphs"] = len(result["paragraphs"])
            
            # Extract tables
            for table_idx, table in enumerate(doc.getElementsByType(Table)):
                table_data = {
                    "table_number": table_idx + 1,
                    "rows": []
                }
                
                for row in table.getElementsByType(TableRow):
                    row_data = []
                    for cell in row.getElementsByType(TableCell):
                        cell_text = extractText(cell).strip()
                        row_data.append(cell_text)
                    table_data["rows"].append(row_data)
                
                result["tables"].append(table_data)
            
            result["total_tables"] = len(result["tables"])
            
            # Extract and OCR images from the document
            try:
                import zipfile
                
                # ODT files are ZIP archives - extract images from Pictures/ folder
                with zipfile.ZipFile(filepath, 'r') as odt_zip:
                    file_list = odt_zip.namelist()
                    image_files = [f for f in file_list if 'Pictures/' in f and 
                                any(f.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'])]
                    
                    for image_file in image_files:
                        try:
                            image_bytes = odt_zip.read(image_file)
                            image_name = os.path.basename(image_file)
                            
                            # Process image with OCR using existing function
                            # Materialise the embedded image into the document's
                            # extraction directory, then OCR it in place. Writing
                            # it out - rather than to a temp file that is unlinked
                            # immediately - is what lets the router treat the image
                            # as a child object with its own row, hash and lineage.
                            tmp_path = self._materialise_embedded_image(
                                image_bytes, image_name, filepath, result
                            )
                            ocr_result = self._ocr_embedded_image(tmp_path)
                            if ocr_result:
                                ocr_result["name"] = image_name
                                if ocr_result.get("text") or ocr_result.get("error"):
                                    result["extracted_images"].append(ocr_result)
                        except Exception as e:
                            logger.warning(f"Failed to process embedded image {image_file}: {e}")
                            continue
                
                if result["extracted_images"]:
                    logger.info(f"Extracted and processed {len(result['extracted_images'])} images from ODT")
            except Exception as e:
                logger.debug(f"Image extraction from ODT failed (non-critical): {e}")

            _apply_odf_annotations(result, filepath)

            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    def read_ods_file(self, filepath):
        """Independent ODS (OpenDocument Spreadsheet) reader function."""
        try:
            _odf_mod_7 = _odf("odf.opendocument")
            load = _odf_mod_7.load
            _odf_mod_8 = _odf("odf.table")
            Table = _odf_mod_8.Table
            TableRow = _odf_mod_8.TableRow
            TableCell = _odf_mod_8.TableCell
        except ImportError:
            return {
                "error": "odfpy not installed. Install with: pip install odfpy",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}

            encrypted = _odf_encryption_report(filepath)
            if encrypted:
                encrypted["filepath"] = filepath
                return encrypted
            
            doc = load(filepath)
            
            result = {
                "filepath": filepath,
                "sheet_names": [],
                "sheets": {},
                "total_sheets": 0,
                "extracted_images": []
            }
            
            # Extract tables (sheets)
            for table_idx, table in enumerate(doc.getElementsByType(Table)):
                sheet_name = table.getAttribute("name") or f"Sheet{table_idx + 1}"
                result["sheet_names"].append(sheet_name)
                
                sheet_data = {
                    "name": sheet_name,
                    "data": []
                }
                
                # Repeated rows and cells are runs, not single nodes: the
                # blank rows 2-3 of a sheet are written once with
                # number-rows-repeated="2", and empty cells the same way.
                # Collapsing them moved every address above the gap.
                pending_blank_rows = 0
                for row in table.getElementsByType(TableRow):
                    row_data = _ods_row_cells(row)
                    repeat = _ods_repeat(row, "numberrowsrepeated", cap=100000)
                    if not any(row_data):
                        # Held back so a sheet padded to its limit does not
                        # become a hundred thousand empty rows; flushed when a
                        # row with content follows.
                        pending_blank_rows += repeat
                        continue
                    while pending_blank_rows:
                        sheet_data["data"].append([])
                        pending_blank_rows -= 1
                    for _ in range(min(repeat, 100000)):
                        sheet_data["data"].append(list(row_data))
                
                result["sheets"][sheet_name] = sheet_data
            
            result["total_sheets"] = len(result["sheets"])
            
            # Extract and OCR images from the spreadsheet
            try:
                import zipfile
                
                # ODS files are ZIP archives - extract images from Pictures/ folder
                with zipfile.ZipFile(filepath, 'r') as ods_zip:
                    file_list = ods_zip.namelist()
                    image_files = [f for f in file_list if 'Pictures/' in f and 
                                any(f.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'])]
                    
                    for image_file in image_files:
                        try:
                            image_bytes = ods_zip.read(image_file)
                            image_name = os.path.basename(image_file)
                            
                            # Process image with OCR using existing function
                            # Materialise the embedded image into the document's
                            # extraction directory, then OCR it in place. Writing
                            # it out - rather than to a temp file that is unlinked
                            # immediately - is what lets the router treat the image
                            # as a child object with its own row, hash and lineage.
                            tmp_path = self._materialise_embedded_image(
                                image_bytes, image_name, filepath, result
                            )
                            ocr_result = self._ocr_embedded_image(tmp_path)
                            if ocr_result:
                                ocr_result["name"] = image_name
                                if ocr_result.get("text") or ocr_result.get("error"):
                                    result["extracted_images"].append(ocr_result)
                        except Exception as e:
                            logger.warning(f"Failed to process embedded image {image_file}: {e}")
                            continue
                
                if result["extracted_images"]:
                    logger.info(f"Extracted and processed {len(result['extracted_images'])} images from ODS")
            except Exception as e:
                logger.debug(f"Image extraction from ODS failed (non-critical): {e}")
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    def read_odp_file(self, filepath):
        """
        Independent ODP (OpenDocument Presentation) reader function.
        Preserves original order of elements within slides.
        """
        try:
            _odf_mod_9 = _odf("odf.opendocument")
            load = _odf_mod_9.load
            _odf_mod_10 = _odf("odf.draw")
            Page = _odf_mod_10.Page
            TextBox = _odf_mod_10.TextBox
            _odf_mod_11 = _odf("odf.presentation")
            FooterDecl = _odf_mod_11.FooterDecl
            HeaderDecl = _odf_mod_11.HeaderDecl
            Notes = _odf_mod_11.Notes
            _odf_mod_12 = _odf("odf.text")
            P = _odf_mod_12.P
            _odf_mod_13 = _odf("odf.table")
            TableRow = _odf_mod_13.TableRow
            TableCell = _odf_mod_13.TableCell
        except ImportError:
            return {
                "error": "odfpy not installed. Install with: pip install odfpy",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}

            encrypted = _odf_encryption_report(filepath)
            if encrypted:
                encrypted["filepath"] = filepath
                return encrypted
            
            doc = load(filepath)
            
            result = {
                "filepath": filepath,
                "slides": [],
                "total_slides": 0,
                "extracted_images": []
            }
            
            # Extract slides (pages) with elements in order
            pages = doc.getElementsByType(Page)
            for slide_idx, page in enumerate(pages):
                slide_data = {
                    "slide_number": slide_idx + 1,
                    "elements": [],  # Ordered list of all elements in the slide
                    "texts": []  # Keep for backward compatibility
                }
                
                element_position = 0
                
                # Extract elements in document order
                # Process all child elements of the page
                for child in page.childNodes:
                    if not hasattr(child, 'tagName'):
                        continue
                    
                    element_data = {
                        "type": None,
                        "position": element_position
                    }
                    
                    # Check for text boxes (frames with text)
                    if child.tagName == 'draw:frame' or (hasattr(child, 'tagName') and 'frame' in str(child.tagName)):
                        # Extract text from text boxes
                        text_boxes = child.getElementsByType(TextBox) if hasattr(child, 'getElementsByType') else []
                        text_content = []
                        
                        for text_box in text_boxes:
                            for para in text_box.getElementsByType(P) if hasattr(text_box, 'getElementsByType') else []:
                                if para.childNodes:
                                    para_text = _odf_node_text(para)
                                    if para_text.strip():
                                        text_content.append(para_text.strip())
                        
                        # Also check for direct paragraph children
                        for para in child.getElementsByType(P) if hasattr(child, 'getElementsByType') else []:
                            if para.childNodes:
                                para_text = _odf_node_text(para)
                                if para_text.strip():
                                    text_content.append(para_text.strip())
                        
                        if text_content:
                            element_data["type"] = "text"
                            element_data["text"] = "\n".join(text_content)
                            slide_data["texts"].extend(text_content)
                    
                    # Check for tables
                    elif child.tagName == 'table:table' or (hasattr(child, 'tagName') and 'table' in str(child.tagName)):
                        table = child if hasattr(child, 'getElementsByType') else None
                        if table:
                            element_data["type"] = "table"
                            element_data["table_number"] = len([e for e in slide_data["elements"] if e.get("type") == "table"]) + 1
                            element_data["rows"] = []
                            
                            for row in table.getElementsByType(TableRow) if hasattr(table, 'getElementsByType') else []:
                                row_data = []
                                for cell in row.getElementsByType(TableCell) if hasattr(row, 'getElementsByType') else []:
                                    cell_text = ""
                                    for para in cell.getElementsByType(P) if hasattr(cell, 'getElementsByType') else []:
                                        if para.childNodes:
                                            cell_text += _odf_node_text(para)
                                    row_data.append(cell_text.strip())
                                element_data["rows"].append(row_data)
                    
                    # Auto-shapes, groups and captions carry text too. A
                    # presentation built in Impress is mostly custom-shapes:
                    # testODP_NPE.odp has 99 frames and dozens of
                    # draw:custom-shape boxes, and the old branch recognised
                    # only draw:frame and table:table, so every bullet inside a
                    # shape was dropped. "The Medicalization of Society" - the
                    # title of a slide - simply was not in the stored text.
                    elif any(marker in str(child.tagName).lower()
                             for marker in ('custom-shape', 'draw:g', 'caption',
                                            'text-box', 'measure', 'connector')):
                        shape_paragraphs = [
                            _odf_node_text(para)
                            for para in (child.getElementsByType(P)
                                         if hasattr(child, 'getElementsByType') else [])
                        ]
                        shape_paragraphs = [text for text in shape_paragraphs if text]
                        if shape_paragraphs:
                            element_data["type"] = "text"
                            element_data["shape"] = str(child.tagName)
                            element_data["text"] = "\n".join(shape_paragraphs)
                            slide_data["texts"].extend(shape_paragraphs)

                    # Check for images
                    elif 'image' in str(child.tagName).lower():
                        element_data["type"] = "image"
                        element_data["image_name"] = f"slide_{slide_idx + 1}_element_{element_position + 1}"
                    
                    # Only add element if it has a recognized type
                    if element_data["type"]:
                        slide_data["elements"].append(element_data)
                        element_position += 1
                
                # Coverage pass. Whatever the element walk did not claim is
                # still text on this slide - inside a shape this reader does not
                # model, a nested group, or a frame-in-frame. Storing it last is
                # better than not storing it, and the element records
                # ``location: overflow`` so the provenance stays honest about
                # where it came from.
                stored_texts = {text for text in slide_data["texts"]}
                for para in page.getElementsByType(P):
                    text = _odf_node_text(para)
                    if text and text not in stored_texts:
                        stored_texts.add(text)
                        slide_data["texts"].append(text)
                        slide_data["elements"].append({
                            "type": "text",
                            "text": text,
                            "location": "overflow",
                            "position": len(slide_data["elements"]),
                        })
                
                # Speaker notes live in a presentation:notes page nested inside
                # the slide's draw:page, and they are content: citations,
                # source lines and the speaker's own argument. The reader
                # walked only the slide's direct children, so notes were never
                # read at all (testODP_NPE.odp lost "McGraw-Hill", "Copyright",
                # the author's name). The pipeline already stores ``notes`` for
                # PPTX, so the same key is used here.
                try:
                    notes_pages = page.getElementsByType(Notes)
                except Exception:
                    notes_pages = []
                notes_lines = []
                for notes_page in notes_pages:
                    for para in notes_page.getElementsByType(P):
                        text = _odf_node_text(para)
                        if text and text not in notes_lines:
                            notes_lines.append(text)
                if notes_lines:
                    slide_data["notes"] = "\n".join(notes_lines)

                result["slides"].append(slide_data)
            
            result["total_slides"] = len(result["slides"])

            # Footers, headers and date fields are visible page furniture, and
            # they are the only place a presentation sometimes records its
            # copyright line or author: testODP_NPE.odp keeps
            # "Copyright 2009 Tme McGraw Hill Companies All Rights Reserved" and
            # "Copyright 2009 Alan S Berger" in presentation:footer-decl, which
            # the slide walk never touched. They are stored as forensic_text,
            # the channel the pipeline indexes, tagged with where they came from.
            furniture = []
            for element in doc.getElementsByType(FooterDecl):
                text = _odf_node_text(element)
                if text:
                    furniture.append(f"Footer ({element.getAttribute('name') or 'default'}): {text}")
            for element in doc.getElementsByType(HeaderDecl):
                text = _odf_node_text(element)
                if text:
                    furniture.append(f"Header ({element.getAttribute('name') or 'default'}): {text}")
            if furniture:
                result["forensic_text"] = "\n".join(furniture)

            # Extract and OCR images from the presentation
            try:
                import zipfile
                
                # ODP files are ZIP archives - extract images from Pictures/ folder
                with zipfile.ZipFile(filepath, 'r') as odp_zip:
                    file_list = odp_zip.namelist()
                    image_files = [f for f in file_list if 'Pictures/' in f and 
                                any(f.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'])]
                    
                    for image_file in image_files:
                        try:
                            image_bytes = odp_zip.read(image_file)
                            image_name = os.path.basename(image_file)
                            
                            # Process image with OCR using existing function
                            # Materialise the embedded image into the document's
                            # extraction directory, then OCR it in place. Writing
                            # it out - rather than to a temp file that is unlinked
                            # immediately - is what lets the router treat the image
                            # as a child object with its own row, hash and lineage.
                            tmp_path = self._materialise_embedded_image(
                                image_bytes, image_name, filepath, result
                            )
                            ocr_result = self._ocr_embedded_image(tmp_path)
                            if ocr_result:
                                ocr_result["name"] = image_name
                                if ocr_result.get("text") or ocr_result.get("error"):
                                    result["extracted_images"].append(ocr_result)
                        except Exception as e:
                            logger.warning(f"Failed to process embedded image {image_file}: {e}")
                            continue
                
                if result["extracted_images"]:
                    logger.info(f"Extracted and processed {len(result['extracted_images'])} images from ODP")
            except Exception as e:
                logger.debug(f"Image extraction from ODP failed (non-critical): {e}")
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    def read_rtf_file(self, filepath):
        """Independent RTF (Rich Text Format) reader function."""
        if not os.path.exists(filepath):
            return {"error": "File not found", "filepath": filepath}
        
        # Try RTFDE (recommended)
        try:
            from RTFDE import deRTF
            with open(filepath, 'rb') as f:
                rtf_content = f.read()
            
            try:
                text = deRTF(rtf_content)
                
                result = {
                    "filepath": filepath,
                    "text": text,
                    "method": "RTFDE",
                    "total_characters": len(text),
                    "total_lines": len(text.splitlines())
                }
                
                paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                if paragraphs:
                    result["paragraphs"] = paragraphs
                    result["total_paragraphs"] = len(paragraphs)
                
                return result
            except Exception as e:
                logger.debug(f"RTFDE processing failed: {str(e)}", extra={"file_path": filepath})
        except ImportError:
            logger.debug("RTFDE not available, trying next method", extra={"file_path": filepath})
        except Exception as e:
            logger.debug(f"RTFDE import/execution failed: {str(e)}", extra={"file_path": filepath})
        
        # Try striprtf
        try:
            from striprtf.striprtf import rtf_to_text
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                rtf_content = f.read()
            
            text = rtf_to_text(rtf_content)
            
            result = {
                "filepath": filepath,
                "text": text,
                "method": "striprtf",
                "total_characters": len(text),
                "total_lines": len(text.splitlines())
            }
            
            paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
            if paragraphs:
                result["paragraphs"] = paragraphs
                result["total_paragraphs"] = len(paragraphs)
            
            return result
        except ImportError:
            logger.debug("striprtf not available, trying next method", extra={"file_path": filepath})
        except Exception as e:
            logger.debug(f"striprtf processing failed: {str(e)}", extra={"file_path": filepath})
        
        # Try compressed-rtf
        try:
            from compressed_rtf import compressed_rtf_to_text
            with open(filepath, 'rb') as f:
                rtf_content = f.read()
            
            text = compressed_rtf_to_text(rtf_content)
            
            result = {
                "filepath": filepath,
                "text": text,
                "method": "compressed-rtf",
                "total_characters": len(text),
                "total_lines": len(text.splitlines())
            }
            
            paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
            if paragraphs:
                result["paragraphs"] = paragraphs
                result["total_paragraphs"] = len(paragraphs)
            
            return result
        except ImportError:
            logger.debug("compressed-rtf not available, trying next method", extra={"file_path": filepath})
        except Exception as e:
            logger.debug(f"compressed-rtf processing failed: {str(e)}", extra={"file_path": filepath})
        
        # Try basic text extraction (fallback)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            # Simple RTF text extraction (removes RTF control words)
            import re
            text = re.sub(r'\\[a-z]+\d*\s?', '', content)
            text = re.sub(r'\{[^}]*\}', '', text)
            text = text.replace('\\par', '\n').replace('\\line', '\n')
            text = ' '.join(text.split())
            
            if text.strip():
                result = {
                    "filepath": filepath,
                    "text": text,
                    "method": "basic_extraction",
                    "note": "Basic text extraction - formatting may be lost",
                    "total_characters": len(text),
                    "total_lines": len(text.splitlines())
                }
                
                paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                if paragraphs:
                    result["paragraphs"] = paragraphs
                    result["total_paragraphs"] = len(paragraphs)
                
                return result
        except Exception as e:
            logger.debug(f"Basic RTF extraction failed: {str(e)}", extra={"file_path": filepath})
        
        # All methods failed
        error_msg = (
            "Could not read .rtf file. Please install one of the following:\n"
            "  - RTFDE: pip install RTFDE (recommended)\n"
            "  - striprtf: pip install striprtf\n"
            "  - compressed-rtf: pip install compressed-rtf"
        )
        
        return {
            "error": error_msg,
            "filepath": filepath
        }

