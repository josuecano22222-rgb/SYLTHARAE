"""
Search Service - Enhanced Full-Text Search with PostgreSQL
Provides advanced search capabilities including full-text search, sorting, and filtering
Now includes Google-like search algorithms: BM25, query expansion, fuzzy matching, autocomplete
"""

import html
import logging
import re
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, date
from Api.utils import execute_query, load_text_content, get_connection, return_connection
from core.content_markers import strip_line_markers, strip_structural_markers
from Api.services.search_algorithms import (
    SearchRanker, QueryExpander, FuzzyMatcher, QueryUnderstanding
)
from core.search.ripgrep import (
    build_ripgrep_pattern,
    get_scanner_from_settings,
    positive_terms_from_patterns,
)
from core.search.sql_relevance import sql_relevance_score_expr
from core.search.counts import apply_count_mode
from core.search.perf import SearchTrace
from core.search.cursor import relevance_cursor
from core.search.normalize import pagination_query_signature
from core.search.limits import SearchLimits
from core.search.planner import plan_search
from core.search.retrieval import batch_load_text_windows
from core.search.ripgrep import RipgrepMatch
from core.search.session import SearchSessionRegistry
from core.search.words import (
    can_use_fast_inverted_path,
    inverted_index_and_clause,
    normalize_search_token,
    resolve_word_ids_for_terms,
)
from core.search.short_circuit import assess_indexed_vocabulary_short_circuit
from core.search.boolean_or_flat import (
    flat_or_literal_sql,
    flatten_disjunction_terms,
    is_pure_disjunction,
)
from settings import get_settings

logger = logging.getLogger(__name__)


# The predicate vocabulary (literal matching, boolean parsing, structured
# filters) now lives in core/criteria/sql.py so interactive search, saved
# searches, monitoring, reports and exports share one definition of "matches".
# The old private names are kept as aliases for existing callers and tests.
from core.criteria.sql import (  # noqa: E402
    escape_like as _escape_like,
    escape_postgres_regex as _escape_postgres_regex,  # noqa: F401 - compatibility alias of the former private name
    filter_predicates as _filter_predicates,
    literal_field_matches as _literal_field_matches,
    parse_boolean_expression as _parse_boolean_search_expression,
    postgres_word_pattern as _postgres_word_pattern,  # noqa: F401 - compatibility alias of the former private name
    text_match_clause as _text_match_clause,  # noqa: F401 - compatibility alias of the former private name
)


# Spreadsheet structure tracking: maps a line of stored spreadsheet text to
# its worksheet + row, so a search hit can cite the exact cell ("Budget - B4")
# instead of a document-wide line number.
_SHEET_HEADER_RE = re.compile(r"^Sheet:\s*([^|\n]+)", re.IGNORECASE)
_SHEET_META_RE = re.compile(r"^(?:Rows|Columns):\s*:?\d+", re.IGNORECASE)


def _column_letter(index):
    """1-based column index -> spreadsheet letter (A..Z, AA..)."""
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _spreadsheet_row_map(lines):
    """Row location per line index for stored spreadsheet text.

    Mirrors the display parser: "Sheet:" lines switch worksheets, Rows:/
    Columns: metadata lines are skipped, interior blank lines count as rows,
    and a single blank line right before the next sheet header is the block
    separator. Returns {line_index: (sheet_name_or_None, row_number)} for
    every data line - a line without a tab is a one-cell row in column A.
    """
    locations = {}
    current_sheet = None
    row = 0
    pending_blanks = 0

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            pending_blanks += 1
            continue

        header = _SHEET_HEADER_RE.match(stripped)
        if header:
            current_sheet = header.group(1).strip()
            row = 0
            pending_blanks = 0
            continue

        if _SHEET_META_RE.match(stripped):
            continue

        # Every remaining line is a row of the sheet, including a row whose
        # cells collapsed to a single value: the "Notes" sheet of a workbook
        # with A1="Author" and B2="..." was read as one row too few, so the
        # B2 value was cited as B1. A line without a tab is column A.
        #
        # Blank lines immediately before a DATA row are interior blank rows
        # of the sheet (mirrors the display parser: cell B5 stays B5).
        if pending_blanks:
            row += pending_blanks
            pending_blanks = 0
        row += 1
        locations[idx] = (current_sheet, row)

    return locations


class SearchService:
    """
    Enhanced search service with PostgreSQL full-text search capabilities.
    
    This service provides:
    - Full-text search using PostgreSQL tsvector/tsquery
    - Google-like BM25 ranking algorithm
    - Query expansion with synonyms
    - Fuzzy matching for typo tolerance
    - Autocomplete/suggestions
    - Multi-field boosting
    - Recency boost
    - Phrase matching
    - Sortable search results
    - Advanced filtering
    - Search history tracking
    - Saved searches management
    """
    
    #: PERF-01: how many in-content line matches to collect per file.
    #: ``None`` used to mean "scan every line of every result", which made
    #: response time grow linearly with ``per_page`` (measured: 0.17 s at
    #: per_page=5 vs 0.71 s at per_page=50 on 59 files).
    MAX_LINE_MATCHES_PER_FILE = 10

    #: Metadata about the most recent advanced-search line scan (for API/UI).
    _last_search_engine: Dict[str, Any] = {}
    _last_search_plan: Dict[str, Any] = {}
    _last_pagination_cursor: Optional[str] = None
    _last_count_meta: Dict[str, Any] = {}
    _last_count_query: Optional[str] = None
    _last_count_params: Optional[Tuple[Any, ...]] = None
    _last_candidate_query: Optional[str] = None
    _last_candidate_params: Optional[Tuple[Any, ...]] = None
    _last_short_circuit: Optional[Dict[str, Any]] = None
    _last_literal_compile: Optional[str] = None

    # Initialize algorithm components (singleton pattern)
    _search_ranker = None
    _query_expander = None
    _fuzzy_matcher = None
    _query_understanding = None
    
    @classmethod
    def _get_ranker(cls):
        """Get or create search ranker instance."""
        if cls._search_ranker is None:
            cls._search_ranker = SearchRanker()
        return cls._search_ranker
    
    @classmethod
    def _get_expander(cls):
        """Get or create query expander instance."""
        if cls._query_expander is None:
            cls._query_expander = QueryExpander()
        return cls._query_expander
    
    @classmethod
    def _get_fuzzy_matcher(cls):
        """Get or create fuzzy matcher instance."""
        if cls._fuzzy_matcher is None:
            cls._fuzzy_matcher = FuzzyMatcher()
        return cls._fuzzy_matcher
    
    @classmethod
    def _get_query_understanding(cls):
        """Get or create query understanding instance."""
        if cls._query_understanding is None:
            cls._query_understanding = QueryUnderstanding()
        return cls._query_understanding
    
    @staticmethod
    def full_text_search(
        query: str,
        file_type: Optional[str] = None,
        source_id: Optional[int] = None,
        side_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        category_id: Optional[int] = None,
        sort_by: str = 'relevance',
        sort_order: str = 'desc',
        limit: int = 100,
        offset: int = 0,
        analyst_scope: Optional[str] = None,
        hide_duplicates: bool = False
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Perform full-text search using PostgreSQL tsvector/tsquery.

        Args:
            query: Search query string
            file_type: Filter by file type
            source_id: Filter by source ID
            side_id: Filter by side ID
            date_from: Start date filter (YYYY-MM-DD)
            date_to: End date filter (YYYY-MM-DD)
            category_id: Filter by category ID
            sort_by: Sort field ('relevance', 'date', 'name', 'type', 'size')
            sort_order: Sort order ('asc' or 'desc')
            limit: Maximum number of results
            offset: Offset for pagination
            analyst_scope: Analyst-categorization search scope
                ('uncategorized' | 'categorized' | 'all'; FR-2.x). Operates
                strictly on analyst categorization status - never on smart
                categorization (FR-2.4).

        Returns:
            Tuple of (results list, total count)
        """
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Build WHERE conditions
            where_conditions = []
            params = []
            
            # Google-like search: supports partial words, multiple words, numbers, and partial text
            ranking_query = query.strip() if query else ''
            if query and len(query.strip()) >= 2:
                query_clean = query.strip()
                search_terms = query_clean.split()
                ranking_query = ' '.join(term.strip('"') for term in search_terms)
                
                # Build search conditions using ILIKE for partial matching (Google-like)
                # This supports:
                # - Partial word matching (e.g., "fin" matches "financial")
                # - Multiple words with OR logic (at least one word matches)
                # - Numbers (e.g., "2023", "12345")
                # - Partial text matching (e.g., "report 2023" matches files with "report" or "2023")
                
                search_conditions = []
                
                # For each search term, create a condition that matches:
                # 1. File names containing the term (partial match)
                # 2. Words in file content containing the term (partial match)
                for term in search_terms:
                    # Escape special characters for ILIKE
                    term_pattern = f'%{_escape_like(term)}%'
                    
                    # Search in file names
                    search_conditions.append(f"p.file_name ILIKE %s")
                    params.append(term_pattern)
                    
                    # Search in content words
                    search_conditions.append(f"""
                        EXISTS (
                            SELECT 1 FROM words_hashs wp
                            JOIN words w ON wp.word_id = w.id
                            WHERE wp.hash_id = (
                            SELECT hc0.hash_id FROM hash_contexts hc0
                            WHERE hc0.id = p.context_id
                        )
                            AND w.word ILIKE %s
                        )
                    """)
                    params.append(term_pattern)
                
                # Combine all conditions with OR (Google-like: at least one term matches)
                # This allows searching for "financial report" to find files with either "financial" OR "report"
                if search_conditions:
                    where_conditions.append(f"({' OR '.join(search_conditions)})")
                
                # Also try full-text search for better ranking (optional enhancement)
                # This provides better relevance scoring for exact word matches
                try:
                    tsquery_terms = ' & '.join([term.replace("'", "''") for term in search_terms])
                    # Add full-text search as additional boost (not required, just for ranking)
                    # We'll use this in the ORDER BY for relevance scoring
                except:
                    tsquery_terms = None
            
            # File type filter (one value or a multi-select list).
            if file_type:
                file_types = list(file_type) if isinstance(file_type, (list, tuple)) else [file_type]
                file_types = [str(value) for value in file_types if value]
                if len(file_types) == 1:
                    where_conditions.append("p.file_type = %s")
                    params.append(file_types[0])
                elif file_types:
                    placeholders = ','.join(['%s'] * len(file_types))
                    where_conditions.append(f"p.file_type IN ({placeholders})")
                    params.extend(file_types)
            
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
                where_conditions.append("""
                    EXISTS (
                        SELECT 1 FROM words_hashs wp2
                        JOIN words_categorys wc ON wp2.word_id = wc.word_id
                        WHERE wp2.hash_id = hc.hash_id AND wc.category_id = %s
                    )
                """)
                params.append(category_id)

            # Analyst-categorization scope filter (FR-2.x). Runs purely on
            # analyst_file_categories; smart categorization status is never
            # consulted (FR-2.4).
            from Api.services.analyst_categories import scope_condition
            scope_sql = scope_condition(analyst_scope, alias="p")
            if scope_sql:
                where_conditions.append(scope_sql)

            where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"

            # Build ORDER BY clause for window function and final sorting
            order_by_clause = SearchService._build_order_by(sort_by, sort_order, query)
            # For final ORDER BY, remove table alias prefix since we're selecting from CTE
            order_by_final = order_by_clause.replace('p.', '')
            
            # For window function ORDER BY, we need to handle relevance_score specially
            # PostgreSQL doesn't allow referencing column aliases in window functions in the same SELECT
            # So we need to use the full expression when sorting by relevance
            order_by_window = order_by_clause
            if sort_by == 'relevance' and query:
                # Extract the sort order (ASC/DESC)
                order = 'DESC' if sort_order.lower() == 'desc' else 'ASC'
                # Use the same relevance calculation as in the SELECT clause
                # This ensures consistent ranking
                order_by_window = f"""
                    (CASE
                        WHEN query IS NOT NULL THEN
                            COALESCE((
                                SELECT 
                                    SUM(
                                        CASE 
                                            WHEN p.file_name ILIKE '%%' || term || '%%' THEN 2.0
                                            WHEN EXISTS (
                                                SELECT 1 FROM words_hashs wp
                                                JOIN words w ON wp.word_id = w.id
                                                WHERE wp.hash_id = (
                            SELECT hc0.hash_id FROM hash_contexts hc0
                            WHERE hc0.id = p.context_id
                        )
                                                AND w.word ILIKE '%%' || term || '%%'
                                            ) THEN 1.0
                                            ELSE 0.0
                                        END
                                    )
                                FROM unnest(string_to_array(query, ' ')) AS term
                            ), 0)
                        ELSE 0
                    END) {order}, p.file_date DESC
                """
            
            # The displayed total follows the same duplicate policy as the
            # result rows. Exact duplicate identity is the indexed source hash;
            # records without one remain distinct by path id.
            count_expression = (
                "COUNT(DISTINCT COALESCE(h.hash::text, 'path:' || p.id::text))"
                if hide_duplicates else "COUNT(DISTINCT p.id)"
            )
            count_query = f"""
                SELECT {count_expression}
                FROM paths p
                LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
                WHERE {where_clause}
            """
            
            cursor.execute(count_query, tuple(params))
            total_count = cursor.fetchone()[0]
            
            # Main search query with two stable ranks: first collapse joins to
            # one row per path, then (when requested) collapse exact content
            # hashes to one visible representative. Sorting remains server-side
            # for correct pagination and totals.
            content_order = order_by_final
            if not re.search(r"\bid\b", content_order):
                content_order = f"{content_order}, id ASC"
            duplicate_filter = "content_rank = 1" if hide_duplicates else "1=1"
            search_query = f"""
                WITH ranked_results AS (
                    SELECT 
                        p.id,
                        p.file_name,
                        p.file_path,
                        p.file_type,
                        p.file_size,
                        p.file_date,
                        p.file_status,
                        p.date_creation,
                        COALESCE(s.name, 'Unknown') as source_name,
                        COALESCE(si.name, 'Unknown') as side_name,
                        hc.source_id,
                        hc.side_id,
                        h.hash::text as content_hash,
                        CASE
                            WHEN query IS NOT NULL THEN
                                -- Google-like relevance: count matching search terms
                                -- Higher score for matches in file name, lower for content matches
                                (
                                    -- Check each search term and sum relevance
                                    -- File name matches get 2.0 points, content matches get 1.0 point
                                    COALESCE((
                                        SELECT 
                                            SUM(
                                                CASE 
                                                    WHEN p.file_name ILIKE '%%' || term || '%%' THEN 2.0
                                                    WHEN EXISTS (
                                                        SELECT 1 FROM words_hashs wp
                                                        JOIN words w ON wp.word_id = w.id
                                                        WHERE wp.hash_id = (
                            SELECT hc0.hash_id FROM hash_contexts hc0
                            WHERE hc0.id = p.context_id
                        )
                                                        AND w.word ILIKE '%%' || term || '%%'
                                                    ) THEN 1.0
                                                    ELSE 0.0
                                                END
                                            )
                                        FROM unnest(string_to_array(query, ' ')) AS term
                                    ), 0)
                                )
                            ELSE 0
                        END as relevance_score,
                        ROW_NUMBER() OVER (PARTITION BY p.id ORDER BY {order_by_window}) as rn
                    FROM paths p
                    LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
                    LEFT JOIN sources s ON hc.source_id = s.id
                    LEFT JOIN sides si ON hc.side_id = si.id
                    CROSS JOIN LATERAL (
                        SELECT %s::text as query
                    ) q
                    WHERE {where_clause}
                ),
                path_results AS (
                    SELECT * FROM ranked_results WHERE rn = 1
                ),
                deduplicated_results AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(content_hash, 'path:' || id::text)
                        ORDER BY {content_order}
                    ) AS content_rank
                    FROM path_results
                )
                SELECT 
                    id,
                    file_name,
                    file_path,
                    file_type,
                    file_size,
                    file_date,
                    file_status,
                    date_creation,
                    source_name,
                    side_name,
                    source_id,
                    side_id,
                    relevance_score
                FROM deduplicated_results
                WHERE {duplicate_filter}
                ORDER BY {order_by_final}
                LIMIT %s OFFSET %s
            """
            
            # Add query parameter for relevance calculation
            if ranking_query and len(ranking_query.strip()) >= 2:
                # Remove query syntax (quotes/operators) for score terms while
                # keeping the exact phrase predicates in the WHERE clause.
                params_with_query = [ranking_query.strip()] + params + [limit, offset]
            else:
                params_with_query = [''] + params + [limit, offset]
            
            cursor.execute(search_query, tuple(params_with_query))
            
            # Fetch results
            results = []
            for row in cursor.fetchall():
                result = {
                    'id': row[0],
                    'file_name': row[1],
                    'file_type': row[3],
                    'file_size': row[4],
                    'file_date': row[5].isoformat() if row[5] else None,
                    'file_status': str(row[6]),
                    'date_creation': row[7].isoformat() if row[7] else None,
                    'source_name': row[8],
                    'side_name': row[9],
                    'source_id': row[10],
                    'side_id': row[11],
                    'relevance_score': float(row[12]) if row[12] else 0.0
                }
                
                if query and len(query.strip()) >= 2:
                    try:
                        content = load_text_content(row[0]) or ''
                    except Exception:
                        content = ''
                    fields = SearchService._matching_fields(
                        result['file_name'], content, query,
                        metadata_text=' '.join((
                            str(result.get('file_type') or ''),
                            str(result.get('source_name') or ''),
                            str(result.get('side_name') or ''),
                        )))
                    result['match_fields'] = fields
                    result['matched_in'] = [
                        field for field, matched in fields.items() if matched
                    ]
                    line_matches = SearchService._find_matching_lines(
                        row[0], query,
                        max_matches=SearchService.MAX_LINE_MATCHES_PER_FILE,
                        content=content,
                    )
                    if line_matches:
                        result['line_matches'] = line_matches
                        result['line_match_count'] = len(line_matches)
                        result['snippet'] = line_matches[0]['line_text']
                
                results.append(result)
            
            cursor.close()
            return_connection(conn)
            
            return results, total_count
            
        except Exception as e:
            logger.error(f"Full-text search error: {e}", exc_info=True)
            if conn:
                return_connection(conn)
            return [], 0
    
    @staticmethod
    def _query_match_patterns(
        query: str, *, case_sensitive: bool = False, whole_word: bool = False
    ) -> List[Tuple[str, re.Pattern]]:
        """Compile positive query words/phrases for result annotation."""
        if not query or not query.strip():
            return []
        intent = SearchService._get_query_understanding().detect_intent(query)
        values = list(intent.get('phrases') or [])
        values.extend(
            str(item.get('term', '')).strip().strip('()')
            for item in (intent.get('terms') or [])
            if isinstance(item, dict) and item.get('term')
        )
        if (not values and not intent.get('has_operators')
                and not intent.get('has_quotes')):
            values = [query.strip()]
        patterns = []
        seen = set()
        for value in values:
            value = str(value).strip()
            seen_key = value if case_sensitive else value.casefold()
            if not value or seen_key in seen:
                continue
            seen.add(seen_key)
            source = (r'\s+'.join(re.escape(part) for part in value.split())
                      if ' ' in value else re.escape(value))
            if whole_word:
                source = rf'(?<!\w)(?:{source})(?!\w)'
            flags = 0 if case_sensitive else re.IGNORECASE
            patterns.append((value, re.compile(source, flags)))
        return patterns

    @staticmethod
    def _matching_fields(
        file_name: str,
        content: str,
        query: str,
        *,
        case_sensitive: bool = False,
        whole_word: bool = False,
        metadata_text: str = '',
    ) -> Dict[str, bool]:
        """Report which searchable fields contain a positive query match."""
        patterns = SearchService._query_match_patterns(
            query, case_sensitive=case_sensitive, whole_word=whole_word)
        searchable_content = strip_structural_markers(content or '')
        return {
            'file_name': any(pattern.search(str(file_name or '')) for _value, pattern in patterns),
            'content': any(pattern.search(searchable_content) for _value, pattern in patterns),
            'metadata': any(pattern.search(str(metadata_text or '')) for _value, pattern in patterns),
        }

    @staticmethod
    def _find_matching_lines(
        path_id: int,
        query: str,
        max_matches: Optional[int] = None,
        content: Optional[str] = None,
        case_sensitive: bool = False,
        whole_word: bool = False,
        candidate_line_numbers: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Find lines matching positive query terms and preserve their context."""
        try:
            if content is None:
                content = load_text_content(path_id)
            if not content:
                return []

            lines = content.split('\n')
            patterns = SearchService._query_match_patterns(
                query, case_sensitive=case_sensitive, whole_word=whole_word)
            if not patterns:
                return []
            if candidate_line_numbers is not None and not candidate_line_numbers:
                return []
            pattern_source = '|'.join(
                source.pattern for _value, source in
                sorted(patterns, key=lambda item: len(item[0]), reverse=True)
            )
            pattern = re.compile(pattern_source, 0 if case_sensitive else re.IGNORECASE)
            
            # Exact locations for spreadsheet rows: worksheet + row number.
            # Only for spreadsheets (worksheet headers present) and pure
            # tabular files (CSV-like): slide/word tables would otherwise
            # get a misleading pseudo-address.
            has_tabs = any("\t" in ln for ln in lines)
            has_sheets = any(_SHEET_HEADER_RE.match(ln.strip()) for ln in lines)
            looks_tabular = (
                has_tabs
                and not has_sheets
                and not any(re.match(r"^(Slide\s+\d+|Page\s+\d+|\[Style:)", ln.strip()) for ln in lines)
            )
            row_locations = _spreadsheet_row_map(lines) if has_tabs else {}

            # Find matching lines
            matches = []
            if candidate_line_numbers is not None:
                iterable = (
                    (num, lines[num - 1])
                    for num in candidate_line_numbers
                    if 1 <= num <= len(lines)
                )
            else:
                iterable = enumerate(lines, start=1)
            for line_num, line in iterable:
                # Structural markers (sheet headers, slide numbers, page
                # headers, ...) are display scaffolding, not content: they
                # never count as search matches. Labelled lines (From: X,
                # Subject: Y) match on their VALUE - the label word itself
                # is not searchable. The reported line_text stays the
                # original display line.
                searchable_line = strip_line_markers(line)
                if not searchable_line.strip():
                    continue
                # Check if the entire query phrase appears in this line
                found = pattern.search(searchable_line)
                if found:
                    # Get context (previous and next line if available)
                    context_before = lines[line_num - 2] if line_num > 1 else None
                    context_after = lines[line_num] if line_num < len(lines) else None
                    
                    # Highlight the entire phrase in the line.
                    # AUDIT (SEC-04): the line is file content and must be
                    # HTML-escaped before <mark> is injected. This field is
                    # rendered with innerHTML by
                    # static/js/modules/search/advanced-search.js
                    # (``match.highlighted_line || escapeHtml(match.line_text)``)
                    # while the sibling ``line_text`` IS escaped - so any
                    # content carrying HTML metacharacters was injected into
                    # the DOM unescaped.
                    highlighted_line = pattern.sub(
                        lambda m: f'<mark>{html.escape(m.group())}</mark>',
                        html.escape(line)
                    )
                    
                    match_entry = {
                        'line_number': line_num,
                        'line_text': line,
                        'highlighted_line': highlighted_line,
                        'context_before': context_before,
                        'context_after': context_after
                    }

                    # Spreadsheet hits cite the EXACT cell (sheet + column +
                    # row), not just a document-wide line number.
                    sheet_row = row_locations.get(line_num - 1)
                    if sheet_row is not None and (sheet_row[0] is not None or looks_tabular):
                        sheet_name, row_num = sheet_row
                        column = searchable_line.count("\t", 0, found.start()) + 1
                        cell = f"{_column_letter(column)}{row_num}"
                        match_entry['location'] = f"{sheet_name} - {cell}" if sheet_name else cell

                    matches.append(match_entry)
                    
                    # Limit number of matches if specified
                    if max_matches and len(matches) >= max_matches:
                        break
            
            return matches
            
        except Exception as e:
            logger.error(f"Error finding matching lines for path_id={path_id}: {e}", exc_info=True)
            return []

    @staticmethod
    def _line_matches_from_ripgrep(
        rg_matches: List[RipgrepMatch],
        content: str,
        *,
        max_matches: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Build snippet rows from ripgrep JSON (no full-document regex rescan)."""
        if not rg_matches or not content:
            return []
        lines = content.split("\n")
        has_tabs = any("\t" in ln for ln in lines)
        has_sheets = any(_SHEET_HEADER_RE.match(ln.strip()) for ln in lines)
        looks_tabular = (
            has_tabs
            and not has_sheets
            and not any(re.match(r"^(Slide\s+\d+|Page\s+\d+|\[Style:)", ln.strip()) for ln in lines)
        )
        row_locations = _spreadsheet_row_map(lines) if has_tabs else {}
        out: List[Dict[str, Any]] = []
        for match in rg_matches:
            line_num = match.line_number
            if line_num < 1 or line_num > len(lines):
                continue
            line = lines[line_num - 1]
            needle = match.matched_text or ""
            highlighted = html.escape(line)
            if needle:
                idx = line.find(needle)
                if idx < 0:
                    idx = line.lower().find(needle.lower())
                if idx >= 0:
                    end = idx + len(needle)
                    highlighted = (
                        f"{html.escape(line[:idx])}"
                        f"<mark>{html.escape(line[idx:end])}</mark>"
                        f"{html.escape(line[end:])}"
                    )
            entry = match.to_line_match_entry(
                context_before=lines[line_num - 2] if line_num > 1 else None,
                context_after=lines[line_num] if line_num < len(lines) else None,
                highlighted_line=highlighted,
            )
            sheet_row = row_locations.get(line_num - 1)
            if sheet_row is not None and (sheet_row[0] is not None or looks_tabular):
                sheet_name, row_num = sheet_row
                searchable_line = strip_line_markers(line)
                column = searchable_line.count("\t") + 1 if "\t" in searchable_line else 1
                cell = f"{_column_letter(column)}{row_num}"
                entry["location"] = f"{sheet_name} - {cell}" if sheet_name else cell
            out.append(entry)
            if max_matches and len(out) >= max_matches:
                break
        return out

    @staticmethod
    def _format_advanced_results(
        paginated_docs: List[Dict[str, Any]],
        *,
        query: str,
        case_sensitive: bool = False,
        whole_word: bool = False,
        include_snippets: bool = True,
    ) -> List[Dict[str, Any]]:
        """Build API result rows; optionally attach line-level snippets."""
        if not include_snippets:
            results: List[Dict[str, Any]] = []
            for doc in paginated_docs:
                result = SearchService._base_result_from_doc(doc)
                if query and len(query.strip()) >= 2:
                    result['snippets_pending'] = True
                    result['snippet'] = result['file_name']
                results.append(result)
            SearchService._last_search_engine = {
                'line_matcher': 'deferred',
                'ripgrep': get_scanner_from_settings().status(),
            }
            return results
        enriched, engine = SearchService.enrich_results_with_snippets(
            SearchService._base_results_from_docs(paginated_docs),
            query=query,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
        )
        SearchService._last_search_engine = engine
        return enriched

    @staticmethod
    def _base_result_from_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'id': doc['id'],
            'file_name': doc['file_name'],
            'file_type': doc['file_type'],
            'file_size': doc['file_size'],
            'file_date': doc['file_date'].isoformat() if doc.get('file_date') else None,
            'file_status': doc['file_status'],
            'date_creation': doc['date_creation'].isoformat() if doc.get('date_creation') else None,
            'source_name': doc['source_name'],
            'side_name': doc['side_name'],
            'source_id': doc['source_id'],
            'side_id': doc['side_id'],
            'relevance_score': doc.get('relevance_score', 0.0),
        }

    @staticmethod
    def _base_results_from_docs(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [SearchService._base_result_from_doc(doc) for doc in docs]

    @staticmethod
    def enrich_results_with_snippets(
        results: List[Dict[str, Any]],
        *,
        query: str,
        case_sensitive: bool = False,
        whole_word: bool = False,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Load text and line matches for result rows (ripgrep-accelerated)."""
        if not query or len(query.strip()) < 2 or not results:
            return results, {
                'line_matcher': 'python',
                'ripgrep': get_scanner_from_settings().status(),
            }

        trace = SearchTrace.current()
        search_cfg = get_settings().get_search_config()
        limits = SearchLimits.from_settings(search_cfg)
        max_bytes = min(
            int(search_cfg.get("snippet_load_max_bytes", 262144)),
            limits.max_document_bytes,
        )
        workers = min(
            int(search_cfg.get("snippet_worker_concurrency", 4)),
            limits.max_snippet_workers,
        )
        rg_session = SearchSessionRegistry.get(trace.correlation_id if trace else None)
        path_ids = [int(row["id"]) for row in results if row.get("id") is not None]
        io_span = trace.span("io_text_windows", bytes_read=max_bytes * len(path_ids)) if trace else None
        if io_span:
            io_span.__enter__()
        try:
            content_by_id = batch_load_text_windows(
                path_ids, max_bytes_per_file=max_bytes, max_workers=workers,
            )
            for path_id in path_ids:
                if path_id not in content_by_id:
                    content_by_id[path_id] = ""
        finally:
            if io_span:
                io_span.__exit__(None, None, None)

        ripgrep_detailed: Dict[int, List[RipgrepMatch]] = {}
        line_matcher = 'python'
        scanner = get_scanner_from_settings()
        scanner_status = scanner.status()
        if scanner.available and not (rg_session and rg_session.is_cancelled):
            patterns = SearchService._query_match_patterns(
                query, case_sensitive=case_sensitive, whole_word=whole_word)
            rg_pattern = build_ripgrep_pattern(
                positive_terms_from_patterns(patterns),
                whole_word=whole_word,
            )
            if rg_pattern:
                ripgrep_detailed = scanner.scan_batch_detailed(
                    content_by_id,
                    rg_pattern,
                    case_sensitive=case_sensitive,
                    whole_word=False,
                    max_matches_per_file=min(
                        SearchService.MAX_LINE_MATCHES_PER_FILE,
                        limits.max_snippets_per_document,
                    ),
                    session=rg_session,
                )
                if ripgrep_detailed:
                    line_matcher = 'ripgrep'

        for row in results:
            file_id = int(row['id'])
            content = content_by_id.get(file_id, '')
            fields = SearchService._matching_fields(
                row['file_name'], content, query,
                case_sensitive=case_sensitive, whole_word=whole_word,
                metadata_text=' '.join((
                    str(row.get('file_type') or ''),
                    str(row.get('source_name') or ''),
                    str(row.get('side_name') or ''),
                )))
            row['match_fields'] = fields
            row['matched_in'] = [field for field, matched in fields.items() if matched]
            rg_structured = ripgrep_detailed.get(file_id)
            if rg_structured:
                line_matches = SearchService._line_matches_from_ripgrep(
                    rg_structured,
                    content,
                    max_matches=SearchService.MAX_LINE_MATCHES_PER_FILE,
                )
            else:
                line_matches = SearchService._find_matching_lines(
                    file_id, query,
                    max_matches=SearchService.MAX_LINE_MATCHES_PER_FILE,
                    content=content,
                    case_sensitive=case_sensitive,
                    whole_word=whole_word,
                )
            if line_matches:
                row['line_matches'] = line_matches
                row['line_match_count'] = len(line_matches)
                row['snippet'] = line_matches[0]['line_text']
            elif fields['file_name']:
                row['snippet'] = row['file_name']
            row.pop('snippets_pending', None)

        return results, {'line_matcher': line_matcher, 'ripgrep': scanner_status}
    
    @staticmethod
    def _build_order_by(sort_by: str, sort_order: str, query: Optional[str] = None) -> str:
        """
        Build ORDER BY clause based on sort parameters.
        
        Args:
            sort_by: Sort field name
            sort_order: Sort order ('asc' or 'desc')
            query: Optional search query for relevance sorting
        
        Returns:
            ORDER BY clause string (without p.id prefix, as we use window functions)
        """
        order = 'DESC' if sort_order.lower() == 'desc' else 'ASC'
        
        if sort_by == 'relevance' and query:
            return f"relevance_score {order}, p.file_date DESC"
        elif sort_by == 'date':
            return f"p.file_date {order}, p.id"
        elif sort_by == 'name':
            return f"p.file_name {order}, p.id"
        elif sort_by == 'type':
            return f"p.file_type {order}, p.file_name ASC, p.id"
        elif sort_by == 'size':
            return f"p.file_size {order}, p.file_name ASC, p.id"
        else:
            return f"p.file_date DESC, p.id"
    
    @staticmethod
    def simple_search(
        query: str,
        limit: int = 100,
        offset: int = 0,
        analyst_scope: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Simple search using ILIKE for backward compatibility.

        Args:
            query: Search query string
            limit: Maximum number of results
            offset: Offset for pagination
            analyst_scope: Analyst-categorization search scope
                ('uncategorized' | 'categorized' | 'all'; FR-2.x). Operates
                strictly on analyst categorization status - never on smart
                categorization (FR-2.4).

        Returns:
            Tuple of (results list, total count)
        """
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            if not query or len(query.strip()) < 2:
                cursor.close()
                return_connection(conn)
                return [], 0
            
            query_clean = query.strip()
            search_terms = query_clean.split()
            
            # Google-like search: build conditions for each term with OR logic
            search_conditions = []
            search_params = []
            
            for term in search_terms:
                # Escape ILIKE wildcards only (see _escape_like).
                term_pattern = f'%{_escape_like(term)}%'
                
                # Search in file names
                search_conditions.append("p.file_name ILIKE %s")
                search_params.append(term_pattern)
                
                # Search in content words
                search_conditions.append("""
                    EXISTS (
                        SELECT 1 FROM words_hashs wp
                        JOIN words w ON wp.word_id = w.id
                        WHERE wp.hash_id = (
                            SELECT hc0.hash_id FROM hash_contexts hc0
                            WHERE hc0.id = p.context_id
                        )
                        AND w.word ILIKE %s
                    )
                """)
                search_params.append(term_pattern)
            
            # Analyst-categorization scope filter (FR-2.x) - analyst status
            # only, never smart categorization (FR-2.4).
            from Api.services.analyst_categories import scope_condition
            scope_sql = scope_condition(analyst_scope, alias="p")

            where_clause = f"({' OR '.join(search_conditions)})" if search_conditions else "1=1"
            # AND the scope separately so it is never absorbed by the OR
            # term-matching group above.
            if scope_sql:
                where_clause = f"({where_clause}) AND {scope_sql}"
            
            # Count query
            count_query = f"""
                SELECT COUNT(DISTINCT p.id)
                FROM paths p
                LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
                WHERE {where_clause}
            """
            
            cursor.execute(count_query, tuple(search_params))
            total_count = cursor.fetchone()[0]
            
            # Main query with relevance scoring
            search_query = f"""
                SELECT DISTINCT ON (p.id)
                    p.id, p.file_name, p.file_path, p.file_type,
                    p.file_size, p.file_date, p.file_status, p.date_creation,
                    COALESCE(s.name, 'Unknown') as source_name,
                    COALESCE(si.name, 'Unknown') as side_name,
                    hc.source_id, hc.side_id,
                    -- Google-like relevance score
                    COALESCE((
                        SELECT 
                            SUM(
                                CASE 
                                    WHEN p.file_name ILIKE '%%' || term || '%%' THEN 2.0
                                    WHEN EXISTS (
                                        SELECT 1 FROM words_hashs wp
                                        JOIN words w ON wp.word_id = w.id
                                        WHERE wp.hash_id = (
                            SELECT hc0.hash_id FROM hash_contexts hc0
                            WHERE hc0.id = p.context_id
                        )
                                        AND w.word ILIKE '%%' || term || '%%'
                                    ) THEN 1.0
                                    ELSE 0.0
                                END
                            )
                        FROM unnest(string_to_array(%s, ' ')) AS term
                    ), 0) as relevance_score
                FROM paths p
                LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
                LEFT JOIN sources s ON hc.source_id = s.id
                LEFT JOIN sides si ON hc.side_id = si.id
                WHERE {where_clause}
                ORDER BY p.id, relevance_score DESC, p.file_date DESC
                LIMIT %s OFFSET %s
            """
            
            cursor.execute(search_query, tuple(search_params + [query_clean, limit, offset]))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    'id': row[0],
                    'file_name': row[1],
                    'file_type': row[3],
                    'file_size': row[4],
                    'file_date': row[5].isoformat() if row[5] else None,
                    'file_status': str(row[6]),
                    'date_creation': row[7].isoformat() if row[7] else None,
                    'source_name': row[8],
                    'side_name': row[9],
                    'source_id': row[10],
                    'side_id': row[11],
                    'relevance_score': float(row[12]) if len(row) > 12 and row[12] is not None else 0.0
                })
            
            cursor.close()
            return_connection(conn)
            
            return results, total_count
            
        except Exception as e:
            logger.error(f"Simple search error: {e}", exc_info=True)
            if conn:
                return_connection(conn)
            return [], 0
    
    @staticmethod
    def advanced_search(
        query: str,
        file_type: Optional[str] = None,
        source_id: Optional[int] = None,
        side_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        category_id: Optional[int] = None,
        source_ids: Optional[List[int]] = None,
        side_ids: Optional[List[int]] = None,
        category_ids: Optional[List[int]] = None,
        sort_by: str = 'relevance',
        sort_order: str = 'desc',
        limit: int = 100,
        offset: int = 0,
        use_bm25: bool = True,
        use_expansion: bool = True,
        use_fuzzy: bool = True,
        analyst_scope: Optional[str] = None,
        analyst_category_ids: Optional[List[int]] = None,
        file_statuses: Optional[List[str]] = None,
        hide_duplicates: bool = False,
        case_sensitive: bool = False,
        whole_word: bool = False,
        include_snippets: bool = True,
        fast_mode: bool = False,
        count_mode: Optional[str] = None,
        cursor_after_score: Optional[float] = None,
        cursor_after_id: Optional[int] = None,
        cursor_has_date: bool = False,
        cursor_after_date: Optional[date] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Advanced search using Google-like algorithms (BM25, query expansion, fuzzy matching).
        
        Args:
            query: Search query string
            file_type: Filter by file type (can be string or list)
            source_id: Filter by source ID (single value, for backward compatibility)
            side_id: Filter by side ID (single value, for backward compatibility)
            date_from: Start date filter (YYYY-MM-DD)
            date_to: End date filter (YYYY-MM-DD)
            category_id: Filter by category ID (single value, for backward compatibility)
            source_ids: Filter by multiple source IDs (list)
            side_ids: Filter by multiple side IDs (list)
            category_ids: Filter by multiple category IDs (list)
            sort_by: Sort field ('relevance', 'date', 'name', 'type', 'size')
            sort_order: Sort order ('asc' or 'desc')
            limit: Maximum number of results
            offset: Offset for pagination
            use_bm25: Use BM25 ranking algorithm
            use_expansion: Use query expansion
            use_fuzzy: Use fuzzy matching
            analyst_scope: Analyst-categorization search scope
                ('uncategorized' | 'categorized' | 'all'; FR-2.x). Operates
                strictly on analyst categorization status - never on smart
                categorization (FR-2.4).
            analyst_category_ids: Filter by analyst (manual) category IDs -
                a separate dimension from the smart ``category_ids`` filter
                (FR-1.4 / FR-3.1).
            file_statuses: Optional ``Read``/``Unread`` file-status filter.
                ``None`` leaves status unfiltered; an empty list matches none.
            hide_duplicates: Return one result per exact stored content hash.
            include_snippets: When False, skip loading full text and line
                matches (use ``enrich_results_with_snippets`` for a follow-up).
            fast_mode: SQL pagination and ranking — no Python BM25 over large
                candidate pools (intended for million-row corpora).
        
        Returns:
            Tuple of (results list, total count)
        """
        if fast_mode:
            use_bm25 = False
        search_cfg = get_settings().get_search_config()
        if count_mode is None:
            count_mode = str(search_cfg.get("search_count_mode", "timed"))
        trace = SearchTrace.current()
        # Initialize lists if not provided
        if source_ids is None:
            source_ids = []
        if side_ids is None:
            side_ids = []
        if category_ids is None:
            category_ids = []
        if analyst_category_ids is None:
            analyst_category_ids = []
        
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                session = SearchSessionRegistry.get(trace.correlation_id if trace else None)
                if session and session.is_cancelled:
                    return [], 0
                try:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid_row = cursor.fetchone()
                    if session and pid_row:
                        session.register_pg_backend(int(pid_row[0]))
                except Exception:
                    pass
                interactive_ms = int(
                    search_cfg.get("search_interactive_statement_timeout_ms", 15000)
                )
                try:
                    cursor.execute(
                        "SET LOCAL statement_timeout = %s",
                        (f"{interactive_ms}ms",),
                    )
                except Exception:
                    pass

                # Understand query intent
                intent = SearchService._get_query_understanding().detect_intent(query)
                prelim_terms = (
                    [t.strip() for t in query.strip().split() if t.strip()]
                    if query and len(query.strip()) >= 2
                    and not intent.get("has_operators")
                    else []
                )
                resolved_prelim = (
                    resolve_word_ids_for_terms(prelim_terms, cursor=cursor)
                    if prelim_terms
                    else {}
                )
                SearchService._last_search_plan = plan_search(
                    query=query,
                    has_metadata_filters=any((
                        file_type, source_ids, side_ids, category_ids,
                        analyst_category_ids, date_from, date_to,
                        file_statuses is not None,
                    )),
                    case_sensitive=case_sensitive,
                    whole_word=whole_word,
                    has_boolean_ops=bool(intent.get("has_operators")),
                    has_quotes=bool(intent.get("has_quotes")),
                    fast_mode=fast_mode,
                    defer_snippets=not include_snippets,
                    resolved_word_ids=len(resolved_prelim),
                    term_count=len(prelim_terms),
                    count_mode=count_mode,
                    use_bm25=use_bm25,
                    cursor_present=(
                        cursor_after_score is not None and cursor_after_id is not None
                    ),
                ).to_dict()
                SearchService._last_pagination_cursor = None
                SearchService._last_count_query = None
                SearchService._last_count_params = None
                SearchService._last_candidate_query = None
                SearchService._last_candidate_params = None
                SearchService._last_short_circuit = None
                SearchService._last_literal_compile = None
                short_circuit_meta: Optional[Dict[str, Any]] = None
                ranking_terms = [str(phrase) for phrase in intent.get('phrases', [])]
                ranking_terms.extend(
                    str(term.get('term', '')) for term in (intent.get('terms') or [])
                    if isinstance(term, dict) and term.get('term')
                )
                ranking_query = ' '.join(term for term in ranking_terms if term) or query.strip()
                
                # Build WHERE conditions
                where_conditions = []
                params = []

                def literal_field_matches(value):
                    """Search indexed text plus searchable file/provenance metadata."""
                    return _literal_field_matches(
                        value, case_sensitive=case_sensitive, whole_word=whole_word)

                # Explicit queries, case-sensitive searches, and whole-word
                # searches use literal predicates against text, filenames and
                # provenance metadata. Full-text indexes cannot enforce those
                # modes, exact phrases, or boolean grouping.
                plain_term_matching = False
                needs_literal_matching = (
                    intent['has_quotes'] or intent['has_operators']
                    or '(' in query or ')' in query
                    or case_sensitive or whole_word
                )

                def compile_literal_expression(expression):
                    kind = expression[0]
                    if kind == 'term':
                        clauses, values = literal_field_matches(expression[1])
                        return f"({' OR '.join(clauses)})", values
                    if kind == 'not':
                        child_sql, child_values = compile_literal_expression(expression[1])
                        return f"NOT ({child_sql})", child_values
                    left_sql, left_values = compile_literal_expression(expression[1])
                    right_sql, right_values = compile_literal_expression(expression[2])
                    operator = 'AND' if kind == 'and' else 'OR'
                    return (f"({left_sql} {operator} {right_sql})",
                            left_values + right_values)

                if needs_literal_matching:
                    expression = _parse_boolean_search_expression(query)
                    if expression is None:
                        where_conditions.append("1=0")
                    else:
                        short_circuit_meta = assess_indexed_vocabulary_short_circuit(
                            query=query,
                            intent=intent,
                            case_sensitive=case_sensitive,
                            whole_word=whole_word,
                            expansion_terms=[],
                            cursor=cursor,
                        )
                        if short_circuit_meta:
                            SearchService._last_short_circuit = dict(short_circuit_meta)
                            where_conditions.append("1=0")
                        elif (
                            not case_sensitive
                            and not whole_word
                            and is_pure_disjunction(expression)
                            and len(flatten_disjunction_terms(expression)) >= 2
                        ):
                            or_terms = flatten_disjunction_terms(expression)
                            expression_sql, expression_params = flat_or_literal_sql(
                                or_terms,
                                case_sensitive=case_sensitive,
                                whole_word=whole_word,
                            )
                            SearchService._last_literal_compile = "flat_or"
                            where_conditions.append(expression_sql)
                            params.extend(expression_params)
                        else:
                            expression_sql, expression_params = compile_literal_expression(
                                expression)
                            SearchService._last_literal_compile = "nested_literal"
                            where_conditions.append(expression_sql)
                            params.extend(expression_params)
                elif query and len(query.strip()) >= 2:
                    # Regular search with potential expansion.
                    search_query = query.strip()
                    plain_term_matching = True

                # EXPANSION-FIX (FR-1.1): expanded terms widen recall - they
                # are OR alternatives, never additional AND requirements. The
                # previous implementation merged the synonyms into the query
                # string, so a search for "report" silently also required
                # "document", "summary" AND "analysis" to be present and
                # returned nothing. It also referenced ``search_query`` before
                # assignment for complex operator queries (NameError swallowed
                # by the outer try -> empty results).
                expansion_terms = []
                if use_expansion and plain_term_matching:
                    expander = SearchService._get_expander()
                    candidate_terms = expander.expand(search_query)
                    original_lower = {t.lower() for t in search_query.split()}
                    expansion_terms = [
                        t for t in candidate_terms
                        if t and t.lower() not in original_lower
                    ][:3]  # Limit expansions
                
                plain_term_used_fast_inverted = False
                if plain_term_matching:
                    # Create tsquery from search terms - handle multi-word searches
                    # Split by spaces and join with & (AND) for multi-word support
                    search_terms = [t.strip() for t in search_query.split() if t.strip()]
                    
                    if not search_terms:
                        # No valid terms
                        where_conditions.append("1=0")  # No results
                    elif short_circuit_meta is None:
                        short_circuit_meta = assess_indexed_vocabulary_short_circuit(
                            query=search_query,
                            intent=intent,
                            case_sensitive=case_sensitive,
                            whole_word=whole_word,
                            expansion_terms=expansion_terms,
                            cursor=cursor,
                        )
                        if short_circuit_meta:
                            SearchService._last_short_circuit = dict(short_circuit_meta)
                            where_conditions.append("1=0")
                    if (
                        search_terms
                        and short_circuit_meta is None
                        and can_use_fast_inverted_path(
                            search_terms,
                            case_sensitive=case_sensitive,
                            whole_word=whole_word,
                            has_boolean_ops=bool(intent.get("has_operators")),
                            has_quotes=bool(intent.get("has_quotes")),
                            cursor=cursor,
                        )
                        and not expansion_terms
                    ):
                        id_map = resolve_word_ids_for_terms(search_terms, cursor=cursor)
                        word_ids = [
                            id_map[normalize_search_token(term)]
                            for term in search_terms
                        ]
                        inv_sql, inv_params = inverted_index_and_clause(word_ids)
                        fname_clauses = [
                            "p.file_name ILIKE %s" for _ in search_terms
                        ]
                        fname_params = [
                            f"%{_escape_like(term)}%" for term in search_terms
                        ]
                        fname_sql = (
                            " AND ".join(fname_clauses)
                            if len(fname_clauses) > 1
                            else fname_clauses[0]
                        )
                        where_conditions.append(
                            f"(({inv_sql}) OR ({fname_sql}))"
                        )
                        params.extend(inv_params + fname_params)
                        plain_term_used_fast_inverted = True
                    elif search_terms and short_circuit_meta is None and len(search_terms) == 1:
                        # Single word - use prefix matching for better results
                        term = search_terms[0].replace("'", "''")
                        tsquery_terms = f"{term}:*"
                        core_condition = """
                            (
                                to_tsvector('english', COALESCE(p.file_name, '')) @@ to_tsquery(%s)
                                OR p.file_name ILIKE %s
                                OR EXISTS (
                                    SELECT 1 FROM words_hashs wp
                                    JOIN words w ON wp.word_id = w.id
                                    WHERE wp.hash_id = (
                            SELECT hc0.hash_id FROM hash_contexts hc0
                            WHERE hc0.id = p.context_id
                        )
                                    AND (
                                        to_tsvector('english', w.word) @@ to_tsquery(%s)
                                        OR w.word ILIKE %s
                                    )
                                )
                            )
                        """
                        like_pattern = f'%{_escape_like(search_terms[0])}%'
                        core_params = [tsquery_terms, like_pattern, tsquery_terms, like_pattern]
                    elif search_terms and short_circuit_meta is None:
                        # Multi-word search - use AND logic (all words must appear)
                        # Use & operator for AND logic in PostgreSQL tsquery
                        tsquery_terms = ' & '.join([term.replace("'", "''") for term in search_terms])
                        
                        # Also create ILIKE patterns for each term (fallback for better multi-word matching)
                        like_patterns = [f'%{_escape_like(term)}%' for term in search_terms]
                        
                        # Build condition: all terms must appear in file name OR all terms appear in content
                        core_condition = """
                            (
                                (
                                    to_tsvector('english', COALESCE(p.file_name, '')) @@ to_tsquery(%s)
                                    OR (p.file_name ILIKE ALL(ARRAY[%s]))
                                )
                                OR EXISTS (
                                    SELECT 1 FROM words_hashs wp
                                    JOIN words w ON wp.word_id = w.id
                                    WHERE wp.hash_id = (
                            SELECT hc0.hash_id FROM hash_contexts hc0
                            WHERE hc0.id = p.context_id
                        )
                                    AND (
                                        to_tsvector('english', w.word) @@ to_tsquery(%s)
                                        OR w.word ILIKE ANY(ARRAY[%s])
                                    )
                                )
                            )
                        """
                        core_params = [tsquery_terms, like_patterns, tsquery_terms, like_patterns]

                    if search_terms and not plain_term_used_fast_inverted and short_circuit_meta is None:
                        # Keep the full-text/tokenized path for ranking-friendly
                        # recall, and add literal predicates for names, content,
                        # and provenance fields that are not part of the vector.
                        literal_terms = []
                        literal_params = []
                        for search_term in search_terms:
                            field_clauses, field_params = literal_field_matches(
                                search_term)
                            literal_terms.append(
                                f"({' OR '.join(field_clauses)})")
                            literal_params.extend(field_params)
                        literal_condition = (
                            f"({' AND '.join(literal_terms)})" if literal_terms
                            else "1=0")

                        if expansion_terms:
                            # OR the expanded synonyms alongside the original
                            # terms so expansion only ever widens the result set.
                            exp_placeholders = ','.join(['%s'] * len(expansion_terms))
                            expansion_condition = f"""
                                (
                                    p.file_name ILIKE ANY(ARRAY[{exp_placeholders}])
                                    OR EXISTS (
                                        SELECT 1 FROM words_hashs wp
                                        JOIN words w ON wp.word_id = w.id
                                        WHERE wp.hash_id = (
                            SELECT hc0.hash_id FROM hash_contexts hc0
                            WHERE hc0.id = p.context_id
                        )
                                        AND w.word ILIKE ANY(ARRAY[{exp_placeholders}])
                                    )
                                )
                            """
                            exp_patterns = [f'%{_escape_like(t)}%' for t in expansion_terms]
                            where_conditions.append(
                                f"({core_condition} OR {literal_condition} "
                                f"OR {expansion_condition})")
                            params.extend(
                                core_params + literal_params
                                + exp_patterns + exp_patterns)
                        else:
                            where_conditions.append(
                                f"({core_condition} OR {literal_condition})")
                            params.extend(core_params + literal_params)
            
                # Structured filters: the single shared implementation
                # (core/criteria/sql.py::filter_predicates), identical for
                # interactive search and every compiled Criteria consumer.
                # Legacy single-value parameters are folded into the lists.
                effective_sources = list(source_ids) or ([source_id] if source_id else [])
                effective_sides = list(side_ids) or ([side_id] if side_id else [])
                effective_categories = (list(category_ids)
                                        or ([category_id] if category_id else []))
                if isinstance(file_type, (list, tuple)):
                    effective_types = list(file_type)
                elif file_type:
                    effective_types = [file_type]
                else:
                    effective_types = []
                filter_sql, filter_params = _filter_predicates(
                    source_ids=effective_sources,
                    side_ids=effective_sides,
                    file_types=effective_types,
                    date_from=date_from,
                    date_to=date_to,
                    file_statuses=file_statuses,
                    category_ids=effective_categories,
                )
                where_conditions.extend(filter_sql)
                params.extend(filter_params)

                # Analyst-category filter (FR-3.1): a separate dimension from the
                # smart category filter above - reads ONLY analyst tables and is
                # never satisfied by a smart category id (FR-1.4).
                # Same shared predicate as every compiled Criteria consumer
                # (core/criteria/sql.py::filter_predicates).
                if analyst_category_ids:
                    afc_sql, afc_params = _filter_predicates(
                        analyst_category_ids=analyst_category_ids)
                    where_conditions.extend(afc_sql)
                    params.extend(afc_params)

                # Analyst-categorization scope filter (FR-2.x). Runs purely on
                # analyst_file_categories; smart categorization status is never
                # consulted (FR-2.4). The EXISTS/NOT EXISTS probe is served by
                # idx_afc_path_id so scoped searches perform like unscoped ones
                # (NFR-4).
                from Api.services.analyst_categories import scope_condition
                scope_sql = scope_condition(analyst_scope, alias="p")
                if scope_sql:
                    where_conditions.append(scope_sql)

                where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
                limits = SearchLimits.from_settings(search_cfg)
                limit = min(limit, limits.max_response_results)

                if session and session.is_cancelled:
                    cursor.execute("SET LOCAL statement_timeout = 0")
                    return [], 0
            
                # Count with the same exact-content identity used by the visible
                # result list. Missing hashes remain distinct path records.
                count_expression = (
                    "COUNT(DISTINCT COALESCE(h.hash::text, 'path:' || p.id::text))"
                    if hide_duplicates else "COUNT(DISTINCT p.id)"
                )
                count_query = f"""
                    SELECT {count_expression}
                    FROM paths p
                    LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
                    WHERE {where_clause}
                """
                SearchService._last_count_query = count_query
                SearchService._last_count_params = tuple(params)

                skip_heavy_sql = bool(short_circuit_meta)
                total_count = 0
                if skip_heavy_sql:
                    sc_extra = dict(short_circuit_meta or {})
                    if trace:
                        with trace.span("word_resolution", **sc_extra):
                            pass
                    count_span = trace.span("db_count", **sc_extra) if trace else None
                    if count_span:
                        count_span.__enter__()
                        count_span.__exit__(None, None, None)
                    SearchService._last_count_meta = {
                        "total": 0,
                        "total_is_approximate": False,
                        "count_mode": count_mode,
                        "note": sc_extra.get("short_circuit_reason"),
                    }
                else:
                    count_span = trace.span("db_count") if trace else None
                    if count_span:
                        count_span.__enter__()
                    try:
                        total_count, approx, note = apply_count_mode(
                            cursor,
                            count_query,
                            tuple(params),
                            mode=count_mode,
                            timeout_ms=int(search_cfg.get("search_count_timeout_ms", 250)),
                        )
                        SearchService._last_count_meta = {
                            "total": total_count,
                            "total_is_approximate": approx,
                            "count_mode": count_mode,
                            "note": note,
                        }
                    finally:
                        if count_span:
                            count_span.__exit__(None, None, None)
                    if total_count is None:
                        total_count = 0
            
                sort_direction = 'ASC' if sort_order.lower() == 'asc' else 'DESC'
                candidate_order = {
                    'date': f'p.file_date {sort_direction}, p.id ASC',
                    'name': f'p.file_name {sort_direction}, p.id ASC',
                    'type': f'p.file_type {sort_direction}, p.file_name ASC, p.id ASC',
                    'size': f'p.file_size {sort_direction}, p.file_name ASC, p.id ASC',
                }.get(sort_by, 'p.file_date DESC, p.id ASC')
                candidate_order_final = candidate_order.replace('p.', '')

                use_sql_pagination = fast_mode or not use_bm25
                use_sql_relevance = (
                    use_sql_pagination
                    and sort_by == 'relevance'
                    and query
                    and len(query.strip()) >= 2
                )
                relevance_expr = ""
                relevance_params: List[Any] = []
                if use_sql_relevance:
                    relevance_expr, relevance_params = sql_relevance_score_expr(
                        ranking_terms or [query.strip()])
                relevance_order = (
                    f"relevance_score {sort_direction}, p.file_date DESC, p.id ASC"
                )
                relevance_order_final = relevance_order.replace('p.', '')

                use_keyset = (
                    use_sql_pagination
                    and use_sql_relevance
                    and cursor_after_score is not None
                    and cursor_after_id is not None
                    and sort_order.lower() == "desc"
                    and not hide_duplicates
                )
                keyset_params: List[Any] = []
                effective_where = where_clause
                if use_keyset and relevance_expr:
                    # The ORDER BY is ``score DESC, file_date DESC, id ASC`` (NULL
                    # dates sort first under DESC). The rows after the cursor are
                    # therefore: a lower score; or an equal score with an earlier
                    # date; or an equal score and date with a higher id. A cursor
                    # without a recorded date (legacy v2 token) keeps the original
                    # (score, id) comparison.
                    if cursor_has_date and cursor_after_date is not None:
                        tie_clause = (
                            "(p.file_date < %s OR "
                            "(p.file_date = %s AND p.id > %s))"
                        )
                        tie_params: List[Any] = [
                            cursor_after_date, cursor_after_date, cursor_after_id,
                        ]
                    elif cursor_has_date:
                        # NULL dates come first in DESC order: after a NULL-date
                        # row come all dated rows, then NULL-date rows with a
                        # higher id.
                        tie_clause = (
                            "(p.file_date IS NOT NULL OR "
                            "(p.file_date IS NULL AND p.id > %s))"
                        )
                        tie_params = [cursor_after_id]
                    else:
                        tie_clause = "p.id > %s"
                        tie_params = [cursor_after_id]
                    effective_where = (
                        f"{where_clause} AND ("
                        f"({relevance_expr}) < %s OR "
                        f"(({relevance_expr}) = %s AND {tie_clause})"
                        f")"
                    )
                    # The WHERE clause is: <filters> AND (<expr> < %s OR (<expr> = %s
                    # AND <tie>)). The relevance expression has its own placeholders,
                    # so its parameters sit between the two cursor scores. Omitting
                    # them shifted every later parameter, the statement raised
                    # IndexError, and the next page came back empty.
                    keyset_params = [
                        cursor_after_score,
                        *relevance_params,
                        cursor_after_score,
                        *tie_params,
                    ]

                if use_sql_pagination:
                    fetch_limit = min(limit, limits.max_candidates)
                    if use_keyset:
                        limit_clause = "LIMIT %s"
                        limit_params = [fetch_limit]
                    else:
                        sql_offset = offset
                        limit_clause = "LIMIT %s OFFSET %s"
                        limit_params = [fetch_limit, sql_offset]
                else:
                    # The ranked pool must reach the window being asked for: the
                    # slice below is documents[offset:offset + limit], so a pool
                    # sized from limit alone returns nothing past the first few pages.
                    fetch_limit = min(max(limit * 3, offset + limit), limits.max_rank_pool)
                    sql_offset = 0
                    limit_clause = "LIMIT %s"
                    limit_params = [fetch_limit]

                if hide_duplicates:
                    if use_sql_relevance:
                        # A window ORDER BY cannot use an output alias of the same
                        # SELECT (``relevance_score`` is one), so repeat the score
                        # expression here. Its parameters are bound in the matching
                        # position below. Same direction and tie-breaks as
                        # ``relevance_order``.
                        inner_order = f"{relevance_expr} {sort_direction}, p.file_date DESC, p.id ASC"
                    else:
                        inner_order = candidate_order
                    relevance_select = (
                        f", {relevance_expr} AS relevance_score" if use_sql_relevance else ""
                    )
                    outer_order = (
                        relevance_order_final if use_sql_relevance else candidate_order_final
                    )
                    candidate_query = f"""
                        WITH candidate_paths AS (
                            SELECT
                                p.id,
                                p.file_name,
                                p.file_path,
                                p.file_type,
                                p.file_size,
                                p.file_date,
                                p.file_status,
                                p.date_creation,
                                COALESCE(s.name, 'Unknown') as source_name,
                                COALESCE(si.name, 'Unknown') as side_name,
                                hc.source_id,
                                hc.side_id
                                {relevance_select},
                                ROW_NUMBER() OVER (
                                    PARTITION BY COALESCE(h.hash::text, 'path:' || p.id::text)
                                    ORDER BY {inner_order}
                                ) AS duplicate_rank
                            FROM paths p
                            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
                            LEFT JOIN sources s ON hc.source_id = s.id
                            LEFT JOIN sides si ON hc.side_id = si.id
                            WHERE {effective_where}
                        )
                        SELECT id, file_name, file_path, file_type, file_size,
                               file_date, file_status, date_creation, source_name,
                               side_name, source_id, side_id
                               {', relevance_score' if use_sql_relevance else ''}
                        FROM candidate_paths
                        WHERE duplicate_rank = 1
                        ORDER BY {outer_order}
                        {limit_clause}
                    """
                else:
                    relevance_select = (
                        f", {relevance_expr} AS relevance_score" if use_sql_relevance else ""
                    )
                    outer_order = (
                        relevance_order if use_sql_relevance else candidate_order
                    )
                    candidate_query = f"""
                        SELECT DISTINCT
                            p.id,
                            p.file_name,
                            p.file_path,
                            p.file_type,
                            p.file_size,
                            p.file_date,
                            p.file_status,
                            p.date_creation,
                            COALESCE(s.name, 'Unknown') as source_name,
                            COALESCE(si.name, 'Unknown') as side_name,
                            hc.source_id,
                            hc.side_id
                            {relevance_select}
                        FROM paths p
                        LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
                        LEFT JOIN sources s ON hc.source_id = s.id
                        LEFT JOIN sides si ON hc.side_id = si.id
                        WHERE {effective_where}
                        ORDER BY {outer_order}
                        {limit_clause}
                    """
            
                # Placeholders in ``relevance_expr`` appear in SELECT before WHERE.
                if use_sql_relevance and use_keyset:
                    candidate_params = (
                        list(relevance_params)
                        + list(params)
                        + list(relevance_params)
                        + list(keyset_params)
                        + list(limit_params)
                    )
                elif use_sql_relevance and hide_duplicates:
                    # SELECT score, window ORDER BY score (same parameters again), WHERE, LIMIT.
                    candidate_params = (
                        list(relevance_params) + list(relevance_params)
                        + list(params) + list(limit_params)
                    )
                elif use_sql_relevance:
                    candidate_params = (
                        list(relevance_params) + list(params) + list(limit_params)
                    )
                else:
                    candidate_params = list(params) + list(limit_params)
                if session and session.is_cancelled:
                    cursor.execute("SET LOCAL statement_timeout = 0")
                    return [], 0

                SearchService._last_candidate_query = candidate_query
                SearchService._last_candidate_params = tuple(candidate_params)

                if skip_heavy_sql:
                    sc_extra = dict(short_circuit_meta or {})
                    cand_span = trace.span("db_candidates", **sc_extra) if trace else None
                    if cand_span:
                        cand_span.__enter__()
                        cand_span.__exit__(None, None, None)
                    candidates = []
                else:
                    cand_span = trace.span("db_candidates") if trace else None
                    if cand_span:
                        cand_span.__enter__()
                    try:
                        cursor.execute(candidate_query, tuple(candidate_params))
                        candidates = cursor.fetchall()
                    finally:
                        if cand_span:
                            cand_span.__exit__(None, None, None)
            
                # Convert to document dictionaries
                documents = []
                for row in candidates:
                    doc = {
                        'id': row[0],
                        'file_name': row[1] or '',
                        'file_path': row[2] or '',
                        'file_type': row[3],
                        'file_size': row[4],
                        'file_date': row[5],
                        'file_status': str(row[6]),
                        'date_creation': row[7],
                        'source_name': row[8],
                        'side_name': row[9],
                        'source_id': row[10],
                        'side_id': row[11],
                        'content': '',
                    }
                    if use_sql_relevance and len(row) > 12:
                        doc['relevance_score'] = float(row[12] or 0.0)
                    documents.append(doc)
            
                # Apply advanced ranking if enabled
                if use_bm25 and query and documents:
                    try:
                        # Load content snippets for ranking (first 1000 chars)
                        for doc in documents:
                            try:
                                content = load_text_content(doc['id'])
                                # Rank on real content only - structural markers
                                # would skew BM25 toward label-heavy files.
                                content = strip_structural_markers(content) if content else ''
                                doc['content'] = content[:1000]
                            except:
                                doc['content'] = ''
                    
                        # Rank documents using BM25 and other algorithms
                        ranker = SearchService._get_ranker()
                        ranked_docs = ranker.rank_documents(
                            query=query,
                            documents=documents,
                            use_expansion=False,  # Already expanded in query
                            use_fuzzy=use_fuzzy
                        )
                    
                        # Extract documents and scores
                        documents = [doc for doc, score in ranked_docs]
                    
                        # Update relevance scores
                        for i, (doc, score) in enumerate(ranked_docs):
                            if i < len(documents):
                                documents[i]['relevance_score'] = float(score)
                    except Exception as e:
                        logger.warning(f"Advanced ranking failed, using basic ranking: {e}")
                        # Fallback to basic relevance
                        for doc in documents:
                            doc['relevance_score'] = 0.0
            
                # Apply sorting (skipped when SQL already ordered and paginated)
                if not use_sql_pagination:
                    if sort_by == 'relevance':
                        documents.sort(
                            key=lambda x: x.get('relevance_score', 0),
                            reverse=(sort_order.lower() == 'desc'))
                    elif sort_by == 'date':
                        documents.sort(
                            key=lambda x: x.get('file_date') or date.min,
                            reverse=(sort_order.lower() == 'desc'))
                    elif sort_by == 'name':
                        documents.sort(
                            key=lambda x: x.get('file_name', '').lower(),
                            reverse=(sort_order.lower() == 'desc'))
                    elif sort_by == 'type':
                        documents.sort(
                            key=lambda x: (x.get('file_type', ''), x.get('file_name', '').lower()),
                            reverse=(sort_order.lower() == 'desc'))
                    elif sort_by == 'size':
                        documents.sort(
                            key=lambda x: x.get('file_size', 0),
                            reverse=(sort_order.lower() == 'desc'))
            
                # Apply pagination (skipped when SQL already paginated)
                paginated_docs = (
                    documents if use_sql_pagination else documents[offset:offset + limit]
                )

                results = SearchService._format_advanced_results(
                    paginated_docs,
                    query=query,
                    case_sensitive=case_sensitive,
                    whole_word=whole_word,
                    include_snippets=include_snippets,
                )
                if (
                    use_sql_relevance
                    and paginated_docs
                    and len(paginated_docs) >= fetch_limit
                ):
                    last = paginated_docs[-1]
                    score = float(last.get("relevance_score") or 0.0)
                    page_sig = pagination_query_signature(
                        query=query or "",
                        file_type=file_type,
                        source_ids=source_ids,
                        side_ids=side_ids,
                        category_ids=category_ids,
                        analyst_category_ids=analyst_category_ids,
                        date_from=date_from,
                        date_to=date_to,
                        analyst_scope=analyst_scope,
                        file_statuses=file_statuses,
                        hide_duplicates=hide_duplicates,
                        sort_by=sort_by,
                        sort_order=sort_order,
                        case_sensitive=case_sensitive,
                        whole_word=whole_word,
                    )
                    SearchService._last_pagination_cursor = relevance_cursor(
                        score,
                        int(last["id"]),
                        sort_order,
                        query_signature=page_sig,
                        file_date=last.get("file_date"),
                        file_date_known=True,
                    )

                try:
                    cursor.execute("SET LOCAL statement_timeout = 0")
                except Exception:
                    pass
                cursor.close()
                # PHASE 9 FIX: this return was previously nested inside the for
                # loop, so an empty result set fell through and returned None,
                # crashing the search endpoint with a 500.
                return results, total_count
                
        except Exception as e:
            logger.error(f"Advanced search error: {e}", exc_info=True)
            SearchService._last_search_engine = {
                'line_matcher': 'python',
                'ripgrep': get_scanner_from_settings().status(),
            }
            return [], 0
    
    @staticmethod
    def autocomplete(
        query: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get autocomplete suggestions for a search query.
        
        Args:
            query: Partial query string
            limit: Maximum number of suggestions
        
        Returns:
            List of suggestion dictionaries
        """
        try:
            if not query or len(query.strip()) < 2:
                return []
            
            with get_connection() as conn:
                cursor = conn.cursor()
                
                query_lower = query.strip().lower()
                pattern = f'{_escape_like(query_lower)}%'
                
                # Get suggestions from file names
                suggestions_query = """
                    SELECT DISTINCT file_name, COUNT(*) as count
                    FROM paths
                    WHERE LOWER(file_name) LIKE %s
                    GROUP BY file_name
                    ORDER BY count DESC, file_name
                    LIMIT %s
                """
                
                cursor.execute(suggestions_query, (pattern, limit))
                file_suggestions = cursor.fetchall()
                
                # Get suggestions from words
                word_suggestions_query = """
                    SELECT DISTINCT w.word, COUNT(*) as count
                    FROM words w
                    JOIN words_hashs wp ON w.id = wp.word_id
                    WHERE LOWER(w.word) LIKE %s
                    GROUP BY w.word
                    ORDER BY count DESC, w.word
                    LIMIT %s
                """
                
                cursor.execute(word_suggestions_query, (pattern, limit))
                word_suggestions = cursor.fetchall()
                
                # Combine and deduplicate suggestions
                suggestions = []
                seen = set()
                
                # Add file name suggestions
                for name, count in file_suggestions:
                    if name and name.lower() not in seen:
                        suggestions.append({
                            'text': name,
                            'type': 'file_name',
                            'count': count
                        })
                        seen.add(name.lower())
                
                # Add word suggestions
                for word, count in word_suggestions:
                    if word and word.lower() not in seen and len(word) >= 3:
                        suggestions.append({
                            'text': word,
                            'type': 'word',
                            'count': count
                        })
                        seen.add(word.lower())
                
                # Apply fuzzy matching for better suggestions
                if len(suggestions) < limit:
                    fuzzy_matcher = SearchService._get_fuzzy_matcher()
                    all_file_names = [s['text'] for s in suggestions if s['type'] == 'file_name']
                    
                    # Get more candidates for fuzzy matching
                    cursor.execute("""
                        SELECT DISTINCT file_name
                        FROM paths
                        WHERE LENGTH(file_name) >= 3
                        LIMIT 1000
                    """)
                    candidates = [row[0] for row in cursor.fetchall() if row[0]]
                    
                    fuzzy_matches = fuzzy_matcher.get_close_matches(
                        query_lower,
                        candidates,
                        n=limit - len(suggestions),
                        cutoff=0.6
                    )
                    
                    for match in fuzzy_matches:
                        if match.lower() not in seen:
                            suggestions.append({
                                'text': match,
                                'type': 'fuzzy_match',
                                'count': 0
                            })
                            seen.add(match.lower())
                
                # Sort by relevance (exact matches first, then by count)
                suggestions.sort(key=lambda x: (
                    0 if x['text'].lower().startswith(query_lower) else 1,
                    -x['count'],
                    x['text'].lower()
                ))
                
                cursor.close()
                return suggestions[:limit]
            
        except Exception as e:
            logger.error(f"Autocomplete error: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_search_suggestions(
        query: str,
        limit: int = 5
    ) -> List[str]:
        """
        Get search query suggestions (simpler version for quick suggestions).
        
        Args:
            query: Partial query string
            limit: Maximum number of suggestions
        
        Returns:
            List of suggestion strings
        """
        suggestions = SearchService.autocomplete(query, limit=limit)
        return [s['text'] for s in suggestions]

