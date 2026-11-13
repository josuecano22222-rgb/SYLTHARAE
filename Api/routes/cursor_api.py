"""
API Routes for Cursor-Based Pagination
Provides endpoints for billion+ record queries with streaming support
"""

from flask import Blueprint, request, jsonify, Response, stream_with_context
from Api.cursor_pagination import get_cursor_paginator, SortDirection
import json
import logging
import time
from core.errors import client_error, client_safe_message

logger = logging.getLogger(__name__)

# Create blueprint
cursor_api_bp = Blueprint('cursor_api', __name__)


@cursor_api_bp.route('/api/query/cursor', methods=['GET'])
def api_query_cursor():
    """
    API endpoint for cursor-based pagination queries.
    
    Query parameters:
        table: Table name (required)
        cursor: Cursor ID for pagination
        limit: Results per page (default: 50)
        sort_column: Column to sort by
        sort_direction: ASC or DESC (default: ASC)
        isolation_level: Transaction isolation level
        filters: JSON string of filter conditions
        
    Returns:
        JSON response with results and pagination metadata
    """
    try:
        # Get parameters
        table = request.args.get('table')
        if not table:
            return jsonify({'success': False, 'error': 'Table parameter is required'}), 400
        
        cursor = request.args.get('cursor', type=int)
        limit = request.args.get('limit', 50, type=int)
        sort_column = request.args.get('sort_column')
        sort_direction_str = request.args.get('sort_direction', 'ASC').upper()
        isolation_level = request.args.get('isolation_level', 'REPEATABLE READ')
        
        # Parse sort direction
        try:
            sort_direction = SortDirection[sort_direction_str]
        except KeyError:
            sort_direction = None
        
        # Parse filters
        filters = None
        filters_str = request.args.get('filters')
        if filters_str:
            try:
                filters = json.loads(filters_str)
            except json.JSONDecodeError:
                return jsonify({'success': False, 'error': 'Invalid filters JSON'}), 400
        
        # Parse joins
        joins = None
        joins_str = request.args.get('joins')
        if joins_str:
            try:
                joins = json.loads(joins_str)
            except json.JSONDecodeError:
                return jsonify({'success': False, 'error': 'Invalid joins JSON'}), 400
        
        # Parse select columns
        select_columns = None
        select_columns_str = request.args.get('select_columns')
        if select_columns_str:
            try:
                select_columns = json.loads(select_columns_str)
            except json.JSONDecodeError:
                return jsonify({'success': False, 'error': 'Invalid select_columns JSON'}), 400
        
        # Get table alias (optional)
        table_alias = request.args.get('table_alias')
        
        # Get paginator and fetch page
        paginator = get_cursor_paginator(table)
        paginator.isolation_level = isolation_level
        
        result = paginator.get_page(
            cursor=cursor,
            limit=limit,
            sort_column=sort_column,
            sort_direction=sort_direction,
            filters=filters,
            joins=joins,
            select_columns=select_columns,
            table_alias=table_alias
        )
        
        return jsonify({
            'success': True,
            **result
        })
    
    except ValueError as e:
        return client_error(e, subsystem='Api.routes.cursor_api', success_key='success', status=400)
    except Exception as e:
        logger.error(f"Cursor query error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.cursor_api', success_key='success', status=500)


@cursor_api_bp.route('/api/query/stream', methods=['GET', 'POST'])
def api_query_stream():
    """
    API endpoint for streaming query results.
    
    Supports both GET and POST methods. Returns streaming JSON response.
    
    Query/Body parameters:
        table: Table name (required)
        limit: Results per page
        filters: Filter conditions
        Other pagination parameters
        
    Returns:
        Streaming JSON response with results
    """
    try:
        # Get parameters from GET or POST
        if request.method == 'POST':
            data = request.get_json() or {}
            table = data.get('table')
            limit = data.get('limit', 1000)
            filters = data.get('filters')
            joins = data.get('joins')
            select_columns = data.get('select_columns')
            batch_size = data.get('batch_size', 50)
            table_alias = data.get('table_alias')
        else:
            table = request.args.get('table')
            limit = request.args.get('limit', 1000, type=int)
            filters_str = request.args.get('filters')
            joins_str = request.args.get('joins')
            select_columns_str = request.args.get('select_columns')
            batch_size = request.args.get('batch_size', 50, type=int)
            table_alias = request.args.get('table_alias')
            
            filters = json.loads(filters_str) if filters_str else None
            joins = json.loads(joins_str) if joins_str else None
            select_columns = json.loads(select_columns_str) if select_columns_str else None
        
        if not table:
            return jsonify({'success': False, 'error': 'Table parameter is required'}), 400
        
        # Validate batch_size
        batch_size = max(1, min(1000, batch_size))
        limit = max(1, min(100000, limit))  # Max 100K records for streaming
        
        def generate_stream():
            """Generator function for SSE streaming"""
            try:
                paginator = get_cursor_paginator(table)
                cursor = None
                total_records = 0
                batch = []
                
                # Send initial message
                yield f"data: {json.dumps({'type': 'start', 'table': table, 'limit': limit})}\n\n"
                
                start_time = time.time()
                
                while total_records < limit:
                    # Fetch next batch
                    result = paginator.get_page(
                        cursor=cursor,
                        limit=min(batch_size, limit - total_records),
                        filters=filters,
                        joins=joins,
                        select_columns=select_columns,
                        table_alias=table_alias
                    )
                    
                    data = result.get('data', [])
                    if not data:
                        break
                    
                    # Add to batch
                    batch.extend(data)
                    total_records += len(data)
                    
                    # Send batch if full or last batch
                    if len(batch) >= batch_size or not result.get('has_next'):
                        message_data = {
                            'type': 'data',
                            'records': batch,
                            'total_so_far': total_records,
                            'has_more': result.get('has_next', False)
                        }
                        yield f"data: {json.dumps(message_data)}\n\n"
                        batch = []
                    
                    # Update cursor
                    cursor = result.get('next_cursor')
                    
                    # Check if done
                    if not result.get('has_next') or total_records >= limit:
                        break
                
                # Send completion message
                elapsed_time = time.time() - start_time
                completion_data = {
                    'type': 'complete',
                    'total_records': total_records,
                    'elapsed_time': elapsed_time,
                    'records_per_second': total_records / elapsed_time if elapsed_time > 0 else 0
                }
                yield f"data: {json.dumps(completion_data)}\n\n"
            
            except Exception as e:
                logger.error(f"Streaming error: {e}", exc_info=True)
                yield f"data: {json.dumps({'type': 'error', 'error': client_safe_message(e, subsystem='Api.routes.cursor_api')})}\n\n"
        
        return Response(
            stream_with_context(generate_stream()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive'
            }
        )
    
    except Exception as e:
        logger.error(f"Stream query error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.cursor_api', success_key='success', status=500)


@cursor_api_bp.route('/api/query/integrity', methods=['GET'])
def api_query_integrity():
    """
    API endpoint for checking query integrity.
    
    Query parameters:
        table: Table name (required)
        cursor: Cursor ID (required)
        expected_count: Expected record count (required)
        filters: JSON string of filter conditions (optional)
        
    Returns:
        JSON response with integrity check results
    """
    try:
        table = request.args.get('table')
        cursor = request.args.get('cursor', type=int)
        expected_count = request.args.get('expected_count', type=int)
        filters_str = request.args.get('filters')
        
        if not table:
            return jsonify({'success': False, 'error': 'Table parameter is required'}), 400
        if cursor is None:
            return jsonify({'success': False, 'error': 'Cursor parameter is required'}), 400
        if expected_count is None:
            return jsonify({'success': False, 'error': 'Expected_count parameter is required'}), 400
        
        filters = None
        if filters_str:
            try:
                filters = json.loads(filters_str)
            except json.JSONDecodeError:
                return jsonify({'success': False, 'error': 'Invalid filters JSON'}), 400
        
        paginator = get_cursor_paginator(table)
        result = paginator.check_integrity(
            cursor=cursor,
            expected_count=expected_count,
            filters=filters
        )
        
        return jsonify({
            'success': True,
            **result
        })
    
    except Exception as e:
        logger.error(f"Integrity check error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.cursor_api', success_key='success', status=500)

