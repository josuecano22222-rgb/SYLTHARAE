"""
Title Display Utilities
Utility functions for displaying and filtering titles
"""
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


def display_titles_sorted(titles: List[Dict], sort_by: str = 'name', 
                          reverse: bool = False) -> List[Dict]:
    """
    Return all titles sorted by a specific field.
    
    Args:
        titles: List of title dictionaries
        sort_by: Field to sort by (default: 'name')
        reverse: Sort in descending order if True
    
    Returns:
        Sorted list of titles
    """
    if not titles:
        return []
    
    try:
        return sorted(titles, key=lambda x: str(x.get(sort_by, '')).lower(), 
                     reverse=reverse)
    except Exception as e:
        logger.warning(f"Could not sort by '{sort_by}': {e}")
        return titles


def filter_titles_by_search(titles: List[Dict], search: str) -> List[Dict]:
    """
    Filter titles by search term without similarity comparison.
    
    Args:
        titles: List of title dictionaries
        search: Search term to filter by
    
    Returns:
        Filtered list of titles
    """
    if not titles or not search:
        return titles
    
    search_lower = search.lower()
    return [
        title for title in titles 
        if search_lower in title.get('name', '').lower()
    ]