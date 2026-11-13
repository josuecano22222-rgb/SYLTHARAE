"""SQL queries for punctuation table"""
from .base_queries import BaseQueries


class PunctuationQueries(BaseQueries):
    """SQL queries related to punctuation"""
    
    @staticmethod
    def get_by_id() -> str:
        """Get punctuation by ID"""
        return """
            SELECT id, punctuation_text
            FROM punctuation
            WHERE id = %s
        """
    
    @staticmethod
    def get_all() -> str:
        """Get all punctuation entries"""
        return "SELECT id, punctuation_text FROM punctuation ORDER BY punctuation_text"
    
    @staticmethod
    def get_by_text() -> str:
        """Get punctuation by text"""
        return """
            SELECT id, punctuation_text
            FROM punctuation
            WHERE punctuation_text = %s
        """
    
    @staticmethod
    def insert_punctuation() -> str:
        """Insert new punctuation"""
        return """
            INSERT INTO punctuation (punctuation_text)
            VALUES (%s)
            ON CONFLICT (punctuation_text) DO UPDATE SET punctuation_text = EXCLUDED.punctuation_text
            RETURNING id
        """
    
    @staticmethod
    def update_punctuation() -> str:
        """Update punctuation text"""
        return """
            UPDATE punctuation 
            SET punctuation_text = %s 
            WHERE id = %s
        """
    
    @staticmethod
    def delete_punctuation() -> str:
        """Delete punctuation"""
        return "DELETE FROM punctuation WHERE id = %s"
    
    @staticmethod
    def search_punctuation() -> str:
        """Search punctuation with optional filter"""
        return """
            SELECT id, punctuation_text
            FROM punctuation
            WHERE %s IS NULL OR punctuation_text ILIKE %s
            ORDER BY punctuation_text
            LIMIT %s
        """
    
    @staticmethod
    def get_or_create_punctuation() -> str:
        """Get existing punctuation or create new one"""
        return """
            INSERT INTO punctuation (punctuation_text) 
            VALUES (%s) 
            ON CONFLICT (punctuation_text) DO UPDATE SET punctuation_text = EXCLUDED.punctuation_text
            RETURNING id
        """
    
    @staticmethod
    def get_punctuation_ids_batch() -> str:
        """Get multiple punctuation IDs at once"""
        return """
            SELECT punctuation_text, id
            FROM punctuation
            WHERE punctuation_text = ANY(%s)
        """
    
    @staticmethod
    def bulk_insert_punctuation(placeholders: str = '(%s)') -> str:
        """Bulk insert punctuation marks"""
        return f"""
            INSERT INTO punctuation (punctuation_text)
            VALUES {placeholders}
            ON CONFLICT (punctuation_text) DO NOTHING
        """
