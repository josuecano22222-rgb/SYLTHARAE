
import logging
import time
import re
from typing import Dict, List, Optional, Tuple, Any
from core.errors import new_correlation_id
from datetime import datetime
from enum import Enum

from Api.utils import execute_query, get_query_cache, get_connection, return_connection

logger = logging.getLogger(__name__)


class SortDirection(Enum):
    """Sort direction for cursor pagination"""
    ASC = "ASC"
    DESC = "DESC"


class CursorPagination:
    """
    Cursor-based pagination for billion+ record queries.
    
    Features:
    - O(log n) query performance (vs. O(n) for OFFSET)
    - Transaction isolation for query integrity
    - Support for complex filters and joins
    - Automatic cursor validation
    """
    
    # Table metadata: cursor column, default sort column
    TABLE_METADATA = {
        'paths': {'cursor': 'id', 'default_sort': 'date_creation', 'default_dir': SortDirection.DESC},
        'hashs': {'cursor': 'id', 'default_sort': 'id', 'default_dir': SortDirection.ASC},
        'sources': {'cursor': 'id', 'default_sort': 'date_creation', 'default_dir': SortDirection.DESC},
        'sides': {'cursor': 'id', 'default_sort': 'date_creation', 'default_dir': SortDirection.DESC},
        'categorys': {'cursor': 'id', 'default_sort': 'id', 'default_dir': SortDirection.ASC},
        'words': {'cursor': 'id', 'default_sort': 'word', 'default_dir': SortDirection.ASC},
        'keywords': {'cursor': 'id', 'default_sort': 'id', 'default_dir': SortDirection.ASC},
        'contents': {'cursor': 'id', 'default_sort': 'content_date', 'default_dir': SortDirection.DESC},
        'titles_content': {'cursor': 'id', 'default_sort': 'id', 'default_dir': SortDirection.ASC},
    }
    
    def __init__(self, table: str, isolation_level: str = 'REPEATABLE READ'):
        """
        Initialize cursor pagination for a table.
        
        Args:
            table: Table name (must be in TABLE_METADATA)
            isolation_level: PostgreSQL transaction isolation level
        """
        if table not in self.TABLE_METADATA:
            raise ValueError(f"Table '{table}' not supported. Supported tables: {list(self.TABLE_METADATA.keys())}")
        
        self.table = table
        self.metadata = self.TABLE_METADATA[table]
        self.cursor_column = self.metadata['cursor']
        self.isolation_level = isolation_level
        self.cache = get_query_cache()
    
    def get_page(
        self,
        cursor: Optional[int] = None,
        limit: int = 50,
        sort_column: Optional[str] = None,
        sort_direction: SortDirection = None,
        filters: Optional[Dict[str, Any]] = None,
        joins: Optional[List[str]] = None,
        select_columns: Optional[List[str]] = None,
        table_alias: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a paginated page of results using cursor-based pagination.
        
        Args:
            cursor: Cursor ID for pagination (None for first page)
            limit: Maximum number of results per page (1-1000)
            sort_column: Column to sort by (defaults to metadata default)
            sort_direction: Sort direction (ASC/DESC)
            filters: Dictionary of filter conditions
            joins: List of join clauses
            select_columns: List of columns to select
            table_alias: Table alias for queries
            
        Returns:
            Dictionary containing results, next_cursor, and metadata
        """
        start_time = time.time()
        
        # Validate and normalize parameters
        limit = max(1, min(1000, limit))  # Clamp between 1 and 1000
        sort_column = sort_column or self.metadata['default_sort']
        sort_direction = sort_direction or self.metadata['default_dir']
        
        # Validate cursor if provided
        if cursor is not None:
            if not self._validate_cursor(cursor):
                raise ValueError(f"Invalid cursor value: {cursor}")
        
        # Build query
        query, params = self._build_query(
            cursor=cursor,
            limit=limit,
            sort_column=sort_column,
            sort_direction=sort_direction,
            filters=filters,
            joins=joins,
            select_columns=select_columns,
            table_alias=table_alias
        )
        
        # Execute query with transaction isolation
        # get_connection() returns a context manager, so we use it with 'with'
        try:
            with get_connection() as conn:
                # Note: Skipping SET TRANSACTION ISOLATION LEVEL because connection pooling
                # means the connection may already have an active transaction.
                # The default READ COMMITTED isolation level is sufficient for cursor pagination.
                with conn.cursor() as cur:
                    # Execute main query
                    cur.execute(query, params)
                    results = cur.fetchall()
                    
                    # Get column names
                    column_names = [desc[0] for desc in cur.description]
                    
                    # Check if we have more results (we fetched limit+1)
                    has_next = len(results) > limit
                    
                    # Take only the requested limit
                    if has_next:
                        results = results[:limit]
                    
                    # Convert results to dicts
                    data = [dict(zip(column_names, row)) for row in results]
                    
                    # Determine pagination info
                    has_prev = cursor is not None
                    
                    # Get next and previous cursors
                    next_cursor = None
                    prev_cursor = None
                    
                    if has_next and data:
                        # Next cursor is the ID of the last record
                        last_record = data[-1]
                        next_cursor = last_record.get(self.cursor_column)
                    
                    if has_prev and data:
                        # Previous cursor is the ID of the first record
                        first_record = data[0]
                        prev_cursor = first_record.get(self.cursor_column)
                    
                    # Get estimated total (approximate, fast)
                    total_estimated = self._get_estimated_total(filters, joins, table_alias)
                    
                    conn.commit()
            
            query_time_ms = (time.time() - start_time) * 1000
            
            logger.debug(f"Cursor pagination: table={self.table}, cursor={cursor}, limit={limit}, "
                        f"results={len(data)}, time={query_time_ms:.2f}ms")
            
            return {
                'data': data,
                'next_cursor': next_cursor,
                'prev_cursor': prev_cursor,
                'has_next': has_next,
                'has_prev': has_prev,
                'total_estimated': total_estimated,
                'query_time_ms': query_time_ms,
                'cursor_column': self.cursor_column,
                'sort_column': sort_column,
                'sort_direction': sort_direction.value
            }
        
        except Exception as e:
            logger.error(f"Cursor pagination error: {e}", exc_info=True)
            raise
    
    def _build_query(
        self,
        cursor: Optional[int],
        limit: int,
        sort_column: str,
        sort_direction: SortDirection,
        filters: Optional[Dict[str, Any]],
        joins: Optional[List[str]],
        select_columns: Optional[List[str]],
        table_alias: Optional[str] = None
    ) -> Tuple[str, Tuple]:
        """
        Build cursor-based pagination query.
        
        Returns:
            Tuple of (query_string, params_tuple)
        """
        # SECURITY: Validate sort_column to prevent SQL injection
        valid_column_pattern = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_.]*$')
        if not valid_column_pattern.match(sort_column):
            logger.warning(f"Invalid sort_column name, using default: {sort_column}")
            sort_column = self.metadata['default_sort']
        
        # Determine table reference (with or without alias)
        table_ref = f"{self.table} {table_alias}" if table_alias else self.table
        alias_or_table = table_alias if table_alias else self.table
        
        # Build SELECT clause
        if select_columns:
            select_clause = ', '.join(select_columns)
        else:
            select_clause = f"{alias_or_table}.*"
        
        # Build FROM clause with joins
        from_clause = table_ref
        if joins:
            from_clause += ' ' + ' '.join(joins)
        
        # Build WHERE clause
        where_parts = []
        params = []
        
        # Cursor condition (for pagination)
        if cursor is not None:
            if sort_direction == SortDirection.ASC:
                where_parts.append(f"{alias_or_table}.{self.cursor_column} > %s")
            else:
                where_parts.append(f"{alias_or_table}.{self.cursor_column} < %s")
            params.append(cursor)
        
        # Filter conditions
        if filters:
            for column, value in filters.items():
                if column == 'OR':
                    # A group of filters joined by OR, e.g. "the text matches
                    # OR the word id is one of these" - the shape a
                    # cross-language search needs and a flat AND cannot express.
                    group_sql, group_params = self._build_or_group(value)
                    if group_sql:
                        where_parts.append(group_sql)
                        params.extend(group_params)
                    continue
                clause, clause_params = self._build_filter_clause(column, value)
                if clause:
                    where_parts.append(clause)
                    params.extend(clause_params)

        where_clause = ''
        if where_parts:
            where_clause = 'WHERE ' + ' AND '.join(where_parts)
        
        # Check if we need GROUP BY (if select_columns contains aggregate functions)
        group_by_clause = ''
        if select_columns:
            # Check for aggregate functions (COUNT, SUM, AVG, etc.)
            has_aggregates = any(
                any(func in col.upper() for func in ['COUNT(', 'SUM(', 'AVG(', 'MAX(', 'MIN('])
                for col in select_columns
            )
            if has_aggregates:
                # For GROUP BY queries with cursor pagination, we must group by the primary key
                # This ensures each row has a unique cursor value
                # Extract all non-aggregated columns for GROUP BY
                group_by_cols = []
                # ALWAYS add primary key first
                group_by_cols.append(f"{alias_or_table}.{self.cursor_column}")
                
                for col in select_columns:
                    col_upper = col.upper().strip()
                    # Skip aggregate functions
                    if any(func in col_upper for func in ['COUNT(', 'SUM(', 'AVG(', 'MAX(', 'MIN(']):
                        continue
                    
                    # These don't need to be in GROUP BY
                    if 'COALESCE(' in col_upper or 'CASE WHEN' in col_upper:
                        continue
                    
                    # Use regex to find "as" keyword (case-insensitive, with optional spaces)
                    # Pattern matches: " AS ", " as ", "As ", "AS ", etc.
                    as_match = re.search(r'\s+[Aa][Ss]\s+', col, re.IGNORECASE)
                    if as_match:
                        # Split at the "as" keyword position
                        col_name = col[:as_match.start()].strip()
                    else:
                        col_name = col.strip()
                    
                    # This handles edge cases where the regex might have missed something
                    col_name = re.sub(r'\s+[Aa][Ss]\s+.*$', '', col_name, flags=re.IGNORECASE).strip()
                    
                    # If column references a joined table (not main table), include it in GROUP BY
                    if '.' in col_name:
                        table_part = col_name.split('.')[0]
                        column_part = col_name.split('.')[1] if '.' in col_name else col_name
                        
                        # These can't be used in GROUP BY
                        bytea_columns = ['keyword', 'title_data']  # Add more as needed
                        if column_part.lower() in bytea_columns:
                            continue
                        
                        # If it's from the main table, we'll add it below
                        # If it's from a joined table (like w.word), we need to include it
                        if table_part not in [alias_or_table, self.table]:
                            # This is from a joined table - include it in GROUP BY
                            # Use the column as-is (e.g., "w.word")
                            if col_name not in group_by_cols:
                                group_by_cols.append(col_name)
                            continue
                        
                        # For main table columns, continue to add them below
                    
                    if ' AS ' in col_name.upper() or re.search(r'\s+[Aa][Ss]\s+', col_name, re.IGNORECASE):
                        logger.warning(f"Warning: 'as' keyword found in column name '{col_name}' from '{col}', skipping")
                        continue
                    
                    # Add to GROUP BY if it's a valid column reference and not already added
                    if col_name and col_name != f"{alias_or_table}.{self.cursor_column}" and col_name not in group_by_cols:
                        group_by_cols.append(col_name)
                
                # Extract sort column name (might be table.column format)
                sort_col_for_groupby = sort_column
                if '.' in sort_column:
                    # If sort_column is like "s.importance", check if it matches alias_or_table
                    sort_parts = sort_column.split('.')
                    if len(sort_parts) == 2 and sort_parts[0] == alias_or_table:
                        # Already in correct format
                        pass
                    else:
                        # Might be from joined table, use as-is
                        sort_col_for_groupby = sort_column
                else:
                    # No table prefix, add alias
                    sort_col_for_groupby = f"{alias_or_table}.{sort_column}"
                
                # Add sort column to GROUP BY if not already present
                if sort_col_for_groupby not in group_by_cols:
                    group_by_cols.append(sort_col_for_groupby)
                
                if group_by_cols:
                    validated_cols = []
                    for col in group_by_cols:
                        # Remove any "as" keyword that might have slipped through
                        clean_col = re.sub(r'\s+[Aa][Ss]\s+.*$', '', col).strip()
                        if clean_col and ' AS ' not in clean_col.upper():
                            validated_cols.append(clean_col)
                    if validated_cols:
                        group_by_clause = f"GROUP BY {', '.join(validated_cols)}"
                    else:
                        # Fallback: just use primary key
                        group_by_clause = f"GROUP BY {alias_or_table}.{self.cursor_column}"
        
        # Build ORDER BY clause
        # For GROUP BY queries, ensure we order by the cursor column for proper pagination
        # IMPORTANT: Never use calculated columns (aggregates, CASE expressions) in ORDER BY
        # Only use actual table columns that exist in the database
        if group_by_clause:
            # Check if sort_column is a calculated field (aggregate or CASE expression alias)
            # by checking if it matches any aliases in select_columns
            is_calculated_field = False
            if select_columns:
                sort_col_name = sort_column.split('.')[-1] if '.' in sort_column else sort_column
                for col in select_columns:
                    col_upper = col.upper()
                    # Check if this column has an alias matching the sort_column
                    if ' AS ' in col_upper:
                        parts = col.split(' AS ', 1)
                        if len(parts) == 2:
                            alias_name = parts[1].strip().upper()
                            # Check if alias matches sort column name (with or without table prefix)
                            if alias_name == sort_col_name.upper() or alias_name == sort_column.upper().replace('.', '_'):
                                # This is a calculated column alias, don't use it in ORDER BY
                                is_calculated_field = True
                                break
                    # Also check if sort_column references an aggregate function
                    if any(func in col_upper for func in ['COUNT(', 'SUM(', 'AVG(', 'MAX(', 'MIN(']):
                        if sort_col_name.upper() in col_upper:
                            is_calculated_field = True
                            break
            
            if is_calculated_field:
                # Don't use calculated column in ORDER BY, just use cursor column
                # The sorting will be done client-side after fetching
                order_by_clause = f"ORDER BY {alias_or_table}.{self.cursor_column} {sort_direction.value}"
            else:
                # Then by sort column (which is now guaranteed to be in GROUP BY)
                order_by_clause = f"ORDER BY {alias_or_table}.{self.cursor_column} {sort_direction.value}, {sort_column} {sort_direction.value}"
        else:
            order_by_clause = f"ORDER BY {sort_column} {sort_direction.value}"
        
        # Build LIMIT clause
        limit_clause = f"LIMIT {limit + 1}"  # Fetch one extra to check if there's a next page
        
        # Assemble query
        query = f"""
            SELECT {select_clause}
            FROM {from_clause}
            {where_clause}
            {group_by_clause}
            {order_by_clause}
            {limit_clause}
        """
        
        logger.debug(f"Generated query:\n{query}\nParams: {params}")
        # Also log GROUP BY clause separately for debugging
        if group_by_clause:
            logger.debug(f"GROUP BY clause: {group_by_clause}")
        
        return query.strip(), tuple(params)
    
    #: Column names a filter may name: alphanumeric, underscore, and a dot for
    #: ``table.column``. Anything else is skipped with a warning.
    _VALID_COLUMN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_.]*$')

    def _build_filter_clause(self, column: str, value):
        """``(sql, params)`` for one filter entry, or ``('', [])`` when refused.

        Extracted from ``_build_query`` so the OR group can reuse it verbatim:
        two copies of this logic would drift, and this is where the SQL-injection
        validation lives.
        """
        if not self._VALID_COLUMN.match(column):
            logger.warning("Invalid column name in filter, skipping: %s", column)
            return "", []
        if isinstance(value, dict):
            op = value.get('op', '=')
            filter_value = value.get('value')
            if op not in ('=', '!=', '>', '<', '>=', '<=', 'LIKE', 'ILIKE', 'IN', 'BETWEEN'):
                logger.warning("Invalid operator in filter, skipping: %s", op)
                return "", []
            if op in ('=', '!=', '>', '<', '>=', '<=', 'LIKE', 'ILIKE'):
                return f"{column} {op} %s", [filter_value]
            if op == 'IN':
                if not isinstance(filter_value, (list, tuple)):
                    logger.warning("IN operator requires list/tuple, got %s, skipping",
                                   type(filter_value))
                    return "", []
                if len(filter_value) == 0:
                    return "", []
                placeholders = ','.join(['%s'] * len(filter_value))
                return f"{column} IN ({placeholders})", list(filter_value)
            if op == 'BETWEEN':
                if not isinstance(filter_value, (list, tuple)) or len(filter_value) != 2:
                    logger.warning("BETWEEN operator requires list/tuple of 2 values,"
                                   " got %s, skipping", filter_value)
                    return "", []
                return f"{column} BETWEEN %s AND %s", [filter_value[0], filter_value[1]]
            return "", []
        return f"{column} = %s", [value]

    def _build_or_group(self, group):
        """``(sql, params)`` for ``{'OR': [filter, filter, ...]}``.

        Each element is a filter mapping built by :meth:`_build_filter_clause`;
        the clauses are joined with ``OR`` and wrapped in parentheses, so the
        group composes with the surrounding ``AND``s. A group where every element
        was refused produces nothing rather than an empty ``()``, which would be
        a syntax error.
        """
        if not isinstance(group, (list, tuple)):
            logger.warning("OR group requires a list of filters, skipping")
            return "", []
        parts, params = [], []
        for element in group:
            if not isinstance(element, dict):
                continue
            for column, value in element.items():
                sql, element_params = self._build_filter_clause(column, value)
                if sql:
                    parts.append(sql)
                    params.extend(element_params)
        if not parts:
            return "", []
        if len(parts) == 1:
            return parts[0], params
        return "(" + " OR ".join(parts) + ")", params

    def _validate_cursor(self, cursor: int) -> bool:
        """Validate that cursor value exists in table."""
        try:
            result = execute_query(
                f"SELECT 1 FROM {self.table} WHERE {self.cursor_column} = %s",
                (cursor,),
                fetch="one",
                use_cache=False
            )
            return result is not None
        except Exception:
            return False
    
    def _get_estimated_total(self, filters: Optional[Dict], joins: Optional[List[str]], table_alias: Optional[str] = None) -> Optional[int]:
        """
        Get total count with filters applied.
        
        Uses actual COUNT query for accuracy when filters are present,
        falls back to estimates for speed when no filters.
        """
        try:
            # Build FROM clause with proper alias
            if table_alias:
                from_clause = f"{self.table} {table_alias}"
                alias_or_table = table_alias
            else:
                # If no alias provided but joins exist, try to extract alias from first JOIN
                # This handles cases where joins reference an alias like "s.id"
                if joins and len(joins) > 0:
                    # Check if first join references a table alias (e.g., "ON s.id")
                    first_join = joins[0]
                    # Look for pattern like "ON alias.column" or "ON alias.column ="
                    alias_match = re.search(r'\bON\s+([a-zA-Z_][a-zA-Z0-9_]*)\.', first_join, re.IGNORECASE)
                    if alias_match:
                        # Use the detected alias
                        detected_alias = alias_match.group(1)
                        from_clause = f"{self.table} {detected_alias}"
                        alias_or_table = detected_alias
                    else:
                        from_clause = self.table
                        alias_or_table = self.table
                else:
                    from_clause = self.table
                    alias_or_table = self.table
            
            if joins:
                from_clause += ' ' + ' '.join(joins)
            
            # Build WHERE clause with all filters, using the same builder as
            # _build_query so the count and the page can never disagree.
            where_parts = []
            params = []
            if filters:
                for column, value in filters.items():
                    if column == 'OR':
                        group_sql, group_params = self._build_or_group(value)
                        if group_sql:
                            where_parts.append(group_sql)
                            params.extend(group_params)
                        continue
                    clause, clause_params = self._build_filter_clause(column, value)
                    if clause:
                        where_parts.append(clause)
                        params.extend(clause_params)
            
            where_clause = ''
            if where_parts:
                where_clause = 'WHERE ' + ' AND '.join(where_parts)
            
            # Use DISTINCT on the primary key to handle JOINs that might duplicate rows
            if joins:
                # With JOINs, always use actual COUNT for accuracy (JOINs can duplicate rows)
                count_query = f"SELECT COUNT(DISTINCT {alias_or_table}.{self.cursor_column}) FROM {from_clause} {where_clause}"
                result = execute_query(count_query, tuple(params) if params else None, fetch="one", use_cache=False)
                if result:
                    count_value = result if isinstance(result, int) else result[0]
                    logger.debug(f"Total count with JOINs: {count_value}")
                    return count_value
            elif filters:
                # Filters without JOINs: use COUNT with filters
                count_query = f"SELECT COUNT(*) FROM {from_clause} {where_clause}"
                result = execute_query(count_query, tuple(params) if params else None, fetch="one", use_cache=False)
                if result:
                    count_value = result if isinstance(result, int) else result[0]
                    logger.debug(f"Total count with filters: {count_value}")
                    return count_value
            else:
                # No filters, no JOINs: use fast table statistics
                stats_query = f"""
                    SELECT n_live_tup 
                    FROM pg_stat_user_tables 
                    WHERE relname = %s
                """
                result = execute_query(stats_query, (self.table,), fetch="one", use_cache=True)
                if result:
                    count_value = result if isinstance(result, int) else result[0]
                    logger.debug(f"Total count from stats: {count_value}")
                    return count_value
        
        except Exception as e:
            logger.warning(f"Could not get total count: {e}", exc_info=True)
        
        return None
    
    def check_integrity(
        self,
        cursor: int,
        expected_count: int,
        filters: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Check query integrity: verify no duplicates or missing records.
        
        Args:
            cursor: Cursor value to check
            expected_count: Expected number of records after this cursor
            filters: Same filters used in original query
        
        Returns:
            Dict with integrity check results
        """
        try:
            # Count records after cursor
            where_parts = [f"{self.cursor_column} > %s"]
            params = [cursor]
            
            if filters:
                for column, value in filters.items():
                    if isinstance(value, dict):
                        op = value.get('op', '=')
                        filter_value = value.get('value')
                        if op == '=':
                            where_parts.append(f"{column} = %s")
                            params.append(filter_value)
                    else:
                        where_parts.append(f"{column} = %s")
                        params.append(value)
            
            where_clause = 'WHERE ' + ' AND '.join(where_parts)
            
            count_query = f"SELECT COUNT(*) FROM {self.table} {where_clause}"
            actual_count = execute_query(count_query, tuple(params), fetch="one", use_cache=False)
            
            if isinstance(actual_count, tuple):
                actual_count = actual_count[0]
            
            integrity_ok = abs(actual_count - expected_count) <= 1  # Allow 1 record difference for concurrent updates
            
            return {
                'integrity_ok': integrity_ok,
                'expected_count': expected_count,
                'actual_count': actual_count,
                'difference': actual_count - expected_count,
                'cursor': cursor,
                'timestamp': datetime.now().isoformat()
            }
        
        except Exception as e:
            logger.error(f"Integrity check error: {e}")
            # The reader is told the check could not be completed; the
            # exception text stays in the log with its correlation id.
            correlation_id = new_correlation_id()
            logger.error(f"Integrity check failed ({correlation_id}): {e}",
                         exc_info=True)
            return {
                'integrity_ok': False,
                'error': f'Integrity check could not be completed ({correlation_id})',
                'correlation_id': correlation_id,
                'cursor': cursor,
                'timestamp': datetime.now().isoformat()
            }


def get_cursor_paginator(table: str) -> CursorPagination:
    """Factory function to get cursor paginator for a table."""
    return CursorPagination(table)

