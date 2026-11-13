"""SQL queries for sides table"""
from .base_queries import BaseQueries


class SideQueries(BaseQueries):
    """SQL queries related to sides"""
    
    @staticmethod
    def get_by_id() -> str:
        """Get side by ID"""
        return """
            SELECT id, name, importance, date_creation
            FROM sides
            WHERE id = %s
        """
    @staticmethod
    def get_all() -> str:
        return "SELECT id, name FROM sides"
    
    @staticmethod
    def get_by_name() -> str:
        """Get side by name"""
        return """
            SELECT id, name, importance, date_creation
            FROM sides
            WHERE name = %s
        """
    
    @staticmethod
    def list_sides() -> str:
        """List sides with optional search"""
        return """
            SELECT id, name, importance, date_creation
            FROM sides
            WHERE %s IS NULL OR name ILIKE %s
            ORDER BY name
            LIMIT %s
        """
    
    @staticmethod
    def insert_side() -> str:
        """Insert new side"""
        return """
            INSERT INTO sides (name, importance, date_creation)
            VALUES (%s, %s, %s)
            RETURNING id
        """
    
    @staticmethod
    def update_side() -> str:
        """Update side"""
        return "UPDATE sides SET name = %s, importance = %s WHERE id = %s"
    
    @staticmethod
    def delete_side() -> str:
        """Delete side"""
        return "DELETE FROM sides WHERE id = %s"
    
    @staticmethod
    def check_side_usage() -> str:
        """Files stored under this side.

        Sides are not a column of ``hashs``: a document belongs to one source
        *and* one side through ``hash_contexts``, and ``paths.context_id``
        points at that row. The old statement selected ``hashs.side_id``, which
        does not exist, so deleting any side raised UndefinedColumn and the
        route answered 500 instead of refusing a side that is in use.
        """
        return """
            SELECT COUNT(DISTINCT p.id)
            FROM paths p
            JOIN hash_contexts hc ON hc.id = p.context_id
            WHERE hc.side_id = %s
        """
    
    @staticmethod
    def get_or_create_side() -> str:
        """Get existing side or create new one"""
        return """
            INSERT INTO sides (name, importance, date_creation) 
            VALUES (%s, %s, %s) 
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name 
            RETURNING id
        """