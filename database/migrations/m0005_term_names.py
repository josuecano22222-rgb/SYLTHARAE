"""Term names in every supported language: one row per name, keyed on ``words``.

Why this table exists
=====================

A category's name *is* a ``words`` row (``categorys.word_id`` is UNIQUE and
``words.word`` is UNIQUE), and a keyword is a packed list of ``words`` ids. So a
translation attached to ``words.id`` covers category names, the terms assigned to
a category and the words inside a keyword at once, and it cannot create a second
category: inserting ``سياسي`` into ``words`` would do exactly that.

Shape
=====

``geo_place_names`` (``m0001``) already answers this question for places, with a
``match_key`` column, an index on it and a script check. This table mirrors it,
so there is one convention for "a name in a language" in the schema rather than
two:

* ``match_key`` is always computed by the application (``core.i18n.term_key``),
  never accepted from a client - a wrong key silently defeats search;
* ``script`` must be the script the language is written in, except for a
  ``transliteration``, which is Latin by definition;
* one ``preferred`` name per ``(word_id, language)``, enforced by a partial
  unique index rather than by convention;
* deleting a word deletes its names (``ON DELETE CASCADE``);
* ``UNIQUE (word_id, language, match_key, name_type)`` makes the loader's
  ``ON CONFLICT`` upsert idempotent.

Nothing in ``m0001``-``m0004`` is edited. The migration is reversible: the
downgrade drops the table, its indexes and its constraints, and
``tests/integration/test_term_names_schema.py`` performs upgrade -> downgrade ->
re-upgrade against a real database.
"""

version = "0005"
name = "term_names"

#: The languages and scripts the interface already supports. The CHECK is the
#: database's copy of ``core.geo.names.LANGUAGE_SCRIPT``; a test asserts the two
#: agree, so they cannot drift.
_LANGUAGES = "'en', 'ar', 'he', 'fa', 'hr'"
_NAME_TYPES = "'preferred', 'alternate', 'transliteration'"
_SOURCES = "'curated', 'imported', 'admin', 'backfill'"

_SCRIPT_CHECK = f"""
        CHECK (
            (name_type = 'transliteration' AND script = 'Latn')
            OR (name_type <> 'transliteration' AND (
                   (language IN ('en', 'hr') AND script = 'Latn')
                OR (language IN ('ar', 'fa') AND script = 'Arab')
                OR (language = 'he' AND script = 'Hebr')
            ))
        )
"""


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE term_names (
                id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                word_id INTEGER NOT NULL
                    REFERENCES words(id) ON DELETE CASCADE ON UPDATE CASCADE,
                -- ``btrim`` alone trims spaces; a tab-and-newline name is still
                -- empty to a reader, so the whitespace set is explicit.
                name VARCHAR(255) NOT NULL
                    CHECK (btrim(name, E' \t\n\r\f\v') <> ''),
                match_key VARCHAR(255) NOT NULL
                    CHECK (btrim(match_key, E' \t\n\r\f\v') <> ''),
                language VARCHAR(8) NOT NULL CHECK (language IN ({_LANGUAGES})),
                name_type VARCHAR(16) NOT NULL CHECK (name_type IN ({_NAME_TYPES})),
                script CHAR(4) NOT NULL {_SCRIPT_CHECK},
                source VARCHAR(40) NOT NULL DEFAULT 'curated'
                    CHECK (source IN ({_SOURCES})),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (word_id, language, match_key, name_type)
            )
            """
        )
        # Query expansion looks up a normalised query, never a word id.
        cur.execute("CREATE INDEX idx_term_names_key ON term_names (match_key)")
        # Display looks up a page of word ids in one language.
        cur.execute("CREATE INDEX idx_term_names_word_language "
                    "ON term_names (word_id, language)")
        # Exactly one preferred name per word and language.
        cur.execute("CREATE UNIQUE INDEX uq_term_names_preferred "
                    "ON term_names (word_id, language) WHERE name_type = 'preferred'")


def downgrade(conn) -> None:
    """Remove everything ``upgrade`` created, in one statement set."""
    with conn.cursor() as cur:
        cur.execute("DROP INDEX IF EXISTS uq_term_names_preferred")
        cur.execute("DROP INDEX IF EXISTS idx_term_names_word_language")
        cur.execute("DROP INDEX IF EXISTS idx_term_names_key")
        cur.execute("DROP TABLE IF EXISTS term_names")
