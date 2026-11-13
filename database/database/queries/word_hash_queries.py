"""SQL queries for words_hashs (word index of canonical content).

Migration m0011 re-keyed the word index from ``words_paths`` to canonical
content (``hash_id``).  Word extraction is content-derived: one content hash
has ONE word index, shared by every context and occurrence.
"""
from .base_queries import BaseQueries


class WordHashQueries(BaseQueries):
    """SQL queries for word-to-content relationships"""

    @staticmethod
    def insert_word_hash(placeholders="(%s, %s, %s, %s)") -> str:
        """Batch-insert word/content rows (concurrency-safe)."""
        return f"""
            INSERT INTO words_hashs (hash_id, word_id, word_count, position_indexer)
            VALUES {placeholders}
            ON CONFLICT (hash_id, word_id) DO NOTHING
        """

    @staticmethod
    def insert_word_hash_one() -> str:
        """Insert one word/content row, updating counts on repeat.

        The (hash_id, word_id) unique constraint (m0011) makes this safe
        under concurrent ingestion of the same content.
        """
        return """
            INSERT INTO words_hashs (hash_id, word_id, word_count, position_indexer)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (hash_id, word_id)
            DO UPDATE SET word_count = EXCLUDED.word_count,
                          position_indexer = EXCLUDED.position_indexer
            RETURNING hash_id;
        """

    @staticmethod
    def get_word_positions_by_hash() -> str:
        """Word positions for canonical content"""
        return """
            SELECT word_id, position_indexer
            FROM words_hashs
            WHERE hash_id = %s
        """

    @staticmethod
    def get_hash_id_for_path() -> str:
        """Resolve an occurrence to its canonical content id."""
        return """
            SELECT c.hash_id
            FROM paths p
            JOIN hash_contexts c ON c.id = p.context_id
            WHERE p.id = %s
        """
