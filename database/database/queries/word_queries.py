"""SQL queries for words table"""
from .base_queries import BaseQueries

class WordQueries(BaseQueries):
    """SQL queries related to words"""
    
    @staticmethod
    def get_by_id() -> str:
        """Get word by ID"""
        return "SELECT id, word FROM words WHERE id = %s"
    
    @staticmethod
    def get_by_text() -> str:
        """Get word by text"""
        return "SELECT id, word FROM words WHERE word = %s"
    
    @staticmethod
    def search_words() -> str:
        """Search words with partial match"""
        return """
            SELECT id, word
            FROM words
            WHERE word ILIKE %s
            ORDER BY word
            LIMIT %s
        """
    @staticmethod
    def get_all(columns_id = 'id', columns_word = 'word'):
        return f"SELECT {columns_id}, {columns_word} FROM words"
    
    @staticmethod
    def insert_word() -> str:
        """Insert new word"""
        return """
            INSERT INTO words (word)
            VALUES (%s)
            ON CONFLICT (word) DO UPDATE
                SET word = EXCLUDED.word
            RETURNING id;
        """
    @staticmethod
    def get_words_by_ids() -> str:
        """Return ``id, word`` pairs for a batch of word ids."""
        return """
            SELECT id, word
            FROM words
            WHERE id = ANY(%s)
        """
    
    @staticmethod
    def update_word() -> str:
        """Update word text"""
        return "UPDATE words SET word = %s WHERE id = %s"
    
    @staticmethod
    def delete_word() -> str:
        """Delete word"""
        return "DELETE FROM words WHERE id = %s"
    
    @staticmethod
    def check_word_exists() -> str:
        """Check if word exists"""
        return "SELECT id FROM words WHERE word = %s"
    
    @staticmethod
    def get_word_ids_batch() -> str:
        """Get multiple word IDs at once"""
        return """
            SELECT word, id
            FROM words
            WHERE word = ANY(%s)
        """
    
    
    @staticmethod
    def get_words_by_file() -> str:
        """Get words in a file with counts (file resolved to canonical content)"""
        return """
            SELECT w.id, w.word, wp.word_count
            FROM words w
            JOIN words_hashs wp ON w.id = wp.word_id
            WHERE wp.hash_id = (
                SELECT c.hash_id
                FROM paths p
                JOIN hash_contexts c ON c.id = p.context_id
                WHERE p.id = %s
            )
            ORDER BY wp.word_count DESC
            LIMIT %s
        """
    
    @staticmethod
    def get_word_frequencies() -> str:
        """Get word frequencies for a file (file resolved to canonical content)"""
        return """
            SELECT w.word, wp.word_count
            FROM words_hashs wp
            JOIN words w ON wp.word_id = w.id
            WHERE wp.hash_id = (
                SELECT c.hash_id
                FROM paths p
                JOIN hash_contexts c ON c.id = p.context_id
                WHERE p.id = %s
            )
            ORDER BY wp.word_count DESC
            LIMIT %s
        """
    
    @staticmethod
    def get_word_frequency_total() -> str:
        """Get total frequency of word across all canonical contents"""
        return "SELECT SUM(word_count) FROM words_hashs WHERE word_id = %s"
    
    @staticmethod
    def get_word_usage_count() -> str:
        """Count stored files (occurrences) whose content uses a word"""
        return """
            SELECT COUNT(DISTINCT p.id)
            FROM words_hashs wp
            JOIN hash_contexts c ON c.hash_id = wp.hash_id
            JOIN paths p ON p.context_id = c.id
            WHERE wp.word_id = %s
        """
    
    @staticmethod
    def get_words_with_usage() -> str:
        """Get words with usage counts (paginated; usage = stored files)"""
        return """
            SELECT 
                w.id, 
                w.word,
                COALESCE(COUNT(DISTINCT p.id), 0) as usage_count
            FROM words w
            LEFT JOIN words_hashs wp ON w.id = wp.word_id
            LEFT JOIN hash_contexts c ON c.hash_id = wp.hash_id
            LEFT JOIN paths p ON p.context_id = c.id
            WHERE w.word ILIKE %s
            GROUP BY w.id, w.word
            ORDER BY usage_count DESC, w.id ASC
            LIMIT %s OFFSET %s
        """
    
    @staticmethod
    def get_email_words() -> str:
        """
        Get email addresses with filters.
        Note: ORDER BY clause is built dynamically in the repository.
        Base query supports domain filtering with all modes.
        
        Parameters (in order):
        1. like_pattern: '%@%' (must contain @)
        2. not_like_pattern: '%@%@%' (must not contain multiple @)
        3. search: search term or NULL
        4. search_pattern: '%search%' or NULL (for ILIKE)
        5. domain: domain filter or NULL (if NULL, skips all domain filtering)
        6. domain_lower_exact: domain.lower() or NULL (for exact match)
        7. domain_pattern_contains: '%domain%' or NULL (for contains match)
        8. domain_pattern_endswith: '%domain' or NULL (for endswith match)
        9. limit: page size
        10. offset: pagination offset
        """
        return """
            SELECT 
                w.word, 
                COUNT(DISTINCT p.id) as usage_count,
                COUNT(*) OVER() as total_count
            FROM words w
            LEFT JOIN words_hashs wp ON w.id = wp.word_id
            LEFT JOIN hash_contexts c ON c.hash_id = wp.hash_id
            LEFT JOIN paths p ON p.context_id = c.id
            WHERE w.word LIKE %s
              AND w.word NOT LIKE %s
              AND LENGTH(w.word) > 5
              AND (%s IS NULL OR w.word ILIKE %s)
              AND (
                  %s IS NULL OR
                  (%s IS NOT NULL AND LOWER(split_part(w.word, '@', 2)) = %s) OR
                  (%s IS NOT NULL AND LOWER(split_part(w.word, '@', 2)) LIKE %s) OR
                  (%s IS NOT NULL AND LOWER(split_part(w.word, '@', 2)) LIKE %s)
              )
              AND (%s IS NULL OR c.source_id = %s)
              AND (%s IS NULL OR c.side_id = %s)
            GROUP BY w.id, w.word
            LIMIT %s OFFSET %s
        """
    
    @staticmethod
    def get_email_domains() -> str:
        """
        Get email domains with counts, supporting filters.
        
        Parameters (in order):
        1. like_pattern: '%@%' (must contain @)
        2. not_like_pattern: '%@%@%' (must not contain multiple @)
        3. search: search term or NULL
        4. search_pattern: '%search%' or NULL (for ILIKE)
        5. domain: domain filter or NULL (if NULL, skips all domain filtering)
        6. domain_lower_exact: domain.lower() or NULL (for exact match)
        7. domain_pattern_contains: '%domain%' or NULL (for contains match)
        8. domain_pattern_endswith: '%domain' or NULL (for endswith match)
        9. limit: maximum number of domains to return
        """
        return """
            SELECT
                LOWER(split_part(w.word, '@', 2)) AS domain,
                COUNT(*) AS email_count
            FROM words w
            WHERE w.word LIKE %s
              AND w.word NOT LIKE %s
              AND LENGTH(w.word) > 5
              AND (%s IS NULL OR w.word ILIKE %s)
              AND (
                  %s IS NULL OR
                  LOWER(split_part(w.word, '@', 2)) = %s OR
                  LOWER(split_part(w.word, '@', 2)) LIKE %s OR
                  LOWER(split_part(w.word, '@', 2)) LIKE %s
              )
            GROUP BY domain
            ORDER BY email_count DESC, domain ASC
            LIMIT %s
        """