"""Reading order, ligatures, hyphenation and table structure for PDF pages.

A PDF page has no paragraphs, no columns and no words: it has glyphs at
coordinates. ``page.get_text("text")`` returns MuPDF's own guess at the reading
order, which is good for a single column of prose and wrong for everything the
format was invented for - two-column journals, sidebars, headers and footers,
tables, forms, rotated inserts. Recall metrics do not notice: every word is
there, in an order that makes the text useless for search, quotation or an LLM.

This module turns one page into a *model* rather than a string:

``text``      the page's text in reading order, dehyphenated and normalised
``text_raw``  MuPDF's own order, kept so the change is auditable
``blocks``    one record per text block: bbox, column, direction, lines, fonts
``columns``   how many columns the block layout was cut into
``tables``    grids found by MuPDF's table finder, row-major cell text
``stats``     characters, words, hyphen joins, ligatures, line directions

Reading order is recovered with a recursive X-Y cut: a page is split at the
widest horizontal whitespace band that no block crosses, then at the widest
vertical one, and so on, until each region holds a single block. That is the
classic document-layout algorithm and it gets headers, footers, multi-column
bodies and sidebars right without a model or a font of training data. Blocks are
projected onto their own text direction first, so text set at 90 degrees is
ordered along its baseline instead of down the page.
"""
from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# Characters PDF text extraction makes a mess of
# --------------------------------------------------------------------------- #
#: Presentation-form ligatures. A PDF that embeds the glyph "ﬁ" extracts as
#: U+FB01, which no search for "file" will ever match.
LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st", "\ufb13": "oe",
    "\ufb14": "oe", "\ufb15": "ij", "\ufb16": "ij", "\ufb17": "tt",
}

#: Invisible characters that carry no content. U+200C/U+200D are deliberately
#: NOT here: in Arabic and Persian they change the word (ZWNJ is a letter
#: separator), so dropping them would corrupt the text.
INVISIBLE = {
    "\u00ad",   # soft hyphen: an optional break, not a character of the word
    "\u200e", "\u200f",   # LRM / RLM
    "\u2060",   # word joiner
    "\ufeff",   # BOM / zero-width no-break space
}
#: Spaces of every width become an ordinary space.
SPACES = {
    "\u00a0", "\u2007", "\u2009", "\u200a", "\u202f", "\u205f", "\u3000",
    "\u2000", "\u2001", "\u2002", "\u2003", "\u2004", "\u2005", "\u2006",
    "\u2008", "\u200b",
}
_LIG_RE = re.compile("|".join(re.escape(k) for k in LIGATURES))
_SPACE_RE = re.compile("|".join(re.escape(k) for k in SPACES))
_INVISIBLE_RE = re.compile("|".join(re.escape(k) for k in INVISIBLE))
#: U+200B is zero-width but it *is* a word boundary: removing it would weld two
#: words together, so it becomes a space instead.

#: A word broken at the end of a line: letters, a hyphen, a newline, letters.
#: The right-hand side must start lowercase: "Inter-\nnational" is a break,
#: "GmbH-\nMünchen" is a compound and keeps its hyphen.
_SOFT_BREAK_RE = re.compile(r"(?<=[^\W\d_])\-[\u00ad]?[ \t]*\n[ \t]*(?=[^\W\d_])", re.UNICODE)
_WORD_CHAR_RE = re.compile(r"[^\W\d_]", re.UNICODE)



def normalize_text(text: str) -> Tuple[str, Dict[str, int]]:
    """Ligatures out, invisible characters gone, odd spaces made ordinary.

    Returns the text and a count of what was changed, because "we cleaned it"
    without a number is not evidence.
    """
    if not text:
        return "", {"ligatures": 0, "invisible": 0, "spaces": 0}
    ligatures = len(_LIG_RE.findall(text))
    invisible = len(_INVISIBLE_RE.findall(text))
    spaces = len(_SPACE_RE.findall(text))
    out = _LIG_RE.sub(lambda m: LIGATURES[m.group(0)], text)
    out = _INVISIBLE_RE.sub("", out)
    out = _SPACE_RE.sub(" ", out)
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    return out, {"ligatures": ligatures, "invisible": invisible, "spaces": spaces}


def dehyphenate(text: str) -> Tuple[str, int]:
    """Rejoin words broken across a line by a hyphen.

    ``("Inter-\\nnational")`` becomes ``"International"``. The decision is made
    on the character after the break: a break before a lowercase letter is
    reported as a line break by virtually every typesetter, while a capital or a
    digit after the hyphen is a real hyphen in a compound. The rule is
    conservative on purpose - leaving a hyphen in is a smaller error than
    welding two words together.
    """
    if not text or "\n" not in text:
        return text, 0

    joins = 0

    def _join(match: "re.Match[str]") -> str:
        nonlocal joins
        start = match.start()
        end = match.end()
        after = text[end:end + 1]
        before = text[max(0, start - 12):start]
        word = before.split()[-1] if before.split() else ""
        # Only join when both halves look like letters; short fragments ("e-")
        # are usually list markers or initials.
        if len(word) < 2 or not after or not after.islower():
            return match.group(0)
        joins += 1
        return ""

    fixed = _SOFT_BREAK_RE.sub(_join, text)
    return fixed, joins


def text_quality(text: str) -> float:
    """How much of ``text`` looks like language, 0.0 - 1.0.

    Used to decide whether a page's own text layer is worth keeping: a page
    scanned and run through a bad OCR pass can carry a text layer made of
    U+FFFD and single glyphs, and keeping it because it is non-empty would hide
    the loss. Lexical words (2+ letters) score 1, anything else scores 0.
    """
    if not text or not text.strip():
        return 0.0
    tokens = text.split()
    if not tokens:
        return 0.0
    # Dot leaders, rules and bullet glyphs are layout, not recognition
    # residue: a table of contents is 40% ". . . ." by token count, and scoring
    # it as garbage made the reader hand the page to OCR.
    # Dot leaders, page numbers and rules are layout, and a table of contents
    # is mostly them: counting them made a perfectly readable page look like
    # recognition residue, so the page was handed to OCR and came back shorter.
    # The question this function answers is "of the *words* on this page, how
    # many are words", so only tokens containing letters take part.
    words = [t for t in tokens if _WORD_CHAR_RE.search(t)]
    if not words:
        return 0.0
    good = sum(1 for t in words if len(_WORD_CHAR_RE.findall(t)) >= 2)
    replacement = text.count("\ufffd") / max(1, len(text))
    return max(0.0, good / len(words) - min(0.5, replacement * 5))


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #
def _lines_of(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [line for line in block.get("lines") or [] if line.get("spans")]


def _block_text(block: Dict[str, Any]) -> str:
    parts = []
    for line in _lines_of(block):
        parts.append("".join(str(span.get("text") or "") for span in line["spans"]))
    return "\n".join(parts)


def _direction(block: Dict[str, Any]) -> Tuple[float, float]:
    """Dominant baseline direction of a block, from its first line."""
    for line in _lines_of(block):
        direction = line.get("dir")
        if direction:
            return (float(direction[0]), float(direction[1]))
    return (1.0, 0.0)


def blocks_from_page(page: Any) -> List[Dict[str, Any]]:
    """Every block on the page, text and image, with geometry and typography."""
    data = page.get_text("dict")
    out: List[Dict[str, Any]] = []
    for index, block in enumerate(data.get("blocks") or []):
        bbox = tuple(float(v) for v in block.get("bbox") or (0, 0, 0, 0))
        if block.get("type") != 0:
            out.append({
                "index": index, "type": "image", "bbox": bbox,
                "width": int(block.get("width") or 0),
                "height": int(block.get("height") or 0),
                "text": "",
            })
            continue
        text = _block_text(block)
        sizes: List[float] = []
        fonts: List[str] = []
        line_boxes = []
        for line in _lines_of(block):
            line_boxes.append(tuple(float(v) for v in line.get("bbox") or (0, 0, 0, 0)))
            for span in line["spans"]:
                if span.get("size"):
                    sizes.append(round(float(span["size"]), 1))
                if span.get("font"):
                    fonts.append(str(span["font"]))
        direction = _direction(block)
        out.append({
            "index": index,
            "type": "text",
            "bbox": bbox,
            "text": text,
            "dir": direction,
            "lines": len(line_boxes),
            "line_boxes": line_boxes,
            "font_size": statistics.median(sizes) if sizes else None,
            "fonts": sorted(set(fonts))[:6],
        })
    return out


def _axes(bbox: Sequence[float], direction: Tuple[float, float]) -> Tuple[float, float, float, float]:
    """Project a bbox onto the block's own axes: (v0, v1, u0, u1).

    ``u`` runs along the baseline (the reading direction), ``v`` across it (the
    direction in which lines stack). Ordering on (v, u) orders a rotated page
    the way a reader of that page would, without special cases.
    """
    x0, y0, x1, y1 = bbox
    dx, dy = direction
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        dx, dy = 1.0, 0.0
    ux, uy = dx, dy
    vx, vy = -dy, dx
    corners = ((x0, y0), (x1, y0), (x0, y1), (x1, y1))
    us = [cx * ux + cy * uy for cx, cy in corners]
    vs = [cx * vx + cy * vy for cx, cy in corners]
    return min(vs), max(vs), min(us), max(us)


def _best_cut(blocks: List[Dict[str, Any]], axis: int, min_gap: float) -> Optional[int]:
    """Index at which to cut ``blocks`` along ``axis`` of their axes tuple.

    A cut is valid only if no block straddles it, so the two halves are exactly
    the blocks above/below (or left/right of) the whitespace band.
    """
    if len(blocks) < 2:
        return None
    starts = sorted({round(b["axes"][axis], 2) for b in blocks})
    ends = sorted({round(b["axes"][axis + 1], 2) for b in blocks})
    best_gap, best_cut = 0.0, None
    for cut in starts:
        left = [b for b in blocks if b["axes"][axis + 1] <= cut]
        right = [b for b in blocks if b["axes"][axis] >= cut]
        if len(left) + len(right) != len(blocks) or not left or not right:
            continue
        gap = min(b["axes"][axis] for b in right) - max(b["axes"][axis + 1] for b in left)
        if gap >= min_gap and gap > best_gap:
            best_gap, best_cut = gap, cut
    if best_cut is None:
        return None
    left = [b for b in blocks if b["axes"][axis + 1] <= best_cut]
    right = [b for b in blocks if b["axes"][axis] >= best_cut]
    blocks[:] = left + right
    return len(left)


def reading_order(blocks: List[Dict[str, Any]], page_width: float, page_height: float,
                  line_height: float = 12.0, depth: int = 0) -> List[Dict[str, Any]]:
    """Order blocks the way a reader would: bands top to bottom, columns inside.

    The recursion alternates: the widest clean horizontal band splits the page
    into strips, then the widest clean vertical band splits a strip into
    columns, and each part is ordered the same way. Depth is capped so a
    pathological page cannot recurse without bound.
    """
    if len(blocks) <= 1 or depth > 24:
        return sorted(blocks, key=lambda b: (round(b["axes"][0], 1), b["axes"][2]))
    work = list(blocks)
    cut = _best_cut(work, 0, max(2.0, line_height * 0.6))
    if cut is None:
        cut = _best_cut(work, 2, max(4.0, page_width * 0.02))
    if cut is None:
        return sorted(work, key=lambda b: (round(b["axes"][0], 1), round(b["axes"][2], 1)))
    left, right = work[:cut], work[cut:]
    return (reading_order(left, page_width, page_height, line_height, depth + 1)
            + reading_order(right, page_width, page_height, line_height, depth + 1))


def assign_columns(ordered: List[Dict[str, Any]]) -> int:
    """Number the vertical bands the blocks fall into, and tag each block.

    ``block["column"]`` used to be the block's index in the reading order, which
    is a position, not a column: a two-column page reported columns 0..30 and
    the stored structure could not be used to reconstruct the layout. The bands
    are computed on the blocks' own ``u`` axis (so a rotated page bands along
    its baseline), and the reading position is kept as ``block["order"]``.
    """
    if not ordered:
        return 0
    bands: List[List[float]] = []
    span_of: Dict[int, int] = {}
    for position, block in enumerate(sorted(ordered, key=lambda b: b["axes"][2])):
        u0, u1 = block["axes"][2], block["axes"][3]
        if bands and u0 <= bands[-1][1] + 2:
            bands[-1][1] = max(bands[-1][1], u1)
        else:
            bands.append([u0, u1])
        span_of[id(block)] = len(bands) - 1
    for position, block in enumerate(ordered):
        block["column"] = span_of.get(id(block), 0)
        block["order"] = position
    return max(1, len(bands))


def columns_of(ordered: List[Dict[str, Any]], page_width: float) -> int:
    """How many distinct column bands the ordered blocks fall into."""
    if not ordered:
        return 0
    bands: List[List[float]] = []
    for block in sorted(ordered, key=lambda b: b["axes"][2]):
        u0, u1 = block["axes"][2], block["axes"][3]
        if bands and u0 <= bands[-1][1] + 2:
            bands[-1][1] = max(bands[-1][1], u1)
        else:
            bands.append([u0, u1])
    return max(1, len(bands))


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
def tables_from_page(page: Any) -> List[Dict[str, Any]]:
    """Grids MuPDF can find on the page, as row-major cell text.

    ``find_tables`` needs the page's own drawing lines or a lattice of
    consistent columns; it raises on odd pages, so failure is recorded as "no
    tables" and the flow text - which still contains every cell - is unaffected.
    """
    finder = getattr(page, "find_tables", None)
    if finder is None:
        return []
    try:
        found = finder()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for index, table in enumerate(getattr(found, "tables", []) or [], 1):
        try:
            rows = [[("" if cell is None else str(cell)) for cell in row]
                    for row in table.extract()]
        except Exception:
            continue
        bbox = tuple(float(v) for v in (getattr(table, "bbox", None) or (0, 0, 0, 0)))
        out.append({
            "table_number": index,
            "bbox": bbox,
            "rows": rows,
            "row_count": len(rows),
            "column_count": max((len(r) for r in rows), default=0),
            "text": "\n".join("\t".join(r) for r in rows),
        })
    return out


# --------------------------------------------------------------------------- #
# The page model
# --------------------------------------------------------------------------- #
def page_model(page: Any, want_tables: bool = True) -> Dict[str, Any]:
    """Everything about one page's text: order, geometry, structure, quality."""
    text_raw = page.get_text("text") or ""
    normalized_raw, normal_stats = normalize_text(text_raw)

    blocks = blocks_from_page(page)
    text_blocks = [b for b in blocks if b["type"] == "text" and b["text"].strip()]
    line_heights = []
    for block in text_blocks:
        boxes = block.get("line_boxes") or []
        if len(boxes) >= 1:
            line_heights.extend(max(1.0, box[3] - box[1]) for box in boxes)
    line_height = statistics.median(line_heights) if line_heights else 12.0

    for block in text_blocks:
        v0, v1, u0, u1 = _axes(block["bbox"], block["dir"])
        block["axes"] = (v0, v1, u0, u1)

    rect = page.rect
    ordered = reading_order(text_blocks, rect.width, rect.height, line_height)

    columns = assign_columns(ordered)

    pieces: List[str] = []
    joins = 0
    normal = {"ligatures": 0, "invisible": 0, "spaces": 0}
    for block in ordered:
        fixed, block_joins = dehyphenate(normalize_text(block["text"])[0])
        block_stats = normalize_text(block["text"])[1]
        for key in normal:
            normal[key] += block_stats[key]
        joins += block_joins
        block["text"] = fixed
        pieces.append(fixed)

    text = "\n".join(p for p in pieces if p)
    # Dehyphenate across block boundaries too: a paragraph can be split into
    # several blocks by a font change mid-word.
    text, cross_joins = dehyphenate(text)
    joins += cross_joins

    tables = tables_from_page(page) if want_tables else []
    stats = {
        "chars": len(text),
        "words": len(text.split()),
        "blocks": len(text_blocks),
        "image_blocks": sum(1 for b in blocks if b["type"] == "image"),
        "hyphen_joins": joins,
        "ligatures": normal["ligatures"],
        "invisible_removed": normal["invisible"],
        "odd_spaces": normal["spaces"],
        "tables": len(tables),
        "quality": round(text_quality(text), 3),
        "line_height": round(line_height, 2),
    }
    return {
        "text": text,
        "text_raw": normalized_raw,
        "blocks": ordered,
        "columns": columns,
        "tables": tables,
        "rotation": int(getattr(page, "rotation", 0) or 0),
        "width": round(rect.width, 2),
        "height": round(rect.height, 2),
        "stats": stats,
    }
