"""Hard size limits for text tokens on their way to PostgreSQL.

Why this exists
---------------
The ``words`` and ``punctuation`` tables store their text in
``TEXT UNIQUE NOT NULL`` columns (migration m0001).  PostgreSQL backs
every UNIQUE constraint with a btree index, and a btree index refuses
any index row larger than one third of a buffer page -- 2704 bytes for
the default 8 KB block size (btree version 4):

    psycopg2.errors.ProgramLimitExceeded: index row size 3544 exceeds
    btree version 4 maximum 2704 for index "words_word_key"
    HINT:  Values larger than 1/3 of a buffer page cannot be indexed.

A single oversized token therefore aborts the whole
word-dictionary insert, rolls back the entire document transaction and
fails the storage of the complete file.  This was seen in the wild with PST/e-mail ingests:
message bodies embed multi-kilobyte unbroken base64 or ``data:`` URI
runs, and the tokenizer's word pattern matches such a run as one
giant "word".

Real tokens never come close to the limit (the longest dictionary
words are a few dozen characters and even URLs stay below ~2000), so
tokens whose UTF-8 byte length exceeds a wide safety margin are simply
refused entry to the index.  Nothing is lost for the user: the
verbatim document text is preserved in the raw display store
(``contents_raw``) for viewing and in-document search -- only the
per-token search index skips the oversized blob.
"""

# Maximum UTF-8 byte length of a single token that may be stored in a
# btree-indexed TEXT column.  2000 bytes leaves ample headroom below the
# 2704-byte index-row ceiling (index tuple overhead plus the worst case
# of failed compression), even for 4-byte UTF-8 scripts.
MAX_WORD_BYTES = 2000

# Strings of at most this many *characters* are guaranteed to fit,
# because UTF-8 encodes one character in at most 4 bytes.  Short-
# circuiting on the character count keeps the check allocation-free
# for virtually every real token.
_MAX_FAST_CHARS = MAX_WORD_BYTES // 4


def word_exceeds_db_limit(word: str) -> bool:
    """Return True when ``word`` is too large for a btree-indexed TEXT column.

    The comparison is on UTF-8 *bytes*, not characters, so multibyte
    scripts (Hebrew, Arabic, CJK, emoji) are measured exactly.
    """
    if not word:
        return False
    if len(word) <= _MAX_FAST_CHARS:
        return False
    return len(word.encode("utf-8", "surrogatepass")) > MAX_WORD_BYTES
