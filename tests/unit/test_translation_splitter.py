"""Sentence splitting and the 512-token ceiling.

A translation engine is asked for one sentence at a time: a whole document in
one call loses the sentence boundaries the reader needs to line the original up
with the translation, and most engines truncate past a fixed number of tokens.
The splitter therefore has two jobs and both are pinned here:

* split on sentence punctuation, including the Arabic question mark (U+061F),
  the Hebrew sof pasuq-like full stop is a normal '.' so it needs nothing
  special, and the ellipsis;
* never hand an engine more than ``MAX_TOKENS`` estimated tokens, even for a
  "sentence" that has no punctuation at all (a pasted table row, a URL list).

The estimate is deliberately conservative and documented: tokens are
whitespace-delimited words. That over-counts for Latin scripts (where a word is
one or two subword tokens) and can under-count for scripts written without
spaces, which is why a character ceiling applies as well.
"""

from services.translation.text import (
    MAX_CHARS,
    MAX_TOKENS,
    estimate_tokens,
    split_segments,
    split_sentences,
)


def test_sentences_split_on_the_punctuation_the_corpus_uses():
    text = ("The meeting ended at noon. Did anyone object? Yes - twice! "
            "هل وصلت الرسالة؟ بالطبع. سأعود غدًا.")
    sentences = split_sentences(text)
    assert sentences[:3] == [
        "The meeting ended at noon.",
        "Did anyone object?",
        "Yes - twice!",
    ]
    # The Arabic question mark ends a sentence just like '?' does.
    assert "هل وصلت الرسالة؟" in sentences
    assert all(s.strip() for s in sentences)


def test_a_paragraph_without_punctuation_is_still_split_by_the_ceiling():
    text = " ".join(f"word{i}" for i in range(MAX_TOKENS * 3))
    segments = split_segments(text)
    assert len(segments) >= 3
    assert all(estimate_tokens(segment) <= MAX_TOKENS for segment in segments)
    # Nothing is lost: the words are the same set, in order.
    rejoined = " ".join(segments).split()
    assert rejoined == text.split()


def test_every_segment_respects_the_token_and_character_ceilings():
    giant_word = "x" * (MAX_CHARS * 2)
    segments = split_segments(f"Short one. {giant_word}")
    assert all(len(segment) <= MAX_CHARS for segment in segments)
    assert all(estimate_tokens(segment) <= MAX_TOKENS for segment in segments)


def test_whitespace_is_normalised_and_empty_input_yields_nothing():
    assert split_segments("   \n\t  ") == []
    assert split_segments("") == []
    assert split_segments("a   b\n\nc") == ["a b c"]


def test_estimate_tokens_is_whitespace_words_and_never_zero_for_text():
    assert estimate_tokens("") == 0
    assert estimate_tokens("one") == 1
    assert estimate_tokens("one two  three") == 3
