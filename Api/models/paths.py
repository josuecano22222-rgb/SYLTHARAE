from dataclasses import dataclass
from datetime import date
from typing import Optional, Dict, Any


@dataclass
class PathCreate:
    file_name: str
    file_path: str
    file_size: int
    file_type: str
    file_status: str = "Unread"
    file_date: Optional[date] = None
    context_id: int = 0

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "PathCreate":
        """
        Create PathCreate instance from dictionary.
        
        Args:
            data: Dictionary containing path data
            
        Returns:
            PathCreate instance
        """
        file_name = str(data.get("file_name", "")).strip()
        file_path = str(data.get("file_path", "")).strip()
        file_size = int(data.get("file_size", 0))
        file_type = str(data.get("file_type", "")).strip()
        file_status = str(data.get("file_status", "Unread"))
        file_date_val = data.get("file_date")
        context_id = int(data.get("context_id", 0))

        if not file_name or not file_path or not file_type:
            raise ValueError("file_name, file_path, and file_type are required")
        if file_size < 0:
            raise ValueError("file_size must be >= 0")
        if file_status not in ("Read", "Unread"):
            raise ValueError("file_status must be 'Read' or 'Unread'")
        if context_id <= 0:
            raise ValueError("context_id must be a positive integer")

        # Accept ISO date string or date object or None
        parsed_date: Optional[date] = None
        if file_date_val:
            if isinstance(file_date_val, date):
                parsed_date = file_date_val
            else:
                # Try ISO format
                try:
                    from datetime import datetime
                    parsed_date = datetime.fromisoformat(str(file_date_val)).date()
                except Exception:
                    raise ValueError("file_date must be ISO date string (YYYY-MM-DD) or date")

        return PathCreate(
            file_name=file_name,
            file_path=file_path,
            file_size=file_size,
            file_type=file_type,
            file_status=file_status,
            file_date=parsed_date,
            context_id=context_id,
        )


@dataclass
class PathOut:
    id: int
    file_name: str
    file_path: str
    file_size: int
    file_type: str
    file_status: str
    file_date: Optional[date]
    date_creation: date
    context_id: int


