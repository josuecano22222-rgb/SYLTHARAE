"""SQL queries for keywords table"""
from .base_queries import BaseQueries

class KeywordQueries(BaseQueries):
    """SQL queries related to keywords"""
    @staticmethod
    def get_by_id() -> str:
        """Get keyword by ID"""
        return """
            SELECT id, keyword, category_id, date_creation
            FROM keywords
            WHERE id = %s
        """
    @staticmethod
    def list_by_category() -> str:
        """List keywords by category"""
        return """
            SELECT id, keyword, category_id, date_creation
            FROM keywords
            WHERE category_id = %s
            ORDER BY date_creation DESC
            LIMIT %s
        """
    @staticmethod
    def list_all() -> str:
        """List all keywords"""
        return """
            SELECT id, keyword, category_id, date_creation
            FROM keywords
            ORDER BY date_creation DESC
            LIMIT %s
        """
    @staticmethod
    def get_all() -> str:
        return"""
            SELECT id, keyword FROM keywords
        """
    @staticmethod
    def update_by_id() -> str:
        return"""
            UPDATE keywords
            SET keyword = %s,
                category_id = %s
            WHERE id = %s
            RETURNING id;
        """
    @staticmethod
    def update_by_category_id() -> str:
        return """
            UPDATE keywords
            SET keyword = %s
            WHERE category_id = %s
            RETURNING id;
        """

    @staticmethod
    def get_keywords_by_file() -> str:
        """Get keywords in a file (file resolved to canonical content)"""
        return """
            SELECT k.id, k.keyword, k.category_id, kp.word_count
            FROM keywords k
            JOIN keywords_hashs kp ON k.id = kp.keyword_id
            WHERE kp.hash_id = (
                SELECT c.hash_id
                FROM paths p
                JOIN hash_contexts c ON c.id = p.context_id
                WHERE p.id = %s
            )
            ORDER BY kp.word_count DESC
        """
    @staticmethod
    def get_keyword_frequencies() -> str:
        """Get keyword frequencies for a file (file resolved to canonical content)"""
        return """
            SELECT k.keyword, kp.word_count
            FROM keywords_hashs kp
            JOIN keywords k ON kp.keyword_id = k.id
            WHERE kp.hash_id = (
                SELECT c.hash_id
                FROM paths p
                JOIN hash_contexts c ON c.id = p.context_id
                WHERE p.id = %s
            )
            ORDER BY kp.word_count DESC
            LIMIT %s
        """
    @staticmethod
    def insert_keyword() -> str:
        """Insert new keyword"""
        return """
            INSERT INTO keywords (keyword, category_id)
            VALUES (%s, %s)
            RETURNING id
        """
    @staticmethod
    def check_keyword_exists() -> str:
        """Check if keyword exists"""
        return """
            SELECT id FROM keywords
            WHERE keyword = %s AND category_id = %s
            LIMIT 1
        """
    @staticmethod
    def delete_keyword() -> str:
        """Delete keyword"""
        return "DELETE FROM keywords WHERE id = %s"
    
    @staticmethod
    def delete_keyword_paths() -> str:
        """Delete keyword-content associations"""
        return "DELETE FROM keywords_hashs WHERE keyword_id = %s"
    
    @staticmethod
    def get_all_keywords() -> str:
        """Get all keywords"""
        return "SELECT id, keyword FROM keywords"
    
    @staticmethod
    def get_keywords_with_usage() -> str:
        """Get keywords with usage counts (usage = stored files)"""
        return """
            SELECT 
                k.id, 
                k.keyword,
                k.category_id,
                COALESCE(COUNT(DISTINCT p.id), 0) as usage_count,
                w.word as category_name
            FROM keywords k
            LEFT JOIN keywords_hashs kp ON k.id = kp.keyword_id
            LEFT JOIN hash_contexts hc ON hc.hash_id = kp.hash_id
            LEFT JOIN paths p ON p.context_id = hc.id
            LEFT JOIN categorys c ON k.category_id = c.id
            LEFT JOIN words w ON c.word_id = w.id
            WHERE %s IS NULL OR w.word ILIKE %s
            GROUP BY k.id, k.keyword, k.category_id, w.word
            ORDER BY usage_count DESC, k.id ASC
            LIMIT %s OFFSET %s
        """

