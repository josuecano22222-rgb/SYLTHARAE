"""
Performance Optimization Utilities
Provides caching decorators and query optimization helpers
"""

from functools import wraps
from flask import request, jsonify, current_app
from datetime import timedelta
import time
import logging

from Api.utils import execute_query
from core.serialization import pack_int_list, unpack_int_list

logger = logging.getLogger(__name__)


def cache_response(timeout=300, key_prefix='api_cache'):
    """
    Cache API response decorator.
    
    Args:
        timeout: Cache timeout in seconds (default: 5 minutes)
        key_prefix: Cache key prefix
    """
    def decorator(f):
        """Inner decorator function for caching"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            """Decorated function with caching support"""
            # Check if caching is enabled
            cache = getattr(current_app, 'cache', None)
            if not cache:
                return f(*args, **kwargs)
            
            # Generate cache key
            cache_key = f"{key_prefix}:{request.path}:{str(sorted(request.args.items()))}"
            
            # Try to get from cache
            cached = cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache HIT: {cache_key}")
                return jsonify(cached)
            
            # Execute function
            result = f(*args, **kwargs)
            
            # Cache the result if it's a successful response
            if hasattr(result, 'status_code') and result.status_code == 200:
                try:
                    if hasattr(result, 'get_json'):
                        json_data = result.get_json()
                        if json_data:
                            cache.set(cache_key, json_data, timeout=timeout)
                            logger.debug(f"Cache SET: {cache_key} (TTL: {timeout}s)")
                except Exception as e:
                    logger.debug(f"Cache SET failed: {e}")
            
            return result
        return decorated_function
    return decorator


def optimize_query_params():
    """
    Optimize common query parameters for better database performance.
    Returns optimized parameters dict.
    """
    params = {}
    
    # Limit pagination to reasonable values
    limit = request.args.get('limit', type=int)
    if limit:
        params['limit'] = min(max(1, limit), 1000)  # Cap at 1000
    else:
        params['limit'] = 50  # Default
    
    # Optimize offset (prefer cursor-based pagination)
    offset = request.args.get('offset', type=int)
    if offset:
        params['offset'] = max(0, offset)
    
    # Get cursor for cursor-based pagination
    cursor = request.args.get('cursor', type=int)
    if cursor:
        params['cursor'] = cursor
    
    return params


def batch_load_words(word_ids):
    """
    Batch load words to avoid N+1 queries.
    
    Args:
        word_ids: List of word IDs
        
    Returns:
        Dict mapping word_id -> word text
    """
    if not word_ids:
        return {}
    
    
    # Batch load all words in one query
    placeholders = ','.join(['%s'] * len(word_ids))
    query = f"SELECT id, word FROM words WHERE id IN ({placeholders})"
    
    try:
        results = execute_query(query, tuple(word_ids), fetch="all", use_cache=True)
        if results:
            return {row[0]: row[1] for row in results}
    except Exception as e:
        logger.error(f"Error batch loading words: {e}")
    
    return {}


def batch_load_keywords(keyword_ids):
    """
    Batch load keywords to avoid N+1 queries.
    
    Args:
        keyword_ids: List of keyword IDs
        
    Returns:
        Dict mapping keyword_id -> keyword data
    """
    if not keyword_ids:
        return {}
    
    
    # Batch load all keywords
    placeholders = ','.join(['%s'] * len(keyword_ids))
    query = f"SELECT id, keyword FROM keywords WHERE id IN ({placeholders})"
    
    try:
        results = execute_query(query, tuple(keyword_ids), fetch="all", use_cache=True)
        keyword_map = {}
        
        if results:
            # Get all word IDs from keywords
            all_word_ids = set()
            keyword_word_map = {}
            
            for row in results:
                keyword_id = row[0]
                keyword_bytes = row[1]
                try:
                    if keyword_bytes:
                        word_ids = unpack_int_list(keyword_bytes)
                        if word_ids and isinstance(word_ids, list):
                            keyword_word_map[keyword_id] = word_ids
                            all_word_ids.update(word_ids)
                except Exception as e:
                    logger.debug(f"Error unpickling keyword {keyword_id}: {e}")
            
            # Batch load all words
            word_dict = batch_load_words(list(all_word_ids))
            
            # Build keyword text
            for keyword_id, word_ids in keyword_word_map.items():
                words = [word_dict.get(wid, '') for wid in word_ids if wid in word_dict]
                keyword_map[keyword_id] = ' '.join(words) if words else ''
        
        return keyword_map
    except Exception as e:
        logger.error(f"Error batch loading keywords: {e}")
    
    return {}


class QueryTimer:
    """Context manager for timing database queries"""
    
    def __init__(self, query_name="Query"):
        self.query_name = query_name
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time:
            duration = time.time() - self.start_time
            if duration > 0.5:  # Log queries slower than 500ms
                logger.warning(f"⚠️ SLOW QUERY ({duration:.3f}s): {self.query_name}")
            elif duration > 0.1:  # Log queries slower than 100ms
                logger.debug(f"Query ({duration:.3f}s): {self.query_name}")
        
        return False  # Don't suppress exceptions

