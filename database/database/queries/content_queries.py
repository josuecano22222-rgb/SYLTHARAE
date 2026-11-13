"""SQL queries for contents / contents_raw (canonical content stores).

Migration m0011 re-keyed both stores from ``path_id`` to canonical content
(``hash_id``): extracted text and display text are content-derived and exist
ONCE per content hash, shared by every context and occurrence.
"""
from .base_queries import BaseQueries


class ContentQueries(BaseQueries):
    """SQL queries related to content"""

    @staticmethod
    def insert_content() -> str:
        """Insert content chunk for canonical content"""
        return """
            INSERT INTO contents (content_data, content_date, hash_id)
            VALUES (%s, %s, %s)
            RETURNING id
        """

    @staticmethod
    def get_content_chunks() -> str:
        """Get content chunks for canonical content"""
        return """
            SELECT id, content_data, content_date
            FROM contents
            WHERE hash_id = %s
            LIMIT %s
        """

    @staticmethod
    def get_content_count() -> str:
        """Count content chunks for canonical content"""
        return "SELECT COUNT(*) FROM contents WHERE hash_id = %s"

    @staticmethod
    def load_content() -> str:
        """Load all content for canonical content"""
        return """
            SELECT id, content_data 
            FROM contents 
            WHERE hash_id=%s 
            ORDER BY id
        """

    @staticmethod
    def get_content_stats() -> str:
        """Get content statistics for canonical content"""
        return """
            SELECT 
                COUNT(*) as chunk_count,
                SUM(LENGTH(content_data)) as total_bytes
            FROM contents 
            WHERE hash_id = %s
        """

    @staticmethod
    def delete_content() -> str:
        """Delete all content for canonical content"""
        return "DELETE FROM contents WHERE hash_id = %s"

    @staticmethod
    def content_exists() -> str:
        """Process-or-reuse switch: has this content been extracted already?"""
        return "SELECT 1 FROM contents WHERE hash_id = %s LIMIT 1"

    # ------------------------------------------------------------------
    # Raw (structured) content - the display-fidelity store. See
    # migration m0010 for the rationale.
    # ------------------------------------------------------------------

    @staticmethod
    def insert_raw_content() -> str:
        """Insert one raw-content chunk"""
        return """
            INSERT INTO contents_raw (hash_id, chunk_seq, content, char_count)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (hash_id, chunk_seq) DO NOTHING
        """

    @staticmethod
    def load_raw_content() -> str:
        """Load all raw-content chunks for canonical content, in order"""
        return """
            SELECT content
            FROM contents_raw
            WHERE hash_id = %s
            ORDER BY chunk_seq
        """

    @staticmethod
    def delete_raw_content() -> str:
        """Delete all raw content for canonical content"""
        return "DELETE FROM contents_raw WHERE hash_id = %s"
