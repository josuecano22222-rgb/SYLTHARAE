"""SQL queries for words_categorys table"""
from .base_queries import BaseQueries

class WordCategoryQueries(BaseQueries):
    """SQL queries for word-category relationships"""
    
    @staticmethod
    def link_word_to_category() -> str:
        """Link word to category"""
        return """
            INSERT INTO words_categorys (word_id, category_id) 
            VALUES (%s, %s)
        """
    
    @staticmethod
    def check_link_exists() -> str:
        """Check if word-category link exists"""
        return """
            SELECT 1 FROM words_categorys 
            WHERE word_id = %s AND category_id = %s
        """
    
    @staticmethod
    def get_words_by_category() -> str:
        """Get words in a category"""
        return """
            SELECT DISTINCT w.id, w.word
            FROM words w
            JOIN words_categorys wc ON w.id = wc.word_id
            WHERE wc.category_id = %s
            ORDER BY w.word
            LIMIT %s
        """
    
    @staticmethod
    def get_categories_by_word_id() -> str:
        """Get all categories (tags) for a word"""
        return """
            SELECT c.id, w.word as category_name
            FROM words_categorys wc
            JOIN categorys c ON wc.category_id = c.id
            JOIN words w ON c.word_id = w.id
            WHERE wc.word_id = %s
        """