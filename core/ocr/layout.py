"""Assemble recognised blocks into lines using their positions.

An OCR engine returns text fragments with boxes. The fragments arrive in
whatever order the detector produced them, and concatenating them with
newlines asserts that every fragment is its own line - which is false. Two
measured corpus failures came from exactly that assertion:

* ``Documents/testPDF_AdobeIllustrator.pdf`` - the title lies on a curve, so
  the detector cut "Revere" into two boxes; joined by newline the stored text
  read ``TheR\\n\\nGolf Club``, losing the word space and inventing a line
  break in the middle of a word;
* table rows and label/value pairs elsewhere - fragments of one visual line
  became separate lines, so a number and its unit were no longer adjacent.

The rules here are geometric, not linguistic: a line is a band of boxes whose
vertical extents overlap, boxes are ordered left to right inside it, and a
gap wider than a fraction of the character height is a word space. Nothing
here guesses at characters, so it can reorder and re-punctuate but never
invent text.

All coordinates are source-image pixels, the convention ``OcrBlock.bbox``
documents.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

#: A box joins a line when this share of the shorter box's height overlaps the
#: line's band. 0.5 tolerates tilted boxes (a curved title) and clipped
#: ascenders while keeping a genuinely lower line separate.
LINE_OVERLAP = 0.5

#: Fallback for boxes that share a centre within this multiple of the band
#: height, which catches near-zero-height boxes (dots, dashes).
CENTRE_TOLERANCE = 0.55

#: A horizontal gap wider than this multiple of the character height is a word
#: space; anything smaller is where a single glyph was cut and is joined dry.
SPACE_GAP_RATIO = 0.25

#: A vertical gap larger than this multiple of the line height is a paragraph
#: break and is stored as a blank line.
PARAGRAPH_GAP_RATIO = 1.9


class _Line:
    """A horizontal band of blocks."""

    __slots__ = ("blocks", "y0", "y1", "height")

    def __init__(self, block) -> None:
        x0, y0, x1, y1 = block.bbox
        self.blocks = [block]
        self.y0 = float(y0)
        self.y1 = float(y1)
        self.height = max(1.0, float(y1) - float(y0))

    @property
    def centre(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def overlap_with(self, block) -> float:
        x0, y0, x1, y1 = block.bbox
        top = max(self.y0, float(y0))
        bottom = min(self.y1, float(y1))
        if bottom <= top:
            return 0.0
        shorter = min(self.height, max(1.0, float(y1) - float(y0)))
        return (bottom - top) / shorter

    def centre_distance(self, block) -> float:
        x0, y0, x1, y1 = block.bbox
        return abs(((float(y0) + float(y1)) / 2.0) - self.centre)

    def add(self, block) -> None:
        x0, y0, x1, y1 = block.bbox
        self.blocks.append(block)
        self.y0 = min(self.y0, float(y0))
        self.y1 = max(self.y1, float(y1))
        self.height = max(1.0, self.y1 - self.y0)


def _height(block) -> float:
    return max(1.0, float(block.bbox[3]) - float(block.bbox[1]))


def _join_line(blocks: Sequence) -> str:
    ordered = sorted(blocks, key=lambda b: float(b.bbox[0]))
    parts: List[str] = []
    previous = None
    for block in ordered:
        text = (block.text or "").strip()
        if not text:
            continue
        if previous is not None:
            gap = float(block.bbox[0]) - float(previous.bbox[2])
            font = min(_height(previous), _height(block))
            if gap > SPACE_GAP_RATIO * font:
                parts.append(" ")
        parts.append(text)
        previous = block
    return "".join(parts)


def reconstruct_lines(blocks: Sequence, *, paragraph_gap: Optional[float] = None) -> str:
    """Return ``blocks`` as text whose line structure follows their boxes.

    Blocks without a box cannot be placed; they are kept, one per line, after
    the positioned text, because dropping recognised words to preserve a
    layout that was never measured would be the larger loss.
    """
    positioned = [b for b in blocks if getattr(b, "bbox", None)]
    unplaced = [b for b in blocks if not getattr(b, "bbox", None)]

    lines: List[_Line] = []
    for block in sorted(positioned, key=lambda b: (float(b.bbox[1]), float(b.bbox[0]))):
        best = None
        best_score = 0.0
        for line in lines:
            overlap = line.overlap_with(block)
            if overlap >= LINE_OVERLAP and overlap > best_score:
                best, best_score = line, overlap
                continue
            if overlap <= 0.0 and line.centre_distance(block) <= CENTRE_TOLERANCE * line.height:
                # Near-zero-height boxes never overlap enough to qualify.
                score = CENTRE_TOLERANCE * line.height - line.centre_distance(block)
                if score > best_score:
                    best, best_score = line, score
        if best is None:
            lines.append(_Line(block))
        else:
            best.add(block)

    lines.sort(key=lambda line: (line.centre, min(float(b.bbox[0]) for b in line.blocks)))

    ratio = PARAGRAPH_GAP_RATIO if paragraph_gap is None else paragraph_gap
    out: List[str] = []
    previous: Optional[_Line] = None
    for line in lines:
        text = _join_line(line.blocks)
        if not text:
            continue
        if previous is not None:
            gap = line.y0 - previous.y1
            if gap > ratio * max(previous.height, line.height):
                out.append("\n\n")
            else:
                out.append("\n")
        out.append(text)
        previous = line

    for block in unplaced:
        text = (block.text or "").strip()
        if not text:
            continue
        if out:
            out.append("\n")
        out.append(text)
    return "".join(out)
