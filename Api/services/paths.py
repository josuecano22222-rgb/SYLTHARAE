from typing import Optional

from Api.models.paths import PathCreate, PathOut
from Api.repositories.paths import PathsRepository


class PathsService:
    @staticmethod
    def create_path(data: PathCreate) -> int:
        """
        Create a new path record in the database.
        
        Args:
            data: PathCreate model containing path information
            
        Returns:
            ID of the newly created path record
            
        Raises:
            RuntimeError: If path creation fails
        """
        new_id = PathsRepository.create(data)
        if new_id is None:
            raise RuntimeError("Failed to create path record")
        return int(new_id)

    @staticmethod
    def get_path(path_id: int) -> Optional[PathOut]:
        row = PathsRepository.get(path_id)
        if not row:
            return None
        return PathOut(
            id=row[0],
            file_name=row[1],
            file_path=row[2],
            file_size=row[3],
            file_type=row[4],
            file_status=str(row[5]),
            file_date=row[6],
            date_creation=row[7],
            context_id=row[8],
        )


