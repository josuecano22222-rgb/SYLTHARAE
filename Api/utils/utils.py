import time
import logging
import threading
from datetime import datetime, date
from collections import defaultdict

from core.serialization import unpack_int_list
from core.keyword_search import html_rank, keyword_page
from services.i18n.term_search import expand_for_search

# Unified database interface - import from centralized database module
# This provides a single source of truth for all database operations
from database import (
    get_file, get_file_content, search_files, get_recent_files,
    get_source, get_source_by_name, list_sources, search_sources, create_source, insert_source,
    get_side, get_side_by_name, list_sides, search_sides, create_side,
    get_statistics_query,
    get_processing_statistics_query,
    get_format_overview_query,
    get_category_statistics_detailed_query,
    get_period_comparison_query,
    get_email_words_db,  # Import the actual function that accepts parameters
    get_keyword_stats,
    get_categories_for_dropdown,
    list_categories
)

# Backward compatibility imports


logger = logging.getLogger(__name__)

# ==================== DATABASE CONNECTION AND QUERY EXECUTION ====================

# Global query cache instance
_query_cache = None
_query_executor = None
_query_executor_lock = threading.Lock()


def _get_query_executor():
    """Get or create query executor instance (thread-safe singleton)"""
    global _query_executor
    if _query_executor is None:
        with _query_executor_lock:
            # Double-check pattern to prevent race conditions
            if _query_executor is None:
                from database import DatabaseHub
                try:
                    _query_executor = DatabaseHub()
                    # Verify the executor's database pool is initialized
                    if not _query_executor.db._pool:
                        raise ConnectionError("DatabaseHub created but connection pool not initialized")
                except Exception as e:
                    logger.error(f"Failed to create DatabaseHub: {e}")
                    _query_executor = None
                    raise
    return _query_executor


def get_query_cache():
    """Get query cache instance (singleton) - uses enhanced cache with TTL"""
    global _query_cache
    if _query_cache is None:
        try:
            # Try to use enhanced cache
            from database import EnhancedQueryCache
            from settings import get_database_config
            
            # Get cache configuration (could be added to config)
            db_config = get_database_config()
            max_size = getattr(db_config, 'cache_max_size', 1000)
            default_ttl = getattr(db_config, 'cache_default_ttl', 300)  # 5 minutes
            max_memory_mb = getattr(db_config, 'cache_max_memory_mb', None)
            
            _query_cache = EnhancedQueryCache(
                max_size=max_size,
                default_ttl=default_ttl,
                max_memory_mb=max_memory_mb
            )
            logger.info(f"Initialized enhanced query cache (max_size={max_size}, ttl={default_ttl}s)")
        except ImportError:
            # Fallback to simple cache if enhanced cache not available
            logger.warning("Enhanced cache not available, using simple cache")
            class SimpleQueryCache:
                def __init__(self):
                    self._cache = {}
                    self._lock = threading.Lock()
                
                def get(self, key):
                    with self._lock:
                        return self._cache.get(key)
                
                def set(self, key, value):
                    with self._lock:
                        self._cache[key] = value
                
                def clear(self):
                    with self._lock:
                        self._cache.clear()
                
                def get_stats(self):
                    with self._lock:
                        return {
                            'size': len(self._cache),
                            'keys': list(self._cache.keys())[:10]  # First 10 keys for debugging
                        }
            
            _query_cache = SimpleQueryCache()
    return _query_cache


def get_query():
    """Get query cache instance (alias for get_query_cache)"""
    return get_query_cache()


def invalidate_query_cache():
    """Invalidate every in-process read cache after a committed write.

    Category/keyword changes affect dashboard aggregates, dropdowns, filtered
    lists, and text lookups at the same time. A TTL-only cache makes those
    changes appear to be lost for up to five minutes.
    """
    try:
        get_query_cache().clear()
    except Exception:
        logger.warning("Could not invalidate query cache", exc_info=True)


def execute_query(query, params=None, fetch="all", use_cache=False):
    """
    Execute a SQL query with optional caching and fetch modes.
    
    Args:
        query: SQL query string
        params: Query parameters tuple or list
        fetch: Fetch mode - "one" (single row), "all" (all rows), None (no fetch)
        use_cache: Whether to use query cache
    
    Returns:
        - If fetch="one": Single tuple or None
        - If fetch="all": List of tuples or []
        - If fetch=None: None
    """
    # INSERT/UPDATE/DELETE have no result set unless they RETURNING. The
    # default fetch="all" then raises ProgrammingError and turns a write
    # into an HTTP 500 (keyword PUT, and any other missed fetch=None).
    if fetch == "all" and isinstance(query, str):
        stripped = query.lstrip()
        verb = stripped.split(None, 1)[0].upper() if stripped else ""
        if verb in ("INSERT", "UPDATE", "DELETE") and "RETURNING" not in query.upper():
            fetch = None
    try:
        # Generate cache key if caching is enabled
        cache_key = None
        if use_cache:
            cache = get_query_cache()
            # Generate cache key from query and params
            if params:
                cache_key = f"{query}:{params}"
            else:
                cache_key = query
            
            # Enhanced cache uses query and params directly
            if hasattr(cache, 'get') and hasattr(cache, 'set'):
                # Enhanced cache - use cache_key as the key
                cached_result = cache.get(cache_key)
            else:
                # Simple cache (backward compatibility)
                cached_result = cache.get(cache_key)
            
            if cached_result is not None:
                # Handle fetch mode for cached results
                if fetch == "one":
                    return cached_result[0] if cached_result else None
                elif fetch == "all":
                    return cached_result
                return None
        
        # Execute query using DatabaseHub
        executor = _get_query_executor()
        # Use context manager for connection
        with executor._get_connection() as conn:
            cursor = conn.cursor()
            
            try:
                if params:
                    cursor.execute(query, tuple(params) if isinstance(params, list) else params)
                else:
                    cursor.execute(query)
                
                # Handle fetch modes
                if fetch == "one":
                    result = cursor.fetchone()
                elif fetch == "all":
                    result = cursor.fetchall()
                else:
                    conn.commit()
                    result = None
                
                if fetch is not None:
                    conn.commit()
                
                # Cache result if caching is enabled
                if use_cache and cache_key and result is not None:
                    cache = get_query_cache()
                    # Enhanced cache uses cache_key
                    if hasattr(cache, 'set'):
                        # Enhanced cache
                        cache.set(cache_key, result)
                    else:
                        # Simple cache (backward compatibility)
                        cache.set(cache_key, result)
                
                return result if result else (None if fetch == "one" else [])
                
            finally:
                cursor.close()
            
    except Exception as e:
        logger.error(f"Error executing query: {e}", exc_info=True)
        raise



def extract_folder_path_from_filepath(file_path):

    folders = []
    file_path = (file_path or '').replace('\\', '/')
    
    if '::' in file_path:
        # Archive file with internal path
        base, inner = file_path.split('::', 1)
        base_parts = [p for p in base.split('/') if p]
        
        if base_parts:
            # Add all folders leading to archive
            for i in range(len(base_parts) - 1):
                folders.append(base_parts[i])
            # Add archive name itself
            folders.append(base_parts[-1])
        
        # Add folders inside archive
        inner_parts = [p for p in inner.split('/') if p]
        # Drop final part if it looks like a file (has extension)
        if inner_parts and ('.' in inner_parts[-1] and len(inner_parts[-1]) - inner_parts[-1].rfind('.') <= 6):
            inner_dirs = inner_parts[:-1]
        else:
            inner_dirs = inner_parts
        folders.extend(inner_dirs)
    else:
        # Regular file path
        parts = [p for p in file_path.split('/') if p]
        if len(parts) > 1:
            # Add all folders (exclude filename)
            folders.extend(parts[:-1])
    
    return '/'.join(folders) if folders else ''



def select_info_sources():
    """Get all sources using direct query imports"""
    sources = list_sources(limit=1000)
    return {s['id']: s['name'] for s in sources}


def select_info_sides():
    """Get all sides using direct query imports"""
    sides = list_sides(limit=1000)
    return {s['id']: s['name'] for s in sides}


def select_info_file_types():
    """Get distinct file types from database with caching"""
    try:
        # Use cached query for better performance
        result = execute_query("""
            SELECT DISTINCT file_type
            FROM paths
            WHERE file_type IS NOT NULL AND file_type != ''
            ORDER BY file_type
        """, fetch="all", use_cache=True)
        if result:
            # Return as dict with file_type as both key and value for consistency
            return {ft[0]: ft[0] for ft in result if ft and len(ft) > 0}
        return {}
    except Exception as e:
        logger.error(f"Error fetching file types: {e}", exc_info=True)
        return {}


def insert_info_sources(name, job="", importance="", country="", city="", description="", accounts="", note="", attachments="", date_creation=None, ownership=None, access_status=None, date_source_discovery=None, category_id=None):
    """Create new source using direct query imports"""
    try:
        # Convert importance to float if it's a string
        if isinstance(importance, str):
            try:
                importance = float(importance) if importance else 0.5
            except (ValueError, TypeError):
                importance = 0.5
        elif importance is None or importance == "":
            importance = 0.5
        
        # Ensure job and country are not empty (required fields)
        if not job:
            job = "Unknown"
        if not country:
            country = "Unknown"
        
        # Convert date_source_discovery to ISO string if it's a datetime object
        date_discovery_str = None
        if date_source_discovery:
            if isinstance(date_source_discovery, (datetime, date)):
                date_discovery_str = date_source_discovery.isoformat()
            elif isinstance(date_source_discovery, str):
                date_discovery_str = date_source_discovery
            else:
                date_discovery_str = None
        
        return insert_source(
            name=name,
            job=job,
            importance=float(importance),
            country=country,
            city=city or None,
            description=description or None,
            accounts=accounts or None,
            note=note or None,
            attachments=attachments or None,
            ownership=ownership,
            access_status=access_status,
            date_source_discovery=date_discovery_str,
            category_id=category_id
        )
    except Exception as e:
        logger.error(f"Error inserting source: {e}", exc_info=True)
        raise


def insert_info_sides(name, importance, date_creation=None):
    """Create new side using direct query imports"""
    try:
        # Convert importance to float if it's a string
        if isinstance(importance, str):
            try:
                importance = float(importance) if importance else 0.5
            except (ValueError, TypeError):
                importance = 0.5
        elif importance is None or importance == "":
            importance = 0.5
        
        return create_side(
            name=name,
            importance=float(importance)
        )
    except Exception as e:
        logger.error(f"Error inserting side: {e}", exc_info=True)
        raise


def get_categorys_word_id():
    """Get category word IDs (fixed naming: was Get_categorys_word_id)"""
    try:
        categories = list_categories(limit=10000)
        return {cat['word_id']: cat['id'] for cat in categories}
    except Exception as e:
        logger.error(f"Error getting category word IDs: {e}")
        return {}

# Backward compatibility alias
Get_categorys_word_id = get_categorys_word_id


def select_info_categories():
    """Get all categories with names for dropdowns"""
    return get_categories_for_dropdown()


def insert_category(word_category):
    """Insert category.

    CAT-02: accepts either a category *name* (creates/reuses the backing word,
    as this function always has) or a numeric word *id* — ``/category/add``
    resolves the word first and passes its id. Previously the id was fed to
    ``INSERT INTO words`` as text, creating a bogus word named after the id
    (e.g. category "Cat Debug X" rendered as "68") and linking the category to
    that bogus word.
    """
    if isinstance(word_category, int) or (isinstance(word_category, str) and word_category.isdigit()):
        word_id = int(word_category)
        query = "INSERT INTO categorys (word_id) VALUES (%s) ON CONFLICT (word_id) DO UPDATE SET word_id = EXCLUDED.word_id RETURNING id"
        result = execute_query(query, [word_id], fetch="one")
        return result[0] if isinstance(result, (tuple, list)) else result
    query = "INSERT INTO words (word) VALUES (%s) ON CONFLICT (word) DO UPDATE SET word = EXCLUDED.word RETURNING id"
    word_id = execute_query(query, [word_category], fetch="one")
    if word_id:
        word_id = word_id[0] if isinstance(word_id, (tuple, list)) else word_id
        query = "INSERT INTO categorys (word_id) VALUES (%s) ON CONFLICT (word_id) DO UPDATE SET word_id = EXCLUDED.word_id RETURNING id"
        result = execute_query(query, [word_id], fetch="one")
        return result[0] if isinstance(result, (tuple, list)) else result
    return None


def load_text_content(file_id):
    """Load text content using queries module"""
    return get_file_content(file_id)


def load_text_keyword(keyword_id):
    """Load keyword text from keyword ID by unpickling word IDs and converting to text"""
    try:
        
        # Get the pickled keyword data from the keywords table
        query = """
            SELECT keyword 
            FROM keywords 
            WHERE id = %s
        """
        result = execute_query(query, (keyword_id,), fetch="one")
        
        if not result:
            logger.warning(f"No keyword data found for ID {keyword_id}")
            return None
        
        # For BYTEA columns, result is the bytes directly (or tuple with bytes)
        keyword_bytes = result if isinstance(result, bytes) else (result[0] if isinstance(result, tuple) else result)
        
        # Unpickle to get word IDs
        try:
            word_ids = unpack_int_list(keyword_bytes)
        except Exception as e:
            logger.error(f"Error unpickling keyword {keyword_id}: {e}")
            return None
        
        if not word_ids or not isinstance(word_ids, list):
            logger.warning(f"Invalid word_ids format for keyword {keyword_id}: {word_ids}")
            return None
        
        # Convert word IDs to actual words
        if len(word_ids) == 0:
            return None
        
        placeholders = ','.join(['%s'] * len(word_ids))
        words_query = f"""
            SELECT word 
            FROM words 
            WHERE id IN ({placeholders})
            ORDER BY ARRAY_POSITION(ARRAY[{placeholders}]::INTEGER[], id)
        """
        words_result = execute_query(words_query, word_ids + word_ids)
        
        if not words_result:
            logger.warning(f"No words found for keyword {keyword_id} with word_ids {word_ids}")
            return None
        
        # Join words into a phrase
        words_list = [word[0] for word in words_result if word and word[0]]
        keyword_text = ' '.join(words_list)
        
        return keyword_text if keyword_text else None
        
    except Exception as e:
        logger.error(f"Error loading keyword text for ID {keyword_id}: {e}")
        import traceback
        traceback.print_exc()
        return None


def load_text_title(title_id):
    """Load title text from title ID by unpickling word IDs and converting to text"""
    try:
        
        # Get the pickled title data from the titles_content table
        query = """
            SELECT title_data 
            FROM titles_content 
            WHERE id = %s
        """
        result = execute_query(query, (title_id,), fetch="one")
        
        if not result:
            logger.warning(f"No title data found for ID {title_id}")
            return None
        
        # For BYTEA columns, result is the bytes directly (or tuple with bytes)
        title_bytes = result if isinstance(result, bytes) else (result[0] if isinstance(result, tuple) else result)
        
        # Unpickle to get word IDs
        try:
            # Validate bytes before unpickling
            if not isinstance(title_bytes, (bytes, bytearray, memoryview)):
                logger.warning(f"Invalid title_bytes type for title {title_id}: {type(title_bytes)}")
                return None
            
            # Convert to bytes if needed
            if isinstance(title_bytes, memoryview):
                title_bytes = bytes(title_bytes)
            elif not isinstance(title_bytes, bytes):
                title_bytes = bytes(title_bytes)
            
            # Validate minimum size
            if len(title_bytes) < 2:
                logger.debug(f"Title {title_id} has invalid pickle data (too short)")
                return None
            
            word_ids = unpack_int_list(title_bytes)
        except (ValueError, TypeError, EOFError) as e:
            # Silently skip corrupted data to prevent log spam
            logger.debug(f"Error unpickling title {title_id}: {type(e).__name__}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error unpickling title {title_id}: {e}")
            return None
        
        if not word_ids or not isinstance(word_ids, list):
            logger.warning(f"Invalid word_ids format for title {title_id}: {word_ids}")
            return None
        
        # Convert word IDs to actual words
        if len(word_ids) == 0:
            return None
        
        placeholders = ','.join(['%s'] * len(word_ids))
        words_query = f"""
            SELECT word 
            FROM words 
            WHERE id IN ({placeholders})
            ORDER BY ARRAY_POSITION(ARRAY[{placeholders}]::INTEGER[], id)
        """
        words_result = execute_query(words_query, word_ids + word_ids)
        
        if not words_result:
            logger.warning(f"No words found for title {title_id} with word_ids {word_ids}")
            return None
        
        # Join words into a phrase
        words_list = [word[0] for word in words_result if word and word[0]]
        title_text = ' '.join(words_list)
        
        return title_text if title_text else None
        
    except Exception as e:
        logger.error(f"Error loading title text for ID {title_id}: {e}")
        import traceback
        traceback.print_exc()
        return None


def batch_load_keywords_from_rows(keyword_rows):
    """
    Batch load keywords from keyword rows/dicts.
    
    This function processes keyword rows (from database queries) and loads
    associated word data. It differs from batch_load_keywords() which takes
    keyword IDs directly.
    
    Args:
        keyword_rows: List of keyword rows (tuples or dicts) containing keyword data
        
    Returns:
        Dictionary mapping keyword_id to keyword data with associated words
        
    Note:
        This function differs from batch_load_keywords() in performance_utils.py
        which takes keyword IDs directly. This version processes rows from queries.
    """

    try:
        
        if not keyword_rows:
            return {}
        
        # Handle both tuple and dict formats
        is_dict = isinstance(keyword_rows[0], dict)
        
        # Step 1: Collect all word IDs from all keywords
        all_word_ids = set()
        keyword_word_map = {}
        
        for row in keyword_rows:
            if is_dict:
                keyword_id = row.get('id')
                keyword_bytes = row.get('keyword')
            else:
                # Assume row format: (id, keyword_bytes, ...)
                keyword_id = row[0]
                keyword_bytes = None
                
                # First check row[1] directly (it should be the keyword BYTEA)
                if len(row) > 1:
                    if isinstance(row[1], (bytes, bytearray, memoryview)):
                        keyword_bytes = row[1]
                    else:
                        # Fallback: search through remaining items
                        for item in row[2:]:
                            if isinstance(item, (bytes, bytearray, memoryview)):
                                keyword_bytes = item
                                break
                        if not keyword_bytes:
                            logger.warning(f"Could not find bytes in row for keyword {keyword_id}. Row length: {len(row)}, types: {[type(x).__name__ for x in row]}")
            
            if keyword_id and keyword_bytes:
                try:
                    word_ids = unpack_int_list(keyword_bytes)
                    if word_ids and isinstance(word_ids, list):
                        keyword_word_map[keyword_id] = word_ids
                        all_word_ids.update(word_ids)
                except Exception as e:
                    logger.warning(f"Error unpickling keyword {keyword_id}: {e}")
        
        # Step 2: Batch load all words in one query
        word_dict = {}
        if all_word_ids:
            placeholders = ','.join(['%s'] * len(all_word_ids))
            words_query = f"SELECT id, word FROM words WHERE id IN ({placeholders})"
            words_result = execute_query(words_query, list(all_word_ids))
            if words_result:
                word_dict = {row[0]: row[1] for row in words_result}
        
        # Step 3: Build keyword text map
        keyword_text_map = {}
        for keyword_id, word_ids in keyword_word_map.items():
            words = [word_dict.get(wid, '') for wid in word_ids if wid in word_dict]
            if words:
                keyword_text_map[keyword_id] = ' '.join(words)
        
        return keyword_text_map
    
    except Exception as e:
        logger.error(f"Error batch loading keywords: {e}")
        return {}


def batch_load_titles(title_rows):

    try:
        
        if not title_rows:
            return {}
        
        is_dict = isinstance(title_rows[0], dict)
        
        # Step 1: Collect all word IDs from all titles
        all_word_ids = set()
        title_word_map = {}
        
        for row in title_rows:
            if is_dict:
                title_id = row.get('id')
                title_bytes = row.get('title_data')
            else:
                title_id = row[0]
                title_bytes = None
                for item in row[1:]:
                    if isinstance(item, (bytes, bytearray, memoryview)):
                        title_bytes = item
                        break
            
            if title_id and title_bytes:
                try:
                    word_ids = unpack_int_list(title_bytes)
                    if word_ids and isinstance(word_ids, list):
                        title_word_map[title_id] = word_ids
                        all_word_ids.update(word_ids)
                except Exception as e:
                    logger.warning(f"Error unpickling title {title_id}: {e}")
        
        # Step 2: Batch load all words
        word_dict = {}
        if all_word_ids:
            placeholders = ','.join(['%s'] * len(all_word_ids))
            words_query = f"SELECT id, word FROM words WHERE id IN ({placeholders})"
            words_result = execute_query(words_query, list(all_word_ids))
            if words_result:
                word_dict = {row[0]: row[1] for row in words_result}
        
        # Step 3: Build title text map
        title_text_map = {}
        for title_id, word_ids in title_word_map.items():
            words = [word_dict.get(wid, '') for wid in word_ids if wid in word_dict]
            if words:
                title_text_map[title_id] = ' '.join(words)
        
        return title_text_map
    
    except Exception as e:
        logger.error(f"Error batch loading titles: {e}")
        return {}


def select_classification(id_content=0):
    """Select classification data"""
    query_words = """
        SELECT
            w_category.word AS item_classf,
            w.word AS word,
            wc.word_count AS counts
        FROM paths pa
        JOIN hash_contexts ha ON ha.id = pa.context_id
        JOIN hashs hh ON hh.id = ha.hash_id
        JOIN sources s ON s.id = ha.source_id
        JOIN words_hashs wc ON wc.hash_id = hh.id
        JOIN words w ON w.id = wc.word_id
        JOIN words_categorys wi ON wi.word_id = w.id
        JOIN categorys i ON i.id = wi.category_id
        JOIN words w_category ON w_category.id = i.word_id
    """
    if id_content != 0:
        query_words += " WHERE pa.id = %s"
        result = execute_query(query_words, (id_content,), fetch="all")
        return result if result else []
    result = execute_query(query_words, fetch="all")
    return result if result else []


def compute_percentage(data):
    """Compute percentage from data"""
    counts = defaultdict(int)
    for item_classf, _word, count in data:
        counts[item_classf] += count
    total = sum(counts.values())
    if total == 0:
        return {}
    return {
        item: {"count": c, "percentage": (c / total) * 100}
        for item, c in counts.items()
    }


def get_statistics():
    """Get general statistics using direct query imports"""
    return get_statistics_query()


def get_content_stats(file_id=None):

    if file_id:
        # File-specific content statistics
        query = """
            SELECT 
                COUNT(*) as chunk_count,
                SUM(LENGTH(content_data)) as total_size
            FROM contents
            WHERE hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s)
        """
        result = execute_query(query, (file_id,), fetch="one")
        if result and isinstance(result, tuple) and len(result) >= 2:
            return {
                'chunks': result[0] or 0,
                'total_size': result[1] or 0,
                'estimated_words': (result[1] or 0) // 5
            }
        return {'chunks': 0, 'total_size': 0, 'estimated_words': 0}
    else:
        # Global content statistics
        query = """
            SELECT 
                COUNT(*) as total_files,
                SUM(file_size) as total_size,
                AVG(file_size) as avg_size
            FROM paths
        """
        result = execute_query(query, fetch="one")
        if result and isinstance(result, tuple) and len(result) >= 3:
            return {
                'total_files': result[0] or 0,
                'total_size': result[1] or 0,
                'avg_size': result[2] or 0
            }
        return {'total_files': 0, 'total_size': 0, 'avg_size': 0}


def get_processing_statistics():
    """Get detailed processing statistics using direct query imports"""
    return get_processing_statistics_query()


def get_format_overview():
    """Get per-format (extension) aggregate statistics for the Format Browser."""
    return get_format_overview_query()


def get_category_statistics_detailed():
    """Get detailed category statistics using direct query imports"""
    return get_category_statistics_detailed_query()


def get_period_comparison():
    """Get period comparison statistics using direct query imports"""
    return get_period_comparison_query()


def get_processing_chart_data(days=7):
    """
    Get processing chart data for the last N days.
    
    Args:
        days: Number of days to retrieve data for
    
    Returns:
        List of tuples: [(date, count), ...]
    """
    try:
        from datetime import datetime, timedelta
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)
        
        query = """
            SELECT DATE(date_creation) as date, COUNT(*) as count
            FROM paths
            WHERE DATE(date_creation) >= %s AND DATE(date_creation) <= %s
            GROUP BY DATE(date_creation)
            ORDER BY date ASC
        """
        results = execute_query(query, (start_date, end_date), fetch="all")
        return [(row[0], row[1]) for row in results] if results else []
    except Exception as e:
        logger.error(f"Error getting processing chart data: {e}")
        return []


def get_optimized_category_files(category_id, limit=100, offset=0, source_filter=None, side_filter=None, date_from=None, date_to=None):
    """Optimized query for category files with proper filtering"""
    try:
        where_conditions = ["wc.category_id = %s"]
        params = [category_id]
        
        # Add source filter
        if source_filter:
            where_conditions.append("hc.source_id = %s")
            params.append(source_filter)
        
        # Add side filter  
        if side_filter:
            where_conditions.append("hc.side_id = %s")
            params.append(side_filter)
        
        # Add date filters
        if date_from:
            where_conditions.append("p.file_date >= %s")
            params.append(date_from)
        
        if date_to:
            where_conditions.append("p.file_date <= %s")
            params.append(date_to)
        
        where_clause = " AND ".join(where_conditions)
        
        query = f"""
            SELECT DISTINCT
                p.id,
                p.file_name,
                p.file_type,
                p.file_date,
                p.date_creation,
                COALESCE(SUM(wp.word_count), 0) as relevance,
                COALESCE(s.name, 'Unknown') as source_name,
                COALESCE(si.name, 'Unknown') as side_name
            FROM paths p
            JOIN hash_contexts hc ON p.context_id = hc.id
            JOIN words_hashs wp ON wp.hash_id = hc.hash_id
            JOIN words_categorys wc ON wc.word_id = wp.word_id
            
            LEFT JOIN sources s ON hc.source_id = s.id
            LEFT JOIN sides si ON hc.side_id = si.id
            WHERE {where_clause}
            GROUP BY p.id, p.file_name, p.file_type, p.file_date, p.date_creation, s.name, si.name
            ORDER BY relevance DESC
            LIMIT %s OFFSET %s
        """
        
        params.extend([limit, offset])
        return execute_query(query, tuple(params), fetch="all")
        
    except Exception as e:
        logger.error(f"Optimized category files query error: {e}")
        return []


def get_optimized_email_words(limit=50, offset=0, source_filter=None, side_filter=None, date_from=None, date_to=None):
    """Optimized query for email words with proper filtering"""
    try:
        page = (offset // limit) + 1 if limit > 0 else 1
        per_page = limit
        email_words, total = get_email_words(
            page=page,
            per_page=per_page,
            source_filter=source_filter,
            side_filter=side_filter,
            date_from=date_from,
            date_to=date_to
        )
        return email_words
        
    except Exception as e:
        logger.error(f"Optimized email words query error: {e}")
        return []


def get_optimized_search_results(query, file_type=None, source_id=None, side_id=None, date_from=None, date_to=None, category_id=None, limit=100, analyst_scope=None):

    try:
        where_conditions = []
        params = []
        
        # Google-like search: supports partial words, multiple words, numbers, and partial text
        if query and len(query.strip()) >= 2:
            query_clean = query.strip()
            search_terms = query_clean.split()
            
            # Build search conditions for each term with OR logic
            search_conditions = []
            
            for term in search_terms:
                # Escape special characters for ILIKE
                term_escaped = term.replace("'", "''").replace("%", "\\%").replace("_", "\\_")
                term_pattern = f'%{term_escaped}%'
                
                # Search in file names
                search_conditions.append("p.file_name ILIKE %s")
                params.append(term_pattern)
                
                # Search in content words
                search_conditions.append("""
                    EXISTS (
                        SELECT 1 FROM words_hashs wp
                        JOIN words w ON wp.word_id = w.id
                        WHERE wp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = p.id)
                        AND w.word ILIKE %s
                    )
                """)
                params.append(term_pattern)
            
            # Combine all conditions with OR (Google-like: at least one term matches)
            if search_conditions:
                where_conditions.append(f"({' OR '.join(search_conditions)})")
        
        # File type filter
        if file_type:
            where_conditions.append("p.file_type = %s")
            params.append(file_type)
        
        # Source filter
        if source_id:
            where_conditions.append("hc.source_id = %s")
            params.append(source_id)
        
        # Side filter
        if side_id:
            where_conditions.append("hc.side_id = %s")
            params.append(side_id)
        
        # Date filters
        if date_from:
            where_conditions.append("p.file_date >= %s")
            params.append(date_from)
        
        if date_to:
            where_conditions.append("p.file_date <= %s")
            params.append(date_to)
        
        # Category filter
        if category_id:
            where_conditions.append("EXISTS (SELECT 1 FROM words_hashs wp2 JOIN words_categorys wc ON wp2.word_id = wc.word_id WHERE wp2.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = p.id) AND wc.category_id = %s)")
            params.append(category_id)

        # Analyst-categorization scope filter (FR-2.x) - filters strictly on
        # analyst categorization status; smart categorization status is never
        # consulted (FR-2.4).
        from Api.services.analyst_categories import scope_condition
        scope_sql = scope_condition(analyst_scope, alias="p")
        if scope_sql:
            where_conditions.append(scope_sql)

        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        # Add relevance scoring if query exists
        relevance_select = ""
        relevance_order = "p.file_date DESC"
        query_param_for_relevance = None
        
        if query and len(query.strip()) >= 2:
            query_clean = query.strip()
            query_param_for_relevance = query_clean
            relevance_select = """,
                COALESCE((
                    SELECT 
                        SUM(
                            CASE 
                                WHEN p.file_name ILIKE '%%' || term || '%%' THEN 2.0
                                WHEN EXISTS (
                                    SELECT 1 FROM words_hashs wp
                                    JOIN words w ON wp.word_id = w.id
                                    WHERE wp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = p.id)
                                    AND w.word ILIKE '%%' || term || '%%'
                                ) THEN 1.0
                                ELSE 0.0
                            END
                        )
                    FROM unnest(string_to_array(%s, ' ')) AS term
                ), 0) as relevance_score"""
            relevance_order = "relevance_score DESC, p.file_date DESC"
        
        search_query = f"""
            SELECT DISTINCT p.id, p.file_name, p.file_type, p.file_date,
                   s.name as source_name, si.name as side_name, p.file_status{relevance_select}
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
            LEFT JOIN sources s ON hc.source_id = s.id
            LEFT JOIN sides si ON hc.side_id = si.id
            WHERE {where_clause}
            ORDER BY {relevance_order}
            LIMIT %s
        """
        
        # Add query parameter for relevance if needed
        if query_param_for_relevance:
            params.append(query_param_for_relevance)
        params.append(limit)
        
        results = execute_query(search_query, tuple(params), fetch="all", use_cache=True)
        
        return results
        
    except Exception as e:
        logger.error(f"Optimized search query error: {e}")
        return []


# ==================== WORD AND KEYWORD FREQUENCY FUNCTIONS ====================

def get_word_frequencies(file_id, limit=50):
    """
    Get word frequencies for a file from database.
    
    Args:
        file_id: Path ID (file ID)
        limit: Maximum number of results to return
    
    Returns:
        List of tuples: [(word, count), ...] sorted by count descending
    """
    try:
        query = """
            SELECT w.word, wp.word_count
            FROM words_hashs wp
            JOIN words w ON w.id = wp.word_id
            WHERE wp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s)
            ORDER BY wp.word_count DESC
            LIMIT %s
        """
        results = execute_query(query, (file_id, limit), fetch="all")
        return [(row[0], row[1]) for row in results] if results else []
    except Exception as e:
        logger.error(f"Error getting word frequencies for file {file_id}: {e}")
        return []


def get_keyword_frequencies_db(file_id, limit=50):
    """
    Get keyword frequencies for a file from database (raw BYTEA data).
    This is the database-level function that returns raw keyword bytes.
    
    Args:
        file_id: Path ID (file ID)
        limit: Maximum number of results to return
    
    Returns:
        List of tuples: [(keyword_bytes, count), ...] sorted by count descending
    """
    try:
        query = """
            SELECT k.keyword, kp.word_count
            FROM keywords_hashs kp
            JOIN keywords k ON k.id = kp.keyword_id
            WHERE kp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s)
            ORDER BY kp.word_count DESC
            LIMIT %s
        """
        results = execute_query(query, (file_id, limit), fetch="all")
        return results if results else []
    except Exception as e:
        logger.error(f"Error getting keyword frequencies from database for file {file_id}: {e}")
        return []


# ==================== FILE STATISTICS FUNCTIONS ====================

def get_file_word_count(file_id):
    """
    Get total word count for a file.
    
    Args:
        file_id: Path ID (file ID)
    
    Returns:
        Integer: Total word count
    """
    try:
        query = """
            SELECT SUM(wp.word_count)
            FROM words_hashs wp
            WHERE wp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s)
        """
        result = execute_query(query, (file_id,), fetch="one")
        if result and isinstance(result, tuple):
            return result[0] or 0
        return 0
    except Exception as e:
        logger.error(f"Error getting word count for file {file_id}: {e}")
        return 0


def get_content_count(file_id):
    """
    Get content chunk count for a file.
    
    Args:
        file_id: Path ID (file ID)
    
    Returns:
        Integer: Number of content chunks
    """
    try:
        query = """
            SELECT COUNT(*)
            FROM contents
            WHERE hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s)
        """
        result = execute_query(query, (file_id,), fetch="one")
        if result and isinstance(result, tuple):
            return result[0] or 0
        return 0
    except Exception as e:
        logger.error(f"Error getting content count for file {file_id}: {e}")
        return 0


def get_categories_by_file(file_id):
    """
    Get categories associated with a file.
    
    Args:
        file_id: Path ID (file ID)
    
    Returns:
        List of dictionaries with category information
    """
    try:
        query = """
            SELECT DISTINCT
                c.id,
                w.word as name,
                COUNT(DISTINCT wp.word_id) as word_count
            FROM paths p
            JOIN hash_contexts hc ON p.context_id = hc.id
            JOIN words_hashs wp ON wp.hash_id = hc.hash_id
            JOIN words_categorys wc ON wc.word_id = wp.word_id
            JOIN categorys c ON c.id = wc.category_id
            JOIN words w ON w.id = c.word_id
            WHERE p.id = %s
            GROUP BY c.id, w.word
            ORDER BY word_count DESC
        """
        results = execute_query(query, (file_id,), fetch="all")
        if results:
            return [
                {
                    'id': row[0],
                    'name': row[1],
                    'word_count': row[2]
                }
                for row in results
            ]
        return []
    except Exception as e:
        logger.error(f"Error getting categories for file {file_id}: {e}")
        return []


# ==================== DATABASE CONNECTION MANAGEMENT ====================

def get_connection():
    """
    Get a database connection from the DatabaseHub.
    This is a wrapper for cursor pagination and other modules that need direct connections.
    
    Returns:
        psycopg2 connection object
    """
    executor = _get_query_executor()
    # API-04 FIX: return a *connection*, not the pool's context manager.
    # The previous implementation returned executor._get_connection(), which
    # is a @contextmanager object; calling .cursor() on it raised
    # AttributeError and made every preview request fail with a 500.
    # The returned object supports BOTH call styles:
    #   conn = get_connection(); ...; return_connection(conn)
    #   with get_connection() as conn: ...   (releases to pool on exit)
    return _PooledConnection(executor)


class _PooledConnection:
    """A pooled connection usable as a raw connection and as a context manager."""

    def __init__(self, executor):
        self._executor = executor
        self._conn = executor.db.connect()
        self._released = False

    def __getattr__(self, name):
        # Delegate everything (cursor, commit, rollback, closed, ...) to the
        # real psycopg2 connection.
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                self._conn.rollback()
            else:
                self._conn.commit()
        finally:
            self.release()
        return False

    def release(self):
        if not self._released:
            self._released = True
            try:
                self._executor.db.putconn(self._conn)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Failed to return database connection to pool: %s",
                    exc.__class__.__name__,
                )

    def close(self):
        self.release()


def return_connection(conn):
    """
    Return a database connection to the pool.

    API-04 FIX: this previously was a no-op, leaking pooled connections; now
    the connection is properly released back to the shared pool. Accepts both
    :class:`_PooledConnection` wrappers and raw psycopg2 connections.
    """
    if conn is None:
        return
    try:
        if hasattr(conn, "release"):
            conn.release()
            return
        executor = _get_query_executor()
        executor.db.putconn(conn)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Failed to return database connection to pool: %s", exc.__class__.__name__
        )


# ==================== CATEGORY FUNCTIONS ====================

def get_categories_with_stats(limit: int = 100) -> list:
    """
    Get categories with statistics (file count, word count)

    Args:
        limit: Maximum number of categories to return

    Returns:
        List of category dictionaries with stats
    """
    try:
        query = """
            SELECT 
                c.id,
                w.word as name,
                c.parent_id,
                COUNT(DISTINCT p.id) as file_count,
                COUNT(DISTINCT wc.word_id) as word_count
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            LEFT JOIN words_categorys wc ON wc.category_id = c.id
            LEFT JOIN words_hashs wp ON wp.word_id = wc.word_id
            LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
            LEFT JOIN paths p ON p.context_id = hc.id
            GROUP BY c.id, w.word, c.parent_id
            ORDER BY file_count DESC, w.word
            LIMIT %s
        """
        results = execute_query(query, (limit,), fetch="all")
        if results:
            return [
                {
                    'id': row[0],
                    'name': row[1] or 'Unnamed Category',
                    'parent_id': row[2],
                    'file_count': row[3] or 0,
                    'word_count': row[4] or 0
                }
                for row in results
            ]
        return []
    except Exception as e:
        logger.error(f"Error getting categories with stats: {e}")
        return []


#: The columns a category list may be ordered by, as SQL - the allowlist that
#: turns a table header's sort key into an ORDER BY clause. Text only ever
#: comes from the caller's key here, never from the request itself.
_CATEGORY_SORT_COLUMNS = {
    'name': 'w.word',
    'files': 'file_count',
    'words': 'word_count',
    'id': 'c.id',
}


def get_categories_paged(
    search: str = None,
    page: int = 1,
    per_page: int = 20,
    sort_by: str = 'files',
    sort_order: str = 'desc',
    known_total: int = None,
) -> tuple:
    """
    Get one page of categories with statistics, searched, sorted server-side.

    The list page used to load up to a thousand categories and search, sort
    and page them in the browser; this gives the same view one page at a
    time, in the order the table headers asked for.

    Args:
        search: Optional category-name substring (case-insensitive)
        page: Page number (1-based)
        per_page: Results per page
        sort_by: One of name | files | words | id
        sort_order: 'asc' or 'desc'
        known_total: Optional first-page count reused by bounded export batches

    Returns:
        Tuple of (categories_list, total_count)
    """
    try:
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 200))
        if sort_by not in _CATEGORY_SORT_COLUMNS:
            sort_by = 'files'
        if sort_order not in ('asc', 'desc'):
            sort_order = 'desc'
        direction = 'ASC' if sort_order == 'asc' else 'DESC'
        order_column = _CATEGORY_SORT_COLUMNS[sort_by]

        where_sql = ""
        params: list = []
        if search:
            # Cross-language recall: a query in any supported language also
            # matches the categories whose name in that language normalises to
            # it (services/i18n/term_labels.py owns the guards). With no match
            # this is exactly the predicate the page always had.
            expansion = expand_for_search(search)
            if expansion.word_ids:
                where_sql = "WHERE (w.word ILIKE %s OR w.id = ANY(%s))"
                params.extend([f"%{search}%", list(expansion.word_ids)])
            else:
                where_sql = "WHERE w.word ILIKE %s"
                params.append(f"%{search}%")

        count_query = f"""
            SELECT COUNT(*)
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            {where_sql}
        """
        if known_total is None:
            total_result = execute_query(count_query, tuple(params), fetch="one")
            total = total_result[0] if total_result else 0
        else:
            total = known_total

        offset = (page - 1) * per_page
        query = f"""
            SELECT
                c.id,
                w.word as name,
                c.parent_id,
                COUNT(DISTINCT p.id) as file_count,
                COUNT(DISTINCT wc.word_id) as word_count
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            LEFT JOIN words_categorys wc ON wc.category_id = c.id
            LEFT JOIN words_hashs wp ON wp.word_id = wc.word_id
            LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
            LEFT JOIN paths p ON p.context_id = hc.id
            {where_sql}
            GROUP BY c.id, w.word, c.parent_id
            ORDER BY {order_column} {direction}, w.word ASC
            LIMIT %s OFFSET %s
        """
        results = execute_query(query, tuple(params) + (per_page, offset), fetch="all")
        categories = [
            {
                'id': row[0],
                'name': row[1] or 'Unnamed Category',
                'parent_id': row[2],
                'file_count': row[3] or 0,
                'word_count': row[4] or 0
            }
            for row in (results or [])
        ]
        return categories, total or 0
    except Exception as e:
        logger.error(f"Error getting paged categories: {e}")
        return [], 0


def get_category(category_id: int) -> dict:
    """
    Get a category by ID
    
    Args:
        category_id: Category ID
    
    Returns:
        Category dictionary or None
    """
    try:
        query = """
            SELECT c.id, w.word as name, c.parent_id
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            WHERE c.id = %s
        """
        result = execute_query(query, (category_id,), fetch="one")
        if result:
            return {
                'id': result[0],
                'name': result[1] or 'Unnamed Category',
                'parent_id': result[2]
            }
        return None
    except Exception as e:
        logger.error(f"Error getting category {category_id}: {e}")
        return None


def get_subcategories(parent_id: int, limit: int = 100) -> list:
    """
    Get subcategories of a parent category

    Args:
        parent_id: Parent category ID
        limit: Maximum number of subcategories to return

    Returns:
        List of subcategory dictionaries
    """
    try:
        query = """
            SELECT c.id, w.word as name, c.parent_id,
                   COUNT(DISTINCT p.id) as file_count,
                   COUNT(DISTINCT wc.word_id) as word_count
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            LEFT JOIN words_categorys wc ON wc.category_id = c.id
            LEFT JOIN words_hashs wp ON wp.word_id = wc.word_id
            LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
            LEFT JOIN paths p ON p.context_id = hc.id
            WHERE c.parent_id = %s
            GROUP BY c.id, w.word, c.parent_id
            ORDER BY file_count DESC, w.word
            LIMIT %s
        """
        results = execute_query(query, (parent_id, limit), fetch="all")
        if results:
            return [
                {
                    'id': row[0],
                    'name': row[1] or 'Unnamed Category',
                    'parent_id': row[2],
                    'file_count': row[3] or 0,
                    'word_count': row[4] or 0
                }
                for row in results
            ]
        return []
    except Exception as e:
        logger.error(f"Error getting subcategories for parent {parent_id}: {e}")
        return []


def get_main_categories(limit: int = 100) -> list:
    """
    Get main categories (those without parent)

    Args:
        limit: Maximum number of main categories to return

    Returns:
        List of main category dictionaries with subcategory counts
    """
    try:
        query = """
            SELECT c.id, w.word as name, c.parent_id,
                   COUNT(DISTINCT p.id) as file_count,
                   COUNT(DISTINCT wc.word_id) as word_count,
                   COUNT(child.id) as subcategory_count
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            LEFT JOIN words_categorys wc ON wc.category_id = c.id
            LEFT JOIN words_hashs wp ON wp.word_id = wc.word_id
            LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
            LEFT JOIN paths p ON p.context_id = hc.id
            LEFT JOIN categorys child ON child.parent_id = c.id
            WHERE c.parent_id IS NULL
            GROUP BY c.id, w.word, c.parent_id
            ORDER BY file_count DESC, w.word
            LIMIT %s
        """
        results = execute_query(query, (limit,), fetch="all")
        if results:
            return [
                {
                    'id': row[0],
                    'name': row[1] or 'Unnamed Category',
                    'parent_id': row[2],
                    'file_count': row[3] or 0,
                    'word_count': row[4] or 0,
                    'subcategory_count': row[5] or 0
                }
                for row in results
            ]
        return []
    except Exception as e:
        logger.error(f"Error getting main categories: {e}")
        return []


def get_category_with_children(category_id: int) -> dict:
    """
    Get a category with its subcategories

    Args:
        category_id: Category ID

    Returns:
        Category dictionary with 'subcategories' key containing list of subcategories
    """
    try:
        category = get_category(category_id)
        if not category:
            return None
        subcategories = get_subcategories(category_id)
        category['subcategories'] = subcategories
        return category
    except Exception as e:
        logger.error(f"Error getting category with children {category_id}: {e}")
        return None


def get_words_by_category(category_id: int, limit: int = 100) -> list:
    """
    Get words in a category
    
    Args:
        category_id: Category ID
        limit: Maximum number of words to return
    
    Returns:
        List of word dictionaries
    """
    try:
        query = """
            SELECT DISTINCT w.id, w.word, COUNT(DISTINCT p.id) as usage_count
            FROM words_categorys wc
            JOIN words w ON w.id = wc.word_id
            LEFT JOIN words_hashs wp ON wp.word_id = w.id
            LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
            LEFT JOIN paths p ON p.context_id = hc.id
            WHERE wc.category_id = %s
            GROUP BY w.id, w.word
            ORDER BY usage_count DESC, w.word
            LIMIT %s
        """
        results = execute_query(query, (category_id, limit), fetch="all")
        if results:
            return [
                {
                    'id': row[0],
                    'word': row[1],
                    'usage_count': row[2] or 0
                }
                for row in results
            ]
        return []
    except Exception as e:
        logger.error(f"Error getting words by category {category_id}: {e}")
        return []


#: The columns a words-in-category list may be ordered by, as SQL.
_CATEGORY_WORDS_SORT_COLUMNS = {
    'word': 'w.word',
    'usage_count': 'usage_count',
    'id': 'w.id',
}


def get_words_by_category_paged(
    category_id: int,
    search: str = None,
    page: int = 1,
    per_page: int = 20,
    sort_by: str = 'word',
    sort_order: str = 'asc',
    known_total: int = None,
) -> tuple:
    """
    Get one page of a category's words, searched and sorted server-side.

    The words-in-category page used to load up to ten thousand rows and do
    everything in the browser; this serves the same table one page at a time.

    Args:
        category_id: Category ID
        search: Optional word substring (case-insensitive)
        page: Page number (1-based)
        per_page: Results per page
        sort_by: One of word | usage_count | id
        sort_order: 'asc' or 'desc'
        known_total: Optional first-page count reused by bounded export batches

    Returns:
        Tuple of (words_list, total_count)
    """
    try:
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 200))
        if sort_by not in _CATEGORY_WORDS_SORT_COLUMNS:
            sort_by = 'word'
        if sort_order not in ('asc', 'desc'):
            sort_order = 'asc'
        direction = 'ASC' if sort_order == 'asc' else 'DESC'
        order_column = _CATEGORY_WORDS_SORT_COLUMNS[sort_by]

        where_parts = ["wc.category_id = %s"]
        params: list = [category_id]
        if search:
            where_parts.append("w.word ILIKE %s")
            params.append(f"%{search}%")
        where_sql = "WHERE " + " AND ".join(where_parts)

        count_query = f"""
            SELECT COUNT(*)
            FROM words_categorys wc
            JOIN words w ON w.id = wc.word_id
            {where_sql}
        """
        if known_total is None:
            total_result = execute_query(count_query, tuple(params), fetch="one")
            total = total_result[0] if total_result else 0
        else:
            total = known_total

        offset = (page - 1) * per_page
        query = f"""
            SELECT DISTINCT w.id, w.word, COUNT(DISTINCT p.id) as usage_count
            FROM words_categorys wc
            JOIN words w ON w.id = wc.word_id
            LEFT JOIN words_hashs wp ON wp.word_id = w.id
            LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
            LEFT JOIN paths p ON p.context_id = hc.id
            {where_sql}
            GROUP BY w.id, w.word
            ORDER BY {order_column} {direction}, w.word ASC
            LIMIT %s OFFSET %s
        """
        results = execute_query(query, tuple(params) + (per_page, offset), fetch="all")
        words = [
            {
                'id': row[0],
                'word': row[1],
                'usage_count': row[2] or 0
            }
            for row in (results or [])
        ]
        return words, total or 0
    except Exception as e:
        logger.error(f"Error getting paged words for category {category_id}: {e}")
        return [], 0


# ==================== WORD FUNCTIONS ====================

def get_word_id(word: str) -> int:
    """
    Get word ID by word string
    
    Args:
        word: Word string
    
    Returns:
        Word ID or None
    """
    try:
        query = "SELECT id FROM words WHERE word = %s"
        result = execute_query(query, (word,), fetch="one")
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Error getting word ID for '{word}': {e}")
        return None


def insert_word(word: str) -> int:
    """
    Insert a word and return its ID
    
    Args:
        word: Word string
    
    Returns:
        Word ID or None
    """
    try:
        query = """
            INSERT INTO words (word) 
            VALUES (%s) 
            ON CONFLICT (word) DO UPDATE SET word = EXCLUDED.word 
            RETURNING id
        """
        result = execute_query(query, (word,), fetch="one")
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Error inserting word '{word}': {e}")
        return None


def get_words_with_usage(
    search_term: str = None,
    page: int = 1,
    per_page: int = 10,
    sort_by: str = 'usage_count',
    sort_order: str = 'desc',
    status_filter: str = None
) -> tuple:
    """
    Get words with usage statistics
    
    Args:
        search_term: Optional search term
        page: Page number (1-based)
        per_page: Results per page
        sort_by: Sort column
        sort_order: Sort direction ('asc' or 'desc')
        status_filter: Optional status filter
    
    Returns:
        Tuple of (words_list, total_count)
    """
    try:
        offset = (page - 1) * per_page
        
        # Build WHERE clause
        where_clauses = []
        params = []
        
        if search_term:
            where_clauses.append("w.word ILIKE %s")
            params.append(f"%{search_term}%")
        
        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        
        # Validate sort_by; 'status' is derived (a word is active when it has
        # usage), so it maps to the usage expression the table shows.
        valid_sorts = ['usage_count', 'word', 'id', 'status']
        if sort_by not in valid_sorts:
            sort_by = 'usage_count'
        if sort_order not in ['asc', 'desc']:
            sort_order = 'desc'
        direction = sort_order.upper()
        if sort_by == 'word':
            order_column = 'w.word'
            status_tiebreak = ', w.id'
        elif sort_by == 'id':
            order_column = 'w.id'
            status_tiebreak = ', w.word'
        elif sort_by == 'status':
            order_column = '(COALESCE(u.usage_count, 0) > 0)'
            status_tiebreak = f', COALESCE(u.usage_count, 0) {direction}, w.word'
        else:
            order_column = 'COALESCE(u.usage_count, 0)'
            status_tiebreak = ', w.word'

        query = f"""
            WITH used AS (
                SELECT wp.word_id, COUNT(DISTINCT p.id) AS usage_count
                FROM words_hashs wp
                JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
                JOIN paths p ON p.context_id = hc.id
                GROUP BY wp.word_id
            )
            SELECT w.id, w.word, COALESCE(u.usage_count, 0) AS usage_count
            FROM words w
            LEFT JOIN used u ON u.word_id = w.id
            {where_sql}
            ORDER BY {order_column} {direction}{status_tiebreak}
            LIMIT %s OFFSET %s
        """
        params.extend([per_page, offset])
        
        count_query = f"""
            SELECT COUNT(*)
            FROM words w
            {where_sql}
        """
        count_params = params[:-2]  # Remove limit and offset
        
        results = execute_query(query, tuple(params), fetch="all")
        total_result = execute_query(count_query, tuple(count_params), fetch="one")
        
        total = total_result[0] if total_result else 0
        
        words = [
            {
                'id': row[0],
                'word': row[1],
                'usage_count': row[2] or 0
            }
            for row in results
        ]
        
        return words, total
    except Exception as e:
        logger.error(f"Error getting words with usage: {e}")
        return [], 0


def get_word_detail(word_id: int) -> dict:
    """
    Get word details by ID
    
    Args:
        word_id: Word ID
    
    Returns:
        Word dictionary or None
    """
    try:
        query = """
            SELECT w.id, w.word, COUNT(DISTINCT p.id) as usage_count
            FROM words w
            LEFT JOIN words_hashs wp ON wp.word_id = w.id
            LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
            LEFT JOIN paths p ON p.context_id = hc.id
            WHERE w.id = %s
            GROUP BY w.id, w.word
        """
        result = execute_query(query, (word_id,), fetch="one")
        if result:
            return {
                'id': result[0],
                'word': result[1],
                'usage_count': result[2] or 0
            }
        return None
    except Exception as e:
        logger.error(f"Error getting word detail {word_id}: {e}")
        return None


def word_exists(word: str) -> bool:
    """
    Check if a word exists
    
    Args:
        word: Word string
    
    Returns:
        True if word exists, False otherwise
    """
    try:
        query = "SELECT 1 FROM words WHERE word = %s LIMIT 1"
        result = execute_query(query, (word,), fetch="one")
        return result is not None
    except Exception as e:
        logger.error(f"Error checking word existence '{word}': {e}")
        return False


def create_word(word: str) -> int:
    """
    Create a word (alias for insert_word)
    
    Args:
        word: Word string
    
    Returns:
        Word ID or None
    """
    return insert_word(word)


def update_word_query(word_id: int, word: str) -> bool:
    """
    Update a word
    
    Args:
        word_id: Word ID
        word: New word string
    
    Returns:
        True if successful, False otherwise
    """
    try:
        query = "UPDATE words SET word = %s WHERE id = %s"
        execute_query(query, (word, word_id), fetch=None)
        return True
    except Exception as e:
        logger.error(f"Error updating word {word_id}: {e}")
        return False


def delete_word(word_id: int) -> bool:
    """
    Delete a word
    
    Args:
        word_id: Word ID
    
    Returns:
        True if successful, False otherwise
    """
    try:
        query = "DELETE FROM words WHERE id = %s"
        execute_query(query, (word_id,), fetch=None)
        return True
    except Exception as e:
        logger.error(f"Error deleting word {word_id}: {e}")
        return False


def get_words_usage_by_ids(word_ids: list) -> dict:
    """
    Get usage counts for multiple word IDs
    
    Args:
        word_ids: List of word IDs
    
    Returns:
        Dictionary mapping word_id -> usage_count
    """
    try:
        if not word_ids:
            return {}
        
        placeholders = ','.join(['%s'] * len(word_ids))
        query = f"""
            SELECT w.id, COUNT(DISTINCT p.id) as usage_count
            FROM words w
            LEFT JOIN words_hashs wp ON wp.word_id = w.id
            LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
            LEFT JOIN paths p ON p.context_id = hc.id
            WHERE w.id IN ({placeholders})
            GROUP BY w.id
        """
        results = execute_query(query, tuple(word_ids), fetch="all")
        return {row[0]: row[1] or 0 for row in results} if results else {}
    except Exception as e:
        logger.error(f"Error getting words usage by IDs: {e}")
        return {}


def bulk_delete_words(word_ids: list) -> bool:
    """
    Delete multiple words
    
    Args:
        word_ids: List of word IDs
    
    Returns:
        True if successful, False otherwise
    """
    try:
        if not word_ids:
            return True
        
        placeholders = ','.join(['%s'] * len(word_ids))
        query = f"DELETE FROM words WHERE id IN ({placeholders})"
        execute_query(query, tuple(word_ids), fetch=None)
        return True
    except Exception as e:
        logger.error(f"Error bulk deleting words: {e}")
        return False


def get_words_for_dropdown(limit: int = 1000) -> list:
    """
    Get words for dropdown selection
    
    Args:
        limit: Maximum number of words
    
    Returns:
        List of word dictionaries with id and word
    """
    try:
        query = """
            SELECT id, word
            FROM words
            ORDER BY word
            LIMIT %s
        """
        results = execute_query(query, (limit,), fetch="all")
        return [{'id': row[0], 'word': row[1]} for row in results] if results else []
    except Exception as e:
        logger.error(f"Error getting words for dropdown: {e}")
        return []


# ==================== KEYWORD FUNCTIONS ====================

def get_keywords_with_usage(
    search_term: str = None,
    page: int = 1,
    per_page: int = 10,
    sort_by: str = 'usage_count',
    sort_order: str = 'desc'
) -> tuple:
    """
    Get keywords with usage statistics
    
    Optimized to use batch loading for keyword text search, avoiding expensive
    per-keyword unpickling operations.
    
    Args:
        search_term: Optional search term to filter keywords by text
        page: Page number (1-based)
        per_page: Results per page
        sort_by: Column to order by ('usage_count', 'id', 'status' or 'text').
                 Keyword text is stored pickled, so only 'text' needs the
                 Python-side full-list path; the rest order in SQL.
        sort_order: 'asc' or 'desc'
    
    Returns:
        Tuple of (keywords_rows, total_count)
    """
    if sort_by not in ('usage_count', 'id', 'status', 'text'):
        sort_by = 'usage_count'
    if search_term or sort_by == 'text':
        # Search and text order: streamed and bounded (core/keyword_search.py).
        # Errors are not caught here: a database failure must reach the caller,
        # not look like "no keywords".
        descending = str(sort_order).lower() != 'asc'
        # Cross-language recall, same rule as the API: a keyword matches when
        # one of its words is the translation of what was typed.
        needle_ids = list(expand_for_search(search_term).word_ids) if search_term else None
        rows, total = keyword_page(
            execute_query, page=page, size=per_page,
            needle=search_term.lower() if search_term else None,
            conditions=[], params=[], rank=html_rank(sort_by, descending),
            needle_ids=needle_ids)
        # Same row shape as the SQL path: (id, category_id, keyword, usage_count).
        return [(c[0], c[2], c[1], c[4]) for c in rows], total
    try:
        sort_direction = 'ASC' if str(sort_order).lower() == 'asc' else 'DESC'

        # Usage is aggregated once, then joined. The previous form left-joined
        # every keyword through keywords_hashs → hash_contexts → paths and
        # grouped the expanded row set — that is why the list waited seconds
        # before the first paint.
        if sort_by == 'id':
            order_clause = f"ORDER BY k.id {sort_direction}"
        elif sort_by == 'status':
            order_clause = (
                f"ORDER BY (COALESCE(u.usage_count, 0) > 0) {sort_direction}, "
                f"COALESCE(u.usage_count, 0) {sort_direction}, k.id"
            )
        else:  # usage_count
            order_clause = f"ORDER BY COALESCE(u.usage_count, 0) {sort_direction}, k.id"

        if not search_term and sort_by != 'text':
            offset = (page - 1) * per_page
            
            query = f"""
                WITH used AS (
                    SELECT kp.keyword_id,
                           COUNT(DISTINCT p.id) AS usage_count
                    FROM keywords_hashs kp
                    JOIN hash_contexts hc ON hc.hash_id = kp.hash_id
                    JOIN paths p ON p.context_id = hc.id
                    GROUP BY kp.keyword_id
                )
                SELECT k.id, k.category_id, k.keyword,
                       COALESCE(u.usage_count, 0) AS usage_count
                FROM keywords k
                LEFT JOIN used u ON u.keyword_id = k.id
                {order_clause}
                LIMIT %s OFFSET %s
            """
            
            count_query = "SELECT COUNT(*) FROM keywords"
            
            results = execute_query(query, (per_page, offset), fetch="all")
            total_result = execute_query(count_query, (), fetch="one")
            
            total = total_result[0] if total_result else 0
            return results or [], total
        
    except Exception as e:
        logger.error(f"Error getting keywords with usage: {e}", exc_info=True)
        return [], 0


def get_keyword_id_by_blob(keyword_blob: bytes) -> int:
    """
    Get keyword ID by keyword blob
    
    Args:
        keyword_blob: Keyword blob bytes
    
    Returns:
        Keyword ID or None
    """
    try:
        query = "SELECT id FROM keywords WHERE keyword = %s LIMIT 1"
        result = execute_query(query, (keyword_blob,), fetch="one")
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Error getting keyword ID by blob: {e}")
        return None


def keyword_exists(keyword_id: int) -> bool:
    """
    Check if a keyword exists
    
    Args:
        keyword_id: Keyword ID
    
    Returns:
        True if keyword exists, False otherwise
    """
    try:
        query = "SELECT 1 FROM keywords WHERE id = %s LIMIT 1"
        result = execute_query(query, (keyword_id,), fetch="one")
        return result is not None
    except Exception as e:
        logger.error(f"Error checking keyword existence {keyword_id}: {e}")
        return False


def delete_keyword(keyword_id: int) -> bool:
    """
    Delete a keyword
    
    Args:
        keyword_id: Keyword ID
    
    Returns:
        True if successful, False otherwise
    """
    return db_delete_keyword(keyword_id)


def db_delete_keyword(keyword_id: int) -> bool:
    """
    Delete a keyword from database
    
    Args:
        keyword_id: Keyword ID
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Delete from keywords_hashs first (foreign key constraint)
        execute_query("DELETE FROM keywords_hashs WHERE keyword_id = %s", (keyword_id,), fetch=None)
        # Then delete the keyword
        execute_query("DELETE FROM keywords WHERE id = %s", (keyword_id,), fetch=None)
        return True
    except Exception as e:
        logger.error(f"Error deleting keyword {keyword_id}: {e}")
        return False


# ==================== ARCHIVE FUNCTIONS ====================

#: HAVING clause that decides what a "relation" IS, in the identity model:
#: one canonical content row (``hashs``, unique per hash) that ties more than
#: one artifact together. Two shapes qualify - several contexts hold the
#: content (the same content in different source/side collections; previously
#: a "variant" was a second ``hashs`` row) or several occurrences (stored
#: paths) exist across those contexts.
#:
#: This definition is shared by the sidebar count, the initial page render and
#: the paginated API on purpose. When they disagree, the UI shows a count and
#: an empty list (the "Relations" bug): the count came from a COUNT with this
#: HAVING while the list fetched a page of ``hashs`` in id order and dropped
#: every non-duplicate afterwards, so a page could be empty while the total
#: said there were relations.
RELATION_DUPLICATE_HAVING = "COUNT(DISTINCT p.id) > 1 OR COUNT(DISTINCT hc.id) > 1"


def relation_duplicates_sql(search: str = None):
    """SQL for the duplicate-content relations, one row per canonical content.

    A "relation" is a canonical content row (``hashs``) that ties more than
    one artifact together: several contexts (the same content in different
    source/side collections) or several occurrences (stored paths). In the
    identity model ``hashs`` is unique per hash, so "the same content under
    another source/side" is a second ``hash_contexts`` row, not a second
    content row.

    Returns ``(sql, params)``. The statement is a plain SELECT (no ORDER BY /
    LIMIT) so callers wrap it in a derived table to sort, limit, or count it -
    the duplicate filter stays identical everywhere. Columns: ``id`` (hashs
    row), ``name`` (hash value), ``context_count``, ``file_count`` (number of
    occurrences = stored paths across all contexts).

    Args:
        search: optional case-insensitive substring of the hash value.
    """
    where = ""
    params = []
    if search:
        where = "WHERE h.hash ILIKE %s"
        params.append(f"%{search}%")

    sql = f"""
        SELECT h.id AS id,
               h.hash AS name,
               COUNT(DISTINCT hc.id) AS context_count,
               COUNT(DISTINCT p.id) AS file_count
        FROM hashs h
        LEFT JOIN hash_contexts hc ON hc.hash_id = h.id
        LEFT JOIN paths p ON p.context_id = hc.id
        {where}
        GROUP BY h.id, h.hash
        HAVING {RELATION_DUPLICATE_HAVING}
    """
    return sql, params


def count_relation_duplicates(search: str = None) -> int:
    """Number of content relations (duplicate hashes) - the sidebar count."""
    inner, params = relation_duplicates_sql(search)
    row = execute_query(
        f"SELECT COUNT(*) FROM ({inner}) AS relations",
        tuple(params) if params else None,
        fetch="one",
    )
    if row is None:
        return 0
    if isinstance(row, (tuple, list)):
        return int(row[0] or 0)
    return int(row or 0)


def get_archive_statistics() -> dict:
    """
    Get archive statistics - only counts records that actually have files associated
    
    Returns:
        Dictionary with archive statistics
    """
    try:
        stats = {}
        
        # Total files
        result = execute_query("SELECT COUNT(*) FROM paths", fetch="one")
        stats['total_files'] = result[0] if result else 0
        
        # Total words (only those used in files)
        result = execute_query("""
            SELECT COUNT(DISTINCT w.id) 
            FROM words w
            INNER JOIN words_hashs wp ON w.id = wp.word_id
        """, fetch="one")
        stats['total_words'] = result[0] if result else 0
        
        # Total keywords (only those with files)
        result = execute_query("""
            SELECT COUNT(DISTINCT k.id) 
            FROM keywords k
            INNER JOIN keywords_hashs kp ON k.id = kp.keyword_id
        """, fetch="one")
        stats['total_keywords'] = result[0] if result else 0
        
        # Total categories (only those with files)
        result = execute_query("""
            SELECT COUNT(DISTINCT c.id) 
            FROM categorys c
            INNER JOIN words_categorys wc ON c.id = wc.category_id
            INNER JOIN words_hashs wp ON wc.word_id = wp.word_id
        """, fetch="one")
        stats['total_categories'] = result[0] if result else 0
        
        # Total titles (only those with files)
        result = execute_query("""
            SELECT COUNT(DISTINCT tc.id) 
            FROM titles_content tc
            INNER JOIN hash_contexts hc ON hc.hash_id = tc.hash_id
            INNER JOIN paths p ON p.context_id = hc.id
            WHERE tc.title_status = 'Main'
        """, fetch="one")
        stats['total_titles'] = result[0] if result else 0
        
        # Total sources (only those with files)
        result = execute_query("""
            SELECT COUNT(DISTINCT s.id) 
            FROM sources s
            INNER JOIN hash_contexts hc ON s.id = hc.source_id
            INNER JOIN paths p ON p.context_id = hc.id
        """, fetch="one")
        stats['total_sources'] = result[0] if result else 0
        
        # Total sides (only those with files)
        result = execute_query("""
            SELECT COUNT(DISTINCT si.id) 
            FROM sides si
            INNER JOIN hash_contexts hc ON si.id = hc.side_id
            INNER JOIN paths p ON p.context_id = hc.id
        """, fetch="one")
        stats['total_sides'] = result[0] if result else 0
        
        # Total hashs/relations (only duplicates: file_count > 1 or hash variants)
        # Shares ``relation_duplicates_sql`` with the API endpoint so the
        # sidebar count and the list the user opens can never disagree.
        stats['total_hashes'] = count_relation_duplicates()
        
        # Total geolocation (files with valid GPS coordinates)
        # Geolocation data is stored in paths.coordinates column, not a separate table
        # Filter out NULL, empty strings, and whitespace-only coordinates
        # Note: Full validation of GPS coordinate ranges (lat: -90 to 90, lon: -180 to 180)
        # is done in the API endpoint. This query filters out obviously invalid entries.
        # The API endpoint will further filter to ensure coordinates are valid GPS coordinates.
        result = execute_query("""
            SELECT COUNT(DISTINCT p.id) 
            FROM paths p
            WHERE p.coordinates IS NOT NULL
            AND TRIM(p.coordinates) != ''
            AND LENGTH(TRIM(p.coordinates)) > 0
        """, fetch="one")
        stats['total_geolocation'] = result[0] if result else 0

        # Distinct file types (normalized extension / stored type)
        result = execute_query("""
            SELECT COUNT(*) FROM (
                SELECT LOWER(REPLACE(
                    CASE
                        WHEN p.file_type IS NULL
                             OR TRIM(p.file_type) = ''
                             OR UPPER(p.file_type) = 'FILE'
                        THEN COALESCE(NULLIF(SUBSTRING(p.file_name FROM '\\.([^.]+)$'), ''), 'unknown')
                        ELSE p.file_type
                    END, '.', '')) AS type_key
                FROM paths p
                GROUP BY 1
            ) t
            WHERE type_key IS NOT NULL AND type_key <> ''
        """, fetch="one")
        stats['total_filetypes'] = result[0] if result else 0
        
        return stats
    except Exception as e:
        logger.error(f"Error getting archive statistics: {e}")
        return {}


# ==================== SOURCE FUNCTIONS ====================

def get_source_with_stats(source_id: int) -> dict:
    """
    Get source with statistics
    
    Args:
        source_id: Source ID
    
    Returns:
        Source dictionary with stats or None
    """
    try:
        from database import get_source
        source = get_source(source_id)
        if not source:
            return None
        
        # Get comprehensive statistics for this source
        stats_result = execute_query("""
            SELECT 
                COUNT(DISTINCT p.id) as doc_count,
                COUNT(DISTINCT p.file_type) as file_types,
                COALESCE(SUM(p.file_size), 0) as total_size
            FROM paths p
            JOIN hash_contexts hc ON hc.id = p.context_id JOIN hashs h ON h.id = hc.hash_id
            WHERE hc.source_id = %s
        """, (source_id,), fetch="one")
        
        # Add statistics to source dict
        if stats_result:
            source['doc_count'] = stats_result[0] if stats_result[0] is not None else 0
            source['file_types'] = stats_result[1] if stats_result[1] is not None else 0
            source['total_size'] = stats_result[2] if stats_result[2] is not None else 0
        else:
            source['doc_count'] = 0
            source['file_types'] = 0
            source['total_size'] = 0
        
        # Map entry_date to date_source_discovery for template compatibility
        if 'entry_date' in source and source['entry_date']:
            source['date_source_discovery'] = source['entry_date']
        elif 'date_source_discovery' not in source:
            source['date_source_discovery'] = None
        
        # Ensure all template-expected fields exist with defaults
        if 'file_count' not in source:
            source['file_count'] = source.get('doc_count', 0)
        
        # Get category name if category_id exists
        if source.get('category_id'):
            category_result = execute_query("""
                SELECT w.word
                FROM categorys c
                JOIN words w ON c.word_id = w.id
                WHERE c.id = %s
            """, (source['category_id'],), fetch="one")
            if category_result:
                source['category_name'] = category_result[0]
            else:
                source['category_name'] = None
        else:
            source['category_name'] = None
        
        return source
    except Exception as e:
        logger.error(f"Error getting source with stats {source_id}: {e}")
        return None


# ==================== SEARCH FUNCTIONS ====================

def search_files_by_word(word: str, page: int = 1, per_page: int = 10,
                         analyst_scope=None, sort: str = '', order: str = '') -> tuple:
    """
    Search files by word(s) - Google-like search supporting multiple words and partial matching

    Args:
        word: Word(s) to search for (can be multiple words separated by spaces)
        page: Page number (1-based)
        per_page: Results per page
        analyst_scope: Analyst-categorization search scope
            ('uncategorized' | 'categorized' | 'all'; FR-2.x). Filters strictly
            on analyst categorization status - smart categorization status is
            never consulted (FR-2.4).
        sort: A results-table column the reader clicked ('name' | 'type' |
            'size' | 'date'); anything else keeps the relevance order.
        order: 'asc' | 'desc' (default 'desc').

    Returns:
        Tuple of (results_list, total_count)
    """
    try:
        if not word or len(word.strip()) < 2:
            return [], 0

        offset = (page - 1) * per_page
        query_clean = word.strip()
        search_terms = query_clean.split()

        # Google-like search: build conditions for each term with OR logic
        # This supports partial words, multiple words, numbers, and partial text
        search_conditions = []
        search_params = []

        for term in search_terms:
            # Escape special characters for ILIKE
            term_escaped = term.replace("'", "''").replace("%", "\\%").replace("_", "\\_")
            term_pattern = f'%{term_escaped}%'

            # Search in file names
            search_conditions.append("p.file_name ILIKE %s")
            search_params.append(term_pattern)

            # Search in content words
            search_conditions.append("""
                EXISTS (
                    SELECT 1 FROM words_hashs wp
                    JOIN words w ON wp.word_id = w.id
                    WHERE wp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = p.id)
                    AND w.word ILIKE %s
                )
            """)
            search_params.append(term_pattern)

        # Analyst-categorization scope filter (FR-2.x) - analyst status only,
        # never smart categorization (FR-2.4). ANDed separately so it is never
        # absorbed by the OR term-matching group above.
        from Api.services.analyst_categories import scope_condition
        scope_sql = scope_condition(analyst_scope, alias="p")

        where_clause = f"({' OR '.join(search_conditions)})" if search_conditions else "1=1"
        if scope_sql:
            where_clause = f"({where_clause}) AND {scope_sql}"

        # The results table's header clicks: the same view, in the clicked
        # column's order. The default stays the relevance order.
        SEARCH_SORT_COLUMNS = {
            'name': 'p.file_name',
            'type': 'p.file_type',
            'size': 'p.file_size',
            'date': 'p.date_creation',
        }
        if sort in SEARCH_SORT_COLUMNS:
            direction = 'ASC' if order == 'asc' else 'DESC'
            order_by = (f"{SEARCH_SORT_COLUMNS[sort]} {direction} NULLS LAST, "
                        "relevance_score DESC, p.file_name")
        else:
            order_by = "relevance_score DESC, p.file_name"
        
        # Build relevance score calculation
        relevance_parts = []
        for term in search_terms:
            relevance_parts.append("CASE WHEN p.file_name ILIKE %s THEN 2.0 ELSE 0.0 END")
            relevance_parts.append("CASE WHEN EXISTS (SELECT 1 FROM words_hashs wp JOIN words w ON wp.word_id = w.id WHERE wp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = p.id) AND w.word ILIKE %s) THEN 1.0 ELSE 0.0 END")
        relevance_expr = ' + '.join(relevance_parts) if relevance_parts else '0'
        
        query = f"""
            SELECT DISTINCT
                p.id, p.file_name, p.file_path, p.file_type, p.file_status,
                p.file_date, p.date_creation,
                COALESCE(s.name, 'Unknown') as source_name,
                COALESCE(si.name, 'Unknown') as side_name,
                hc.source_id, hc.side_id,
                -- Google-like relevance score: sum matches for each search term
                ({relevance_expr}) as relevance_score,
                p.file_size
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
            LEFT JOIN sources s ON hc.source_id = s.id
            LEFT JOIN sides si ON hc.side_id = si.id
            WHERE {where_clause}
            ORDER BY {order_by}
            LIMIT %s OFFSET %s
        """
        
        count_query = f"""
            SELECT COUNT(DISTINCT p.id)
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
            WHERE {where_clause}
        """
        
        # Build relevance params (duplicate search_params for relevance calculation)
        relevance_params = []
        for term in search_terms:
            term_escaped = term.replace("'", "''").replace("%", "\\%").replace("_", "\\_")
            term_pattern = f'%{term_escaped}%'
            relevance_params.append(term_pattern)  # For file_name check
            relevance_params.append(term_pattern)  # For content word check
        
        # Execute queries with all parameters
        all_params = search_params + relevance_params + [per_page, offset]
        count_params = search_params
        
        results = execute_query(query, tuple(all_params), fetch="all")
        total_result = execute_query(count_query, tuple(count_params), fetch="one")
        
        total = total_result[0] if total_result else 0
        
        files = [
            {
                'id': row[0],
                'file_name': row[1],
                'file_path': row[2],
                'file_type': row[3],
                'file_status': row[4],
                'file_date': row[5].isoformat() if row[5] else None,
                'date_creation': row[6].isoformat() if row[6] else None,
                'file_size': int(row[12] or 0) if len(row) > 12 else 0,
                'source_name': row[7],
                'side_name': row[8],
                'source_id': row[9],
                'side_id': row[10],
                'relevance_score': float(row[11]) if len(row) > 11 and row[11] is not None else 0.0
            }
            for row in results
        ] if results else []
        
        return files, total
    except Exception as e:
        logger.error(f"Error searching files by word '{word}': {e}")
        return [], 0


# ==================== PROCESSING FUNCTIONS ====================

def process_term(term: str) -> str:
    """
    Process a search term (normalize, lowercase, etc.)
    
    Args:
        term: Search term
    
    Returns:
        Processed term
    """
    if not term:
        return ""
    return term.strip().lower()


# ==================== EMAIL FUNCTIONS ====================

def get_email_words(
    page: int = 1,
    per_page: int = 50,
    search_term: str = None,
    domain: str = None,
    domain_mode: str = 'exact',
    source_filter: int = None,
    side_filter: int = None,
    date_from: str = None,
    date_to: str = None,
    sort_by: str = 'word',
    sort_order: str = 'asc'
) -> tuple:
    """
    Get email words with pagination and filters
    
    Args:
        page: Page number (1-based)
        per_page: Results per page
        search_term: Optional search term to filter email addresses
        domain: Optional domain filter
        domain_mode: Domain filter mode ('exact', 'contains', 'endswith')
        source_filter: Filter by source ID
        side_filter: Filter by side ID
        date_from: Filter from date (ISO format)
        date_to: Filter to date (ISO format)
    
    Returns:
        Tuple of (results_list, total_count)
    """
    try:
        # Call the database function with all parameters (now supports search_term/domain in SQL)
        email_words, total = get_email_words_db(
            page=page,
            per_page=per_page,
            search_term=search_term,
            domain=domain,
            domain_mode=domain_mode,
            source_filter=source_filter,
            side_filter=side_filter,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by,
            sort_order=sort_order
        )
        
        return email_words, total
    except Exception as e:
        logger.error(f"Error getting email words: {e}")
        return [], 0


def get_email_words_all(
    search_term: str = None,
    domain: str = None,
    domain_mode: str = 'exact',
    source_filter: int = None,
    side_filter: int = None,
) -> list:
    """
    Get all email words with optional filters (for export/copy operations)
    
    Args:
        search_term: Optional search term to filter email addresses
        domain: Optional domain filter
        domain_mode: Domain filter mode ('exact', 'contains', 'endswith')
        source_filter: Optional source ID filter
        side_filter: Optional side ID filter
    
    Returns:
        List of tuples: [(email, usage_count), ...]
    """
    try:
        # Get all email words without pagination
        # We'll fetch in batches and combine
        all_results = []
        page = 1
        per_page = 200
        
        while True:
            email_words, total = get_email_words(
                page=page,
                per_page=per_page,
                search_term=search_term,
                domain=domain,
                domain_mode=domain_mode,
                source_filter=source_filter,
                side_filter=side_filter,
            )
            
            if not email_words:
                break
            
            # Convert dictionaries to tuples (email, usage_count)
            for word_dict in email_words:
                email = word_dict.get('word', '')
                usage_count = word_dict.get('usage_count', 0)
                if email and '@' in email:
                    all_results.append((email, usage_count))
            
            if len(email_words) < per_page:
                break
            
            page += 1
        
        return all_results
    except Exception as e:
        logger.error(f"Error getting all email words: {e}")
        return []


def get_email_domains(
    search_term: str = None,
    domain: str = None,
    domain_mode: str = 'exact',
    limit: int = 10000
) -> list:
    """
    Get email domains with usage counts (optimized SQL query)
    
    Args:
        search_term: Optional search term to filter email addresses
        domain: Optional domain filter
        domain_mode: Domain filter mode ('exact', 'contains', 'endswith')
        limit: Maximum number of domains to return
    
    Returns:
        List of tuples: [(domain, count), ...] sorted by count descending
    """
    try:
        from database.services.contents_db_service import ContentDBService
        import logging
        
        logger = logging.getLogger(__name__)
        
        # Validate inputs
        domain_mode = str(domain_mode).lower().strip() if domain_mode else 'exact'
        if domain_mode not in ('exact', 'contains', 'endswith'):
            domain_mode = 'exact'
        
        if limit < 1:
            limit = 10000
        elif limit > 100000:
            limit = 100000  # Reasonable maximum
        
        service = ContentDBService()
        
        # Email pattern: must contain @ and be longer than 5 chars
        like_pattern = '%@%'  # Must contain @
        not_like_pattern = '%@%@%'  # Must not contain multiple @ (simple validation)
        
        # Call repository method (now uses efficient SQL GROUP BY)
        results = service.words_repo.get_email_domains(
            like_pattern=like_pattern,
            not_like_pattern=not_like_pattern,
            search=search_term,
            domain=domain,
            domain_mode=domain_mode,
            limit=limit
        )
        
        if not results:
            return []
        
        # Results are already tuples of (domain, email_count)
        # Convert to list and ensure proper format
        domains_list = []
        for row in results:
            if isinstance(row, tuple) and len(row) >= 2:
                domain_name = row[0]
                email_count = row[1] if row[1] else 0
                if domain_name:  # Skip empty domains
                    domains_list.append((domain_name, email_count))
        
        return domains_list
    except Exception as e:
        logger.error(f"Error getting email domains: {e}", exc_info=True)
        return []


# ==================== PERFORMANCE FUNCTIONS ====================

# Removed unused functions:
# - get_connection_handler() - never used
# - get_cache_stats() - never used  
# - SettingsAccess class - never used (use settings directly)
# - ConnectionManager class - never used (use DatabaseHub directly)

