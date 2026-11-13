"""Database-column export for the list interfaces.

One endpoint, one contract: ``GET /api/export/<interface_id>`` re-runs the
interface's own list query — the same search, sort and filters the table on
screen shows — without pagination, and returns the **requested columns** as a
CSV or Excel download. The reader chooses the columns in the table toolbar;
the server decides what a column *is* from the allowlist below, so a column
name is never trusted from the request.

The three answers this module must not invent: it does not export a column
that is not in the interface's spec, it does not export more rows than
``MAX_EXPORT_ROWS``, and it does not guess a filter the list route would not
apply. Every spec here mirrors its list route's SQL.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, Response, jsonify, request

from Api.utils import (
    execute_query,
    get_categories_paged,
    get_keywords_with_usage,
    get_words_by_category_paged,
    get_words_with_usage,
)
from core.security.disclosure import note_disclosure
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)

exports_bp = Blueprint('exports', __name__)

#: A hard ceiling on one export. The list interfaces page for a reason; an
#: export that tried to materialise everything at once would be the thing
#: the pagination exists to prevent.
MAX_EXPORT_ROWS = 100_000


class ExportTooLarge(ValueError):
    """An export would exceed the declared synchronous row ceiling."""

    def __init__(self, observed: int, *, exact: bool):
        self.observed = int(observed)
        self.exact = bool(exact)
        super().__init__("Export exceeds the synchronous row limit")


class ExportIncomplete(RuntimeError):
    """A paged export could not retrieve the complete counted result set."""


def _raise_if_over_limit(total: int, *, exact: bool = True) -> None:
    if total > MAX_EXPORT_ROWS:
        raise ExportTooLarge(total, exact=exact)


def _paged_rows(loader, *, search, sort_by, sort_order) -> List[Dict[str, Any]]:
    """Read the paged category APIs completely, without their 200-row ceiling.

    Those APIs intentionally cap a browser page at 200. An export must either
    collect all rows (up to the export ceiling) or refuse; returning page one
    would silently produce a partial artifact.
    """
    page_size = 200  # the shared list APIs enforce this maximum
    first, total = loader(page=1, per_page=page_size, search=search,
                          sort_by=sort_by, sort_order=sort_order,
                          known_total=None)
    _raise_if_over_limit(total)
    rows = list(first or [])
    for page in range(2, (total + page_size - 1) // page_size + 1):
        batch, _page_total = loader(page=page, per_page=page_size,
                                    search=search, sort_by=sort_by,
                                    sort_order=sort_order,
                                    known_total=total)
        rows.extend(batch or [])
        if len(rows) > MAX_EXPORT_ROWS:
            raise ExportTooLarge(len(rows), exact=False)
    if len(rows) != total:
        raise ExportIncomplete(
            "The result set changed or could not be fully read; retry the export."
        )
    return rows


#: Each interface's exportable columns: ``(key, label)`` plus the SQL
#: expression a key means. ``None`` marks a column the exporter derives in
#: Python from the row (a status word, not a database value).
ColumnSpec = Tuple[str, str, Optional[str]]

FILE_COLUMNS: Tuple[ColumnSpec, ...] = (
    ('id', 'Id', 'p.id'),
    ('name', 'File Name', 'p.file_name'),
    ('type', 'Type', 'p.file_type'),
    ('size', 'Size', 'p.file_size'),
    ('source', 'Source', "COALESCE(s.name, 'Unknown')"),
    ('side', 'Side', "COALESCE(si.name, 'Unknown')"),
    ('status', 'Status', 'p.file_status'),
    ('date', 'Date', 'p.file_date'),
    ('path', 'Path', 'p.file_path'),
    ('created', 'Created', 'p.date_creation'),
)

FILE_TYPE_COLUMNS: Tuple[ColumnSpec, ...] = (
    ('file_type', 'File type', 'file_type'),
    ('count', 'Documents', 'file_count'),
    ('size', 'Total size', 'total_size'),
)

SOURCE_COLUMNS: Tuple[ColumnSpec, ...] = (
    ('id', 'Id', 's.id'),
    ('name', 'Name', 's.name'),
    ('job', 'Job/Type', 's.job'),
    ('importance', 'Importance', 's.importance'),
    ('country', 'Country', 's.country'),
    ('city', 'City', 's.city'),
    ('description', 'Description', 's.description'),
    ('accounts', 'Social Media Accounts', 's.accounts'),
    ('note', 'Notes', 's.note'),
    ('attachments', 'Attachments', 's.attachments'),
    ('ownership', 'Ownership', 's.ownership'),
    ('access_status', 'Access Status', 's.access_status'),
    ('created', 'Created', 's.date_creation'),
    ('discovered', 'Discovery Date', 's.entry_date'),
    ('category', 'Category', 'w.word'),
    ('documents', 'Documents', 'doc_count'),
)

SIDE_COLUMNS: Tuple[ColumnSpec, ...] = (
    ('id', 'Id', 'si.id'),
    ('name', 'Name', 'si.name'),
    ('importance', 'Importance', 'si.importance'),
    ('sources', 'Sources', 'source_count'),
    ('documents', 'Documents', 'doc_count'),
    ('created', 'Created', 'si.date_creation'),
)

KEYWORD_COLUMNS: Tuple[ColumnSpec, ...] = (
    ('id', 'Id', 'k.id'),
    ('text', 'Keyword', None),
    ('usage_count', 'Usage', 'usage_count'),
    ('status', 'Status', None),
    ('category', 'Category', 'category_name'),
)

WORD_COLUMNS: Tuple[ColumnSpec, ...] = (
    ('id', 'Id', None),
    ('word', 'Word', None),
    ('usage_count', 'Usage Count', 'usage_count'),
    ('status', 'Status', None),
)

#: Categories export on the table's own keys: the list header says Files and
#: Words, so the export column does too.
CATEGORY_COLUMNS: Tuple[ColumnSpec, ...] = (
    ('id', 'Id', 'id'),
    ('name', 'Category Name', 'name'),
    ('files', 'Files', 'file_count'),
    ('words', 'Words', 'word_count'),
)

CATEGORY_WORD_COLUMNS: Tuple[ColumnSpec, ...] = (
    ('id', 'Id', 'id'),
    ('word', 'Word', 'word'),
    ('usage_count', 'Usage Count', 'usage_count'),
)

GEOLOCATION_COLUMNS: Tuple[ColumnSpec, ...] = (
    ('coordinates', 'Coordinates', None),
    ('latitude', 'Latitude', None),
    ('longitude', 'Longitude', None),
    ('name', 'File Name', None),
    ('type', 'Type', None),
    ('path', 'Path', None),
    ('id', 'Id', None),
)


def _status_word(usage_count: Any) -> str:
    """The status word the table shows: used means active."""
    return 'Active' if (usage_count or 0) > 0 else 'Inactive'


def _format_geolocation_coordinates(lat: float, lon: float) -> str:
    lat_dir = 'N' if lat >= 0 else 'S'
    lon_dir = 'E' if lon >= 0 else 'W'
    return f'{abs(lat):.6f}°{lat_dir}, {abs(lon):.6f}°{lon_dir}'


def _is_valid_gps_coordinate(lat: Any, lon: Any) -> bool:
    try:
        lat_float = float(lat)
        lon_float = float(lon)
    except (TypeError, ValueError):
        return False
    return -90 <= lat_float <= 90 and -180 <= lon_float <= 180


def _geolocation_rows() -> List[Dict[str, Any]]:
    """Files with valid GPS coordinates — same filters/sort as the Geolocation section."""
    search = (request.args.get('search') or '').strip()
    sort_by = (request.args.get('sort_by') or 'id').strip()
    sort_dir = 'asc' if str(request.args.get('sort_dir', 'desc')).strip().lower() == 'asc' else 'desc'

    sort_column_map = {
        'id': 'p.id',
        'name': 'p.file_name',
        'file_count': 'p.id',
        'file_size': 'p.file_size',
        'file_date': 'p.file_date',
    }
    sort_column = sort_column_map.get(sort_by, 'p.id')

    where_conditions = [
        "p.coordinates IS NOT NULL",
        "TRIM(p.coordinates) != ''",
        "LENGTH(TRIM(p.coordinates)) > 0",
    ]
    params_list: List[Any] = []

    if search:
        where_conditions.append("p.file_name ILIKE %s")
        params_list.append(f'%{search}%')

    raw_ids = (request.args.get('ids') or '').strip()
    if raw_ids:
        id_values: List[int] = []
        for part in raw_ids.split(','):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError as exc:
                raise ValueError('ids must be comma-separated integers') from exc
            if value > 0:
                id_values.append(value)
        if id_values:
            id_values = id_values[:MAX_EXPORT_ROWS]
            placeholders = ','.join(['%s'] * len(id_values))
            where_conditions.append(f"p.id IN ({placeholders})")
            params_list.extend(id_values)

    where_clause = 'WHERE ' + ' AND '.join(where_conditions)
    query = f"""
        SELECT p.id, p.file_name, p.file_path, p.file_type, p.coordinates
        FROM paths p
        {where_clause}
        ORDER BY {sort_column} {sort_dir.upper()} NULLS LAST, p.id DESC
        LIMIT {MAX_EXPORT_ROWS + 1}
    """
    rows = execute_query(
        query,
        tuple(params_list) if params_list else None,
        fetch='all',
    ) or []

    records: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            file_id = row.get('id')
            file_name = row.get('file_name') or 'Unnamed File'
            file_path = row.get('file_path') or ''
            file_type = row.get('file_type') or 'Unknown'
            coordinates = row.get('coordinates') or ''
        else:
            file_id, file_name, file_path, file_type, coordinates = row
            file_name = file_name or 'Unnamed File'
            file_path = file_path or ''
            file_type = file_type or 'Unknown'
            coordinates = coordinates or ''

        lat = None
        lon = None
        if coordinates:
            try:
                parts = str(coordinates).split(',')
                if len(parts) == 2:
                    lat = float(parts[0].strip())
                    lon = float(parts[1].strip())
            except (ValueError, AttributeError):
                lat = lon = None

        if lat is None or lon is None or not _is_valid_gps_coordinate(lat, lon):
            continue

        records.append({
            'id': file_id,
            'coordinates': _format_geolocation_coordinates(lat, lon),
            'latitude': lat,
            'longitude': lon,
            'name': file_name,
            'type': file_type,
            'path': file_path,
        })
    return records


def _int_arg(name: str, default: int) -> int:
    try:
        value = int(request.args.get(name, '') or default)
    except (TypeError, ValueError):
        return default
    return value


#: A panel's identity, as a query parameter the export honours: the documents
#: of one keyword (its `keywords_hashs` rows) or of one category (documents
#: whose content carries one of the category's words). The panel routes put
#: the same clause on the view; the export must not be able to leak past it.
PANEL_SCOPE_PARAMS = {
    'keyword_id': (
        "EXISTS (SELECT 1 FROM keywords_hashs kh "
        "WHERE kh.hash_id = hc.hash_id AND kh.keyword_id = %s)"),
    'category_id': (
        "EXISTS (SELECT 1 FROM words_hashs wp "
        "JOIN words_categorys wc ON wp.word_id = wc.word_id "
        "WHERE wp.hash_id = hc.hash_id AND wc.category_id = %s)"),
}


def _file_rows() -> List[Dict[str, Any]]:
    """The File Library's own list query, without pagination."""
    from Api.blueprints.files import LIBRARY_JOINS, LIST_SORT_COLUMNS, ORDER_BY
    from Api.services.file_navigation import build_library_filters

    filters = build_library_filters(request.args)
    where_parts = list(filters.where_parts)
    where_params = list(filters.params)
    for name, clause in PANEL_SCOPE_PARAMS.items():
        raw = (request.args.get(name) or '').strip()
        if not raw:
            continue
        try:
            panel_id = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be a positive integer") from exc
        if panel_id < 1:
            raise ValueError(f"{name} must be a positive integer")
        where_parts.append(clause)
        where_params.append(panel_id)
    where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    sort_key = request.args.get('sort', '')
    sort_order = request.args.get('order', 'desc')
    if sort_order not in ('asc', 'desc'):
        sort_order = 'desc'
    if sort_key in LIST_SORT_COLUMNS:
        order_by = f"{LIST_SORT_COLUMNS[sort_key]} {sort_order.upper()} NULLS LAST, p.id DESC"
    else:
        order_by = ORDER_BY

    query = f"""
        SELECT p.id, p.file_name, p.file_type, p.file_size,
               COALESCE(s.name, 'Unknown') as source_name,
               COALESCE(si.name, 'Unknown') as side_name,
               p.file_status, p.file_date, p.file_path, p.date_creation
        FROM paths p
        {' '.join(LIBRARY_JOINS)}
        {where_clause}
        ORDER BY {order_by}
        LIMIT {MAX_EXPORT_ROWS + 1}
    """
    rows = execute_query(query, tuple(where_params) if where_params else None,
                         fetch='all') or []
    keys = ['id', 'name', 'type', 'size', 'source', 'side', 'status',
            'date', 'path', 'created']
    return [dict(zip(keys, row)) for row in rows]


def _file_type_rows() -> List[Dict[str, Any]]:
    """The File Types dashboard's own aggregation, without pagination."""
    query = """
        SELECT COALESCE(NULLIF(BTRIM(file_type), ''), 'Unknown') AS file_type,
               COUNT(*) AS file_count,
               COALESCE(SUM(file_size), 0) AS total_size
        FROM paths
        GROUP BY COALESCE(NULLIF(BTRIM(file_type), ''), 'Unknown')
        ORDER BY file_count DESC, file_type ASC
        LIMIT {limit}
    """.format(limit=MAX_EXPORT_ROWS + 1)
    rows = execute_query(query, fetch='all') or []
    return [
        {
            'file_type': str(row[0] or 'Unknown'),
            'file_count': int(row[1] or 0),
            'total_size': int(row[2] or 0),
        }
        for row in rows
    ]


def _search_result_rows() -> List[Dict[str, Any]]:
    """The Search Results page's own list, without pagination.

    The reader's query (``q``) is what this view IS; the analyst scope and
    the header sort ride along. The export cannot run without a query - an
    empty one exports nothing rather than the whole library.
    """
    from Api.utils import search_files_by_word

    query = (request.args.get('q') or '').strip()
    if len(query) < 2:
        return []
    scope = request.args.get('scope')
    sort = request.args.get('sort', '')
    order = request.args.get('order', '')
    rows, total = search_files_by_word(
        query, 1, MAX_EXPORT_ROWS + 1, analyst_scope=scope, sort=sort, order=order)
    _raise_if_over_limit(total)
    return [
        {
            'id': row['id'],
            'name': row['file_name'],
            'type': row.get('file_type'),
            'size': row.get('file_size') or 0,
            'source': row.get('source_name'),
            'side': row.get('side_name'),
            'status': row.get('file_status'),
            'date': row.get('date_creation') or row.get('file_date'),
            'path': row.get('file_path'),
            'created': row.get('date_creation'),
        }
        for row in rows
    ]


def _source_rows() -> List[Dict[str, Any]]:
    joins = (
        "LEFT JOIN hash_contexts hc ON s.id = hc.source_id "
        "LEFT JOIN paths p ON p.context_id = hc.id "
        "LEFT JOIN categorys c ON s.category_id = c.id "
        "LEFT JOIN words w ON c.word_id = w.id"
    )
    clauses: List[str] = []
    params_list: List[Any] = []

    # Explicit id selection (Export Selected / checkbox batch).
    raw_ids = (request.args.get('ids') or '').strip()
    if raw_ids:
        id_values: List[int] = []
        for part in raw_ids.split(','):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if value > 0:
                id_values.append(value)
        if id_values:
            # Cap selection size so a crafted request cannot dump unbounded ids.
            id_values = id_values[:MAX_EXPORT_ROWS]
            placeholders = ','.join(['%s'] * len(id_values))
            clauses.append(f"s.id IN ({placeholders})")
            params_list.extend(id_values)

    search = (request.args.get('search') or '').strip()
    if search and not raw_ids:
        clauses.append("s.name ILIKE %s")
        params_list.append(f"%{search}%")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params: Tuple = tuple(params_list)

    sort_by = request.args.get('sort', 'importance')
    sort_order = request.args.get('order', 'desc')
    if sort_order not in ('asc', 'desc'):
        sort_order = 'desc'
    columns = {'name': 's.name', 'importance': 's.importance',
               'date': 's.date_creation', 'discovered': 's.entry_date'}
    if sort_by not in columns:
        sort_by = 'importance'
    order_by = f"{columns[sort_by]} {sort_order.upper()} NULLS LAST, s.id DESC"

    rows = execute_query(
        f"""
        SELECT s.id, s.name, s.job, s.importance, s.country, s.city,
               s.description, s.accounts, s.note, s.attachments,
               s.date_creation, s.ownership, s.access_status, s.entry_date,
               w.word AS category_name, COUNT(DISTINCT p.id) AS doc_count
        FROM sources s
        {joins}
        {where}
        GROUP BY s.id, w.word
        ORDER BY {order_by}
        LIMIT {MAX_EXPORT_ROWS + 1}
        """,
        params,
        fetch='all',
    ) or []
    keys = ['id', 'name', 'job', 'importance', 'country', 'city',
            'description', 'accounts', 'note', 'attachments', 'created',
            'ownership', 'access_status', 'discovered', 'category',
            'documents']
    return [dict(zip(keys, row)) for row in rows]


def _side_rows() -> List[Dict[str, Any]]:
    joins = (
        "LEFT JOIN hash_contexts hc ON si.id = hc.side_id "
        "LEFT JOIN paths p ON p.context_id = hc.id"
    )
    clauses: List[str] = []
    params_list: List[Any] = []

    raw_ids = (request.args.get('ids') or '').strip()
    if raw_ids:
        id_values: List[int] = []
        for part in raw_ids.split(','):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if value > 0:
                id_values.append(value)
        if id_values:
            id_values = id_values[:MAX_EXPORT_ROWS]
            placeholders = ','.join(['%s'] * len(id_values))
            clauses.append(f"si.id IN ({placeholders})")
            params_list.extend(id_values)

    search = (request.args.get('search') or '').strip()
    if search and not raw_ids:
        clauses.append("si.name ILIKE %s")
        params_list.append(f"%{search}%")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params: Tuple = tuple(params_list)

    sort_by = request.args.get('sort', 'importance')
    sort_order = request.args.get('order', 'desc')
    if sort_order not in ('asc', 'desc'):
        sort_order = 'desc'
    columns = {'name': 'si.name', 'importance': 'si.importance',
               'date': 'si.date_creation'}
    if sort_by not in columns:
        sort_by = 'importance'
    order_by = f"{columns[sort_by]} {sort_order.upper()} NULLS LAST, si.id DESC"

    rows = execute_query(
        f"""
        SELECT si.id, si.name, si.importance, si.date_creation,
               COUNT(DISTINCT p.id) AS doc_count,
               COUNT(DISTINCT hc.source_id) AS source_count
        FROM sides si
        {joins}
        {where}
        GROUP BY si.id
        ORDER BY {order_by}
        LIMIT {MAX_EXPORT_ROWS + 1}
        """,
        params,
        fetch='all',
    ) or []
    keys = ['id', 'name', 'importance', 'created', 'documents', 'sources']
    return [dict(zip(keys, row)) for row in rows]


def _keyword_rows() -> List[Dict[str, Any]]:
    search = (request.args.get('q') or request.args.get('search') or '').strip()
    sort_by = request.args.get('sort', 'usage_count')
    sort_order = request.args.get('order', 'desc')
    if sort_by not in ('usage_count', 'text', 'id', 'status'):
        sort_by = 'usage_count'
    if sort_order not in ('asc', 'desc'):
        sort_order = 'desc'

    keyword_rows, total = get_keywords_with_usage(
        search_term=search if search else None,
        page=1,
        per_page=MAX_EXPORT_ROWS + 1,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    _raise_if_over_limit(total)
    from Api.utils.utils import batch_load_keywords_from_rows
    text_map = batch_load_keywords_from_rows(keyword_rows) if keyword_rows else {}

    category_names: Dict[int, str] = {}
    category_ids = {row[1] for row in keyword_rows or [] if row[1]}
    if category_ids:
        placeholders = ','.join(['%s'] * len(category_ids))
        cat_rows = execute_query(
            f"""
            SELECT c.id, w.word
            FROM categorys c
            LEFT JOIN words w ON c.word_id = w.id
            WHERE c.id IN ({placeholders})
            """,
            tuple(category_ids),
            fetch='all',
        ) or []
        category_names = {row[0]: row[1] for row in cat_rows}

    records: List[Dict[str, Any]] = []
    for row in keyword_rows or []:
        keyword_id, category_id, _blob, usage_count = row[0], row[1], row[2], row[3]
        text = text_map.get(keyword_id) or ''
        records.append({
            'id': keyword_id,
            'text': text,
            'usage_count': usage_count or 0,
            'status': _status_word(usage_count),
            'category': category_names.get(category_id),
        })
    return records


def _word_rows() -> List[Dict[str, Any]]:
    search = (request.args.get('q') or request.args.get('search') or '').strip()
    sort_by = request.args.get('sort', 'usage_count')
    sort_order = request.args.get('order', 'desc')
    if sort_by not in ('usage_count', 'word', 'id', 'status'):
        sort_by = 'usage_count'
    if sort_order not in ('asc', 'desc'):
        sort_order = 'desc'

    words, total = get_words_with_usage(
        search_term=search if search else None,
        page=1,
        per_page=MAX_EXPORT_ROWS + 1,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    _raise_if_over_limit(total)
    for record in words:
        record['status'] = _status_word(record.get('usage_count'))
    return words


def _category_rows() -> List[Dict[str, Any]]:
    search = (request.args.get('search') or '').strip()
    sort_by = request.args.get('sort', 'files')
    sort_order = request.args.get('order', 'desc')
    if sort_by not in ('name', 'files', 'words', 'id'):
        sort_by = 'files'
    if sort_order not in ('asc', 'desc'):
        sort_order = 'desc'
    categories = _paged_rows(
        lambda **kwargs: get_categories_paged(**kwargs),
        search=search if search else None,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    # The loader speaks its own names (file_count/word_count); the export
    # speaks the table's (files/words).
    return [{'id': c['id'], 'name': c['name'],
             'files': c['file_count'], 'words': c['word_count']}
            for c in categories]


def _category_word_rows() -> List[Dict[str, Any]]:
    category_id = _int_arg('category_id', 0)
    if category_id < 1:
        raise ValueError('category_id is required')
    search = (request.args.get('search') or '').strip()
    sort_by = request.args.get('sort', 'word')
    sort_order = request.args.get('order', 'asc')
    if sort_by not in ('word', 'usage_count', 'id'):
        sort_by = 'word'
    if sort_order not in ('asc', 'desc'):
        sort_order = 'asc'
    return _paged_rows(
        lambda **kwargs: get_words_by_category_paged(category_id, **kwargs),
        search=search if search else None,
        sort_by=sort_by,
        sort_order=sort_order,
    )


#: interface_id -> title, column spec, row loader. The loader reads its
#: filters from the request, exactly as the interface's list route does, so
#: "export" always means "this view, every row of it".
EXPORT_SPECS: Dict[str, Dict[str, Any]] = {
    'file_library': {
        'title': 'file_library',
        'columns': FILE_COLUMNS,
        'rows': _file_rows,
    },
    'file_types': {
        'title': 'file_types',
        'columns': FILE_TYPE_COLUMNS,
        'rows': _file_type_rows,
    },
    'sources': {
        'title': 'sources',
        'columns': SOURCE_COLUMNS,
        'rows': _source_rows,
    },
    'sides': {
        'title': 'sides',
        'columns': SIDE_COLUMNS,
        'rows': _side_rows,
    },
    'keywords': {
        'title': 'keywords',
        'columns': KEYWORD_COLUMNS,
        'rows': _keyword_rows,
    },
    'words': {
        'title': 'words',
        'columns': WORD_COLUMNS,
        'rows': _word_rows,
    },
    'categories': {
        'title': 'categories',
        'columns': CATEGORY_COLUMNS,
        'rows': _category_rows,
    },
    'category_words': {
        'title': 'category_words',
        'columns': CATEGORY_WORD_COLUMNS,
        'rows': _category_word_rows,
    },
    'search_results': {
        'title': 'search_results',
        'columns': FILE_COLUMNS,
        'rows': _search_result_rows,
    },
    'geolocation': {
        'title': 'geolocation',
        'columns': GEOLOCATION_COLUMNS,
        'rows': _geolocation_rows,
    },
}


def _resolve_columns(interface_id: str) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    """The requested columns, or an error when a name is not in the spec."""
    spec = EXPORT_SPECS[interface_id]
    requested = (request.args.get('columns') or '').strip()
    if 'columns' not in request.args:
        return [(key, label) for key, label, _expr in spec['columns']], None
    if not requested:
        return [], "choose at least one export column"
    known = {key: label for key, label, _expr in spec['columns']}
    chosen: List[Tuple[str, str]] = []
    for key in [k.strip() for k in requested.split(',') if k.strip()]:
        if key not in known:
            return [], f"unknown column '{key}' for interface '{interface_id}'"
        if key not in [c[0] for c in chosen]:
            chosen.append((key, known[key]))
    if not chosen:
        return [], "choose at least one export column"
    return chosen, None


def _cell(value: Any) -> Any:
    if value is None:
        return ''
    if isinstance(value, bool):
        return int(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, str):
        from Api.services.document_intelligence import spreadsheet_safe_text
        return spreadsheet_safe_text(value)
    return value


def _export_filename(interface_id: str, export_format: str) -> str:
    """Return a safe, extension-correct download name for the audit record."""
    extension = 'xlsx' if export_format in ('xlsx', 'excel') else 'csv'
    requested = request.args.get('filename')
    if not requested:
        stamp = datetime.now().strftime('%Y%m%d_%H%M')
        return f"{interface_id}_export_{stamp}.{extension}"

    from werkzeug.utils import secure_filename
    safe = secure_filename(requested.strip())
    if not safe:
        raise ValueError('filename must contain at least one letter or number')
    stem = safe.rsplit('.', 1)[0] if '.' in safe else safe
    stem = stem[:160].rstrip(' ._-')
    if not stem:
        raise ValueError('filename must contain at least one letter or number')
    return f'{stem}.{extension}'


def _csv_response(filename: str, columns: List[Tuple[str, str]], records: List[Dict[str, Any]]) -> Response:
    buffer = io.StringIO()
    buffer.write('\ufeff')  # Excel opens UTF-8 CSVs correctly with a BOM
    writer = csv.writer(buffer)
    writer.writerow([label for _key, label in columns])
    for record in records:
        writer.writerow([_cell(record.get(key)) for key, _label in columns])
    return Response(
        buffer.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


def _xlsx_response(filename: str, columns: List[Tuple[str, str]], records: List[Dict[str, Any]]) -> Response:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = filename.rsplit('.', 1)[0][:31] or 'Export'
    sheet.append([label for _key, label in columns])
    for record in records:
        sheet.append([_cell(record.get(key)) for key, _label in columns])
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


def list_export_for_interface(interface_id: str):
    """Shared export handler for ``GET /api/export/<id>`` and domain aliases."""
    spec = EXPORT_SPECS.get(interface_id)
    if spec is None:
        return jsonify({
            'success': False,
            'error': f"unknown interface '{interface_id}'",
            'known': sorted(EXPORT_SPECS),
        }), 404

    columns, error = _resolve_columns(interface_id)
    if error:
        return jsonify({'success': False, 'error': error}), 400

    export_format = (request.args.get('format') or 'csv').lower()
    if export_format not in ('csv', 'xlsx', 'excel'):
        return jsonify({'success': False, 'error': 'format must be csv or xlsx'}), 400

    try:
        filename = _export_filename(interface_id, export_format)
        records = spec['rows']()
        if len(records) > MAX_EXPORT_ROWS:
            raise ExportTooLarge(len(records), exact=False)
    except ExportIncomplete as e:
        return jsonify({
            'success': False,
            'code': 'export_incomplete',
            'error': str(e),
        }), 409
    except ExportTooLarge as e:
        detail = {'max_rows': MAX_EXPORT_ROWS}
        detail['row_count' if e.exact else 'observed_at_least'] = e.observed
        return jsonify({
            'success': False,
            'code': 'export_limit_exceeded',
            'error': f"Export exceeds the {MAX_EXPORT_ROWS:,}-row limit. Narrow the view and try again.",
            **detail,
        }), 413
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:  # pragma: no cover - defensive, logged
        logger.error(f"Export failed for {interface_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Export failed'}), 500

    note_disclosure(kind='list_export', scope=f"interface:{interface_id}",
                    unit='row', row_count=len(records),
                    format='xlsx' if export_format == 'xlsx' or export_format == 'excel' else 'csv',
                    columns=[key for key, _label in columns])

    if export_format in ('xlsx', 'excel'):
        return _xlsx_response(filename, columns, records)
    return _csv_response(filename, columns, records)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@exports_bp.route('/api/export/<interface_id>', methods=['GET'])
def export_interface(interface_id: str):
    """One view, every row, the reader's chosen columns, CSV or Excel."""
    return list_export_for_interface(interface_id)


#: The list route a table lives on -> the export spec that serves it. The
#: templates ask for their export through this map (never as a literal), so
#: the product id is written in exactly one place.
EXPORT_INTERFACE_BY_ENDPOINT = {
    'files.files_list': 'file_library',
    'files.file_type_documents': 'file_library',
    'files.file_types_page': 'file_types',
    'keyword_documents': 'file_library',
    'category_documents': 'file_library',
    'search_page': 'search_results',
    'sources_list': 'sources',
    'sides_list': 'sides',
    'keywords_list': 'keywords',
    'words_list': 'words',
    'categories_list': 'categories',
    'category_words': 'category_words',
}


def export_interface_for(endpoint):
    return EXPORT_INTERFACE_BY_ENDPOINT.get(endpoint or '')


@limiter.limit(INTERACTIVE_READ_LIMIT)
@exports_bp.route('/api/export/structured/plan', methods=['POST'])
def structured_export_plan():
    """Return a folder layout plan for client-side structured export."""
    try:
        payload = request.get_json(silent=True) or {}
        scope = payload.get('scope') or 'selection'
        layout = payload.get('layout') or payload.get('layout_preset') or 'analyst_category'
        file_ids = payload.get('file_ids') or payload.get('fileIds') or []
        filters = payload.get('filters') or {}
        unassigned = (payload.get('unassigned_label') or payload.get('unassignedLabel') or '').strip()
        include_smart = bool(payload.get('include_smart_category') or payload.get('includeSmartCategory'))

        from Api.services.structured_folder_export import build_structured_export_plan

        plan = build_structured_export_plan(
            scope=scope,
            layout=layout,
            file_ids=file_ids,
            filters=filters,
            unassigned_label=unassigned or '_Unassigned',
            include_smart_category=include_smart,
        )
        note_disclosure(kind='structured_export_plan', row_count=plan.get('total') or 0)
        return jsonify(plan)
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        logger.error('Structured export plan failed: %s', exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Could not build export plan'}), 500


def register_exports_routes(app):
    """Register the export blueprint with the Flask app"""
    app.jinja_env.globals['export_interface_for'] = export_interface_for
    app.register_blueprint(exports_bp)
