"""SQL queries for keywords_hashs (keyword index of canonical content).

Migration m0011 re-keyed the keyword index from ``keywords_paths`` to
canonical content (``hash_id``).  Keyword matches are derived from the
canonical word sequence: one content hash has ONE keyword index.
"""
from .base_queries import BaseQueries


class KeywordHashQueries(BaseQueries):
    """SQL queries for keyword-to-content relationships"""

    @staticmethod
    def insert_keyword_hash(placeholders="(%s,%s,%s)") -> str:
        """Batch-insert keyword/content rows (concurrency-safe)."""
        return f"""
            INSERT INTO keywords_hashs (hash_id, keyword_id, word_count)
            VALUES {placeholders}
            ON CONFLICT (hash_id, keyword_id)
            DO UPDATE SET word_count = EXCLUDED.word_count
        """

    @staticmethod
    def insert_keyword_hash_one() -> str:
        """Insert one keyword/content relationship"""
        return """
            INSERT INTO keywords_hashs (hash_id, keyword_id, word_count)
            VALUES (%s, %s, %s)
            ON CONFLICT (hash_id, keyword_id)
            DO UPDATE SET word_count = EXCLUDED.word_count
        """
