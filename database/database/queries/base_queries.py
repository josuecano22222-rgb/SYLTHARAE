"""
Centralized Query Management System
Organizes all SQL queries by domain for easy maintenance and reuse
"""
from typing import Optional, List, Tuple
from dataclasses import dataclass


@dataclass
class QueryParams:
    """Container for query parameters"""
    query: str
    params: Optional[Tuple] = None
    description: str = ""


class BaseQueries:
    """Base class for query collections"""
    
    @staticmethod
    def format_in_clause(values: List) -> str:
        """Helper to format IN clause placeholders"""
        return ','.join(['%s'] * len(values))