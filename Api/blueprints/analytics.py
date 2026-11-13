from flask import Blueprint, request, jsonify
import logging

from Api.utils import execute_query
from core.errors import client_error, client_safe_message

logger = logging.getLogger(__name__)

analytics_bp = Blueprint("analytics", __name__)

db_execute_query = execute_query


def build_filter_clause(source_id=None, side_id=None):
    """Build WHERE clause and parameters for source and side filters"""
    conditions = []
    params = []
    
    if source_id:
        conditions.append("hc.source_id = %s")
        params.append(int(source_id))
    
    if side_id:
        conditions.append("hc.side_id = %s")
        params.append(int(side_id))
    
    if conditions:
        return " AND " + " AND ".join(conditions), params
    return "", []

def is_archive_file(path_name):
    """Check if a path is likely an archive file based on extension"""
    archive_extensions = {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', 
                          '.pst', '.ost', '.jar', '.war', '.ear', '.cab', 
                          '.iso', '.dmg', '.pkg', '.deb', '.rpm', '.apk'}
    path_lower = path_name.lower()
    return any(path_lower.endswith(ext) for ext in archive_extensions)

def build_path_where_clause(path_name):
    """
    Build WHERE clause and parameters for path filtering in SQL queries.
    
    Handles both regular paths and archive paths (with '::' separator).
    Normalizes path separators and creates appropriate LIKE patterns for matching.
    
    Args:
        path_name: Path string to build WHERE clause for (can include '::' for archives)
        
    Returns:
        Tuple of (where_clause, params) where:
        - where_clause: SQL WHERE clause string with placeholders
        - params: List of parameters for the WHERE clause
    """
    path_name = path_name.replace('\\', '/')
    path_name = path_name.rstrip('/')
    
    if not path_name:
        return None, []
    
    if '::' in path_name:
        base_path, inner_path = path_name.split('::', 1)
        normalized_base = base_path.replace('\\', '/')
        normalized_inner = inner_path.replace('\\', '/')
        full_path = f"{normalized_base}::{normalized_inner}"
        
        where_clause = """
            (REPLACE(REPLACE(p.file_path, '\\', '/'), '::', '::') LIKE %s 
             OR REPLACE(p.file_path, '\\', '/') LIKE %s)
        """
        params = [f"{full_path}/%", f"{full_path}::%"]
    else:

        normalized_path = path_name.replace('\\', '/')
        
        is_archive = is_archive_file(normalized_path)

        if len(normalized_path) == 2 and normalized_path[1] == ':':

            where_clause = "REPLACE(p.file_path, '\\', '/') LIKE %s"
            params = [f"{normalized_path}/%"]
        else:
            if is_archive:

                where_clause = """
                    (REPLACE(p.file_path, '\\', '/') LIKE %s 
                     OR REPLACE(p.file_path, '\\', '/') = %s)
                """
                archive_pattern = f"{normalized_path}::%"
                exact_match = normalized_path
                params = [archive_pattern, exact_match]
                print(f"DEBUG: Archive file detected: {normalized_path}, pattern: {archive_pattern}")
            else:
                where_clause = """
                    (REPLACE(p.file_path, '\\', '/') LIKE %s 
                     OR REPLACE(p.file_path, '\\', '/') = %s)
                """
                pattern_with_slash = f"{normalized_path}/%"
                exact_match = normalized_path
                params = [pattern_with_slash, exact_match]
    
    return where_clause, params


@analytics_bp.route('/api/analytics/paths/hierarchical', methods=['GET'])
@analytics_bp.route('/api/analytics/paths/structure', methods=['GET'])
def api_paths_hierarchical():
    """
    API endpoint to get hierarchical path structure.
    
    Returns a tree structure of all file paths, preserving archive structure
    (paths with '::' separator). Supports filtering by source, side, date range, and file type.
    
    Query Parameters:
        source_id (int, optional): Filter by source ID
        side_id (int, optional): Filter by side ID
        date_from (str, optional): Filter files created after this date
        date_to (str, optional): Filter files created before this date
        file_type (str, optional): Filter by file type
        
    Returns:
        JSON response with:
        - structure: Tree structure of paths
        - stats: Statistics about files and folders
        - filters: Applied filter values
    """
    try:
        source_filter = request.args.get('source_id', type=int)
        side_filter = request.args.get('side_id', type=int)
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        file_type_filter = request.args.get('file_type', '')

        query = """
            SELECT 
                p.id,
                p.file_path,
                p.file_name,
                p.file_type,
                p.file_size,
                p.file_status,
                p.date_creation
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
            WHERE p.file_path IS NOT NULL AND p.file_path != ''
        """

        params = []
        if source_filter:
            query += " AND hc.source_id = %s"; params.append(source_filter)
        if side_filter:
            query += " AND hc.side_id = %s"; params.append(side_filter)
        if date_from:
            query += " AND p.file_date >= %s"; params.append(date_from)
        if date_to:
            query += " AND p.file_date <= %s"; params.append(date_to)
        if file_type_filter:
            type_values = [v.strip() for v in str(file_type_filter).split(',') if v.strip()]
            if len(type_values) == 1:
                query += " AND p.file_type = %s"
                params.append(type_values[0])
            elif type_values:
                placeholders = ','.join(['%s'] * len(type_values))
                query += f" AND p.file_type IN ({placeholders})"
                params.extend(type_values)
        query += " ORDER BY p.file_path"

        files_data = db_execute_query(query, tuple(params) if params else None)
        if not files_data:
            return jsonify({'structure': [], 'stats': {}})

        def parse_path_hierarchy(file_path: str):
            """Parse file path into hierarchical parts, preserving archive structure"""
            parts = []
            archive_info = {'is_archive': False, 'archive_path': None, 'inner_path': None}
            
            if '::' in file_path:
                # Archive path: "base/path/archive.zip::inner/folder/file.txt"
                segments = file_path.split('::')
                base_segment = segments[0].replace('\\', '/')
                base_parts = [p for p in base_segment.split('/') if p]
                
                # Store archive info
                archive_info['is_archive'] = True
                archive_info['archive_path'] = base_segment
                
                # Add base path parts
                parts.extend(base_parts)
                
                # Mark the last part (archive file) as archive
                if parts:
                    parts[-1] = f"{parts[-1]} [ARCHIVE]"
                
                # Process inner path (folders/files inside archive)
                if len(segments) > 1:
                    inner_segment = segments[1].replace('\\', '/')
                    inner_parts = [p for p in inner_segment.split('/') if p]
                    archive_info['inner_path'] = inner_segment
                    parts.extend(inner_parts)
            else:
                # Regular file path
                file_path = file_path.replace('\\', '/')
                parts = [p for p in file_path.split('/') if p]
            
            return parts, archive_info

        def build_full_path(parts, archive_info, index):
            """Build full path for a node, preserving archive structure"""
            if archive_info['is_archive'] and index < len(parts):
                # Find where archive starts (marked with [ARCHIVE])
                archive_index = -1
                for i, part in enumerate(parts):
                    if '[ARCHIVE]' in part:
                        archive_index = i
                        break
                
                if archive_index >= 0 and index > archive_index:
                    # Inside archive - use :: separator
                    base_path = '/'.join(parts[:archive_index + 1]).replace(' [ARCHIVE]', '')
                    inner_path = '/'.join(parts[archive_index + 1:index + 1])
                    return f"{base_path}::{inner_path}"
                elif index == archive_index:
                    # The archive file itself
                    return '/'.join(parts[:index + 1]).replace(' [ARCHIVE]', '')
            
            # Regular path or before archive
            return '/'.join(parts[:index + 1])

        root = {}
        file_count = 0
        total_size = 0
        for row in files_data:
            file_id, file_path, file_name, file_type, file_size, file_status, date_creation = (
                row[0], row[1], row[2], row[3], row[4] or 0, row[5], row[6]
            )
            file_count += 1
            total_size += file_size

            parts, archive_info = parse_path_hierarchy(file_path)
            current = root
            for i, part in enumerate(parts[:-1]):
                if part not in current:
                    is_archive = '[ARCHIVE]' in part
                    clean_name = part.replace(' [ARCHIVE]', '')
                    full_path = build_full_path(parts, archive_info, i)
                    
                    current[part] = {
                        'name': clean_name,
                        'full_path': full_path,
                        'type': 'archive' if is_archive else 'folder',
                        'children': {},
                        'files': [],
                        'file_count': 0,
                        'total_size': 0,
                        'processed_count': 0
                    }
                current[part]['file_count'] += 1
                current[part]['total_size'] += file_size
                if file_status == 'Read':
                    current[part]['processed_count'] += 1
                current = current[part]['children']

            if parts:
                parent_index = len(parts) - 2
                parent_key = parts[parent_index] if parent_index >= 0 else 'root'
                if parent_key not in current:
                    full_path = build_full_path(parts, archive_info, parent_index)
                    current[parent_key] = {
                        'name': parent_key.replace(' [ARCHIVE]', ''),
                        'full_path': full_path,
                        'type': 'archive' if '[ARCHIVE]' in parent_key else 'folder',
                        'children': {},
                        'files': [],
                        'file_count': 0,
                        'total_size': 0,
                        'processed_count': 0
                    }
                current[parent_key]['files'].append({
                    'id': file_id,
                    'name': file_name,
                    'type': file_type,
                    'size': file_size,
                    'status': file_status,
                    'created': date_creation.isoformat() if date_creation else None
                })

        def tree_to_list(node_dict):
            """
            Convert hierarchical tree dictionary to flat list.
            
            Args:
                node_dict: Dictionary representing hierarchical file structure
                
            Returns:
                List of file/directory items with metadata
            """
            result = []
            for key, node in sorted(node_dict.items()):
                # 🚀 FIXED: Include isArchive property for frontend compatibility
                item = {
                    'name': node['name'],
                    'full_path': node['full_path'],
                    'fullPath': node['full_path'],  # Also include camelCase for compatibility
                    'type': node['type'],
                    'isArchive': node['type'] == 'archive',  # Add isArchive property
                    'file_count': node['file_count'],
                    'fileCount': node['file_count'],  # Also include camelCase
                    'total_size': node['total_size'],
                    'totalSize': node['total_size'],  # Also include camelCase
                    'processed_count': node['processed_count'],
                    'processedCount': node['processed_count'],  # Also include camelCase
                    'files': node.get('files', []),
                    'children': tree_to_list(node['children']) if node['children'] else []
                }
                result.append(item)
            return result

        structure = tree_to_list(root)
        stats = {
            'total_files': file_count,
            'total_size': total_size,
            'total_folders': len(root),
            'has_archives': any('::' in row[1] for row in files_data)
        }

        return jsonify({
            'structure': structure,
            'stats': stats,
            'filters': {
                'source_id': source_filter,
                'side_id': side_filter,
                'date_from': date_from,
                'date_to': date_to,
                'file_type': file_type_filter
            }
        })
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)


@analytics_bp.route('/api/analytics/storage-stats', methods=['GET'])
def api_storage_stats():
    """
    API endpoint to get storage statistics.
    
    Returns statistics about file storage including:
    - File type distribution with counts and sizes
    - Timeline statistics
    - Largest files
    - Archive statistics
    
    Returns:
        JSON response with storage statistics
    """
    try:
        type_stats = db_execute_query("""
            SELECT COALESCE(file_type, 'Unknown') as type,
                   COUNT(*) as file_count,
                   SUM(file_size) as total_size,
                   AVG(file_size) as avg_size,
                   MIN(file_size) as min_size,
                   MAX(file_size) as max_size
            FROM paths
            GROUP BY file_type
            ORDER BY total_size DESC
            LIMIT 50
        """)

        status_stats = db_execute_query("""
            SELECT file_status, COUNT(*) as file_count, SUM(file_size) as total_size
            FROM paths
            GROUP BY file_status
        """)

        timeline_stats = db_execute_query("""
            SELECT TO_CHAR(date_creation, 'YYYY-MM') as month,
                   COUNT(*) as file_count,
                   SUM(file_size) as total_size
            FROM paths
            WHERE date_creation >= CURRENT_DATE - INTERVAL '12 months'
            GROUP BY TO_CHAR(date_creation, 'YYYY-MM')
            ORDER BY month ASC
        """)

        largest_files = db_execute_query("""
            SELECT p.id, p.file_name, p.file_type, p.file_size, p.file_path,
                   COALESCE(s.name, 'Unknown') as source_name
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
            LEFT JOIN sources s ON hc.source_id = s.id
            ORDER BY p.file_size DESC
            LIMIT 20
        """)

        archive_stats = db_execute_query("""
            SELECT COUNT(*) as archive_file_count,
                   SUM(file_size) as archive_total_size,
                   AVG(file_size) as archive_avg_size
            FROM paths
            WHERE file_path LIKE '%::%'
        """, fetch="one")

        total_stats = db_execute_query("""
            SELECT COUNT(*) as total_files,
                   SUM(file_size) as total_size,
                   AVG(file_size) as avg_size,
                   MIN(file_size) as min_size,
                   MAX(file_size) as max_size
            FROM paths
        """, fetch="one")

        content_stats = db_execute_query("""
            SELECT COUNT(*) as content_chunks,
                   SUM(LENGTH(content_data)) as content_size
            FROM contents
        """, fetch="one")

        return jsonify({
            'total': {
                'files': total_stats[0] if total_stats and isinstance(total_stats, tuple) and len(total_stats) > 0 else 0,
                'size': total_stats[1] if total_stats and isinstance(total_stats, tuple) and len(total_stats) > 1 else 0,
                'avg_size': float(total_stats[2]) if total_stats and isinstance(total_stats, tuple) and len(total_stats) > 2 and total_stats[2] else 0,
                'min_size': total_stats[3] if total_stats and isinstance(total_stats, tuple) and len(total_stats) > 3 else 0,
                'max_size': total_stats[4] if total_stats and isinstance(total_stats, tuple) and len(total_stats) > 4 else 0
            },
            'content_storage': {
                'chunks': content_stats[0] if content_stats and isinstance(content_stats, tuple) and len(content_stats) > 0 else 0,
                'size': content_stats[1] if content_stats and isinstance(content_stats, tuple) and len(content_stats) > 1 else 0
            },
            'by_type': [
                {
                    'type': row[0],
                    'count': row[1],
                    'total_size': row[2] or 0,
                    'avg_size': float(row[3]) if row[3] else 0,
                    'min_size': row[4] or 0,
                    'max_size': row[5] or 0
                } for row in (type_stats or [])
            ],
            'by_status': [
                {
                    'status': row[0],
                    'count': row[1],
                    'total_size': row[2] or 0
                } for row in (status_stats or [])
            ],
            'timeline': [
                {
                    'month': row[0],
                    'count': row[1],
                    'size': row[2] or 0
                } for row in (timeline_stats or [])
            ],
            'largest_files': [
                {
                    'id': row[0], 'name': row[1], 'type': row[2], 'size': row[3] or 0,
                    'path': row[4], 'source': row[5]
                } for row in (largest_files or [])
            ],
            'archives': {
                'count': archive_stats[0] if archive_stats and isinstance(archive_stats, tuple) and len(archive_stats) > 0 else 0,
                'total_size': archive_stats[1] if archive_stats and isinstance(archive_stats, tuple) and len(archive_stats) > 1 else 0,
                'avg_size': float(archive_stats[2]) if archive_stats and isinstance(archive_stats, tuple) and len(archive_stats) > 2 and archive_stats[2] else 0
            }
        })
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)


@analytics_bp.route('/api/analytics/processing-statistics', methods=['GET'])
def api_processing_statistics():
    """
    API endpoint to get processing statistics.
    
    Returns statistics about file processing including:
    - Success rates by file type
    - Processing status breakdown
    - Size categories
    - Error statistics
    - Recent activity
    
    Returns:
        JSON response with processing statistics
    """
    try:
        type_success = db_execute_query("""
            SELECT file_type,
                   COUNT(*) as total,
                   COUNT(*) FILTER (WHERE file_status = 'Read') as processed,
                   ROUND(COUNT(*) FILTER (WHERE file_status = 'Read')::numeric / NULLIF(COUNT(*), 0) * 100, 2) as success_rate
            FROM paths
            GROUP BY file_type
            ORDER BY total DESC
            LIMIT 50
        """)

        processing_speed = db_execute_query("""
            SELECT DATE(date_creation) as date,
                   COUNT(*) as files_processed,
                   COUNT(*) FILTER (WHERE file_status = 'Read') as files_succeeded
            FROM paths
            WHERE date_creation >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY DATE(date_creation)
            ORDER BY date ASC
        """)

        size_categories = db_execute_query("""
            SELECT 
                CASE 
                    WHEN file_size < 1024 * 1024 THEN 'Small (<1MB)'
                    WHEN file_size < 10 * 1024 * 1024 THEN 'Medium (1-10MB)'
                    WHEN file_size < 100 * 1024 * 1024 THEN 'Large (10-100MB)'
                    ELSE 'Very Large (>100MB)'
                END as size_category,
                COUNT(*) as count,
                AVG(file_size) as avg_size,
                COUNT(*) FILTER (WHERE file_status = 'Read') as processed
            FROM paths
            GROUP BY size_category
            ORDER BY avg_size ASC
        """)

        error_stats = db_execute_query("""
            SELECT file_type, COUNT(*) as error_count
            FROM paths
            WHERE file_status != 'Read'
            GROUP BY file_type
            ORDER BY error_count DESC
            LIMIT 10
        """)

        recent_activity = db_execute_query("""
            SELECT DATE(date_creation) as date,
                   COUNT(*) as total_files,
                   SUM(file_size) as total_size,
                   COUNT(*) FILTER (WHERE file_status = 'Read') as processed
            FROM paths
            WHERE date_creation >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY DATE(date_creation)
            ORDER BY date DESC
        """)

        return jsonify({
            'by_type': [
                {
                    'type': row[0], 'total': row[1], 'processed': row[2],
                    'success_rate': float(row[3]) if row[3] else 0
                } for row in (type_success or [])
            ],
            'daily_speed': [
                {
                    'date': row[0].isoformat() if row[0] else None,
                    'files': row[1], 'succeeded': row[2]
                } for row in (processing_speed or [])
            ],
            'by_size': [
                {
                    'category': row[0], 'count': row[1],
                    'avg_size': float(row[2]) if row[2] else 0,
                    'processed': row[3]
                } for row in (size_categories or [])
            ],
            'errors': [
                {'type': row[0], 'count': row[1]} for row in (error_stats or [])
            ],
            'recent_activity': [
                {
                    'date': row[0].isoformat() if row[0] else None,
                    'files': row[1], 'size': row[2] or 0, 'processed': row[3]
                } for row in (recent_activity or [])
            ]
        })
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)




@analytics_bp.route('/api/analytics/sources', methods=['GET'])
def api_analytics_sources():
    """
    API endpoint to get sources for analytics.
    
    Returns a list of all available sources with their IDs, names, and countries.
    
    Returns:
        JSON response with list of sources
    """
    try:
        sources_data = db_execute_query("""
            SELECT id, name, country
            FROM sources
            ORDER BY name ASC
        """)
        
        sources = [
            {'id': row[0], 'name': row[1], 'country': row[2] if len(row) > 2 else None}
            for row in (sources_data or [])
        ]
        
        # 🚀 FIXED: Return consistent format with sides endpoint
        return jsonify({'sources': sources})
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)


@analytics_bp.route('/api/analytics/sides', methods=['GET'])
def api_analytics_sides():
    """API: Get sides for analytics"""
    try:
        from Api.utils import select_info_sides
        sides_dict = select_info_sides()
        sides = [{'id': k, 'name': v} for k, v in sides_dict.items()]
        return jsonify({'sides': sides})
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)


@analytics_bp.route('/api/analytics/word-frequency', methods=['GET'])
def api_word_frequency():
    """
    API endpoint to get word frequency statistics.
    
    Returns the most frequently occurring words across all files.
    
    Query Parameters:
        limit (int, optional): Maximum number of words to return (default: 50, max: 200)
        
    Returns:
        JSON response with list of words and their frequencies
    """
    try:
        limit = request.args.get('limit', 50, type=int)
        limit = min(limit, 200)  # Cap at 200 for performance
        
        word_frequency = db_execute_query("""
            SELECT w.word, COUNT(DISTINCT wp.hash_id) as frequency
            FROM words w
            JOIN words_hashs wp ON w.id = wp.word_id
            GROUP BY w.id, w.word
            ORDER BY frequency DESC
            LIMIT %s
        """, (limit,))
        
        return jsonify({
            'success': True,
            'words': [
                {'word': row[0], 'frequency': row[1]}
                for row in (word_frequency or [])
            ]
        })
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.analytics', success_key='success', status=500)


@analytics_bp.route('/api/analytics/path-hierarchy', methods=['GET'])
def api_path_hierarchy():
    """
    API endpoint to get flat list of path hierarchy for dashboard.
    
    Returns a flat list of paths with statistics, suitable for dashboard display.
    This is different from api_paths_hierarchical() which returns nested structure.
    
    Returns:
        JSON response with:
        - structure: Flat list of paths with statistics
    """
    try:
        # Get all unique paths with their statistics
        paths_data = db_execute_query("""
            SELECT 
                COALESCE(p.file_path, '') as path,
                COUNT(*) as file_count,
                COUNT(*) FILTER (WHERE p.file_status = 'Read') as processed_count,
                COALESCE(SUM(p.file_size), 0) as total_size,
                BOOL_OR(p.file_path LIKE '%::%') as is_archive
            FROM paths p
            WHERE p.file_path IS NOT NULL AND p.file_path != ''
            GROUP BY p.file_path
            ORDER BY file_count DESC
            LIMIT 100
        """)
        
        structure = []
        for row in (paths_data or []):
            path, file_count, processed_count, total_size, is_archive = row
            # Extract path name (last part of path)
            path_parts = path.replace('\\', '/').split('/')
            if '::' in path:
                # For archive paths, use the archive name
                archive_part = path.split('::')[0]
                name = archive_part.split('/')[-1] if '/' in archive_part else archive_part
            else:
                name = path_parts[-1] if path_parts else path
            
            structure.append({
                'name': name,
                'path': path,
                'file_count': file_count,
                'fileCount': file_count,  # camelCase for compatibility
                'processed_count': processed_count,
                'processedCount': processed_count,  # camelCase for compatibility
                'total_size': total_size,
                'totalSize': total_size,  # camelCase for compatibility
                'isArchive': bool(is_archive),
                'is_archive': bool(is_archive)
            })
        
        return jsonify({
            'success': True,
            'structure': structure,
            'hierarchy': structure  # Also include as 'hierarchy' for backward compatibility
        })
    except Exception as e:
        return jsonify({'error': client_safe_message(e, subsystem='Api.routes.analytics'), 'success': False}), 500


@analytics_bp.route('/api/analytics/search-files', methods=['GET'])
def api_search_files():
    """
    API endpoint to search for files.
    
    Searches files by name or path, optionally filtered by file type.
    
    Query Parameters:
        q (str, optional): Search query to match against file names and paths
        type (str, optional): Filter by file type
        limit (int, optional): Maximum number of results (default: 50, max: 100)
        
    Returns:
        JSON response with list of matching files
    """
    try:
        query = request.args.get('q', '').strip()
        file_type = request.args.get('type', '')
        limit = min(request.args.get('limit', 50, type=int), 100)  # Cap at 100
        
        where_conditions = []
        params = []
        
        if query:
            where_conditions.append("(p.file_name ILIKE %s OR p.file_path ILIKE %s)")
            search_pattern = f'%{query}%'
            params.extend([search_pattern, search_pattern])
        
        if file_type:
            type_values = [v.strip() for v in str(file_type).split(',') if v.strip()]
            if len(type_values) == 1:
                where_conditions.append("p.file_type = %s")
                params.append(type_values[0])
            elif type_values:
                placeholders = ','.join(['%s'] * len(type_values))
                where_conditions.append(f"p.file_type IN ({placeholders})")
                params.extend(type_values)
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        files_query = f"""
            SELECT p.id, p.file_name, p.file_type, p.file_size, 
                   p.file_date, p.file_status,
                   COALESCE(s.name, 'Unknown') as source_name,
                   COALESCE(si.name, 'Unknown') as side_name
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
            LEFT JOIN sources s ON hc.source_id = s.id
            LEFT JOIN sides si ON hc.side_id = si.id
            WHERE {where_clause}
            ORDER BY p.date_creation DESC
            LIMIT %s
        """
        params.append(limit)
        
        files = db_execute_query(files_query, tuple(params))
        
        return jsonify({
            'success': True,
            'files': [
                {
                    'id': row[0], 'name': row[1], 'type': row[2],
                    'size': row[3] or 0, 'date': row[4].isoformat() if row[4] else None,
                    'status': row[5], 'source': row[6], 'side': row[7]
                }
                for row in (files or [])
            ]
        })
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.analytics', success_key='success', status=500)


@analytics_bp.route('/api/analytics/content-statistics', methods=['GET'])
def api_content_statistics():
    """
    API endpoint to get content statistics.
    
    Returns statistics about file content including word counts, character counts,
    and other content-related metrics.
    
    Returns:
        JSON response with content statistics
    """
    try:
        from Api.utils import get_category_statistics_detailed
        
        # Get coverage stats
        coverage_stats = db_execute_query("""
            SELECT 
                COUNT(DISTINCT p.id) as total_files,
                COUNT(DISTINCT CASE WHEN c.id IS NOT NULL THEN p.id END) as with_content,
                COUNT(DISTINCT CASE WHEN wp.word_id IS NOT NULL THEN p.id END) as with_words
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id
            LEFT JOIN contents c ON c.hash_id = hc.hash_id
            LEFT JOIN words_hashs wp ON wp.hash_id = hc.hash_id
        """, fetch="one")
        
        # Get top categories (limited)
        category_stats = get_category_statistics_detailed()
        # The query returns 'categories' not 'category_distribution'
        all_categories = category_stats.get('categories', category_stats.get('category_distribution', []))
        # Sort by file_count and take top 10
        categories = sorted(all_categories, key=lambda x: x.get('file_count', 0), reverse=True)[:10]
        
        # Get top words (limited)
        top_words = db_execute_query("""
            SELECT w.word, COUNT(DISTINCT p.id) as file_count
            FROM words w
            JOIN words_hashs wp ON w.id = wp.word_id
            JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
            JOIN paths p ON p.context_id = hc.id
            GROUP BY w.id, w.word
            ORDER BY file_count DESC
            LIMIT 30
        """)
        
        total_files = coverage_stats[0] if coverage_stats and isinstance(coverage_stats, tuple) else 0
        with_content = coverage_stats[1] if coverage_stats and isinstance(coverage_stats, tuple) and len(coverage_stats) > 1 else 0
        with_words = coverage_stats[2] if coverage_stats and isinstance(coverage_stats, tuple) and len(coverage_stats) > 2 else 0
        
        return jsonify({
            'success': True,
            'coverage': {
                'total_files': total_files,
                'with_content': with_content,
                'with_words': with_words
            },
            'categories': [
                {'name': cat.get('name', ''), 'file_count': cat.get('file_count', 0)}
                for cat in categories
            ],
            'top_words': [
                {'word': row[0], 'files': row[1]}
                for row in (top_words or [])
            ]
        })
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.analytics', success_key='success', status=500)


@analytics_bp.route('/api/analytics/path/analytics', methods=['GET'])
def api_path_analytics():
    """
    API endpoint to get analytics for a specific path.
    
    Returns analytics and statistics for files within a given path,
    including file counts, sizes, and content statistics.
    
    Query Parameters:
        path (str, optional): Path to analyze
        source_id (int, optional): Filter by source ID
        side_id (int, optional): Filter by side ID
        
    Returns:
        JSON response with path analytics
    """
    try:
        path_name = request.args.get('path', '').strip()
        source_id = request.args.get('source_id', type=int)
        side_id = request.args.get('side_id', type=int)
        
        if not path_name:
            return jsonify({'error': 'Path parameter required'}), 400
        
        # Build WHERE clause for path matching using helper function
        where_clause, path_params = build_path_where_clause(path_name)
        if not where_clause:
            return jsonify({'error': 'Invalid path'}), 400
        
        # Build filter clause for source and side
        filter_clause, filter_params = build_filter_clause(source_id, side_id)
        
        # Combine all parameters
        params = path_params + filter_params
        
        # Debug: Log the query for troubleshooting
        print(f"DEBUG: Querying path '{path_name}' with filters: source_id={source_id}, side_id={side_id}")
        
        # File type distribution
        type_dist = db_execute_query(f"""
            SELECT COALESCE(p.file_type, 'Unknown') as file_type, COUNT(*) as count
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
            WHERE {where_clause}{filter_clause}
            GROUP BY p.file_type
            ORDER BY count DESC
            LIMIT 20
        """, tuple(params))
        
        # Status distribution - file_status is an enum, so handle NULL properly
        status_dist = db_execute_query(f"""
            SELECT p.file_status, COUNT(*) as count
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
            WHERE {where_clause}{filter_clause}
            AND p.file_status IS NOT NULL
            GROUP BY p.file_status
            ORDER BY count DESC
        """, tuple(params))
        
        # Timeline (last 30 days)
        timeline = db_execute_query(f"""
            SELECT DATE(p.date_creation) as date, COUNT(*) as count
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
            WHERE {where_clause}{filter_clause}
            AND p.date_creation >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY DATE(p.date_creation)
            ORDER BY date ASC
        """, tuple(params))
        
        # Ensure we always return arrays, even if empty
        type_dist_list = [
            {'type': str(row[0]) if row[0] else 'Unknown', 'count': int(row[1]) if row[1] else 0}
            for row in (type_dist or [])
        ]
        status_dist_list = [
            {'status': str(row[0]) if row[0] else 'Unknown', 'count': int(row[1]) if row[1] else 0}
            for row in (status_dist or [])
        ]
        timeline_list = [
            {'date': row[0].isoformat() if row[0] else None, 'count': int(row[1]) if row[1] else 0}
            for row in (timeline or [])
        ]
        
        return jsonify({
            'success': True,
            'typeDistribution': type_dist_list,
            'statusDistribution': status_dist_list,
            'timeline': timeline_list
        })
    except Exception as e:
        import traceback
        print(f"Error in api_path_analytics: {str(e)}")
        print(traceback.format_exc())
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)


@analytics_bp.route('/api/analytics/path/words', methods=['GET'])
def api_path_words():
    """
    API endpoint to get words for a specific path.
    
    Returns the most frequently occurring words in files within the specified path.
    
    Query Parameters:
        path (str, required): Path to analyze
        limit (int, optional): Maximum number of words to return (default: 30, max: 100)
        source_id (int, optional): Filter by source ID
        side_id (int, optional): Filter by side ID
        
    Returns:
        JSON response with list of words and their file counts
    """
    try:
        path_name = request.args.get('path', '').strip()
        limit = min(request.args.get('limit', 30, type=int), 100)
        source_id = request.args.get('source_id', type=int)
        side_id = request.args.get('side_id', type=int)
        
        if not path_name:
            return jsonify({'error': 'Path parameter required'}), 400
        
        # Build WHERE clause for path matching using helper function
        where_clause, path_params = build_path_where_clause(path_name)
        if not where_clause:
            return jsonify({'error': 'Invalid path'}), 400
        
        # Build filter clause for source and side
        filter_clause, filter_params = build_filter_clause(source_id, side_id)
        
        # Combine all parameters
        params = path_params + filter_params + [limit]
        
        words = db_execute_query(f"""
            SELECT w.id, w.word, COUNT(DISTINCT p.id) as file_count
            FROM words w
            JOIN words_hashs wp ON w.id = wp.word_id
            JOIN hash_contexts hc ON hc.hash_id = wp.hash_id JOIN paths p ON p.context_id = hc.id
            WHERE {where_clause}{filter_clause}
            GROUP BY w.id, w.word
            ORDER BY file_count DESC
            LIMIT %s
        """, tuple(params))
        
        # Ensure we always return an array, even if empty
        words_list = [
            {'id': int(row[0]) if row[0] else 0, 'word': str(row[1]) if row[1] else '', 'fileCount': int(row[2]) if row[2] else 0}
            for row in (words or [])
        ]
        
        return jsonify({
            'success': True,
            'words': words_list
        })
    except Exception as e:
        import traceback
        print(f"Error in api_path_words: {str(e)}")
        print(traceback.format_exc())
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)


@analytics_bp.route('/api/analytics/path/files', methods=['GET'])
def api_path_files():
    """
    API endpoint to get files for a specific path.
    
    Returns files within the specified path with their metadata.
    
    Query Parameters:
        path (str, required): Path to query
        limit (int, optional): Maximum number of files to return (default: 50, max: 200)
        source_id (int, optional): Filter by source ID
        side_id (int, optional): Filter by side ID
        
    Returns:
        JSON response with list of files and their metadata
    """
    try:
        path_name = request.args.get('path', '').strip()
        limit = min(request.args.get('limit', 50, type=int), 200)
        source_id = request.args.get('source_id', type=int)
        side_id = request.args.get('side_id', type=int)
        
        if not path_name:
            return jsonify({'error': 'Path parameter required'}), 400
        
        # Build WHERE clause for path matching using helper function
        where_clause, path_params = build_path_where_clause(path_name)
        if not where_clause:
            return jsonify({'error': 'Invalid path'}), 400
        
        # Build filter clause for source and side
        filter_clause, filter_params = build_filter_clause(source_id, side_id)
        
        # Combine all parameters
        params = path_params + filter_params + [limit]
        
        files = db_execute_query(f"""
            SELECT p.id, p.file_name, p.file_type, p.file_size, 
                   p.file_date, p.file_status, p.date_creation,
                   COALESCE(s.name, 'Unknown') as source_name,
                   COALESCE(si.name, 'Unknown') as side_name
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
            LEFT JOIN sources s ON hc.source_id = s.id
            LEFT JOIN sides si ON hc.side_id = si.id
            WHERE {where_clause}{filter_clause}
            ORDER BY p.date_creation DESC
            LIMIT %s
        """, tuple(params))
        
        # Ensure we always return an array, even if empty
        files_list = [
            {
                'id': int(row[0]) if row[0] else 0,
                'name': str(row[1]) if row[1] else '',
                'type': str(row[2]) if row[2] else 'Unknown',
                'size': int(row[3]) if row[3] else 0,
                'date': row[4].isoformat() if row[4] else None,
                'status': str(row[5]) if row[5] else 'Unknown',
                'created': row[6].isoformat() if row[6] else None,
                'source': str(row[7]) if row[7] else 'Unknown',
                'side': str(row[8]) if row[8] else 'Unknown'
            }
            for row in (files or [])
        ]
        
        return jsonify({
            'success': True,
            'files': files_list
        })
    except Exception as e:
        import traceback
        print(f"Error in api_path_files: {str(e)}")
        print(traceback.format_exc())
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)


@analytics_bp.route('/api/analytics/path/classifications', methods=['GET'])
def api_path_classifications():
    """
    API endpoint to get classifications for a specific path.
    
    Returns category/classification statistics for files within the specified path.
    
    Query Parameters:
        path (str, required): Path to analyze
        source_id (int, optional): Filter by source ID
        side_id (int, optional): Filter by side ID
        
    Returns:
        JSON response with classification statistics
    """
    try:
        path_name = request.args.get('path', '').strip()
        source_id = request.args.get('source_id', type=int)
        side_id = request.args.get('side_id', type=int)
        
        if not path_name:
            return jsonify({'error': 'Path parameter required'}), 400
        
        # Build WHERE clause for path matching using helper function
        where_clause, path_params = build_path_where_clause(path_name)
        if not where_clause:
            return jsonify({'error': 'Invalid path'}), 400
        
        # Build filter clause for source and side
        filter_clause, filter_params = build_filter_clause(source_id, side_id)
        
        # Combine all parameters
        params = path_params + filter_params
        
        # Get total files in path
        total_files_result = db_execute_query(f"""
            SELECT COUNT(DISTINCT p.id)
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
            WHERE {where_clause}{filter_clause}
        """, tuple(params), fetch="one")
        total_files = total_files_result if isinstance(total_files_result, int) else (total_files_result[0] if total_files_result and isinstance(total_files_result, tuple) else 0)
        
        # Get categorized files (files that have keywords - multi-word phrases)
        categorized_result = db_execute_query(f"""
            SELECT COUNT(DISTINCT p.id)
            FROM paths p
            JOIN hash_contexts hc ON hc.id = p.context_id
            JOIN keywords_hashs kp ON kp.hash_id = hc.hash_id
            JOIN keywords k ON kp.keyword_id = k.id
            WHERE {where_clause}{filter_clause}
        """, tuple(params), fetch="one")
        categorized_files = categorized_result if isinstance(categorized_result, int) else (categorized_result[0] if categorized_result and isinstance(categorized_result, tuple) else 0)
        
        # Get classification breakdown by KEYWORDS (multi-word phrases, not single words)
        # This table shows categories based on keywords found in files
        # categorys table has word_id, need to join with words to get category name
        classifications = db_execute_query(f"""
            SELECT 
                COALESCE(w.word, 'Uncategorized') as category,
                COUNT(DISTINCT p.id) as file_count,
                COUNT(DISTINCT kp.keyword_id) as total_keywords
            FROM paths p
            JOIN hash_contexts hc ON hc.id = p.context_id
            JOIN keywords_hashs kp ON kp.hash_id = hc.hash_id
            JOIN keywords k ON kp.keyword_id = k.id
            JOIN categorys c ON k.category_id = c.id
            JOIN words w ON c.word_id = w.id
            WHERE {where_clause}{filter_clause}
            GROUP BY c.id, w.word
            ORDER BY file_count DESC
            LIMIT 20
        """, tuple(params))
        
        classifications_list = []
        if classifications:
            # Calculate percentage based on total files, not sum of categorized files
            # (a file can belong to multiple categories via keywords, so sum would be inflated)
            for row in classifications:
                file_count = row[1]
                # Percentage is based on total files in the path
                percentage = (file_count / total_files * 100) if total_files > 0 else 0
                classifications_list.append({
                    'category': row[0],
                    'fileCount': file_count,
                    'totalKeywords': row[2],
                    'percentage': round(percentage, 1)
                })
        
        # Add uncategorized count
        uncategorized_files = total_files - categorized_files
        
        return jsonify({
            'success': True,
            'totalFiles': int(total_files) if total_files else 0,
            'categorizedFiles': int(categorized_files) if categorized_files else 0,
            'uncategorizedFiles': int(uncategorized_files) if uncategorized_files else 0,
            'classifications': classifications_list
        })
    except Exception as e:
        import traceback
        print(f"Error in api_path_classifications: {str(e)}")
        print(traceback.format_exc())
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)


@analytics_bp.route('/api/analytics/path/category-words-analysis', methods=['GET'])
def api_path_category_words_analysis():
    """
    API endpoint to get category words analysis for a specific path.
    
    Returns word analysis grouped by categories for files within the specified path.
    
    Query Parameters:
        path (str, required): Path to analyze
        source_id (int, optional): Filter by source ID
        side_id (int, optional): Filter by side ID
        
    Returns:
        JSON response with category words analysis
    """
    try:
        path_name = request.args.get('path', '').strip()
        source_id = request.args.get('source_id', type=int)
        side_id = request.args.get('side_id', type=int)
        
        if not path_name:
            return jsonify({'error': 'Path parameter required'}), 400
        
        # Build WHERE clause for path matching using helper function
        where_clause, path_params = build_path_where_clause(path_name)
        if not where_clause:
            return jsonify({'error': 'Invalid path'}), 400
        
        # Build filter clause for source and side
        filter_clause, filter_params = build_filter_clause(source_id, side_id)
        
        # Combine all parameters
        params = path_params + filter_params
        
        # Get category breakdown
        # categorys table has word_id, need to join with words to get category name
        categories = db_execute_query(f"""
            SELECT 
                c.id as category_id,
                w.word as category_name,
                COUNT(DISTINCT p.id) as file_count,
                COUNT(DISTINCT wc.word_id) as word_count
            FROM paths p
            JOIN hash_contexts hc ON hc.id = p.context_id
            JOIN words_hashs wp ON wp.hash_id = hc.hash_id
            JOIN words_categorys wc ON wp.word_id = wc.word_id
            JOIN categorys c ON wc.category_id = c.id
            JOIN words w ON c.word_id = w.id
            WHERE {where_clause}{filter_clause}
            GROUP BY c.id, w.word
            ORDER BY file_count DESC
            LIMIT 20
        """, tuple(params))
        
        total_files_result = db_execute_query(f"""
            SELECT COUNT(DISTINCT p.id)
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
            WHERE {where_clause}{filter_clause}
        """, tuple(params), fetch="one")
        total_files = total_files_result if isinstance(total_files_result, int) else (total_files_result[0] if total_files_result and isinstance(total_files_result, tuple) else 0)
        
        total_words_result = db_execute_query(f"""
            SELECT COUNT(DISTINCT wc.word_id)
            FROM paths p
            JOIN hash_contexts hc ON hc.id = p.context_id
            JOIN words_hashs wp ON wp.hash_id = hc.hash_id
            JOIN words_categorys wc ON wp.word_id = wc.word_id
            WHERE {where_clause}{filter_clause}
        """, tuple(params), fetch="one")
        total_words = total_words_result if isinstance(total_words_result, int) else (total_words_result[0] if total_words_result and isinstance(total_words_result, tuple) else 0)
        
        categories_list = []
        if categories:
            for row in categories:
                file_percentage = (row[2] / total_files * 100) if total_files > 0 else 0
                categories_list.append({
                    'categoryId': row[0],
                    'categoryName': row[1],
                    'fileCount': row[2],
                    'wordCount': row[3],
                    'filePercentage': round(file_percentage, 1)
                })
        
        return jsonify({
            'success': True,
            'totalCategories': int(len(categories_list)) if categories_list else 0,
            'totalWords': int(total_words) if total_words else 0,
            'totalFiles': int(total_files) if total_files else 0,
            'categories': categories_list
        })
    except Exception as e:
        import traceback
        print(f"Error in api_path_category_words_analysis: {str(e)}")
        print(traceback.format_exc())
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)


@analytics_bp.route('/api/analytics/path/category-files', methods=['GET'])
def api_path_category_files():
    """
    API endpoint to get files for a specific path and category.
    
    Returns files within the specified path that belong to the specified category.
    
    Query Parameters:
        path (str, required): Path to query
        category (str, required): Category name to filter by
        limit (int, optional): Maximum number of files to return (default: 50, max: 200)
        source_id (int, optional): Filter by source ID
        side_id (int, optional): Filter by side ID
        
    Returns:
        JSON response with list of files in the category
    """
    try:
        path_name = request.args.get('path', '').strip()
        category = request.args.get('category', '').strip()
        limit = min(request.args.get('limit', 50, type=int), 200)
        
        if not path_name or not category:
            return jsonify({'error': 'Path and category parameters required'}), 400
        
        # Build WHERE clause for path matching using helper function
        where_clause, path_params = build_path_where_clause(path_name)
        if not where_clause:
            return jsonify({'error': 'Invalid path'}), 400
        
        if category == 'Uncategorized':
            # Get files without categories
            params = path_params + [limit]
            files = db_execute_query(f"""
                SELECT DISTINCT p.id, p.file_name, p.file_type, p.file_size, 
                       p.file_date, p.file_status, p.date_creation
                FROM paths p
                WHERE {where_clause}
                AND NOT EXISTS (
                    SELECT 1 FROM words_hashs wp
                    JOIN words_categorys wc ON wp.word_id = wc.word_id
                    WHERE wp.hash_id = (
                        SELECT hc0.hash_id FROM hash_contexts hc0
                        WHERE hc0.id = p.context_id
                    )
                )
                ORDER BY p.date_creation DESC
                LIMIT %s
            """, tuple(params))
        else:
            # Get files with specific category (based on KEYWORDS, not words)
            # This is called from the keyword-based classification table
            # categorys table has word_id, need to join with words to get category name
            params = path_params + [category, limit]
            files = db_execute_query(f"""
                SELECT DISTINCT p.id, p.file_name, p.file_type, p.file_size, 
                       p.file_date, p.file_status, p.date_creation
                FROM paths p
                JOIN hash_contexts hc ON hc.id = p.context_id
                JOIN keywords_hashs kp ON kp.hash_id = hc.hash_id
                JOIN keywords k ON kp.keyword_id = k.id
                JOIN categorys c ON k.category_id = c.id
                JOIN words w ON c.word_id = w.id
                WHERE {where_clause}
                AND w.word = %s
                ORDER BY p.date_creation DESC
                LIMIT %s
            """, tuple(params))
        
        # Get keywords for each file (filtered by the selected category)
        files_list = []
        for row in (files or []):
            file_id = row[0]
            # Get keywords for this file that belong to the selected category
            keywords = db_execute_query("""
                SELECT k.id, kp.word_count
                FROM keywords_hashs kp
                JOIN keywords k ON kp.keyword_id = k.id
                JOIN categorys c ON k.category_id = c.id
                JOIN words w ON c.word_id = w.id
                WHERE kp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s)
                AND w.word = %s
                ORDER BY kp.word_count DESC
                LIMIT 10
            """, (file_id, category))
            
            keyword_list = []
            if keywords:
                from Api.utils import load_text_keyword
                for kw_row in keywords:
                    keyword_id = kw_row[0]
                    word_count = kw_row[1] or 0
                    # Decode keyword from BYTEA to text using Python function
                    keyword_text = load_text_keyword(keyword_id) if keyword_id else None
                    if keyword_text:
                        keyword_list.append({
                            'word': keyword_text,
                            'count': word_count
                        })
            
            files_list.append({
                'id': row[0],
                'name': row[1],
                'type': row[2],
                'size': row[3] or 0,
                'date': row[4].isoformat() if row[4] else None,
                'status': row[5],
                'created': row[6].isoformat() if row[6] else None,
                'keywords': keyword_list
            })
        
        return jsonify({
            'success': True,
            'files': files_list
        })
    except Exception as e:
        import traceback
        print(f"Error in api_path_category_files: {str(e)}")
        print(traceback.format_exc())
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)


@analytics_bp.route('/api/analytics/path/category-words-detail', methods=['GET'])
def api_path_category_words_detail():
    """
    API endpoint to get detailed word information for a specific category and path.
    
    Returns words that belong to the specified category within the given path.
    
    Query Parameters:
        category_id (int, required): Category ID to filter by
        path (str, required): Path to analyze
        limit (int, optional): Maximum number of words to return
        
    Returns:
        JSON response with detailed word information for the category
    """
    try:
        category_id = request.args.get('category_id', type=int)
        path_name = request.args.get('path', '').strip()
        limit = min(request.args.get('limit', 100, type=int), 500)
        
        if not category_id or not path_name:
            return jsonify({'error': 'category_id and path parameters required'}), 400
        
        # Build WHERE clause for path matching using helper function
        where_clause, path_params = build_path_where_clause(path_name)
        if not where_clause:
            return jsonify({'error': 'Invalid path'}), 400
        params = [category_id] + path_params + [limit]
        
        words = db_execute_query(f"""
            SELECT DISTINCT w.id, w.word, COUNT(DISTINCT p.id) as file_count
            FROM words w
            JOIN words_categorys wc ON w.id = wc.word_id
            JOIN words_hashs wp ON w.id = wp.word_id
            JOIN hash_contexts hc ON hc.hash_id = wp.hash_id JOIN paths p ON p.context_id = hc.id
            WHERE wc.category_id = %s
            AND {where_clause}
            GROUP BY w.id, w.word
            ORDER BY file_count DESC
            LIMIT %s
        """, tuple(params))
        
        return jsonify({
            'success': True,
            'words': [
                {'id': row[0], 'word': row[1], 'fileCount': row[2]}
                for row in (words or [])
            ]
        })
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)


@analytics_bp.route('/api/analytics/path/word-files', methods=['GET'])
def api_path_word_files():
    """
    API endpoint to get files containing a specific word within a path.
    
    Returns files that contain the specified word, optionally filtered by category.
    
    Query Parameters:
        word (str, required): Word to search for
        path (str, required): Path to search within
        category_id (int, optional): Filter by category ID
        limit (int, optional): Maximum number of files to return
        
    Returns:
        JSON response with list of files containing the word
    """
    try:
        word_id = request.args.get('word_id', type=int)
        path_name = request.args.get('path', '').strip()
        category_id = request.args.get('category_id', type=int)  # Optional category filter
        limit = min(request.args.get('limit', 50, type=int), 200)
        
        if not word_id or not path_name:
            return jsonify({'error': 'word_id and path parameters required'}), 400
        
        # Build WHERE clause for path matching using helper function
        where_clause, path_params = build_path_where_clause(path_name)
        if not where_clause:
            return jsonify({'error': 'Invalid path'}), 400
        params = [word_id] + path_params + [limit]
        
        files = db_execute_query(f"""
            SELECT DISTINCT p.id, p.file_name, p.file_type, p.file_size, 
                   p.file_date, p.file_status, p.date_creation,
                   wp.word_count
            FROM paths p
            JOIN hash_contexts hc ON hc.id = p.context_id
            JOIN words_hashs wp ON wp.hash_id = hc.hash_id
            WHERE wp.word_id = %s
            AND {where_clause}
            ORDER BY wp.word_count DESC
            LIMIT %s
        """, tuple(params))
        
        # Get word text
        word_result = db_execute_query("""
            SELECT word FROM words WHERE id = %s
        """, (word_id,), fetch="one")
        word_text = word_result if isinstance(word_result, str) else (word_result[0] if word_result and isinstance(word_result, tuple) else '')
        
        # Get words for each file
        files_list = []
        if files:
            file_ids = [row[0] for row in files]
            placeholders = ','.join(['%s'] * len(file_ids))
            
            # Get words for these files - filter by category if provided
            if category_id:
                # Only get words that belong to the specified category
                file_words_query = f"""
                    SELECT p.id, w.id, w.word, COUNT(*) as count
                    FROM words_hashs wp
                    JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
                    JOIN paths p ON p.context_id = hc.id
                    JOIN words w ON wp.word_id = w.id
                    JOIN words_categorys wc ON w.id = wc.word_id
                    WHERE p.id IN ({placeholders})
                    AND wc.category_id = %s
                    GROUP BY p.id, w.id, w.word
                    ORDER BY p.id, count DESC
                """
                file_words = db_execute_query(file_words_query, tuple(file_ids + [category_id]))
            else:
                # Get all words for these files (no category filter)
                file_words_query = f"""
                    SELECT p.id, w.id, w.word, COUNT(*) as count
                    FROM words_hashs wp
                    JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
                    JOIN paths p ON p.context_id = hc.id
                    JOIN words w ON wp.word_id = w.id
                    WHERE p.id IN ({placeholders})
                    GROUP BY p.id, w.id, w.word
                    ORDER BY p.id, count DESC
                """
                file_words = db_execute_query(file_words_query, tuple(file_ids))
            
            # Organize words by file_id
            words_by_file = {}
            if file_words:
                for row in file_words:
                    file_id = row[0]
                    word_id_val = row[1]
                    word_text_val = row[2]
                    word_count = row[3]
                    
                    if file_id not in words_by_file:
                        words_by_file[file_id] = []
                    words_by_file[file_id].append({
                        'id': word_id_val,
                        'word': word_text_val,
                        'count': word_count
                    })
            
            # Build files list with words
            for row in files:
                file_id = row[0]
                files_list.append({
                    'id': file_id,
                    'name': row[1],
                    'type': row[2],
                    'size': row[3] or 0,
                    'date': row[4].isoformat() if row[4] else None,
                    'status': row[5],
                    'created': row[6].isoformat() if row[6] else None,
                    'wordCount': row[7] or 0,
                    'words': words_by_file.get(file_id, [])  # Add words for this file (filtered by category if provided)
                })
        
        return jsonify({
            'success': True,
            'word': word_text,
            'files': files_list,
            'categoryFiltered': category_id is not None
        })
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.analytics', status=500)


@analytics_bp.route('/api/analytics/dashboard-summary', methods=['GET'])
def api_dashboard_summary():
    """
    API endpoint to get dashboard summary statistics.
    
    Returns comprehensive statistics for the dashboard including:
    - File counts and sizes
    - Processing statistics
    - File type distribution
    - Recent activity
    
    Returns:
        JSON response with dashboard summary data
    """
    try:
        from Api.utils import get_statistics, get_processing_statistics
        
        # Get statistics with error handling
        try:
            stats = get_statistics()
            if stats is None:
                logger.warning("get_statistics() returned None, using empty dict")
                stats = {}
        except Exception as e:
            logger.error(f"Error calling get_statistics(): {e}", exc_info=True)
            stats = {}
        
        try:
            processing_stats = get_processing_statistics()
            if processing_stats is None:
                logger.warning("get_processing_statistics() returned None, using empty dict")
                processing_stats = {}
        except Exception as e:
            logger.error(f"Error calling get_processing_statistics(): {e}", exc_info=True)
            processing_stats = {}
        
        # PERF (req #17): this endpoint used to re-query the database for
        # unique_types/total_size/processed_files/total_keywords/
        # total_categories/database_size as six *separate* ad-hoc statements
        # (each its own round trip) even though get_statistics() and
        # get_processing_statistics() -- called just above -- had already
        # computed every one of those exact numbers ('unique_file_types',
        # 'total_storage_bytes', 'processed_count', 'total_keywords',
        # 'total_categories', 'database_size_bytes'). That duplicated work
        # under different variable names is why this endpoint measured
        # ~1.4-1.8s: roughly 16 sequential SQL round trips for one response
        # when at most a handful are actually needed. Reusing the values
        # already in `stats`/`processing_stats` removes six of them outright;
        # `recent_files` is the only number here that neither call computes.
        unique_types = stats.get('unique_file_types', 0)
        total_size = stats.get('total_storage_bytes', 0)
        processed_files = processing_stats.get('processed_count', 0)
        total_keywords = stats.get('total_keywords', 0)
        total_categories = stats.get('total_categories', 0)
        database_size = stats.get('database_size_bytes', 0)

        # Get recent files count (last 7 days) -- the one figure genuinely
        # not already produced by get_statistics()/get_processing_statistics().
        recent_files_result = db_execute_query("""
            SELECT COUNT(*) FROM paths
            WHERE date_creation >= CURRENT_DATE - INTERVAL '7 days'
        """, fetch="one")
        recent_files = recent_files_result if isinstance(recent_files_result, int) else (recent_files_result[0] if recent_files_result and isinstance(recent_files_result, tuple) else 0)

        total_files = stats.get('total_files', 0)
        processing_rate = (processed_files / total_files * 100) if total_files > 0 else 0

        return jsonify({

            'success': True,
            'totalFiles': stats.get('total_files', 0),
            'processedFiles': processed_files,
            'uniqueTypes': unique_types,
            'totalWords': stats.get('total_words', 0),
            'totalCategories': total_categories,
            'totalKeywords': total_keywords,
            'totalSize': total_size,
            'databaseSize': database_size,
            'processingRate': round(processing_rate, 1),
            'recentFiles': recent_files
        })
    except Exception as e:
        logger.error(f"Error in dashboard-summary endpoint: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.analytics', success_key='success', status=500)


@analytics_bp.route('/api/analytics/timeline-data', methods=['GET'])
def api_timeline_data():
    """
    API endpoint to get timeline data.
    
    Returns file processing activity over time, grouped by the specified period.
    
    Query Parameters:
        period (str, optional): Time period for grouping ('day', 'week', 'month', 'year')
        
    Returns:
        JSON response with timeline data
    """
    try:
        period = request.args.get('period', 'month')
        
        if period == 'day' or period == '24h':
            interval = "1 day"
            date_format = "YYYY-MM-DD HH24:00"
        elif period == 'week':
            interval = "7 days"
            date_format = "YYYY-MM-DD"
        elif period == 'month':
            interval = "30 days"
            date_format = "YYYY-MM-DD"
        else:  # year
            interval = "365 days"
            date_format = "YYYY-MM"
        
        # Get total files and processed files
        timeline_data = db_execute_query(f"""
            SELECT 
                TO_CHAR(date_creation, '{date_format}') as period,
                COUNT(*) as file_count,
                COUNT(*) FILTER (WHERE file_status = 'Read') as processed_count
            FROM paths
            WHERE date_creation >= CURRENT_DATE - INTERVAL '{interval}'
            GROUP BY TO_CHAR(date_creation, '{date_format}')
            ORDER BY period ASC
        """)
        
        labels = []
        file_counts = []
        processed_counts = []
        
        if timeline_data:
            for row in timeline_data:
                labels.append(row[0])
                file_counts.append(row[1])
                processed_counts.append(row[2])
        
        return jsonify({
            'success': True,
            'labels': labels,
            'fileCount': file_counts,
            'processedCount': processed_counts
        })
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.analytics', success_key='success', status=500)


@analytics_bp.route('/api/analytics/category-distribution', methods=['GET'])
def api_category_distribution():
    """
    API endpoint to get category distribution statistics.
    
    Returns statistics about how files are distributed across different categories.
    
    Returns:
        JSON response with category distribution data
    """
    try:
        from Api.utils import get_category_statistics_detailed
        category_stats = get_category_statistics_detailed()
        
        # The query returns 'categories' not 'category_distribution'
        distribution = category_stats.get('categories', category_stats.get('category_distribution', []))
        
        # Filter out categories with no files for better visualization
        distribution = [cat for cat in distribution if cat.get('file_count', 0) > 0]
        
        labels = [cat.get('name', '') for cat in distribution]
        values = [cat.get('file_count', 0) for cat in distribution]
        
        return jsonify({
            'success': True,
            'labels': labels,
            'values': values
        })
    except Exception as e:
        logger.error(f"Error in category-distribution endpoint: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.analytics', success_key='success', status=500)


@analytics_bp.route('/api/analytics/file-type-distribution', methods=['GET'])
def api_file_type_distribution():
    """
    API endpoint to get file type distribution statistics.
    
    Returns statistics about file types including counts and sizes.
    
    Returns:
        JSON response with file type distribution data
    """
    try:
        type_distribution = db_execute_query("""
            SELECT 
                CASE 
                    WHEN file_type = 'FILE' OR file_type IS NULL OR file_type = '' THEN
                        -- Extract extension from filename as fallback
                        UPPER(COALESCE(
                            NULLIF(SUBSTRING(file_name FROM '\\.([^.]+)$'), ''),
                            'UNKNOWN'
                        ))
                    ELSE file_type
                END as file_type,
                COUNT(*) as count,
                SUM(file_size) as total_size,
                AVG(file_size) as avg_size
            FROM paths
            GROUP BY 
                CASE 
                    WHEN file_type = 'FILE' OR file_type IS NULL OR file_type = '' THEN
                        UPPER(COALESCE(
                            NULLIF(SUBSTRING(file_name FROM '\\.([^.]+)$'), ''),
                            'UNKNOWN'
                        ))
                    ELSE file_type
                END
            ORDER BY count DESC
            LIMIT 50
        """)
        
        return jsonify({
            'success': True,
            'types': [
                {
                    'type': row[0] if row[0] else 'UNKNOWN',
                    'count': row[1],
                    'total_size': row[2] or 0,
                    'avg_size': row[3] or 0
                }
                for row in (type_distribution or [])
            ]
        })
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.analytics', success_key='success', status=500)


