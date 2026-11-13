"""SQL queries for sources table"""
from .base_queries import BaseQueries

class SourceQueries(BaseQueries):
    """SQL queries related to sources"""
    
    @staticmethod
    def get_by_id() -> str:
        """Get source by ID"""
        return """
            SELECT id, name, job, importance, country, city, description,
                   accounts, note, attachments, date_creation, ownership,
                   access_status, entry_date, category_id
            FROM sources
            WHERE id = %s
        """
    @staticmethod
    def get_all() -> str:
        return "SELECT id, name FROM sources"
    
    @staticmethod
    def get_by_name() -> str:
        """Get source by name"""
        return """
            SELECT id, name, job, importance, country, city, description,
                   accounts, note, attachments, date_creation, ownership,
                   access_status, entry_date, category_id
            FROM sources
            WHERE name = %s
        """
    
    @staticmethod
    def list_sources() -> str:
        """List sources with optional search"""
        return """
            SELECT id, name, job, importance, country, city, description,
                   accounts, note, attachments, date_creation, ownership,
                   access_status, category_id
            FROM sources
            WHERE %s IS NULL OR name ILIKE %s
            ORDER BY name
            LIMIT %s
        """
    
    @staticmethod
    def insert_source() -> str:
        """Insert new source"""
        return """
            INSERT INTO sources (
                name, country, city, job, description, importance, 
                attachments, ownership, accounts, note,
                access_status, entry_date, date_creation, category_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
    
    @staticmethod
    def update_source() -> str:
        """Update source fields"""
        return """
            UPDATE sources SET 
                name = %s, country = %s, city = %s, job = %s,
                description = %s, importance = %s, accounts = %s,
                note = %s, attachments = %s, ownership = %s,
                access_status = %s, entry_date = %s, category_id = %s
            WHERE id = %s
            RETURNING id;
        """
    
    @staticmethod
    def delete_source() -> str:
        """Delete source"""
        return "DELETE FROM sources WHERE id = %s"
    
    @staticmethod
    def check_source_usage() -> str:
        """Check if source is used by any files"""
        return """
            SELECT COUNT(DISTINCT p.id)
            FROM paths p
            JOIN hash_contexts hc ON hc.id = p.context_id
            WHERE hc.source_id = %s
        """
    
    @staticmethod
    def get_source_with_stats() -> str:
        """Get source with file statistics"""
        return """
            SELECT s.id, s.name, s.job, s.country, s.city, s.importance, 
                   s.date_creation, s.ownership, s.access_status, 
                   s.category_id,
                   COUNT(DISTINCT p.id) as doc_count,
                   COUNT(DISTINCT p.file_type) as file_types,
                   SUM(p.file_size) as total_size,
                   w.word as category_name
            FROM sources s
            LEFT JOIN hash_contexts hc ON s.id = hc.source_id
            LEFT JOIN paths p ON p.context_id = hc.id
            LEFT JOIN categorys c ON s.category_id = c.id
            LEFT JOIN words w ON c.word_id = w.id
            WHERE s.id = %s
            GROUP BY s.id, s.name, s.job, s.country, s.city, s.importance, 
                     s.date_creation, s.ownership, s.access_status, 
                     s.category_id, w.word
        """
    
    @staticmethod
    def get_or_create_source() -> str:
        """Get existing source or create new one"""
        return """
            INSERT INTO sources (name, job, importance, country, date_creation) 
            VALUES (%s, %s, %s, %s, %s) 
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name 
            RETURNING id
        """

