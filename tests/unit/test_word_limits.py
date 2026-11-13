"""Unit tests for core.word_limits - PostgreSQL btree index safety.

words.word / punctuation.punctuation_text are TEXT UNIQUE columns backed
by btree indexes that refuse entries larger than 1/3 of a buffer page
(2704 bytes).  One oversized token must never reach the database: it
would abort the whole bulk insert and roll back the entire document
transaction (observed with real PST/e-mail ingests embedding multi-KB
base64/data-URI runs).
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.word_limits import MAX_WORD_BYTES, word_exceeds_db_limit  # noqa: E402


def test_short_words_pass_in_every_script():
    assert not word_exceeds_db_limit("")
    assert not word_exceeds_db_limit("hello")
    assert not word_exceeds_db_limit("שלום")      # Hebrew
    assert not word_exceeds_db_limit("مرحبا")     # Arabic
    assert not word_exceeds_db_limit("こんにちは")  # CJK
    assert not word_exceeds_db_limit("user@example.org")


def test_boundary_exactly_at_limit_passes():
    # 2000 ASCII bytes == MAX_WORD_BYTES -> allowed; the resulting index
    # row (~2 KB) still fits the 2704-byte btree ceiling.
    assert not word_exceeds_db_limit("a" * MAX_WORD_BYTES)


def test_one_byte_over_limit_is_rejected():
    assert word_exceeds_db_limit("a" * (MAX_WORD_BYTES + 1))


def test_multibyte_measured_in_bytes_not_characters():
    # Hebrew encodes at 2 bytes/char: 1100 chars = 2200 UTF-8 bytes ->
    # over the limit even though the character count is far below
    # MAX_WORD_BYTES.
    assert word_exceeds_db_limit("א" * 1100)
    # 1000 Hebrew chars = exactly 2000 bytes -> allowed.
    assert not word_exceeds_db_limit("א" * 1000)
    # Emoji encode at 4 bytes/char: 501 emoji = 2004 bytes -> rejected.
    assert word_exceeds_db_limit("😀" * 501)


def test_fast_path_char_count_boundary():
    # At most MAX_WORD_BYTES // 4 characters always fits (UTF-8 uses at
    # most 4 bytes per character) - the check must not reject these.
    assert not word_exceeds_db_limit("x" * (MAX_WORD_BYTES // 4))
    # One character past the fast path but still tiny in bytes -> allowed.
    assert not word_exceeds_db_limit("x" * (MAX_WORD_BYTES // 4 + 1))


def test_real_world_base64_blob_is_rejected():
    # Shape of the token that killed the PST ingest: an unbroken
    # multi-kilobyte base64 run from an e-mail body.
    blob = "iVBORw0KGgoAAAANSUhEUgAA" + "c3RyZWFtYmxvYg" * 300
    assert len(blob.encode("utf-8")) > 2704  # exceeds the btree maximum
    assert word_exceeds_db_limit(blob)


def test_tokenizer_emits_giant_tokens_so_the_guard_is_required():
    # Documents WHY the guard exists: the tokenizer's word pattern keeps
    # an unbroken base64/data-URI run as ONE token, so such runs really
    # do reach the storage layer and must be filtered there.
    from database.processors import get_content_processor

    blob = "iVBORw0KGgo" + "Zx9qQm9iYXNlNjQ" * 400
    tokens = get_content_processor().extract_words_with_punctuation_chunked(
        f"before {blob} after"
    )
    words = [t[0] for t in tokens if t[0]]
    assert blob.lower() in words  # single giant "word"
    assert word_exceeds_db_limit(blob)


def test_url_entities_survive_the_limit():
    # Entities are preserved whole by the tokenizer; a long-but-legal URL
    # (under the byte cap) must still be indexable.
    from database.processors import get_content_processor

    url = "https://example.org/" + "a" * 1500
    tokens = get_content_processor().extract_words_with_punctuation_chunked(
        f"see {url} now"
    )
    words = [t[0] for t in tokens if t[0]]
    assert url.lower() in words
    assert not word_exceeds_db_limit(url)
