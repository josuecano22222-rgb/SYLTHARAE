"""SQL queries for titles_content (deterministic metadata of canonical content).

Migration m0011 re-keyed titles from ``path_id`` to canonical content
(``hash_id``): a document's structural titles are content-derived.
"""
from .base_queries import BaseQueries


class TitleQueries(BaseQueries):
    """SQL queries related to titles"""
    @staticmethod
    def insert_title() -> str:
        """Insert title"""
        return """
            INSERT INTO titles_content (title_data, title_status, hash_id)
            VALUES (%s, %s, %s)
            RETURNING id
        """
    @staticmethod
    def check_title_exists() -> str:
        return """
            SELECT id FROM titles_content 
            WHERE hash_id = %s AND title_status = %s 
            LIMIT 1
        """
    @staticmethod
    def select_by_hash() -> str:
        return """
            SELECT title_data FROM titles_content WHERE hash_id = %s
        """
    @staticmethod
    def select_by_id() -> str:
        return """
            SELECT id, title_data, title_status, hash_id
            FROM titles_content
            WHERE id = %s
        """
    @staticmethod
    def select_hash_for_path() -> str:
        """Resolve an occurrence to its canonical content id."""
        return """
            SELECT c.hash_id
            FROM paths p
            JOIN hash_contexts c ON c.id = p.context_id
            WHERE p.id = %s
        """
