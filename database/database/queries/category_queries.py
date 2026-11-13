from .base_queries import BaseQueries

class CategoryQueries(BaseQueries):
    """SQL queries related to categories"""
    
    @staticmethod
    def get_by_id() -> str:
        """Get category by ID"""
        return """
            SELECT c.id, w.word as category_name, c.word_id, c.parent_id
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            WHERE c.id = %s
        """
    
    @staticmethod
    def list_all_join() -> str:
        """List all categories with hierarchy info"""
        return """
            SELECT c.id, w.word as category_name, c.word_id, c.parent_id
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            ORDER BY w.word
            LIMIT %s
        """
    
    @staticmethod
    def list_all() -> str:
        """List all categories"""
        return """
            SELECT id, word_id, parent_id FROM categorys
        """

    @staticmethod
    def get_by_word_id() -> str:
        """Get category by word ID"""
        return "SELECT id, parent_id FROM categorys WHERE word_id = %s"
    
    @staticmethod
    def insert_category() -> str:
        """Insert new category"""
        return "INSERT INTO categorys (word_id, parent_id) VALUES (%s, %s) RETURNING id"
    
    @staticmethod
    def check_category_exists() -> str:
        """Check if category exists"""
        return "SELECT id FROM categorys WHERE word_id = %s"
    
    @staticmethod
    def get_categories_by_file() -> str:
        """Get categories for a file (file resolved to canonical content)"""
        return """
            SELECT DISTINCT c.id, w.word as name, COUNT(DISTINCT wc.word_id) as word_count
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            LEFT JOIN words_categorys wc ON c.id = wc.category_id
            LEFT JOIN words_hashs wp ON wc.word_id = wp.word_id
            WHERE wp.hash_id = (
                SELECT hc.hash_id
                FROM paths p
                JOIN hash_contexts hc ON hc.id = p.context_id
                WHERE p.id = %s
            )
            GROUP BY c.id, w.word
        """
    
    @staticmethod
    def search_categories() -> str:
        """Search categories with file counts"""
        return """
            SELECT c.id, w.word as name, 
                   COUNT(DISTINCT p.id) as file_count
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            LEFT JOIN words_categorys wc ON c.id = wc.category_id
            LEFT JOIN words_hashs wp ON wc.word_id = wp.word_id
            LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
            LEFT JOIN paths p ON p.context_id = hc.id
            WHERE %s IS NULL OR w.word ILIKE %s
               OR (%s::int[] IS NOT NULL AND c.word_id = ANY(%s::int[]))
            GROUP BY c.id, w.word
            ORDER BY file_count DESC, w.word ASC
            LIMIT %s OFFSET %s
        """
    
    @staticmethod
    def get_categories_with_stats() -> str:
        """Get categories with statistics"""
        return """
            SELECT 
                c.id, 
                w.word as name,
                c.parent_id,
                COALESCE(file_counts.file_count, 0) as file_count,
                COALESCE(word_counts.word_count, 0) as word_count
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            LEFT JOIN (
                SELECT 
                    wc.category_id,
                    COUNT(DISTINCT p.id) as file_count
                FROM words_categorys wc
                LEFT JOIN words_hashs wp ON wc.word_id = wp.word_id
                LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
                LEFT JOIN paths p ON p.context_id = hc.id
                GROUP BY wc.category_id
            ) file_counts ON c.id = file_counts.category_id
            LEFT JOIN (
                SELECT 
                    category_id,
                    COUNT(DISTINCT word_id) as word_count
                FROM words_categorys
                GROUP BY category_id
            ) word_counts ON c.id = word_counts.category_id
            ORDER BY file_count DESC, w.word ASC
            LIMIT %s
        """
    
    @staticmethod
    def get_main_categories() -> str:
        """Get main categories (no parent) with subcategory counts"""
        return """
            SELECT 
                c.id, 
                w.word as name,
                c.parent_id,
                COUNT(child.id) as subcategory_count
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            LEFT JOIN categorys child ON child.parent_id = c.id
            WHERE c.parent_id IS NULL
            GROUP BY c.id, w.word, c.parent_id
            ORDER BY w.word
        """
    
    @staticmethod
    def get_subcategories() -> str:
        """Get subcategories of a parent category"""
        return """
            SELECT c.id, w.word as name, c.word_id, c.parent_id
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            WHERE c.parent_id = %s
            ORDER BY w.word
        """
    
    @staticmethod
    def get_parent_category() -> str:
        """Get parent category of a category"""
        return """
            SELECT c.id, w.word as name, c.word_id, c.parent_id
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            WHERE c.id = (SELECT parent_id FROM categorys WHERE id = %s)
        """
    
    @staticmethod
    def update_category_by_id():
        """Update category word by category ID"""
        return"""
            UPDATE categorys
            SET word_id = %s
            WHERE id = %s
            RETURNING id;
        """
    
    @staticmethod
    def update_category_by_word_id():
        """Update category word by category ID"""
        return"""
            UPDATE categorys
            SET word_id = %s
            WHERE word_id = %s
            RETURNING id;
        """
    
    @staticmethod
    def update_parent_id():
        """Update parent_id of a category"""
        return """
            UPDATE categorys
            SET parent_id = %s
            WHERE id = %s
            RETURNING id;
        """

    
    
