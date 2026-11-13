"""OOXML / ODF package features a document library does not expose.

python-docx, openpyxl and python-pptx return the *visible body* of a document.
They do not return who changed what, which paragraphs are hidden, where the
hyperlinks point, which parts are external references, or which objects are
embedded in the package - all of which are evidence.

This module reads the package itself. It is version- and producer-agnostic
(Word, LibreOffice, Google Export and OpenDocument producers all write these
parts), bounded in the work it will do, and explicit about anything it could
not read.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "cust": "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "ss": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "odf": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "odfmeta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "odfdc": "http://purl.org/dc/elements/1.1/",
}


@dataclass(frozen=True)
class PackageLimits:
    """Caps that keep metadata extraction bounded on pathological packages.

    Attributes:
        max_part_bytes: Largest part this module will read whole (larger parts
            are inventoried but not parsed, and are named in ``skipped_parts``).
        max_text_chars: Cap on each collected text field.
        max_items: Cap on list entries collected per feature (truncation is
            reported as ``<feature>_truncated``).
        max_parts: Cap on how many parts of one kind are read.
        hash_parts_over: Parts larger than this are counted but not hashed.
    """

    max_part_bytes: int = 16 * 1024 * 1024
    max_text_chars: int = 200_000
    max_items: int = 5000
    max_parts: int = 5000
    hash_parts_over: int = 64 * 1024 * 1024


DEFAULT_LIMITS = PackageLimits()

_CORE_PROPERTY_NAMES: Tuple[Tuple[str, str], ...] = (
    ("dc:title", "title"),
    ("dc:subject", "subject"),
    ("dc:creator", "creator"),
    ("cp:keywords", "keywords"),
    ("dc:description", "description"),
    ("cp:lastModifiedBy", "last_modified_by"),
    ("cp:revision", "revision"),
    ("cp:category", "category"),
    ("cp:contentStatus", "content_status"),
    ("cp:version", "version"),
    ("dcterms:created", "created"),
    ("dcterms:modified", "modified"),
    ("cp:lastPrinted", "last_printed"),
)

_APP_PROPERTY_NAMES: Tuple[str, ...] = (
    "Application", "AppVersion", "Company", "Manager", "Template", "Pages",
    "Words", "Characters", "CharactersWithSpaces", "Lines", "Paragraphs",
    "Slides", "Notes", "HiddenSlides", "TotalTime", "ScaleCrop", "SharedDoc",
    "HyperlinksChanged", "DocSecurity", "LinksUpToDate", "PresentationFormat",
)


def _text(element: Optional[ElementTree.Element]) -> str:
    return (element.text or "").strip() if element is not None else ""


def _parse(xml_bytes: bytes) -> Optional[ElementTree.Element]:
    try:
        return ElementTree.fromstring(xml_bytes)
    except Exception:
        return None


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def package_inventory(path: str) -> Dict[str, object]:
    """List the parts of a ZIP-based package with their sizes and digests.

    Cheap and complete: entry names, uncompressed/compressed sizes, per-entry
    CRC, compression method and flag bits. Used by the readers to record the
    package structure of an artifact even when a part cannot be parsed.
    """
    inventory: Dict[str, object] = {"parts": [], "entry_count": 0, "errors": []}
    try:
        with zipfile.ZipFile(path) as package:
            infos = package.infolist()
            inventory["entry_count"] = len(infos)
            inventory["comment"] = package.comment.decode("utf-8", "replace")
            for info in infos:
                inventory["parts"].append({
                    "name": info.filename,
                    "size": info.file_size,
                    "compressed_size": info.compress_size,
                    "crc": f"{info.CRC:08x}",
                    "method": info.compress_type,
                    "encrypted": bool(info.flag_bits & 0x1),
                    "data_descriptor": bool(info.flag_bits & 0x8),
                })
    except Exception as exc:  # unreadable package: say so, do not pretend
        inventory["errors"].append({"scope": "package", "error": str(exc)})
    return inventory


def _relationship_features(package: zipfile.ZipFile, limits: PackageLimits,
                           errors: List[Dict[str, str]]) -> Dict[str, object]:
    """External references, embedded objects and signatures from ``*.rels``."""
    external: List[Dict[str, str]] = []
    embedded: List[Dict[str, str]] = []
    signature_parts: List[str] = []
    relationship_types: Dict[str, int] = {}
    truncated = False

    names = [n for n in package.namelist() if n.endswith(".rels")]
    for name in sorted(names)[: limits.max_parts]:
        try:
            raw = package.read(name)
        except Exception as exc:
            errors.append({"scope": name, "error": str(exc)})
            continue
        if len(raw) > limits.max_part_bytes:
            errors.append({"scope": name,
                           "error": f"relationship part larger than {limits.max_part_bytes} bytes"})
            continue
        root = _parse(raw)
        if root is None:
            errors.append({"scope": name, "error": "relationship part is not well-formed XML"})
            continue
        source = name[:-len(".rels")].rsplit("/", 1)
        source_part = source[0] if source and source[0] else "/"
        for rel in root:
            rel_type = rel.get("Type", "")
            short = rel_type.rsplit("/", 1)[-1]
            relationship_types[short] = relationship_types.get(short, 0) + 1
            entry = {
                "source_part": source_part,
                "relationship_id": rel.get("Id", ""),
                "type": short,
                "target": rel.get("Target", ""),
                "target_mode": rel.get("TargetMode", "Internal"),
            }
            if rel.get("TargetMode") == "External":
                if len(external) < limits.max_items:
                    external.append(entry)
                else:
                    truncated = True
            if short in ("oleObject", "package", "attachedTemplate", "externalLink",
                         "hyperlink", "image", "audio", "video"):
                if short in ("oleObject", "package"):
                    if len(embedded) < limits.max_items:
                        embedded.append(entry)
                    else:
                        truncated = True
            if short in ("digitalSignature", "digitalSignatureOrigin"):
                signature_parts.append(source_part)

    result: Dict[str, object] = {
        "external_references": external,
        "embedded_objects": embedded,
        "relationship_types": relationship_types,
        "signature_parts": sorted(set(signature_parts)),
    }
    if truncated:
        result["relationships_truncated"] = True
    return result


def _core_and_app_properties(package: zipfile.ZipFile, errors: List[Dict[str, str]],
                             limits: PackageLimits) -> Dict[str, object]:
    """Document properties: core, application and custom."""
    properties: Dict[str, object] = {"core": {}, "application": {}, "custom": []}

    def _read(name: str) -> Optional[ElementTree.Element]:
        try:
            raw = package.read(name)
        except KeyError:
            return None
        except Exception as exc:
            errors.append({"scope": name, "error": str(exc)})
            return None
        if len(raw) > limits.max_part_bytes:
            errors.append({"scope": name, "error": "properties part exceeds parse cap"})
            return None
        root = _parse(raw)
        if root is None:
            errors.append({"scope": name, "error": "properties part is not well-formed XML"})
        return root

    core = _read("docProps/core.xml")
    if core is not None:
        for tag, key in _CORE_PROPERTY_NAMES:
            value = _text(core.find(tag, NS))
            if value:
                properties["core"][key] = _clip(value, 4096)
    app = _read("docProps/app.xml")
    if app is not None:
        for tag in _APP_PROPERTY_NAMES:
            value = _text(app.find(f"ep:{tag}", NS))
            if value:
                properties["application"][tag] = _clip(value, 4096)
        headings = [(_text(h.find("vt:lpstr", NS)) or _text(h)) for h in app.findall("ep:HeadingPairs", NS)]
        if any(headings):
            properties["application"]["HeadingPairs"] = [_clip(h, 256) for h in headings if h][:64]
    custom = _read("docProps/custom.xml")
    if custom is not None:
        custom_properties = []
        for prop in custom.findall("cust:property", NS):
            values = [_text(child) for child in prop]
            custom_properties.append({
                "name": prop.get("name", ""),
                "fmtid": prop.get("fmtid", ""),
                "pid": prop.get("pid", ""),
                "value": _clip(" | ".join(v for v in values if v), 4096),
            })
            if len(custom_properties) >= limits.max_items:
                properties["custom_truncated"] = True
                break
        properties["custom"] = custom_properties
    return properties


def _odf_metadata(package: zipfile.ZipFile, errors: List[Dict[str, str]],
                  limits: PackageLimits) -> Dict[str, object]:
    """OpenDocument ``meta.xml`` mirrored into the OOXML property names.

    Records from both formats land in the same keys, so downstream indexing and
    reporting do not need a per-format branch.
    """
    try:
        raw = package.read("meta.xml")
    except KeyError:
        return {}
    except Exception as exc:
        errors.append({"scope": "meta.xml", "error": str(exc)})
        return {}
    if len(raw) > limits.max_part_bytes:
        errors.append({"scope": "meta.xml", "error": "meta.xml exceeds parse cap"})
        return {}
    root = _parse(raw)
    if root is None:
        errors.append({"scope": "meta.xml", "error": "meta.xml is not well-formed XML"})
        return {}

    core: Dict[str, str] = {}
    meta_map = {
        "odfmeta:generator": "application",
        "odfmeta:initial-creator": "creator",
        "odfdc:creator": "last_modified_by",
        "odfdc:title": "title",
        "odfdc:subject": "subject",
        "odfdc:description": "description",
        "odfmeta:creation-date": "created",
        "odfdc:date": "modified",
        "odfmeta:printed-by": "last_printed_by",
        "odfmeta:print-date": "last_printed",
        "odfmeta:editing-cycles": "revision",
        "odfmeta:editing-duration": "total_editing_time",
        "odfmeta:keyword": "keywords",
    }
    for tag, key in meta_map.items():
        value = _text(root.find(tag, NS))
        if value:
            core[key] = _clip(value, 4096)
    custom = []
    for user in root.findall("odfmeta:user-defined", NS):
        custom.append({"name": user.get("{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}name", ""),
                       "value": _clip(_text(user), 4096)})
    stat = root.find("odfmeta:document-statistic", NS)
    application: Dict[str, str] = {}
    if stat is not None:
        for key, value in stat.attrib.items():
            application[key.rsplit("}", 1)[-1]] = value
    return {"core": core, "application": application, "custom": custom}


# ---------------------------------------------------------------------------
# WordprocessingML
# ---------------------------------------------------------------------------
def _word_features(package: zipfile.ZipFile, limits: PackageLimits,
                   errors: List[Dict[str, str]]) -> Dict[str, object]:
    features: Dict[str, object] = {
        "hyperlinks": [], "bookmarks": [], "fields": [], "comments": [],
        "revisions": {"insertions": 0, "deletions": 0, "moves": 0,
                      "format_changes": 0, "authors": [], "deleted_text": []},
        "hidden": {"hidden_runs": 0, "hidden_paragraphs": 0},
        "headers_footers": [], "footnotes_endnotes": [],
    }
    names = package.namelist()
    body_parts = ["word/document.xml"]
    body_parts += sorted(n for n in names if re.match(r"word/(header|footer)\d*\.xml$", n))
    body_parts += sorted(n for n in names if n in ("word/footnotes.xml", "word/endnotes.xml"))
    body_parts += sorted(n for n in names if re.match(r"word/comments\d*\.xml$", n))

    for part in body_parts[: limits.max_parts]:
        try:
            raw = package.read(part)
        except Exception as exc:
            errors.append({"scope": part, "error": str(exc)})
            continue
        if len(raw) > limits.max_part_bytes:
            errors.append({"scope": part,
                           "error": f"part larger than {limits.max_part_bytes} bytes; "
                                    f"not parsed for revision/annotation features"})
            continue
        root = _parse(raw)
        if root is None:
            errors.append({"scope": part, "error": "part is not well-formed XML"})
            continue

        is_comment_part = "comments" in part
        if is_comment_part:
            for comment in root.findall(".//w:comment", NS):
                features["comments"].append({
                    "part": part,
                    "id": comment.get(f"{{{NS['w']}}}id", ""),
                    "author": comment.get(f"{{{NS['w']}}}author", ""),
                    "initials": comment.get(f"{{{NS['w']}}}initials", ""),
                    "date": comment.get(f"{{{NS['w']}}}date", ""),
                    "text": _clip("".join(t.text or "" for t in comment.findall(".//w:t", NS)),
                                  limits.max_text_chars),
                })
                if len(features["comments"]) >= limits.max_items:
                    features["comments_truncated"] = True
                    break
            continue

        if re.match(r"word/(header|footer)\d*\.xml$", part) or "footnotes" in part or "endnotes" in part:
            text = _clip("".join(t.text or "" for t in root.findall(".//w:t", NS)),
                         limits.max_text_chars)
            entry = {"part": part, "text": text}
            if "footnotes" in part or "endnotes" in part:
                features["footnotes_endnotes"].append(entry)
            else:
                features["headers_footers"].append(entry)
            continue

        for link in root.findall(".//w:hyperlink", NS):
            rel_id = link.get(f"{{{NS['r']}}}id", "")
            anchor = link.get(f"{{{NS['w']}}}anchor", "")
            if not rel_id and not anchor:
                continue
            features["hyperlinks"].append({
                "part": part,
                "relationship_id": rel_id,
                "anchor": anchor,
                "text": _clip("".join(t.text or "" for t in link.findall(".//w:t", NS)),
                              limits.max_text_chars),
            })
            if len(features["hyperlinks"]) >= limits.max_items:
                features["hyperlinks_truncated"] = True
                break

        for bookmark in root.findall(".//w:bookmarkStart", NS):
            name = bookmark.get(f"{{{NS['w']}}}name", "")
            if name and not name.startswith("_"):
                features["bookmarks"].append(name)
        if len(features["bookmarks"]) >= limits.max_items:
            features["bookmarks_truncated"] = True
            features["bookmarks"] = features["bookmarks"][: limits.max_items]

        for simple in root.findall(".//w:fldSimple", NS):
            instruction = simple.get(f"{{{NS['w']}}}instr", "")
            if instruction:
                features["fields"].append({
                    "kind": "fldSimple", "instruction": _clip(instruction, 1024),
                    "result": _clip("".join(t.text or "" for t in simple.findall(".//w:t", NS)), 1024),
                })
        for instruction in root.findall(".//w:instrText", NS):
            text = (instruction.text or "").strip()
            if text:
                features["fields"].append({"kind": "fldChar", "instruction": _clip(text, 1024)})
        if len(features["fields"]) >= limits.max_items:
            features["fields_truncated"] = True
            features["fields"] = features["fields"][: limits.max_items]

        # Tracked changes: an artifact whose revisions were "accepted" still
        # carries them here until the changes are actually applied.
        revisions = features["revisions"]
        for tag, key in (("w:ins", "insertions"), ("w:del", "deletions"),
                         ("w:moveFrom", "moves"), ("w:moveTo", "moves"),
                         ("w:rPrChange", "format_changes"), ("w:pPrChange", "format_changes"),
                         ("w:tblPrChange", "format_changes"), ("w:tblGridChange", "format_changes")):
            for node in root.findall(f".//{tag}", NS):
                revisions[key] += 1
                author = node.get(f"{{{NS['w']}}}author", "")
                date = node.get(f"{{{NS['w']}}}date", "")
                if author and author not in revisions["authors"] and \
                        len(revisions["authors"]) < limits.max_items:
                    revisions["authors"].append(author)
                if tag == "w:del" and len(revisions["deleted_text"]) < limits.max_items:
                    deleted = "".join(t.text or "" for t in node.findall(".//w:delText", NS))
                    if deleted:
                        revisions["deleted_text"].append(
                            {"author": author, "date": date,
                             "text": _clip(deleted, limits.max_text_chars)})

        hidden = features["hidden"]
        for run in root.findall(".//w:r", NS):
            props = run.find("w:rPr", NS)
            if props is not None and props.find("w:vanish", NS) is not None:
                hidden["hidden_runs"] += 1
        for paragraph in root.findall(".//w:p", NS):
            props = paragraph.find("w:pPr", NS)
            if props is not None and props.find("w:rPr", NS) is not None and \
                    props.find("w:rPr", NS).find("w:vanish", NS) is not None:
                hidden["hidden_paragraphs"] += 1

    return features


# ---------------------------------------------------------------------------
# SpreadsheetML
# ---------------------------------------------------------------------------
def _excel_features(package: zipfile.ZipFile, limits: PackageLimits,
                    errors: List[Dict[str, str]]) -> Dict[str, object]:
    features: Dict[str, object] = {
        "sheet_states": [], "defined_names": [], "comments": [],
        "external_links": [], "formula_count": 0, "formulas": [],
    }
    names = package.namelist()

    workbook = None
    try:
        raw = package.read("xl/workbook.xml")
        if len(raw) <= limits.max_part_bytes:
            workbook = _parse(raw)
        else:
            errors.append({"scope": "xl/workbook.xml", "error": "workbook part exceeds parse cap"})
    except KeyError:
        pass
    except Exception as exc:
        errors.append({"scope": "xl/workbook.xml", "error": str(exc)})

    if workbook is not None:
        for sheet in workbook.findall(".//ss:sheet", NS):
            state = sheet.get("state", "visible")
            features["sheet_states"].append({"name": sheet.get("name", ""), "state": state})
        for defined in workbook.findall(".//ss:definedName", NS):
            features["defined_names"].append({
                "name": defined.get("name", ""),
                "local_sheet_id": defined.get("localSheetId", ""),
                "hidden": defined.get("hidden", "0"),
                "refers_to": _clip(_text(defined), 2048),
            })
            if len(features["defined_names"]) >= limits.max_items:
                features["defined_names_truncated"] = True
                break
        for link in workbook.findall(".//ss:externalReference", NS):
            features["external_links"].append({
                "relationship_id": link.get(f"{{{NS['r']}}}id", ""),
            })

    for part in sorted(n for n in names if re.match(r"xl/comments\d*\.xml$", n)
                       or n.startswith("xl/threadedComments/"))[: limits.max_parts]:
        try:
            raw = package.read(part)
        except Exception as exc:
            errors.append({"scope": part, "error": str(exc)})
            continue
        if len(raw) > limits.max_part_bytes:
            errors.append({"scope": part, "error": "comment part exceeds parse cap"})
            continue
        root = _parse(raw)
        if root is None:
            errors.append({"scope": part, "error": "comment part is not well-formed XML"})
            continue
        for comment in root.iter():
            tag = comment.tag.rsplit("}", 1)[-1]
            if tag not in ("comment", "threadedComment"):
                continue
            text_names = ("text", "t")
            text = "".join(node.text or "" for node in comment.iter()
                           if node.tag.rsplit("}", 1)[-1] in text_names)
            features["comments"].append({
                "part": part,
                "ref": comment.get("ref", ""),
                "id": comment.get("Id", comment.get("id", "")),
                "author": comment.get("author", comment.get("personId", "")),
                "date": comment.get("date", comment.get("dT", "")),
                "text": _clip(text, limits.max_text_chars),
            })
            if len(features["comments"]) >= limits.max_items:
                features["comments_truncated"] = True
                break

    # Formulas are evidence in their own right: a value read with data_only
    # hides the formula that produced it. Counting is cheap; the text is kept
    # for sheets whose XML is small enough to scan without loading the world.
    formula_pattern = re.compile(rb"<f[ >]")
    formula_text_pattern = re.compile(rb"<f[^>]*>(.*?)</f>", re.DOTALL)
    for part in sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[: limits.max_parts]:
        try:
            raw = package.read(part)
        except Exception as exc:
            errors.append({"scope": part, "error": str(exc)})
            continue
        count = len(formula_pattern.findall(raw))
        features["formula_count"] += count
        if not count:
            continue
        if len(raw) > limits.max_part_bytes:
            features.setdefault("formulas_not_scanned", []).append(part)
            continue
        for match in formula_text_pattern.finditer(raw):
            if len(features["formulas"]) >= limits.max_items:
                features["formulas_truncated"] = True
                break
            text = match.group(1).decode("utf-8", "replace")
            text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
            features["formulas"].append({"part": part, "formula": _clip(text, 1024)})
    return features


# ---------------------------------------------------------------------------
# PresentationML
# ---------------------------------------------------------------------------
def _powerpoint_features(package: zipfile.ZipFile, limits: PackageLimits,
                         errors: List[Dict[str, str]]) -> Dict[str, object]:
    features: Dict[str, object] = {
        "slides": [], "hidden_slides": [], "notes_text": [], "comments": [],
        "hyperlinks": [], "media_parts": [],
    }
    names = package.namelist()
    slide_parts = sorted(n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n))
    for index, part in enumerate(slide_parts[: limits.max_parts], start=1):
        try:
            raw = package.read(part)
        except Exception as exc:
            errors.append({"scope": part, "error": str(exc)})
            continue
        if len(raw) > limits.max_part_bytes:
            errors.append({"scope": part, "error": "slide part exceeds parse cap"})
            continue
        root = _parse(raw)
        if root is None:
            errors.append({"scope": part, "error": "slide part is not well-formed XML"})
            continue
        # The slide part's root element *is* p:sld; only fall back to a child
        # lookup for producers that wrap it.
        presentation = root if root.tag == f"{{{NS['p']}}}sld" else root.find("p:sld", NS)
        show = presentation.get("show", "1") if presentation is not None else "1"
        text = _clip("".join(t.text or "" for t in root.findall(".//a:t", NS)),
                     limits.max_text_chars)
        entry = {"part": part, "index": index, "hidden": show == "0", "text": text}
        features["slides"].append(entry)
        if show == "0":
            features["hidden_slides"].append(entry)
        for link in root.findall(".//a:hlinkClick", NS):
            rel_id = link.get(f"{{{NS['r']}}}id", "")
            if rel_id:
                features["hyperlinks"].append({"part": part, "relationship_id": rel_id,
                                               "action": link.get("action", "")})
                if len(features["hyperlinks"]) >= limits.max_items:
                    features["hyperlinks_truncated"] = True
                    break
        if len(features["slides"]) >= limits.max_items:
            features["slides_truncated"] = True
            break

    for part in sorted(n for n in names if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n))[: limits.max_parts]:
        try:
            raw = package.read(part)
        except Exception as exc:
            errors.append({"scope": part, "error": str(exc)})
            continue
        if len(raw) > limits.max_part_bytes:
            errors.append({"scope": part, "error": "notes part exceeds parse cap"})
            continue
        root = _parse(raw)
        if root is None:
            errors.append({"scope": part, "error": "notes part is not well-formed XML"})
            continue
        # Runs concatenate inside a paragraph and paragraphs are separated by a
        # newline. Joining every a:t in the part with no separator welded
        # adjacent placeholders into words that do not exist:
        # "NotesMasterHeader" + "NotesMasterFooter" became one 29-character
        # token, so a search for either heading missed the notes page and the
        # audit oracle - which does separate paragraphs - counted two words the
        # store appeared not to have.
        paragraphs = []
        for paragraph in root.findall(".//a:p", NS):
            text = "".join(t.text or "" for t in paragraph.findall(".//a:t", NS))
            if text.strip():
                paragraphs.append(text)
        if not paragraphs:
            # A part with runs but no paragraphs is malformed; it still holds
            # text, so it is recorded run by run rather than dropped.
            paragraphs = [t.text for t in root.findall(".//a:t", NS) if t.text]
        features["notes_text"].append({
            "part": part,
            "text": _clip("\n".join(paragraphs), limits.max_text_chars),
        })

    for part in sorted(n for n in names if re.match(r"ppt/comments/.*\.xml$", n))[: limits.max_parts]:
        try:
            raw = package.read(part)
        except Exception as exc:
            errors.append({"scope": part, "error": str(exc)})
            continue
        if len(raw) > limits.max_part_bytes:
            errors.append({"scope": part, "error": "comment part exceeds parse cap"})
            continue
        root = _parse(raw)
        if root is None:
            errors.append({"scope": part, "error": "comment part is not well-formed XML"})
            continue
        for comment in root.iter():
            if comment.tag.rsplit("}", 1)[-1] not in ("cm", "comment"):
                continue
            # PresentationML stores the comment body in p:text (a simple
            # string); modern producers add structured runs under a:t inside
            # it. Both spellings carry the same evidence, so both are read.
            text = "".join(t.text or "" for t in comment.findall(".//a:t", NS))
            if not text:
                text = "".join(t.text or "" for t in comment.findall(".//p:text", NS))
            if not text and comment.tag.rsplit("}", 1)[-1] == "cm":
                text = "".join(child.text or "" for child in comment.iter("p:text")
                               if child.text)
            features["comments"].append({
                "part": part,
                "author": comment.get("author", comment.get("authorId", "")),
                "date": comment.get("dt", comment.get("date", "")),
                "text": _clip(text, limits.max_text_chars),
            })
            if len(features["comments"]) >= limits.max_items:
                features["comments_truncated"] = True
                break

    features["media_parts"] = [n for n in names if n.startswith("ppt/media/")][: limits.max_items]
    return features


def extract_package_features(path: str, app: Optional[str] = None,
                             limits: PackageLimits = DEFAULT_LIMITS) -> Dict[str, object]:
    """Extract forensic features from an OOXML or OpenDocument package.

    Args:
        path: Path to the ZIP-based package.
        app: ``word`` / ``excel`` / ``powerpoint`` / ``odf``; when omitted it is
            inferred from the part list.
        limits: Work caps (see :class:`PackageLimits`).

    Returns:
        A dict with ``properties``, ``relationships``, the application-specific
        feature block, ``package`` (part inventory) and ``extraction_errors``.
        A package that cannot be opened at all returns the error list only -
        the caller decides how to record it, but it is never silent.
    """
    features: Dict[str, object] = {"extraction_errors": []}
    errors: List[Dict[str, str]] = features["extraction_errors"]  # type: ignore[assignment]
    try:
        package = zipfile.ZipFile(path)  # noqa: SIM115 - opened for the whole extraction
    except Exception as exc:
        errors.append({"scope": "package", "error": str(exc)})
        return features

    with package:
        names = package.namelist()
        lowered = {n.lower() for n in names}
        if app is None:
            if "content.xml" in lowered and "meta.xml" in lowered:
                app = "odf"
            elif any(n.startswith("ppt/") for n in lowered):
                app = "powerpoint"
            elif any(n.startswith("xl/") for n in lowered):
                app = "excel"
            elif any(n.startswith("word/") for n in lowered):
                app = "word"
        features["application"] = app or "unknown"
        features["package"] = package_inventory(path)
        # The inventory re-opens the file; keep one authoritative listing here
        # so a caller can rely on entry names without opening the package again.
        features["parts"] = sorted(names)[: limits.max_items]
        if len(names) > limits.max_items:
            features["parts_truncated"] = True

        try:
            features["relationships"] = _relationship_features(package, limits, errors)
        except Exception as exc:
            errors.append({"scope": "relationships", "error": str(exc)})

        try:
            properties = _core_and_app_properties(package, errors, limits)
            odf = _odf_metadata(package, errors, limits) if app == "odf" else {}
            if odf:
                properties = {
                    "core": {**odf.get("core", {}), **properties.get("core", {})},
                    "application": {**odf.get("application", {}),
                                    **properties.get("application", {})},
                    "custom": properties.get("custom", []) + list(odf.get("custom", [])),
                }
            features["properties"] = properties
        except Exception as exc:
            errors.append({"scope": "properties", "error": str(exc)})

        block_name = {"word": "document", "excel": "workbook",
                      "powerpoint": "presentation"}.get(app or "")
        if block_name:
            try:
                block = {"word": _word_features, "excel": _excel_features,
                         "powerpoint": _powerpoint_features}[app](package, limits, errors)
                features[block_name] = block
            except Exception as exc:
                errors.append({"scope": block_name, "error": str(exc)})
        elif app == "odf":
            try:
                features["document"] = _odf_body_features(package, limits, errors)
            except Exception as exc:
                errors.append({"scope": "document", "error": str(exc)})

        features["macros_present"] = any(
            re.search(r"(^|/)vbaProject\.bin$", name, re.IGNORECASE) for name in names)
        features["macro_signature_parts"] = [
            name for name in names if re.search(r"vbaProjectSignature\.bin$", name, re.IGNORECASE)]
        features["encrypted"] = any(
            name.lower() in ("encryptioninfo", "encryptedpackage") for name in names)
    return features


def _odf_body_features(package: zipfile.ZipFile, limits: PackageLimits,
                       errors: List[Dict[str, str]]) -> Dict[str, object]:
    """OpenDocument body features: hidden sections, fields, tracked changes."""
    features: Dict[str, object] = {
        "hyperlinks": [], "fields": [], "bookmarks": [], "revisions": {"insertions": 0,
                                                                      "deletions": 0,
                                                                      "authors": []},
        "hidden_sections": 0,
    }
    try:
        raw = package.read("content.xml")
    except Exception as exc:
        errors.append({"scope": "content.xml", "error": str(exc)})
        return features
    if len(raw) > limits.max_part_bytes:
        errors.append({"scope": "content.xml", "error": "content.xml exceeds parse cap"})
        return features
    root = _parse(raw)
    if root is None:
        errors.append({"scope": "content.xml", "error": "content.xml is not well-formed XML"})
        return features
    text_ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    for link in root.iter(f"{text_ns}a"):
        target = link.get("{http://www.w3.org/1999/xlink}href", "")
        if target:
            features["hyperlinks"].append({"target": target,
                                           "text": _clip("".join(link.itertext()),
                                                         limits.max_text_chars)})
        if len(features["hyperlinks"]) >= limits.max_items:
            features["hyperlinks_truncated"] = True
            break
    for field in root.iter(f"{text_ns}field-master"):
        features["fields"].append({"kind": "field-master", "name": field.get(f"{text_ns}name", "")})
    for bookmark in root.iter(f"{text_ns}bookmark"):
        name = bookmark.get(f"{text_ns}name", "")
        if name:
            features["bookmarks"].append(name)
    for section in root.iter(f"{text_ns}section"):
        if section.get(f"{text_ns}display") == "none":
            features["hidden_sections"] += 1
    for change in root.iter(f"{text_ns}change-start"):
        features["revisions"]["insertions"] += 1
        author = change.get("{urn:oasis:names:tc:opendocument:xmlns:office:1.0}author", "")
        if author and author not in features["revisions"]["authors"]:
            features["revisions"]["authors"].append(author)
    for change in root.iter(f"{text_ns}change"):
        features["revisions"]["deletions"] += 1
        author = change.get("{urn:oasis:names:tc:opendocument:xmlns:office:1.0}author", "")
        if author and author not in features["revisions"]["authors"]:
            features["revisions"]["authors"].append(author)
    return features


def text_from_features(features: Dict[str, object]) -> str:
    """Flatten searchable text out of extracted features.

    Comments, revision text, notes, headers, footers, formulas and macro source
    are all content: an examiner searching for a term must find it whether it
    sits in the body or in a tracked change. This returns the parts that the
    body readers do not already emit.

    Identifiers and states are NOT content. A worksheet named "Sheet1", a
    defined name and a sheet's hidden/visible state are software labels: they
    are recorded structurally (``extraction_provenance -> forensic_features``,
    where they can be queried exactly), and flattening them into the text
    channel made a search for "sheet" return every workbook - the label
    exclusion the storage pipeline guarantees (see
    ``tests/integration/test_structural_marker_exclusion.py``). The body
    readers already print the sheet name as a display marker, so nothing an
    examiner reads is lost by keeping labels out of the index.
    """
    chunks: List[str] = []

    #: Keys whose value identifies a part or names a state rather than carrying
    #: document text. (``part``/``ref``/``id``/... were already excluded; the
    #: identity entries below were the leak.)
    _STRUCTURAL_KEYS = (
        "part", "date", "ref", "id", "relationship_id", "anchor", "fmtid",
        "pid", "author", "initials", "type",
        "name", "state", "hidden", "local_sheet_id", "refers_to",
        "scope", "error",
    )

    #: Whole subtrees that are package inventory: every string in them is an
    #: identifier (worksheet names and states, defined-name identifiers,
    #: relationship ids), never body text.
    _STRUCTURAL_SUBTREES = ("sheet_states", "defined_names", "external_links")

    def _collect(value: object, key: str = "") -> None:
        if isinstance(value, str):
            if key not in _STRUCTURAL_KEYS:
                chunks.append(value)
        elif isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if sub_key in _STRUCTURAL_SUBTREES:
                    continue
                _collect(sub_value, sub_key)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                _collect(item, key)

    document = features.get("document") or features.get("workbook") or \
        features.get("presentation") or {}
    _collect(document)
    document_props = (features.get("properties") or {}).get("custom")  # type: ignore[union-attr]
    _collect(document_props)
    seen = set()
    unique: List[str] = []
    for chunk in chunks:
        text = chunk.strip()
        if text and text not in seen:
            seen.add(text)
            unique.append(text)
    return "\n".join(unique)


def iter_feature_text(features: Dict[str, object]) -> Iterable[str]:
    """Yield searchable strings from a feature block (alias of the above)."""
    text = text_from_features(features)
    if text:
        yield from text.splitlines()


def macro_parts(features: Dict[str, object]) -> Sequence[str]:
    """Package parts that make a document macro-enabled."""
    return [name for name in (features.get("parts") or [])
            if re.search(r"(^|/)vbaProject\.bin$", str(name), re.IGNORECASE)]
