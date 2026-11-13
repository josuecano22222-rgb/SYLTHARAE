"""
Search History Service
Search history (JSON file) and the saved-searches facade (PostgreSQL, m0016)
"""

import json
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime
from pathlib import Path
import threading

logger = logging.getLogger(__name__)

# Thread-safe storage
_history_lock = threading.Lock()

# Storage paths
STORAGE_DIR = Path(__file__).parent.parent.parent / 'data'
HISTORY_FILE = STORAGE_DIR / 'search_history.json'
SAVED_SEARCHES_FILE = STORAGE_DIR / 'saved_searches.json'

# Ensure storage directory exists
STORAGE_DIR.mkdir(exist_ok=True)


class SearchHistoryService:

    
    @staticmethod
    def add_search(
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        result_count: int = 0,
        user_id: Optional[str] = None
    ) -> None:
        """
        Add a search to history.
        
        Args:
            query: Search query string
            filters: Optional search filters
            result_count: Number of results found
            user_id: Optional user identifier
        """
        try:
            with _history_lock:
                history = SearchHistoryService._load_history()
                
                # Add new search entry. `len(history) + 1` looks like a
                # sequential id but is not one: once history is trimmed to
                # the last 100 entries below, len(history) sticks at 100
                # forever, so every entry after the 100th silently got the
                # same id (101) -- a duplicate React key downstream and, more
                # importantly, an id that can't uniquely identify a history
                # row. Mirrors the correct pattern already used by
                # SavedSearchesService.save_search below.
                next_id = max([h.get('id', 0) for h in history] + [0]) + 1
                entry = {
                    'id': next_id,
                    'query': query,
                    'filters': filters or {},
                    'result_count': result_count,
                    'user_id': user_id,
                    'timestamp': datetime.now().isoformat()
                }
                
                history.append(entry)
                
                # Keep only last 100 searches
                if len(history) > 100:
                    history = history[-100:]
                
                SearchHistoryService._save_history(history)
                
        except Exception as e:
            logger.error(f"Error adding search to history: {e}", exc_info=True)
    
    @staticmethod
    def get_history(limit: int = 20, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get search history.
        
        Args:
            limit: Maximum number of entries to return
            user_id: Optional user identifier to filter by
        
        Returns:
            List of search history entries
        """
        try:
            with _history_lock:
                history = SearchHistoryService._load_history()
                
                # Filter by user if provided
                if user_id:
                    history = [h for h in history if h.get('user_id') == user_id]
                
                # Return most recent entries
                return history[-limit:][::-1]  # Reverse to show newest first
                
        except Exception as e:
            logger.error(f"Error getting search history: {e}", exc_info=True)
            return []
    
    @staticmethod
    def clear_history(user_id: Optional[str] = None) -> None:
        """
        Clear search history.
        
        Args:
            user_id: Optional user identifier to clear only their history
        """
        try:
            with _history_lock:
                if user_id:
                    history = SearchHistoryService._load_history()
                    history = [h for h in history if h.get('user_id') != user_id]
                    SearchHistoryService._save_history(history)
                else:
                    SearchHistoryService._save_history([])
                    
        except Exception as e:
            logger.error(f"Error clearing search history: {e}", exc_info=True)
    
    @staticmethod
    def _load_history() -> List[Dict[str, Any]]:
        """Load search history from JSON file."""
        try:
            if HISTORY_FILE.exists():
                with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except Exception as e:
            logger.error(f"Error loading search history: {e}", exc_info=True)
            return []
    
    @staticmethod
    def _save_history(history: List[Dict[str, Any]]) -> None:
        """Save search history to JSON file."""
        try:
            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving search history: {e}", exc_info=True)


class SavedSearchesService:
    """Saved searches - PostgreSQL-backed since migration 0016.

    This facade keeps the historical call signatures; the storage and the
    authorisation rules live in ``Api/services/saved_searches_repository.py``.
    ``data/saved_searches.json`` is no longer read or written except for the
    one-time, idempotent, ledgered import performed on first use.
    """

    @staticmethod
    def _repo():
        from Api.services.saved_searches_repository import (
            SavedSearchRepository, ensure_legacy_imported)

        repo = SavedSearchRepository()
        try:
            ensure_legacy_imported(repo, SAVED_SEARCHES_FILE)
        except Exception as exc:  # the import must never block the feature
            logger.error("Legacy saved-search import failed (%s); it will be "
                         "retried on next start", exc.__class__.__name__, exc_info=True)
            from Api.services import saved_searches_repository as _r
            _r._reset_import_guard_for_tests()
        return repo

    @staticmethod
    def save_search(name: str, query: str, filters: Optional[Dict[str, Any]] = None,
                    user_id: Optional[int] = None) -> int:
        return SavedSearchesService._repo().create(user_id, name, query, filters)["id"]

    @staticmethod
    def get_saved_searches(user_id: Optional[int] = None,
                           is_admin: bool = False) -> List[Dict[str, Any]]:
        return SavedSearchesService._repo().list_visible(user_id, is_admin)

    @staticmethod
    def get_saved_search(search_id: int) -> Optional[Dict[str, Any]]:
        return SavedSearchesService._repo().get(search_id)

    @staticmethod
    def update_saved_search(search_id: int, name: Optional[str] = None,
                            query: Optional[str] = None,
                            filters: Optional[Dict[str, Any]] = None) -> bool:
        return SavedSearchesService._repo().update(
            search_id, name=name, query=query, filters=filters) is not None

    @staticmethod
    def delete_saved_search(search_id: int) -> bool:
        return SavedSearchesService._repo().delete(search_id)

    @staticmethod
    def mark_used(search_id: int) -> None:
        try:
            SavedSearchesService._repo().mark_used(search_id)
        except Exception as e:
            logger.error(f"Error marking saved search as used: {e}", exc_info=True)
