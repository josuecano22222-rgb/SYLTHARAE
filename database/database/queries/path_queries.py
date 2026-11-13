"""SQL queries for files/paths (occurrences of canonical content).

Since migration m0011 a ``paths`` row is an OCCURRENCE: it belongs to a
context (``hash_contexts``: hash + source + side) which belongs to canonical
content (``hashs``).  Every identity join here is therefore::

    paths p -> hash_contexts c -> hashs h

with source/side read from the context (``c.source_id``/``c.side_id``), never
from the canonical row (which has none: one content, many contexts).
"""
from .base_queries import BaseQueries

#: The identity join every listing needs: occurrence -> context -> content.
_IDENTITY_JOIN = """
            JOIN hash_contexts c ON p.context_id = c.id
            JOIN hashs h ON c.hash_id = h.id
            LEFT JOIN sources s ON c.source_id = s.id
            LEFT JOIN sides si ON c.side_id = si.id
"""


class FileQueries(BaseQueries):
    """SQL queries related to files/paths"""
    
    @staticmethod
    def get_file_by_id() -> str:
        """Get complete file information by ID"""
        return f"""
            SELECT p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                   p.file_status, p.file_date, p.date_creation, c.hash_id,
                   c.source_id, c.side_id,
                   s.name as source_name, si.name as side_name
            FROM paths p
            {_IDENTITY_JOIN}
            WHERE p.id = %s
        """
    @staticmethod
    def get_all() -> str:
        return 'SELECT * FROM paths'
    
    @staticmethod
    def search_files() -> str:
        """Search files with multiple filter criteria"""
        return f"""
            SELECT p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                   p.file_status, p.file_date, p.date_creation, c.hash_id,
                   c.source_id, c.side_id,
                   s.name as source_name, si.name as side_name
            FROM paths p
            {_IDENTITY_JOIN}
            WHERE 1=1
              AND (%s IS NULL OR p.file_name ILIKE %s)
              AND (%s IS NULL OR p.file_type = %s)
              AND (%s IS NULL OR c.source_id = %s)
              AND (%s IS NULL OR c.side_id = %s)
              AND (%s IS NULL OR p.file_date >= %s)
              AND (%s IS NULL OR p.file_date <= %s)
            ORDER BY p.date_creation DESC
            LIMIT %s
        """
    
    @staticmethod
    def get_recent_files() -> str:
        """Get most recently created files"""
        return f"""
            SELECT p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                   p.file_status, p.file_date, p.date_creation, c.hash_id,
                   c.source_id, c.side_id,
                   s.name as source_name, si.name as side_name
            FROM paths p
            {_IDENTITY_JOIN}
            ORDER BY p.date_creation DESC
            LIMIT %s
        """
    
    @staticmethod
    def search_files_by_word_count() -> str:
        """Count files containing search word"""
        return """
            SELECT COUNT(DISTINCT p.id)
            FROM paths p
            JOIN hash_contexts c ON p.context_id = c.id
            JOIN words_hashs wp ON c.hash_id = wp.hash_id
            JOIN words w ON wp.word_id = w.id
            WHERE w.word ILIKE %s
        """
    
    @staticmethod
    def search_files_by_word() -> str:
        """Search files by word content with pagination"""
        return f"""
            SELECT DISTINCT ON (p.id) 
                   p.id, p.file_name, p.file_type, p.file_date,
                   COALESCE(s.name, 'Unknown') as source_name, 
                   COALESCE(si.name, 'Unknown') as side_name,
                   COUNT(wp.word_id) OVER (PARTITION BY p.id) as match_count
            FROM paths p
            {_IDENTITY_JOIN}
            JOIN words_hashs wp ON c.hash_id = wp.hash_id
            JOIN words w ON wp.word_id = w.id
            WHERE w.word ILIKE %s
            ORDER BY p.id, match_count DESC
            LIMIT %s OFFSET %s
        """
    
    @staticmethod
    def get_file_word_count() -> str:
        """Count distinct words in a file (resolved to canonical content)"""
        return """
            SELECT COUNT(DISTINCT wp.word_id)
            FROM words_hashs wp
            WHERE wp.hash_id = (
                SELECT c.hash_id
                FROM paths p
                JOIN hash_contexts c ON c.id = p.context_id
                WHERE p.id = %s
            )
        """
    
    @staticmethod
    def insert_path() -> str:
        """Insert file path (occurrence) with conflict handling"""
        return """
            INSERT INTO paths
            (file_name, file_path, file_size, file_type, file_status, file_date, context_id, date_creation, coordinates, extraction_provenance, processing_status, status_detail, attempts, parent_path_id, hierarchy_path, status_updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,NOW())
            RETURNING id;
        """
    
    @staticmethod
    def update_file_status() -> str:
        """Update file processing status"""
        return "UPDATE paths SET file_status = %s WHERE id = %s"

    @staticmethod
    def mark_partial() -> str:
        """Record that a stored object is only partially processed.

        Used when a derived step (raw display text, keywords, title) failed and
        the document was stored without it, and when a container's nested work
        did not finish. The row keeps its data; the status says plainly that
        something is missing, instead of the run reporting a clean success.

        A row can already carry a reason recorded by the reader - a container
        that named the members it could not decode, a page-capped PDF, an
        unsupported object type. Replacing that with a derived-step message
        loses *why* the object was partial in the first place, so the reasons
        are accumulated (bounded to 500 characters) and a repeated message is
        not appended twice.
        """
        return (
            "UPDATE paths SET processing_status = %s,"
            " status_detail = CASE"
            "   WHEN status_detail IS NULL OR status_detail = '' THEN %s"
            "   WHEN position(%s in status_detail) > 0 THEN status_detail"
            "   ELSE left(status_detail || '; ' || %s, 500)"
            " END,"
            " status_updated_at = NOW() WHERE id = %s"
        )

    @staticmethod
    def update_lineage() -> str:
        """Link an extracted child to the container it came from.

        parent_path_id is the queryable relationship; hierarchy_path is the
        human-readable archive::child::grandchild chain. Both are set together
        so they cannot drift apart.
        """
        return (
            "UPDATE paths SET parent_path_id = %s, hierarchy_path = %s,"
            " status_updated_at = NOW() WHERE id = %s"
        )

    @staticmethod
    def get_lineage() -> str:
        """Read a path's lineage, used to build a child's hierarchy_path."""
        return "SELECT file_name, hierarchy_path FROM paths WHERE id = %s"
    
    @staticmethod
    def check_file_processed() -> str:
        """Check if file has been processed"""
        return "SELECT id FROM paths WHERE file_path = %s AND file_status = 'Read'"
    
    @staticmethod
    def get_context_hash_by_path() -> str:
        """Resolve an occurrence to its canonical content id."""
        return """
            SELECT c.hash_id
            FROM paths p
            JOIN hash_contexts c ON c.id = p.context_id
            WHERE p.id = %s
        """

    @staticmethod
    def get_path_hash_by_id() -> str:
        """The canonical content hash string of a stored occurrence."""
        return """
            SELECT h.hash
            FROM paths p
            JOIN hash_contexts c ON c.id = p.context_id
            JOIN hashs h ON h.id = c.hash_id
            WHERE p.id = %s
        """

    @staticmethod
    def get_paths_by_hash() -> str:
        """All occurrences of one canonical content (by hash string)."""
        return """
            SELECT p.id, p.file_name, p.file_path, p.hierarchy_path,
                   p.parent_path_id, p.file_status, p.processing_status,
                   c.source_id, c.side_id
            FROM paths p
            JOIN hash_contexts c ON c.id = p.context_id
            JOIN hashs h ON h.id = c.hash_id
            WHERE h.hash = %s
            ORDER BY p.id
        """
