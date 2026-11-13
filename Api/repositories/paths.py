from typing import Optional, Tuple, List

from Api.models.paths import PathCreate
from Api.utils import execute_query


def insert(query, params):
    """Insert through database facade"""
    try:
        if "RETURNING" not in query.upper():
            query = query.rstrip(";").rstrip() + " RETURNING id"

        result = execute_query(query, tuple(params), fetch="one")
        if result and len(result) > 0 and len(result[0]) > 0:
            return result[0][0]
        return None
    except Exception as e:
        print(f"Insert error: {e}")
        return None


class PathsRepository:
    """Repository for path database operations"""
    
    @staticmethod
    def create(data: PathCreate) -> Optional[int]:
        """
        Create a new path record.
        
        Args:
            data: PathCreate instance with path data
            
        Returns:
            Created path ID or None on failure
        """
        return insert(
            """
            INSERT INTO paths
            (file_name, file_path, file_size, file_type, file_status, file_date, date_creation, context_id)
            VALUES (%s,%s,%s,%s,%s,%s,CURRENT_DATE,%s)
            """,
            (
                data.file_name,
                data.file_path,
                data.file_size,
                data.file_type,
                data.file_status,
                data.file_date,
                data.context_id,
            ),
        )

    @staticmethod
    def get(path_id: int) -> Optional[Tuple]:
        """
        Get a path by ID.
        
        Args:
            path_id: Path ID to retrieve
            
        Returns:
            Path tuple or None if not found
        """
        return execute_query(
            """
            SELECT id, file_name, file_path, file_size, file_type, file_status, file_date, date_creation, context_id
            FROM paths WHERE id=%s
            """,
            (path_id,),
            fetch="one",
        )

    @staticmethod
    def list(offset: int = 0, limit: int = 50, file_type: Optional[str] = None, status: Optional[str] = None) -> List[Tuple]:
        """
        List paths with optional filtering.
        
        Args:
            offset: Pagination offset
            limit: Maximum number of results
            file_type: Optional file type filter
            status: Optional status filter
            
        Returns:
            List of path tuples
        """
        clauses = [
            "SELECT id, file_name, file_path, file_size, file_type, file_status, file_date, date_creation, context_id FROM paths WHERE 1=1"
        ]
        params: list = []
        if file_type:
            clauses.append("AND file_type=%s")
            params.append(file_type)
        if status:
            clauses.append("AND file_status=%s")
            params.append(status)
        clauses.append("ORDER BY date_creation DESC OFFSET %s LIMIT %s")
        params.extend([offset, limit])
        return execute_query(" ".join(clauses), tuple(params)) or []


