"""Text helpers shared by every content detector.

One implementation of: which code points are invisible for matching (Arabic
harakat/tatweel, Hebrew niqqud/cantillation, bidi controls), sentence
splitting, and quoting the evidence sentence around a signal from the
*original* text. ``core.detection.temporal_intel`` and
``core.detection.place_intel`` both use it, so a signal's evidence sentence
is cut the same way whichever detector produced it.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from typing import Callable, List, Optional, Sequence, Tuple

#: Longest evidence sentence stored with a signal (characters). Longer
#: sentences are cut around the signal and flagged ``sentence_truncated``.
MAX_SENTENCE_CHARS = 1000

SENTENCE_END = re.compile(
    r"(?:[!?؟۔]+|\n\s*\n|(?<![\d\s])\.(?=\s+\S)|(?<=(?<!\d)\d{4})\.(?=\s+\S)|\n)")


def dropped(ch: str) -> bool:
    """True for code points ignored when matching (never for letters/digits)."""
    o = ord(ch)
    return (0x064B <= o <= 0x065F or o == 0x0670 or o == 0x0640      # harakat, tatweel
            or 0x0591 <= o <= 0x05C7 and ch not in "\u05BE\u05C3"      # niqqud/cantillation
            or o in (0x200E, 0x200F, 0x061C, 0x2066, 0x2067, 0x2068, 0x2069,
                     0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0xFEFF))


def sentences(norm: str) -> List[Tuple[int, int]]:
    """Sentence spans over a normalised text (offsets into ``norm``)."""
    spans, start = [], 0
    for m in SENTENCE_END.finditer(norm):
        if m.end() > start:
            spans.append((start, m.end()))
        start = m.end()
    if start < len(norm):
        spans.append((start, len(norm)))
    return spans


class SentenceIndex:
    """Sentence spans with O(log n) lookup of the sentence containing a
    position (a linear scan per signal is quadratic on long documents)."""

    def __init__(self, spans: Sequence[Tuple[int, int]]):
        self.spans = list(spans)
        self._starts = [s for s, _ in self.spans]

    def find(self, pos: Optional[int]) -> int:
        """Index of the sentence containing ``pos``, or -1."""
        if pos is None:
            return -1
        i = bisect_right(self._starts, pos) - 1
        if i >= 0 and self.spans[i][0] <= pos < self.spans[i][1]:
            return i
        return -1


def quote_sentence(original: str, span: Callable[[int, int], Tuple[int, int]],
                   spans: "SentenceIndex", norm_pos: Optional[int],
                   char_start: int, char_end: int,
                   max_chars: int = MAX_SENTENCE_CHARS) -> Tuple[str, int, int, bool]:
    """The sentence containing ``[char_start, char_end)``, quoted verbatim.

    ``span`` maps a normalised range to original offsets; ``norm_pos`` is the
    signal's start in normalised coordinates (``None`` when unknown, in which
    case only the signal itself is quoted). Returns ``(sentence, start, end,
    truncated)`` with ``start <= char_start < char_end <= end``.
    """
    if not isinstance(spans, SentenceIndex):
        spans = SentenceIndex(spans)
    s_start, s_end = char_start, char_end
    i = spans.find(norm_pos)
    if i >= 0:
        s_start, s_end = span(*spans.spans[i])
    while s_start < char_start and original[s_start].isspace():
        s_start += 1
    while s_end > char_end and original[s_end - 1].isspace():
        s_end -= 1
    s_start, s_end = min(s_start, char_start), max(s_end, char_end)
    truncated = False
    if s_end - s_start > max_chars:
        half = max(0, (max_chars - (char_end - char_start)) // 2)
        s_start = max(s_start, char_start - half)
        s_end = min(s_end, max(char_end + half, s_start + max_chars))
        truncated = True
    return original[s_start:s_end], s_start, s_end, truncated
