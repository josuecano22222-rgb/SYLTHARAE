"""Sentence splitting and the size ceilings a translation request obeys.

Why split at all
================

A translation is shown to the analyst next to the original, sentence by
sentence, so the two can be compared line for line. Two smaller reasons matter
as much: a document in one engine call loses the boundaries, and most engines
truncate silently once the input passes their context window - which is exactly
the failure mode where a long document comes back looking translated but with
its last third missing.

The ceilings
============

``MAX_TOKENS`` (512) is the per-segment ceiling an engine call is allowed to
see. It is an *estimate*: tokens here are whitespace-delimited words. For Latin
scripts that over-counts (a word is one or two subword tokens), and for scripts
written without spaces - which this corpus holds none of, but a pasted table
row can still arrive without spaces - it under-counts. ``MAX_CHARS`` therefore
applies as a second, absolute ceiling that no estimate can talk its way past.

``MAX_REQUEST_CHARS`` is the request ceiling the API enforces: one selection of
a million words is not a translation request, it is a mistake (or an attempt to
make the server do work it was not asked to do), and the answer says so with
the limit in it.
"""

from __future__ import annotations

import re
from typing import List

__all__ = [
    "MAX_CHARS",
    "MAX_REQUEST_CHARS",
    "MAX_TOKENS",
    "estimate_tokens",
    "normalise",
    "split_segments",
    "split_sentences",
]

#: Per-segment estimated-token ceiling handed to an engine.
MAX_TOKENS = 512
#: Per-segment character ceiling (an absolute bound the estimate cannot exceed).
MAX_CHARS = 5000
#: Whole-request ceiling for a selected passage.
MAX_REQUEST_CHARS = 20_000

#: Sentence-final punctuation used by the languages in this corpus: Latin
#: full stop/exclamation/question, the Arabic question mark (U+061F), the
#: Arabic full stop (U+06D4) and the ellipsis.
_SENTENCE_END = re.compile(r"(?<=[.!?؟۔…])\s+")


def normalise(text: str) -> str:
    """Collapse every run of whitespace to one space; strip the ends."""
    return " ".join((text or "").split())


def estimate_tokens(text: str) -> int:
    """Estimated engine tokens: whitespace-delimited words (see module doc)."""
    return len((text or "").split())


def split_sentences(text: str) -> List[str]:
    """Split *text* at sentence-final punctuation (whitespace normalised)."""
    cleaned = normalise(text)
    if not cleaned:
        return []
    return [part for part in _SENTENCE_END.split(cleaned) if part]


def _cut_long_word(word: str, max_chars: int) -> List[str]:
    return [word[i:i + max_chars] for i in range(0, len(word), max_chars)] or [word]


def _chunks_of_one_sentence(sentence: str, max_tokens: int, max_chars: int) -> List[str]:
    """Break a single over-long sentence into legal chunks, losing no word."""
    if estimate_tokens(sentence) <= max_tokens and len(sentence) <= max_chars:
        return [sentence]

    chunks: List[str] = []
    current: List[str] = []
    length = 0

    def flush() -> None:
        nonlocal current, length
        if current:
            chunks.append(" ".join(current))
        current = []
        length = 0

    for word in sentence.split():
        pieces = _cut_long_word(word, max_chars) if len(word) > max_chars else [word]
        for piece in pieces:
            added = len(piece) + (1 if current else 0)
            if current and (estimate_tokens(" ".join(current + [piece])) > max_tokens
                            or length + added > max_chars):
                flush()
                added = len(piece)
            current.append(piece)
            length += added
    flush()
    return chunks


def split_segments(text: str, max_tokens: int = MAX_TOKENS,
                   max_chars: int = MAX_CHARS) -> List[str]:
    """Split *text* into one segment per sentence, each inside both ceilings.

    One sentence per segment is what makes the side-by-side view useful - the
    reader compares a sentence with its translation - so sentences are not
    packed together just because two of them would fit. A sentence that is
    itself larger than a ceiling is cut on word boundaries (see
    :func:`_chunks_of_one_sentence`), which is also how unpunctuated text is
    handled: it is one enormous "sentence" and is chunked by the ceiling.
    """
    segments: List[str] = []
    for sentence in split_sentences(text):
        segments.extend(_chunks_of_one_sentence(sentence, max_tokens, max_chars))
    return segments
