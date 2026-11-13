"""
General API routes
"""

from flask import request, jsonify, make_response


from Api.utils import (
    execute_query, select_info_sources, select_info_sides, load_text_keyword,
    get_categorys_word_id, load_text_content, get_content_count, get_file_word_count,
    get_categories_by_file, get_query
)


from datetime import date, datetime, timedelta
import logging

from Api.utils import get_processing_statistics, get_statistics, get_format_overview
from Api.services import lineage_service
from Api.services.analyst_categories import AnalystCategoryService
from core.errors import client_error, conflict, is_unique_violation
from core.table_paging import MAX_PAGE
from core.validation import query_int
from core.validation import PayloadError, int_field, number_field, text_field
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter
from database import (
    create_side, create_source, insert_side, insert_source, get_side, get_side_by_name, get_source, get_source_by_name, get_source_by_id,
    search_categories
)
from core.serialization import unpack_int_list
from services.i18n.term_search import expand_for_search

logger = logging.getLogger(__name__)


def register_api_routes(app):
    """Register general API routes with the Flask app"""
    
    @app.route('/api/dashboard/stats')
    def api_dashboard_stats():
        """🚀 OPTIMIZED: Get dashboard statistics with caching headers"""
        try:
            stats = get_statistics()
            processing_stats = get_processing_statistics()
            
            response = make_response(jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'totalDocs': stats.get('total_files', 0),
                'analyzedDocs': stats.get('status', {}).get('Read', 0) if isinstance(stats.get('status'), dict) else 0,
                'pendingDocs': stats.get('status', {}).get('Unread', 0) if isinstance(stats.get('status'), dict) else 0,
                'totalCategories': stats.get('total_categories', 0),
                'processingStats': processing_stats
            }))
            # CACHE-01: no-store is applied globally to /api/* — dashboard
            # stats must reflect deletes/ingests immediately.
            return response
        except Exception as e:
            logger.error(f"Dashboard stats API error: {e}")
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/formats/overview')
    def api_formats_overview():
        """Format Browser: per-extension aggregate statistics.

        Reuses the same paths/hash_contexts/hashs tables and duplicate/
        categorization definitions already authoritative elsewhere in the
        app (see database.get_format_overview_query docstring) -- this is
        the one new read-only aggregate the Format Browser needed that
        neither /api/dashboard/stats nor /api/analytics/file-type-distribution
        already exposed (source/side counts, categorized counts, duplicate
        counts, size range, date range, per format).
        """
        try:
            data = get_format_overview()
            response = make_response(jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                **data,
            }))
            return response
        except Exception as e:
            logger.error(f"Format overview API error: {e}")
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)

    @app.route('/api/sources', methods=['GET', 'POST'])
    def api_sources():
        """API: Get all sources or create a new source"""
        if request.method == 'POST':
            try:
                data = request.get_json(silent=True)
                
                if not data:
                    return jsonify({'success': False, 'error': 'JSON data is required'}), 400
                
                try:
                    name = text_field(data, 'name', required=True, max_length=255,
                                      trim=True)
                    # Every column below is VARCHAR(255) in m0001: a longer
                    # value used to reach the INSERT and come back as a 500.
                    job = text_field(data, 'job', max_length=255)
                    importance = number_field(data, 'importance', default=0.5)
                    country = text_field(data, 'country', max_length=255)
                    city = text_field(data, 'city', max_length=255)
                    description = text_field(data, 'description', max_length=255)
                    accounts = text_field(data, 'accounts', max_length=255)
                    note = text_field(data, 'note', max_length=255)
                    attachments = text_field(data, 'attachments', max_length=255)
                    ownership = text_field(data, 'ownership', max_length=255) or None
                    access_status = text_field(data, 'access_status',
                                               max_length=255) or None
                    category_id = int_field(data, 'category_id')
                except PayloadError as exc:
                    return jsonify({'success': False, **exc.to_dict()}), 400

                date_source_discovery = data.get('date_source_discovery')
                if date_source_discovery == '':
                    date_source_discovery = None
                
                # Convert date_source_discovery to entry_date (backend expects entry_date)
                entry_date = None
                if date_source_discovery:
                    try:
                        from datetime import datetime, date
                        if isinstance(date_source_discovery, str):
                            entry_date = datetime.strptime(date_source_discovery, "%Y-%m-%d").date()
                        elif isinstance(date_source_discovery, (datetime, date)):
                            if isinstance(date_source_discovery, datetime):
                                entry_date = date_source_discovery.date()
                            else:
                                entry_date = date_source_discovery
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid date_source_discovery format '{date_source_discovery}': {e}")
                        entry_date = None
                
                try:
                    source_id = insert_source(
                        name=name, job=job, importance=importance, country=country,
                        city=city, description=description, accounts=accounts,
                        note=note, attachments=attachments, ownership=ownership,
                        access_status=access_status, entry_date=entry_date,
                        id_categorys=category_id
                    )
                    
                    if source_id and source_id > 0:
                        try:
                            cache = get_query()
                            cache.clear()
                            logger.debug("Cleared query cache after creating source")
                        except Exception as e:
                            logger.warning(f"Could not clear cache after creating source: {e}")
                        
                        logger.info(f"Successfully created source: {name} (ID: {source_id})")
                        return jsonify({'success': True, 'id': source_id, 'message': 'Source created successfully'}), 201
                    else:
                        logger.error(f"insert_source returned invalid ID: {source_id}")
                        return jsonify({'success': False, 'error': 'Failed to create source: Invalid source ID returned'}), 500
                except Exception as create_error:
                    if is_unique_violation(create_error):
                        # sources.name is UNIQUE (m0001): a duplicate is the
                        # client's mistake, not a server fault.
                        return conflict('A source with that name already exists',
                                        field='name', success_key='success')
                    logger.error(f"Error in insert_source function: {create_error}", exc_info=True)
                    raise  # Re-raise to be caught by outer except
            except Exception as e:
                if is_unique_violation(e):
                    return conflict('A source with that name already exists',
                                    field='name', success_key='success')
                logger.error(f"Error creating source: {e}", exc_info=True)
                return client_error(e, subsystem='Api.routes.api', success_key='success', public_message='Failed to create source', status=500)
        else:
            # GET request - 🚀 OPTIMIZED with shorter cache (30 seconds for better freshness)
            try:
                sources_dict = select_info_sources()
                sources = [{'id': k, 'name': v} for k, v in sources_dict.items()]
                return jsonify(sources)
            except Exception as e:
                logger.error(f"Error getting sources: {e}", exc_info=True)
                return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/sources/<int:source_id>', methods=['DELETE', 'GET', 'PUT'])
    def api_source_detail(source_id):
        """API: Delete, get, or update a specific source"""
        if request.method == 'DELETE':
            try:
                # Check if source exists
                source = get_source(source_id)
                if not source:
                    return jsonify({'success': False, 'error': 'Source not found'}), 404

                # SRC-01: the actual deletion call had been lost - the handler
                # cleared the cache and returned success without touching the
                # database. Refuse to delete a source that still has files,
                # then delete for real.
                from database.services.contents_db_service import ContentDBService
                db_service = ContentDBService()
                usage_count = db_service.sources_repo.check_source_usage(source_id)
                if usage_count and int(usage_count) > 0:
                    return jsonify({
                        'success': False,
                        'error': f'Source is in use by {usage_count} file(s) and cannot be deleted. Remove or reassign its files first.'
                    }), 409

                db_service.sources_repo.delete_source(source_id)

                try:
                    cache = get_query()
                    cache.clear()
                    logger.debug("Cleared query cache after deleting source")
                except Exception as e:
                    logger.warning(f"Could not clear cache after deleting source: {e}")
                
                logger.info(f"Successfully deleted source: {source_id}")
                return jsonify({'success': True, 'message': 'Source deleted successfully'})
            except Exception as e:
                logger.error(f"Error deleting source {source_id}: {e}", exc_info=True)
                return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
        elif request.method == 'GET':
            try:
                from datetime import date, datetime
                source = get_source_by_id(source_id)
                if source:
                    # Serialize date objects to ISO format strings for JSON
                    source_dict = dict(source)  # Make a copy
                    # Handle date serialization - dates might already be strings from get_source
                    if 'date_creation' in source_dict and source_dict['date_creation']:
                        if isinstance(source_dict['date_creation'], (date, datetime)):
                            source_dict['date_creation'] = source_dict['date_creation'].isoformat()
                        elif isinstance(source_dict['date_creation'], str):
                            # Already a string, keep it as is
                            pass
                    # Map entry_date to date_source_discovery for frontend compatibility
                    if 'entry_date' in source_dict:
                        if isinstance(source_dict['entry_date'], (date, datetime)):
                            source_dict['date_source_discovery'] = source_dict['entry_date'].isoformat()
                        elif isinstance(source_dict['entry_date'], str):
                            source_dict['date_source_discovery'] = source_dict['entry_date']
                        else:
                            source_dict['date_source_discovery'] = None
                    elif 'date_source_discovery' not in source_dict:
                        source_dict['date_source_discovery'] = None
                    return jsonify({'success': True, 'source': source_dict})
                else:
                    # Source not found - could be missing or database error
                    logger.warning(f"Source {source_id} not found or could not be retrieved")
                    return jsonify({'success': False, 'error': 'Source not found'}), 404
            except Exception as e:
                logger.error(f"Error getting source {source_id}: {e}", exc_info=True)
                return client_error(e, subsystem='Api.routes.api', success_key='success', public_message='Error loading source', status=500)
        elif request.method == 'PUT':
            try:
                from datetime import date, datetime
                data = request.get_json(silent=True)
                if not data:
                    return jsonify({'success': False, 'error': 'JSON data is required'}), 400
                
                try:
                    name = text_field(data, 'name', required=True, max_length=255,
                                      trim=True)
                    job = text_field(data, 'job', max_length=255)
                    importance = number_field(data, 'importance')
                    country = text_field(data, 'country', max_length=255)
                    city = text_field(data, 'city', max_length=255)
                    description = text_field(data, 'description', max_length=255)
                    accounts = text_field(data, 'accounts', max_length=255)
                    note = text_field(data, 'note', max_length=255)
                    attachments = text_field(data, 'attachments', max_length=255)
                    ownership = text_field(data, 'ownership', max_length=255) or None
                    access_status = text_field(data, 'access_status',
                                               max_length=255) or None
                    category_id = int_field(data, 'category_id')
                except PayloadError as exc:
                    return jsonify({'success': False, **exc.to_dict()}), 400

                date_source_discovery = data.get('date_source_discovery')
                if date_source_discovery == '':
                    date_source_discovery = None
                
                update_kwargs = {}
                if name:
                    update_kwargs['name'] = name
                if job is not None:
                    update_kwargs['job'] = job
                if importance is not None:
                    try:
                        update_kwargs['importance'] = float(importance)
                    except (ValueError, TypeError):
                        return jsonify({'success': False, 'error': 'Invalid importance value'}), 400
                if country is not None:
                    update_kwargs['country'] = country
                if city is not None:
                    update_kwargs['city'] = city
                if description is not None:
                    update_kwargs['description'] = description
                if accounts is not None:
                    update_kwargs['accounts'] = accounts
                if note is not None:
                    update_kwargs['note'] = note
                if attachments is not None:
                    update_kwargs['attachments'] = attachments
                if ownership is not None:
                    update_kwargs['ownership'] = ownership
                if access_status is not None:
                    update_kwargs['access_status'] = access_status
                if category_id is not None:
                    update_kwargs['category_id'] = category_id
                # Convert date_source_discovery to entry_date (backend expects entry_date)
                if date_source_discovery is not None and date_source_discovery != '':
                    try:
                        from datetime import datetime, date
                        if isinstance(date_source_discovery, str):
                            entry_date = datetime.strptime(date_source_discovery, "%Y-%m-%d").date()
                        elif isinstance(date_source_discovery, (datetime, date)):
                            if isinstance(date_source_discovery, datetime):
                                entry_date = date_source_discovery.date()
                            else:
                                entry_date = date_source_discovery
                        else:
                            entry_date = None
                        if entry_date:
                            update_kwargs['entry_date'] = entry_date
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid date_source_discovery format '{date_source_discovery}': {e}")
                
                from database.services.contents_db_service import ContentDBService
                db_service = ContentDBService()
                
                # Get current source to preserve fields not being updated
                current_source = get_source(source_id)
                if not current_source:
                    return jsonify({'success': False, 'error': 'Source not found'}), 404
                
                # Prepare update parameters - use provided values or current values
                # Handle entry_date conversion from date_source_discovery or current entry_date
                entry_date_value = update_kwargs.get('entry_date')
                if not entry_date_value and 'entry_date' not in update_kwargs:
                    # Use current entry_date if not being updated
                    current_entry_date = current_source.get('entry_date') or current_source.get('date_source_discovery')
                    if isinstance(current_entry_date, str):
                        try:
                            from datetime import datetime
                            entry_date_value = datetime.strptime(current_entry_date, "%Y-%m-%d").date()
                        except ValueError:
                            entry_date_value = None
                    elif isinstance(current_entry_date, (datetime, date)):
                        if isinstance(current_entry_date, datetime):
                            entry_date_value = current_entry_date.date()
                        else:
                            entry_date_value = current_entry_date
                    else:
                        entry_date_value = None
                
                update_params = {
                    'name': update_kwargs.get('name', current_source.get('name', '')),
                    'country': update_kwargs.get('country', current_source.get('country', '')),
                    'job': update_kwargs.get('job', current_source.get('job', '')),
                    'importance': update_kwargs.get('importance', current_source.get('importance', 0.5)),
                    'city': update_kwargs.get('city', current_source.get('city', '')),
                    'description': update_kwargs.get('description', current_source.get('description', '')),
                    'accounts': update_kwargs.get('accounts', current_source.get('accounts', '')),
                    'note': update_kwargs.get('note', current_source.get('note', '')),
                    'attachments': update_kwargs.get('attachments', current_source.get('attachments', '')),
                    'ownership': update_kwargs.get('ownership', current_source.get('ownership')),
                    'access_status': update_kwargs.get('access_status', current_source.get('access_status')),
                    'entry_date': entry_date_value,
                    'category_id': update_kwargs.get('category_id', current_source.get('category_id'))
                }
                
                db_service.sources_repo.update_info_sources(
                    source_id=source_id,
                    **update_params
                )
                
                try:
                    cache = get_query()
                    cache.clear()
                    logger.debug("Cleared query cache after updating source")
                except Exception as e:
                    logger.warning(f"Could not clear cache after updating source: {e}")
                
                logger.info(f"Successfully updated source: {source_id}")
                return jsonify({'success': True, 'message': 'Source updated successfully'})
            except Exception as e:
                if is_unique_violation(e):
                    return conflict('A source with that name already exists',
                                    field='name', success_key='success')
                logger.error(f"Error updating source {source_id}: {e}", exc_info=True)
                return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/sources/<int:source_id>/duplicate', methods=['POST'])
    def api_source_duplicate(source_id):
        """API: Duplicate a source"""
        try:
            # Get the original source
            source = get_source(source_id)
            if not source:
                return jsonify({'success': False, 'error': 'Source not found'}), 404
            
            # Create a new name for the duplicate
            original_name = source.get('name', '')
            new_name = f"{original_name} (Copy)"
            
            # Check if a source with this name already exists, if so, add a number
            counter = 1
            while True:
                # Check if name exists using a direct query
                existing = execute_query(
                    "SELECT id FROM sources WHERE name = %s",
                    (new_name,),
                    fetch="one"
                )
                if not existing:
                    break
                counter += 1
                new_name = f"{original_name} (Copy {counter})"
            
            # Handle date_source_discovery/entry_date - convert to entry_date for backend
            from datetime import date, datetime
            entry_date = None
            # Check both date_source_discovery (frontend name) and entry_date (backend name)
            date_value = source.get('date_source_discovery') or source.get('entry_date')
            if date_value:
                if isinstance(date_value, (date, datetime)):
                    entry_date = date_value if isinstance(date_value, date) else date_value.date()
                elif isinstance(date_value, str):
                    try:
                        # Try parsing ISO format date string
                        entry_date = datetime.fromisoformat(date_value.replace('Z', '+00:00')).date()
                    except (ValueError, AttributeError):
                        try:
                            # Try parsing YYYY-MM-DD format
                            entry_date = datetime.strptime(date_value, '%Y-%m-%d').date()
                        except (ValueError, AttributeError):
                            entry_date = None
            
            # Create the duplicate with all the same fields
            new_source_id = insert_source(
                name=new_name,
                job=source.get('job', ''),
                importance=source.get('importance', 0.5),
                country=source.get('country', ''),
                city=source.get('city', ''),
                description=source.get('description', ''),
                accounts=source.get('accounts', ''),
                note=source.get('note', ''),
                attachments=source.get('attachments', ''),
                ownership=source.get('ownership'),
                access_status=source.get('access_status'),
                entry_date=entry_date,  # Use entry_date instead of date_source_discovery
                category_id=source.get('category_id')
            )
            
            try:
                cache = get_query()
                cache.clear()
                logger.debug("Cleared query cache after duplicating source")
            except Exception as e:
                logger.warning(f"Could not clear cache after duplicating source: {e}")
            
            logger.info(f"Successfully duplicated source {source_id} to {new_source_id} with name '{new_name}'")
            return jsonify({
                'success': True, 
                'message': 'Source duplicated successfully',
                'new_source_id': new_source_id,
                'new_name': new_name
            })
        except Exception as e:
            logger.error(f"Error duplicating source {source_id}: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/sources/<int:source_id>/export', methods=['GET'])
    def api_source_export(source_id):
        """API: Export source data as JSON"""
        try:
            from datetime import date, datetime
            import json
            from decimal import Decimal

            def _json_default(value):
                """AUDIT (API-03): psycopg2 returns NUMERIC columns as
                ``decimal.Decimal``, which ``json.dumps`` cannot encode. This
                made every source export fail with
                "Object of type Decimal is not JSON serializable" (500)."""
                if isinstance(value, Decimal):
                    return float(value)
                if isinstance(value, (date, datetime)):
                    return value.isoformat()
                return str(value)

            # Get the source data
            source = get_source_by_id(source_id)
            if not source:
                return jsonify({'success': False, 'error': 'Source not found'}), 404
            
            # Prepare export data
            export_data = {
                'id': source.get('id'),
                'name': source.get('name'),
                'job': source.get('job'),
                'importance': source.get('importance'),
                'country': source.get('country'),
                'city': source.get('city'),
                'description': source.get('description'),
                'accounts': source.get('accounts'),
                'note': source.get('note'),
                'attachments': source.get('attachments'),
                'ownership': source.get('ownership'),
                'access_status': source.get('access_status'),
                'category_id': source.get('category_id'),
                'date_creation': source.get('date_creation').isoformat() if source.get('date_creation') and isinstance(source.get('date_creation'), (date, datetime)) else (source.get('date_creation') if source.get('date_creation') else None),
                'date_source_discovery': source.get('entry_date').isoformat() if source.get('entry_date') and isinstance(source.get('entry_date'), (date, datetime)) else (source.get('entry_date') if source.get('entry_date') else None),
                'export_date': datetime.now().isoformat(),
                'export_version': '1.0'
            }
            
            # Create JSON response
            json_data = json.dumps(
                export_data, indent=2, ensure_ascii=False, default=_json_default
            )
            
            from core.security.disclosure import note_disclosure
            note_disclosure(kind='source_record', scope=f'source:{source_id}', unit='source',
                            row_count=1, format='json')
            # Return as downloadable file
            from flask import Response
            response = Response(
                json_data,
                mimetype='application/json',
                headers={
                    'Content-Disposition': f'attachment; filename=source_{source_id}_export.json'
                }
            )
            return response
            
        except Exception as e:
            logger.error(f"Error exporting source {source_id}: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/sides', methods=['GET', 'POST'])
    def api_sides():
        """API: Get all sides or create a new side"""
        if request.method == 'POST':
            try:
                data = request.get_json(silent=True)
                
                if not data:
                    if request.data and not request.is_json:
                        return jsonify({'success': False, 'error': 'Content-Type must be application/json'}), 400
                    elif request.data and data is None:
                        return jsonify({'success': False, 'error': 'Invalid JSON data in request body'}), 400
                    else:
                        return jsonify({'success': False, 'error': 'JSON data is required'}), 400
                
                try:
                    name = text_field(data, 'name', required=True, max_length=255,
                                      trim=True)
                    importance = number_field(data, 'importance', default=0.5)
                except PayloadError as exc:
                    return jsonify({'success': False, **exc.to_dict()}), 400
                
                side_id = insert_side(name=name, importance=importance)
                
                if side_id and side_id > 0:
                    try:
                        cache = get_query()
                        cache.clear()
                        logger.debug("Cleared query cache after creating side")
                    except Exception as e:
                        logger.warning(f"Could not clear cache after creating side: {e}")
                    
                    logger.info(f"Successfully created side: {name} (ID: {side_id})")
                    return jsonify({'success': True, 'id': side_id, 'message': 'Side created successfully'}), 201
                else:
                    logger.error(f"insert_side returned invalid ID: {side_id}")
                    return jsonify({'success': False, 'error': 'Failed to create side: Invalid side ID returned'}), 500
            except Exception as e:
                if is_unique_violation(e):
                    return conflict('A side with that name already exists',
                                    field='name', success_key='success')
                logger.error(f"Error creating side: {e}", exc_info=True)
                return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
        else:
            try:
                sides_dict = select_info_sides()
                sides = [{'id': k, 'name': v} for k, v in sides_dict.items()]
                return jsonify(sides)
            except Exception as e:
                logger.error(f"Error getting sides: {e}", exc_info=True)
                return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/sides/<int:side_id>', methods=['DELETE', 'GET', 'PUT'])
    def api_side_detail(side_id):
        """API: Delete, get, or update a specific side"""
        if request.method == 'DELETE':
            try:
                # Check if side exists
                side = get_side(side_id)
                if not side:
                    return jsonify({'success': False, 'error': 'Side not found'}), 404

                # SID-01: like SRC-01, the deletion call had been lost - the
                # handler returned success without deleting. Refuse to delete a
                # side that still has files, then delete for real.
                from database.services.contents_db_service import ContentDBService
                db_service = ContentDBService()
                usage_count = db_service.sides_repo.check_side_usage(side_id)
                if usage_count and int(usage_count) > 0:
                    return jsonify({
                        'success': False,
                        'error': f'Side is in use by {usage_count} file(s) and cannot be deleted. Remove or reassign its files first.'
                    }), 409

                db_service.sides_repo.delete_side(side_id)

                try:
                    cache = get_query()
                    cache.clear()
                    logger.debug("Cleared query cache after deleting side")
                except Exception as e:
                    logger.warning(f"Could not clear cache after deleting side: {e}")
                
                logger.info(f"Successfully deleted side: {side_id}")
                return jsonify({'success': True, 'message': 'Side deleted successfully'})
            except Exception as e:
                logger.error(f"Error deleting side {side_id}: {e}", exc_info=True)
                return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
        elif request.method == 'GET':
            try:
                from datetime import date, datetime
                side = get_side(side_id)
                if side:
                    # Serialize date objects to ISO format strings for JSON
                    side_dict = dict(side)  # Make a copy
                    if 'date_creation' in side_dict and side_dict['date_creation']:
                        if isinstance(side_dict['date_creation'], (date, datetime)):
                            side_dict['date_creation'] = side_dict['date_creation'].isoformat()
                    return jsonify({'success': True, 'side': side_dict})
                else:
                    return jsonify({'success': False, 'error': 'Side not found'}), 404
            except Exception as e:
                logger.error(f"Error getting side {side_id}: {e}", exc_info=True)
                return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
        elif request.method == 'PUT':
            try:
                data = request.get_json(silent=True)
                if not data:
                    return jsonify({'success': False, 'error': 'JSON data is required'}), 400
                
                try:
                    name = text_field(data, 'name', required=True, max_length=255,
                                      trim=True)
                    importance_float = number_field(data, 'importance')
                except PayloadError as exc:
                    return jsonify({'success': False, **exc.to_dict()}), 400

                existing = get_side(side_id)
                if not existing:
                    return jsonify({'success': False, 'error': 'Side not found'}), 404
                if importance_float is None:
                    importance_float = existing.get('importance')
                from database.services.contents_db_service import ContentDBService
                ContentDBService().sides_repo.update_info_sides(
                    side_id, name, importance_float)
                
                try:
                    cache = get_query()
                    cache.clear()
                    logger.debug("Cleared query cache after updating side")
                except Exception as e:
                    logger.warning(f"Could not clear cache after updating side: {e}")
                
                logger.info(f"Successfully updated side: {side_id}")
                return jsonify({'success': True, 'message': 'Side updated successfully'})
            except Exception as e:
                if is_unique_violation(e):
                    return conflict('A side with that name already exists',
                                    field='name', success_key='success')
                logger.error(f"Error updating side {side_id}: {e}", exc_info=True)
                return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/sides/<int:side_id>/duplicate', methods=['POST'])
    def api_side_duplicate(side_id):
        """API: Duplicate a side"""
        try:
            # Get the original side
            side = get_side(side_id)
            if not side:
                return jsonify({'success': False, 'error': 'Side not found'}), 404
            
            # Create a new name for the duplicate
            original_name = side.get('name', '')
            new_name = f"{original_name} (Copy)"
            
            # Check if a side with this name already exists, if so, add a number
            counter = 1
            while True:
                # Check if name exists using a direct query
                existing = execute_query(
                    "SELECT id FROM sides WHERE name = %s",
                    (new_name,),
                    fetch="one"
                )
                if not existing:
                    break
                counter += 1
                new_name = f"{original_name} (Copy {counter})"
            
            # Create the duplicate with the same importance
            importance = side.get('importance', 0.5)
            new_side_id = insert_side(name=new_name, importance=importance)
            
            try:
                cache = get_query()
                cache.clear()
                logger.debug("Cleared query cache after duplicating side")
            except Exception as e:
                logger.warning(f"Could not clear cache after duplicating side: {e}")
            
            logger.info(f"Successfully duplicated side {side_id} to {new_side_id} with name '{new_name}'")
            return jsonify({
                'success': True, 
                'message': 'Side duplicated successfully',
                'new_side_id': new_side_id,
                'new_name': new_name
            })
        except Exception as e:
            logger.error(f"Error duplicating side {side_id}: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)

    @app.route('/api/sides/<int:side_id>/export', methods=['GET'])
    def api_side_export(side_id):
        """API: Export side data as JSON"""
        try:
            from datetime import date, datetime
            import json
            from decimal import Decimal
            from flask import Response
            from core.security.disclosure import note_disclosure

            side = get_side(side_id)
            if not side:
                return jsonify({'success': False, 'error': 'Side not found'}), 404

            def _json_default(value):
                if isinstance(value, Decimal):
                    return float(value)
                if isinstance(value, (date, datetime)):
                    return value.isoformat()
                return str(value)

            export_data = {
                'id': side.get('id'),
                'name': side.get('name'),
                'importance': side.get('importance'),
                'date_creation': side.get('date_creation'),
                'export_date': datetime.now().isoformat(),
                'export_version': '1.0',
            }
            note_disclosure(kind='side_record', scope=f'side:{side_id}', unit='side',
                            row_count=1, format='json')
            return Response(
                json.dumps(export_data, indent=2, ensure_ascii=False, default=_json_default),
                mimetype='application/json',
                headers={'Content-Disposition': f'attachment; filename=side_{side_id}_export.json'},
            )
        except Exception as e:
            logger.error(f"Error exporting side {side_id}: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/categories')
    def api_categories():
        """
        API endpoint to get all categories.

        Returns:
            JSON array of categories with id and name (no-store per the
            global CACHE-01 policy: dropdowns must reflect newly created
            categories immediately)
        """
        from Api.utils import select_info_categories
        categories_data = select_info_categories()
        # get_categories_for_dropdown returns (id, name) tuples; API-CAT-02:
        # the old dict-only comprehension filtered every tuple out, so this
        # endpoint always returned [] and starved every dropdown consumer.
        categories = []
        for cat in categories_data or []:
            if isinstance(cat, dict):
                categories.append({'id': cat.get('id'), 'name': cat.get('name')})
            elif isinstance(cat, (tuple, list)) and len(cat) >= 2:
                categories.append({'id': cat[0], 'name': cat[1]})
        return jsonify(categories)

    @app.route('/api/categories/search')
    @limiter.limit("30 per minute")
    def api_categories_search():
        """Search categories with pagination"""
        try:
            search_query = request.args.get('q', '').strip()
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            
            results, total = search_categories(
                search_query=search_query if search_query else None,
                page=page,
                per_page=per_page
            )
            
            return jsonify({
                'results': results,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'total_pages': (total + per_page - 1) // per_page if total > 0 else 1
                }
            })
        except Exception as e:
            logger.error(f"Error searching categories: {e}")
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/words/search')
    @limiter.limit("30 per minute")
    def api_words_search():
        """Search words with pagination, returns word_id. Optionally excludes words already in a category.
        Uses exact match for multi-word keywords to search for the entire keyword."""
        try:
            search_query = request.args.get('q', '').strip()
            page = query_int("page", 1, minimum=1, maximum=MAX_PAGE)
            per_page = query_int("per_page", 20, minimum=1, maximum=200)
            exclude_category_id = request.args.get('exclude_category_id', type=int)
            offset = (page - 1) * per_page
            
            # Build WHERE clause for excluding words already in category
            exclude_clause = ""
            exclude_params = []
            if exclude_category_id:
                exclude_clause = "AND NOT EXISTS (SELECT 1 FROM words_categorys wc WHERE wc.word_id = w.id AND wc.category_id = %s)"
                exclude_params = [exclude_category_id]
            
            if search_query:
                # Cross-language recall: the picker also offers a word whose
                # translation normalises to the query, so a term can be found by
                # its Arabic name in an English interface. No match keeps the
                # exact-match predicate this endpoint has always used.
                expansion = expand_for_search(search_query)
                translated = list(expansion.word_ids)
                id_clause = " OR w.id = ANY(%s)" if translated else ""
                id_params = [translated] if translated else []

                # Use exact match (case-insensitive, no wildcards) to search for entire keyword, especially for multi-word keywords
                query = f"""
                    SELECT w.id, w.word,
                           COUNT(DISTINCT p.id) as usage_count
                    FROM words w
                    LEFT JOIN words_hashs wp ON w.id = wp.word_id
                    LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
                    LEFT JOIN paths p ON p.context_id = hc.id
                    WHERE (w.word ILIKE %s{id_clause}) {exclude_clause}
                    GROUP BY w.id, w.word
                    ORDER BY usage_count DESC, w.word ASC
                    LIMIT %s OFFSET %s
                """
                # Use exact match without wildcards - ILIKE without % is case-insensitive exact match
                data = execute_query(query, tuple([search_query] + id_params + exclude_params + [per_page, offset]))
                
                # Get total count
                count_query = f"SELECT COUNT(*) FROM words w WHERE (w.word ILIKE %s{id_clause}) {exclude_clause}"
                total_result = execute_query(count_query, tuple([search_query] + id_params + exclude_params), fetch="one")
                total = total_result[0] if total_result and total_result[0] is not None else 0
            else:
                query = f"""
                    SELECT w.id, w.word,
                           COUNT(DISTINCT p.id) as usage_count
                    FROM words w
                    LEFT JOIN words_hashs wp ON w.id = wp.word_id
                    LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
                    LEFT JOIN paths p ON p.context_id = hc.id
                    WHERE 1=1 {exclude_clause}
                    GROUP BY w.id, w.word
                    ORDER BY usage_count DESC, w.word ASC
                    LIMIT %s OFFSET %s
                """
                data = execute_query(query, tuple(exclude_params + [per_page, offset]))
                
                # Get total count
                count_query = f"SELECT COUNT(*) FROM words w WHERE 1=1 {exclude_clause}"
                total_result = execute_query(count_query, tuple(exclude_params), fetch="one")
                total = total_result[0] if total_result and total_result[0] is not None else 0
            
            results = []
            if data:
                for row in data:
                    results.append({
                        'id': row[0],  # word_id
                        'word_id': row[0],  # Also include as word_id for clarity
                        'text': row[1] or '',
                        'word': row[1] or '',
                        'usage_count': row[2] or 0
                    })
            
            return jsonify({
                'results': results,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'total_pages': (total + per_page - 1) // per_page if total > 0 else 1
                }
            })
        except Exception as e:
            logger.error(f"Error searching words: {e}")
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/dashboard/files-filtered')
    def api_dashboard_files_filtered():
        """API: Get files filtered by category, source, sides, and keywords"""
        try:
            category_id = request.args.get('category_id', type=int)
            source_id = request.args.get('source_id', type=int)
            side_id = request.args.get('side_id', type=int)
            keyword_id = request.args.get('keyword_id', type=int)
            
            where_clauses = []
            params = []
            
            query = """
                SELECT 
                    COALESCE(p.file_type, 'Unknown') as file_type,
                    COUNT(DISTINCT p.id) as file_count,
                    SUM(p.file_size) as total_size,
                    AVG(p.file_size) as avg_size
                FROM paths p
                JOIN hash_contexts hc ON p.context_id = hc.id JOIN hashs h ON hc.hash_id = h.id
            """
            
            if keyword_id:
                query += " JOIN keywords_hashs kp ON kp.hash_id = hc.hash_id"
                where_clauses.append("kp.keyword_id = %s")
                params.append(keyword_id)
            
            if category_id:
                query += """
                    JOIN words_hashs wp ON wp.hash_id = hc.hash_id
                    JOIN words_categorys wc ON wp.word_id = wc.word_id
                """
                where_clauses.append("wc.category_id = %s")
                params.append(category_id)
            
            if source_id:
                where_clauses.append("hc.source_id = %s")
                params.append(source_id)
            
            if side_id:
                where_clauses.append("hc.side_id = %s")
                params.append(side_id)
            
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            
            query += """
                GROUP BY p.file_type
                ORDER BY file_count DESC
                LIMIT 50
            """
            
            results = execute_query(query, tuple(params) if params else None)
            
            file_types = []
            if results:
                for row in results:
                    file_types.append({
                        'type': row[0],
                        'count': row[1],
                        'total_size': row[2] or 0,
                        'avg_size': float(row[3]) if row[3] else 0
                    })
            
            return jsonify({'success': True, 'file_types': file_types})
        except Exception as e:
            logger.error(f"Error fetching filtered files: {e}")
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/dashboard/categories-filtered')
    def api_dashboard_categories_filtered():
        """API: Get categories filtered by sides and source"""
        try:
            source_id = request.args.get('source_id', type=int)
            side_id = request.args.get('side_id', type=int)
            
            where_clauses = []
            params = []
            
            query = """
                SELECT 
                    c.id as category_id,
                    w.word as category_name,
                    COUNT(DISTINCT p.id) as file_count,
                    COUNT(DISTINCT wp.word_id) as word_count,
                    ROUND(COUNT(DISTINCT p.id)::numeric / NULLIF(COUNT(DISTINCT wp.word_id), 0), 2) as file_density
                FROM categorys c
                JOIN words w ON c.word_id = w.id
                JOIN words_categorys wc ON c.id = wc.category_id
                JOIN words_hashs wp ON wc.word_id = wp.word_id
                JOIN hash_contexts hc ON hc.hash_id = wp.hash_id JOIN paths p ON p.context_id = hc.id
            """
            
            if source_id:
                where_clauses.append("hc.source_id = %s")
                params.append(source_id)
            
            if side_id:
                where_clauses.append("hc.side_id = %s")
                params.append(side_id)
            
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            
            query += """
                GROUP BY c.id, w.word
                ORDER BY file_density DESC, file_count DESC
                LIMIT 50
            """
            
            # 🚀 OPTIMIZED: Use cache for frequently accessed category data
            results = execute_query(query, tuple(params) if params else None, use_cache=True)
            
            categories = []
            if results:
                for row in results:
                    categories.append({
                        'id': row[0],
                        'name': row[1],
                        'file_count': row[2],
                        'word_count': row[3],
                        'file_density': float(row[4]) if row[4] else 0
                    })
            
            return jsonify({'success': True, 'categories': categories})
        except Exception as e:
            logger.error(f"Error fetching filtered categories: {e}")
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/dashboard/keywords-filtered')
    def api_dashboard_keywords_filtered():
        """API: Get keywords filtered by category, source, and sides"""
        try:
            category_id = request.args.get('category_id', type=int)
            source_id = request.args.get('source_id', type=int)
            side_id = request.args.get('side_id', type=int)
            
            where_clauses = []
            params = []
            
            query = """
                SELECT 
                    k.id as keyword_id,
                    COUNT(DISTINCT p.id) as file_count,
                    COUNT(DISTINCT wp.word_id) as word_count,
                    CASE 
                        WHEN COUNT(DISTINCT wp.word_id) > 0 
                        THEN ROUND(COUNT(DISTINCT p.id)::numeric / NULLIF(COUNT(DISTINCT wp.word_id), 0), 2)
                        ELSE COUNT(DISTINCT p.id)::numeric
                    END as file_density
                FROM keywords k
                JOIN keywords_hashs kp ON k.id = kp.keyword_id
                JOIN hash_contexts hc ON hc.hash_id = kp.hash_id JOIN paths p ON p.context_id = hc.id
                LEFT JOIN words_hashs wp ON wp.hash_id = hc.hash_id
            """
            
            if category_id:
                query += """
                    JOIN words_categorys wc ON wp.word_id = wc.word_id
                """
                where_clauses.append("wc.category_id = %s")
                params.append(category_id)
            
            if source_id:
                where_clauses.append("hc.source_id = %s")
                params.append(source_id)
            
            if side_id:
                where_clauses.append("hc.side_id = %s")
                params.append(side_id)
            
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            
            query += """
                GROUP BY k.id
                ORDER BY file_density DESC, file_count DESC
                LIMIT 50
            """
            
            results = execute_query(query, tuple(params) if params else None)
            
            keywords = []
            if results:
                for row in results:
                    keyword_id = row[0]
                    try:
                        # 🚀 OPTIMIZED: Cache keyword text loading
                        keyword_text = load_text_keyword(keyword_id)
                        if keyword_text:  # Only add if keyword text is valid
                            keywords.append({
                                'id': keyword_id,
                                'text': keyword_text,
                                'file_count': row[1],
                                'word_count': row[2],
                                'file_density': float(row[3]) if row[3] else 0
                            })
                    except Exception as e:
                        logger.warning(f"Could not load keyword {keyword_id}: {e}")
                        continue
            
            return jsonify({'success': True, 'keywords': keywords})
        except Exception as e:
            logger.error(f"Error fetching filtered keywords: {e}")
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/dashboard/sources-filtered')
    def api_dashboard_sources_filtered():
        """API: Get sources filtered by file type, sides, and keywords"""
        try:
            file_type = request.args.get('file_type', '')
            side_id = request.args.get('side_id', type=int)
            keyword_id = request.args.get('keyword_id', type=int)
            
            where_clauses = []
            params = []
            
            query = """
                SELECT 
                    s.id as source_id,
                    s.name as source_name,
                    COUNT(DISTINCT p.id) as file_count,
                    SUM(p.file_size) as total_size,
                    AVG(p.file_size) as avg_size,
                    COUNT(DISTINCT p.file_type) as unique_file_types,
                    COUNT(DISTINCT CASE WHEN p.file_status = 'Read' THEN p.id END) as processed_files,
                    ROUND(COUNT(DISTINCT CASE WHEN p.file_status = 'Read' THEN p.id END)::numeric / 
                          NULLIF(COUNT(DISTINCT p.id), 0) * 100, 2) as processing_rate
                FROM sources s
                JOIN hash_contexts hc ON hc.source_id = s.id
                JOIN paths p ON p.context_id = hc.id
            """
            
            if keyword_id:
                query += " JOIN keywords_hashs kp ON kp.hash_id = hc.hash_id"
                where_clauses.append("kp.keyword_id = %s")
                params.append(keyword_id)
            
            if file_type:
                where_clauses.append("p.file_type = %s")
                params.append(file_type)
            
            if side_id:
                where_clauses.append("hc.side_id = %s")
                params.append(side_id)
            
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            
            query += """
                GROUP BY s.id, s.name
                ORDER BY file_count DESC
                LIMIT 100
            """
            
            results = execute_query(query, tuple(params) if params else None)
            
            sources = []
            if results:
                for row in results:
                    sources.append({
                        'id': row[0],
                        'name': row[1],
                        'file_count': row[2],
                        'total_size': row[3] or 0,
                        'avg_size': float(row[4]) if row[4] else 0,
                        'unique_file_types': row[5],
                        'processed_files': row[6],
                        'processing_rate': float(row[7]) if row[7] else 0
                    })
            
            return jsonify({'success': True, 'sources': sources})
        except Exception as e:
            logger.error(f"Error fetching filtered sources: {e}")
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/dashboard/sides-filtered')
    def api_dashboard_sides_filtered():
        """API: Get sides filtered by file type, category, and keywords"""
        try:
            file_type = request.args.get('file_type', '')
            category_id = request.args.get('category_id', type=int)
            keyword_id = request.args.get('keyword_id', type=int)
            
            where_clauses = []
            params = []
            
            query = """
                SELECT 
                    si.id as side_id,
                    si.name as side_name,
                    si.importance,
                    COUNT(DISTINCT p.id) as file_count,
                    SUM(p.file_size) as total_size,
                    AVG(p.file_size) as avg_size,
                    COUNT(DISTINCT p.file_type) as unique_file_types,
                    COUNT(DISTINCT CASE WHEN p.file_status = 'Read' THEN p.id END) as processed_files,
                    COUNT(DISTINCT hc.source_id) as source_count,
                    ROUND(COUNT(DISTINCT CASE WHEN p.file_status = 'Read' THEN p.id END)::numeric / 
                          NULLIF(COUNT(DISTINCT p.id), 0) * 100, 2) as processing_rate
                FROM sides si
                JOIN hash_contexts hc ON si.id = hc.side_id
                JOIN paths p ON p.context_id = hc.id
            """
            
            if keyword_id:
                query += " JOIN keywords_hashs kp ON kp.hash_id = hc.hash_id"
                where_clauses.append("kp.keyword_id = %s")
                params.append(keyword_id)
            
            if category_id:
                query += """
                    JOIN words_hashs wp ON wp.hash_id = hc.hash_id
                    JOIN words_categorys wc ON wp.word_id = wc.word_id
                """
                where_clauses.append("wc.category_id = %s")
                params.append(category_id)
            
            if file_type:
                where_clauses.append("p.file_type = %s")
                params.append(file_type)
            
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            
            query += """
                GROUP BY si.id, si.name, si.importance
                ORDER BY file_count DESC
                LIMIT 100
            """
            
            results = execute_query(query, tuple(params) if params else None)
            
            sides = []
            if results:
                for row in results:
                    sides.append({
                        'id': row[0],
                        'name': row[1],
                        'importance': row[2] or 0,
                        'file_count': row[3],
                        'total_size': row[4] or 0,
                        'avg_size': float(row[5]) if row[5] else 0,
                        'unique_file_types': row[6],
                        'processed_files': row[7],
                        'source_count': row[8],
                        'processing_rate': float(row[9]) if row[9] else 0
                    })
            
            return jsonify({'success': True, 'sides': sides})
        except Exception as e:
            logger.error(f"Error fetching filtered sides: {e}")
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/dashboard/words')
    def api_dashboard_words():
        """API: Get words sorted by category and unsorted words"""
        try:
            category_id = request.args.get('category_id', type=int)
            
            category_words_query = """
                SELECT 
                    c.id as category_id,
                    cw.word as category_name,
                    w.id as word_id,
                    w.word as word_text,
                    COUNT(DISTINCT p.id) as file_count
                FROM categorys c
                JOIN words cw ON c.word_id = cw.id
                JOIN words_categorys wc ON c.id = wc.category_id
                JOIN words w ON wc.word_id = w.id
                LEFT JOIN words_hashs wp ON w.id = wp.word_id
                LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id LEFT JOIN paths p ON p.context_id = hc.id
            """
            
            params = []
            if category_id:
                category_words_query += " WHERE c.id = %s"
                params.append(category_id)
            
            category_words_query += """
                GROUP BY c.id, cw.word, w.id, w.word
                ORDER BY cw.word, file_count DESC
                LIMIT 500
            """
            
            category_results = execute_query(category_words_query, tuple(params) if params else None)
            
            unsorted_words_query = """
                SELECT 
                    w.id as word_id,
                    w.word as word_text,
                    COUNT(DISTINCT p.id) as file_count
                FROM words w
                LEFT JOIN words_hashs wp ON w.id = wp.word_id
                LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id LEFT JOIN paths p ON p.context_id = hc.id
                WHERE NOT EXISTS (
                    SELECT 1 FROM words_categorys wc WHERE wc.word_id = w.id
                )
                GROUP BY w.id, w.word
                ORDER BY file_count DESC
                LIMIT 100
            """
            
            unsorted_results = execute_query(unsorted_words_query)
            
            words_by_category = {}
            if category_results:
                for row in category_results:
                    cat_id = row[0]
                    cat_name = row[1]
                    word_id = row[2]
                    word_text = row[3]
                    file_count = row[4]
                    
                    if cat_id not in words_by_category:
                        words_by_category[cat_id] = {
                            'id': cat_id,
                            'name': cat_name,
                            'words': []
                        }
                    
                    words_by_category[cat_id]['words'].append({
                        'id': word_id,
                        'text': word_text,
                        'file_count': file_count
                    })
            
            unsorted_words = []
            if unsorted_results:
                for row in unsorted_results:
                    unsorted_words.append({
                        'id': row[0],
                        'word': row[1],  # Changed from 'text' to 'word' to match frontend
                        'file_count': row[2]
                    })
            
            # Flatten words into a single array for frontend compatibility
            all_words = []
            
            # Add words from categories
            for cat_data in words_by_category.values():
                for word in cat_data['words']:
                    all_words.append({
                        'id': word['id'],
                        'word': word['text'],  # Changed from 'text' to 'word'
                        'file_count': word['file_count'],
                        'category': cat_data['name']
                    })
            
            # Only add unsorted words if no category filter is applied
            # When a category is selected, show only words from that category
            if not category_id:
                for word in unsorted_words:
                    all_words.append({
                        'id': word['id'],
                        'word': word['word'],
                        'file_count': word['file_count'],
                        'category': None
                    })
            
            # Sort by file_count descending
            all_words.sort(key=lambda x: x['file_count'], reverse=True)
            
            return jsonify({
                'success': True,
                'words': all_words,  # Main array for frontend
                'words_by_category': list(words_by_category.values()),  # Keep for backward compatibility
                'unsorted_words': unsorted_words  # Keep for backward compatibility
            })
        except Exception as e:
            logger.error(f"Error fetching words: {e}")
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @app.route('/api/dashboard/similar-files')
    def api_dashboard_similar_files():
        """API: Get similar files grouped by hash and title similarity"""
        try:
            from Api.utils.title_similarity import group_similar_titles
            
            similarity_threshold = request.args.get('similarity_threshold', 0.7, type=float)
            min_group_size = request.args.get('min_group_size', 2, type=int)
            limit = min(request.args.get('limit', 100, type=int), 500)
            
            # 1. Find files with same hash (duplicates)
            hash_groups_query = """
                SELECT 
                    h.hash,
                    h.id as hash_id,
                    COUNT(DISTINCT p.id) as file_count
                FROM hashs h
                JOIN hash_contexts hc ON hc.hash_id = h.id
                JOIN paths p ON p.context_id = hc.id
                GROUP BY h.id, h.hash
                HAVING COUNT(DISTINCT p.id) >= %s
                ORDER BY file_count DESC
                LIMIT %s
            """
            
            hash_results = execute_query(hash_groups_query, (min_group_size, limit))
            
            hash_groups = []
            all_path_ids = set()
            
            if hash_results:
                for row in hash_results:
                    hash_value = row[0]
                    hash_id = row[1]
                    file_count = row[2]
                    
                    # Get path IDs for this hash
                    path_ids_query = """
                        SELECT DISTINCT p.id
                        FROM paths p
                        JOIN hash_contexts hc ON p.context_id = hc.id JOIN hashs h ON hc.hash_id = h.id
                        WHERE h.hash = %s
                        ORDER BY p.id
                    """
                    path_ids_result = execute_query(path_ids_query, (hash_value,))
                    path_ids = [r[0] for r in path_ids_result] if path_ids_result else []
                    
                    # Get file details for these paths
                    placeholders = ','.join(['%s'] * len(path_ids))
                    files_query = f"""
                        SELECT 
                            p.id,
                            p.file_name,
                            p.file_path,
                            p.file_size,
                            p.file_type,
                            p.file_date,
                            h.hash,
                            COALESCE(s.name, 'Unknown') as source_name,
                            COALESCE(si.name, 'Unknown') as side_name
                        FROM paths p
                        JOIN hash_contexts hc ON p.context_id = hc.id JOIN hashs h ON hc.hash_id = h.id
                        LEFT JOIN sources s ON hc.source_id = s.id
                        LEFT JOIN sides si ON hc.side_id = si.id
                        WHERE p.id IN ({placeholders})
                        ORDER BY p.file_name
                    """
                    files_data = execute_query(files_query, tuple(path_ids))
                    
                    files_list = []
                    if files_data:
                        for file_row in files_data:
                            file_id = file_row[0]
                            all_path_ids.add(file_id)
                            files_list.append({
                                'id': file_id,
                                'file_name': file_row[1],
                                'file_path': file_row[2],
                                'file_size': file_row[3],
                                'file_type': file_row[4],
                                'file_date': str(file_row[5]) if file_row[5] else None,
                                'hash': file_row[6],
                                'source_name': file_row[7],
                                'side_name': file_row[8]
                            })
                    
                    if files_list:
                        hash_groups.append({
                            'group_type': 'hash',
                            'group_id': len(hash_groups) + 1,
                            'hash': hash_value,
                            'count': file_count,
                            'files': files_list
                        })
            
            # 2. Find files with similar titles
            # Get all titles with their paths
            titles_query = """
                SELECT 
                    tc.id as title_id,
                    p.id,
                    tc.title_data,
                    p.file_name,
                    p.file_path,
                    p.file_size,
                    p.file_type,
                    p.file_date,
                    h.hash,
                    COALESCE(s.name, 'Unknown') as source_name,
                    COALESCE(si.name, 'Unknown') as side_name
                FROM titles_content tc
                JOIN hash_contexts hc ON hc.hash_id = tc.hash_id
                JOIN paths p ON p.context_id = hc.id
                JOIN hashs h ON hc.hash_id = h.id
                LEFT JOIN sources s ON hc.source_id = s.id
                LEFT JOIN sides si ON hc.side_id = si.id
                WHERE tc.title_status = 'Main'
                ORDER BY tc.id DESC
                LIMIT %s
            """
            
            titles_data = execute_query(titles_query, (limit * 2,))
            
            title_groups = []
            if titles_data:
                # Batch load all titles
                all_title_word_ids = set()
                title_word_map = {}
                
                for row in titles_data:
                    title_id = row[0]
                    title_bytes = row[2]
                    try:
                        if title_bytes:
                            # Validate bytes before unpickling
                            if not isinstance(title_bytes, (bytes, bytearray, memoryview)):
                                logger.warning(f"Invalid title_bytes type for title {title_id}: {type(title_bytes)}")
                                continue
                            
                            # Convert to bytes if needed
                            if isinstance(title_bytes, memoryview):
                                title_bytes = bytes(title_bytes)
                            elif not isinstance(title_bytes, bytes):
                                title_bytes = bytes(title_bytes)
                            
                            # Validate minimum size (pickle protocol requires at least a few bytes)
                            if len(title_bytes) < 2:
                                logger.warning(f"Title {title_id} has invalid pickle data (too short)")
                                continue
                            
                            word_ids = unpack_int_list(title_bytes)
                            if word_ids and isinstance(word_ids, list):
                                title_word_map[title_id] = word_ids
                                all_title_word_ids.update(word_ids)
                    except (ValueError, TypeError, EOFError) as e:
                        # Silently skip corrupted data to prevent log spam
                        # Only log if it's a new type of error
                        if 'invalid load key' not in str(e).lower():
                            logger.debug(f"Error unpickling title {title_id}: {type(e).__name__}")
                    except Exception as e:
                        logger.warning(f"Unexpected error unpickling title {title_id}: {e}")
                
                # Batch load all words
                title_word_dict = {}
                if all_title_word_ids:
                    placeholders = ','.join(['%s'] * len(all_title_word_ids))
                    words_query = f"SELECT id, word FROM words WHERE id IN ({placeholders})"
                    words_result = execute_query(words_query, list(all_title_word_ids))
                    if words_result:
                        title_word_dict = {row[0]: row[1] for row in words_result}
                
                # Build title list with text
                titles_list = []
                for row in titles_data:
                    title_id = row[0]
                    path_id = row[1]
                    title_bytes = row[2]
                    
                    # Skip if already in hash groups
                    if path_id in all_path_ids:
                        continue
                    
                    # Decode title text
                    title_text = None
                    if title_id in title_word_map:
                        word_ids = title_word_map[title_id]
                        words = [title_word_dict.get(wid, '') for wid in word_ids if wid in title_word_dict]
                        title_text = ' '.join(words).strip()
                    
                    if title_text:
                        titles_list.append({
                            'id': title_id,
                            'name': title_text,
                            'path_id': path_id,
                            'file_name': row[3],
                            'file_path': row[4],
                            'file_size': row[5],
                            'file_type': row[6],
                            'file_date': str(row[7]) if row[7] else None,
                            'hash': row[8],
                            'source_name': row[9],
                            'side_name': row[10]
                        })
                
                # Group similar titles
                if titles_list:
                    similar_groups = group_similar_titles(titles_list, similarity_threshold)
                    
                    for group in similar_groups:
                        if group['count'] >= min_group_size:
                            # Convert to file format
                            files_list = []
                            for title in group['titles']:
                                files_list.append({
                                    'id': title['path_id'],
                                    'file_name': title['file_name'],
                                    'file_path': title['file_path'],
                                    'file_size': title['file_size'],
                                    'file_type': title['file_type'],
                                    'file_date': title['file_date'],
                                    'hash': title['hash'],
                                    'source_name': title['source_name'],
                                    'side_name': title['side_name'],
                                    'title': title['name']
                                })
                            
                            title_groups.append({
                                'group_type': 'title',
                                'group_id': len(title_groups) + 1,
                                'representative_title': group['representative_title'],
                                'is_identical': group['is_identical'],
                                'count': group['count'],
                                'files': files_list
                            })
            
            return jsonify({
                'success': True,
                'hash_groups': hash_groups,
                'title_groups': title_groups,
                'total_hash_groups': len(hash_groups),
                'total_title_groups': len(title_groups),
                'total_files': sum(g['count'] for g in hash_groups) + sum(g['count'] for g in title_groups)
            })
        except Exception as e:
            logger.error(f"Error fetching similar files: {e}")
            import traceback
            traceback.print_exc()
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
    
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    @app.route('/api/file/<int:file_id>/details')
    def api_file_details(file_id):
        """API endpoint for getting file details as JSON"""
        try:
            file_info = execute_query("""
                SELECT p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                       p.file_status, p.file_date, p.date_creation, hc.hash_id,
                       COALESCE(s.name, 'Unknown') as source_name, 
                       COALESCE(si.name, 'Unknown') as side_name, 
                       COALESCE(h.hash, '') as hash,
                       p.extraction_provenance, p.parent_path_id,
                       p.hierarchy_path, p.processing_status, p.status_detail,
                       p.attempts, p.status_updated_at, p.coordinates,
                       p.error_message
                FROM paths p
                LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
                LEFT JOIN sources s ON hc.source_id = s.id
                LEFT JOIN sides si ON hc.side_id = si.id
                WHERE p.id = %s
            """, (file_id,), fetch="one")
            
            if not file_info:
                return jsonify({'success': False, 'error': 'File not found'}), 404
            
            try:
                content = load_text_content(file_id)
                if content is None:
                    content = ''
            except Exception as e:
                logger.error(f"Error loading content for file_id={file_id}: {e}")
                content = ''
            
            content_count = get_content_count(file_id)
            word_count = get_file_word_count(file_id)
            
            try:
                categories = get_categories_by_file(file_id)
                categories_list = [
                    {
                        'id': cat['id'],
                        'name': cat['name'] or 'Unnamed Category',
                        'word_count': cat['word_count'] or 0
                    }
                    for cat in categories
                ]
            except Exception as e:
                logger.error(f"Error loading categories for file_id={file_id}: {e}")
                categories_list = []
            
            title_text = None
            title_id = None
            try:
                from Api.utils import load_text_title
                title_result = execute_query("""
                    SELECT id FROM titles_content 
                    WHERE hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s) AND title_status = 'Main' 
                    LIMIT 1
                """, (file_id,), fetch="one")
                if title_result:
                    title_id = title_result[0] if isinstance(title_result, tuple) else title_result
                    title_text = load_text_title(title_id)
            except Exception as e:
                logger.warning(f"Error loading title for file_id={file_id}: {e}")
            
            similar_titles = []
            if title_text:
                try:
                    from Api.utils.title_similarity import find_similar_titles
                    # Get all titles for comparison (limited to recent ones for performance)
                    all_titles_data = execute_query("""
                        SELECT tc.id, tc.hash_id, tc.title_data
                        FROM titles_content tc
                        WHERE tc.title_status = 'Main' AND tc.id != %s
                        ORDER BY tc.id DESC
                        LIMIT 1000
                    """, (title_id,) if title_id else (0,), fetch="all")
                    
                    if all_titles_data:
                        from Api.utils import execute_query as eq
                        
                        # Decode all titles
                        all_titles = []
                        all_word_ids = set()
                        title_word_map = {}
                        
                        for row in all_titles_data:
                            tid = row[0]
                            tbytes = row[2]
                            try:
                                if tbytes:
                                    word_ids = unpack_int_list(tbytes)
                                    if word_ids and isinstance(word_ids, list):
                                        title_word_map[tid] = word_ids
                                        all_word_ids.update(word_ids)
                            except Exception:
                                continue
                        
                        if all_word_ids:
                            placeholders = ','.join(['%s'] * len(all_word_ids))
                            words_query = f"SELECT id, word FROM words WHERE id IN ({placeholders})"
                            words_result = eq(words_query, list(all_word_ids))
                            if words_result:
                                word_dict = {row[0]: row[1] for row in words_result}
                                
                                # Build title list
                                for tid, word_ids in title_word_map.items():
                                    words = [word_dict.get(wid, '') for wid in word_ids if wid in word_dict]
                                    if words:
                                        all_titles.append({
                                            'id': tid,
                                            'name': ' '.join(words)
                                        })
                        
                        if all_titles:
                            similar_titles = find_similar_titles(
                                title_text, 
                                all_titles, 
                                similarity_threshold=0.7, 
                                max_results=10
                            )
                except Exception as e:
                    logger.warning(f"Error finding similar titles for file_id={file_id}: {e}")
            
            file_date = file_info[6].isoformat() if file_info[6] else None
            date_creation = file_info[7].isoformat() if file_info[7] else None
            
            content_str = str(content) if content is not None else ''
            
            if not isinstance(categories_list, list):
                categories_list = []
            
            # Provenance, lineage and processing status (migration 0007 columns).
            # Returned as one coherent block rather than scattered fields: the
            # question File Details has to answer is "what is this object, where
            # did each piece originate, how was it derived", and those answers
            # are only meaningful together. Lineage is read from parent_path_id
            # by lineage_service, never inferred from names or filesystem paths.
            # Zip the row against an explicit column list rather than indexing
            # it positionally: positional indexes silently go wrong when the
            # SELECT changes, and the failure surfaces as an IndexError at
            # request time instead of a name that does not exist.
            row = dict(zip(
                ("id", "name", "path", "size", "type", "file_status", "file_date",
                 "date_creation", "hash_id", "source", "side", "hash",
                 "extraction_provenance", "parent_path_id", "hierarchy_path",
                 "processing_status", "status_detail", "attempts",
                 "status_updated_at", "coordinates", "error_message"),
                file_info,
                # strict: a SELECT/column-list mismatch must raise here, loudly,
                # not silently produce a short dict. Without it zip() truncates
                # to the shorter side and the row loses fields unnoticed -
                # which is precisely the positional-index bug this replaced.
                strict=True,
            ))
            lineage = lineage_service.get_file_lineage(file_id)
            provenance = lineage_service.normalise_provenance(
                row["extraction_provenance"]
            )
            status_updated_at = (
                row["status_updated_at"].isoformat()
                if row["status_updated_at"] else None
            )

            # Real place-name mentions found by scanning this file's own
            # extracted text (File Analysis: Geolocation /
            # Api/services/geo_extraction_service.py), keyed off the file's
            # canonical hash so every path sharing that content sees the
            # same mentions. This is the rich, multi-place data behind the
            # single-point `coordinates` backward-compat field above -- the
            # File Details view should show what was actually found, not
            # just a single lat/lon pair.
            geo_mentions = []
            try:
                if row["hash_id"] is not None:
                    mention_rows = execute_query(
                        """
                        SELECT place_name, country, latitude, longitude, mention_count
                        FROM path_geo_mentions
                        WHERE hash_id = %s
                        ORDER BY mention_count DESC, place_name ASC
                        """,
                        (row["hash_id"],),
                        fetch="all",
                    )
                    geo_mentions = [
                        {
                            'place_name': m[0],
                            'country': m[1],
                            'latitude': float(m[2]) if m[2] is not None else None,
                            'longitude': float(m[3]) if m[3] is not None else None,
                            'mention_count': m[4],
                        }
                        for m in (mention_rows or [])
                    ]
            except Exception as e:
                logger.warning(f"Error loading geo mentions for file_id={file_id}: {e}")

            details = {
                'id': file_info[0],
                'name': file_info[1] or 'Unnamed File',
                'path': file_info[2] or '',
                'size': file_info[3] or 0,
                'type': file_info[4] or 'Unknown',
                'status': file_info[5] or 'Unknown',
                'file_date': file_date,
                'date_creation': date_creation,
                'hash_id': file_info[8],
                'source': file_info[9] or 'Unknown',
                'side': file_info[10] or 'Unknown',
                'hash': file_info[11] or '',
                'content_chunks': content_count,
                'word_count': word_count,
                'content': content_str,
                'categories': categories_list,
                'title': title_text,
                'title_id': title_id,
                'similar_titles': similar_titles,
                'extraction_provenance': provenance,
                'parent_path_id': row['parent_path_id'],
                'hierarchy_path': row['hierarchy_path'],
                'processing': {
                    'status': row['processing_status'] or 'unknown',
                    'detail': row['status_detail'],
                    'attempts': row['attempts'] or 0,
                    'updated_at': status_updated_at,
                },
                'coordinates': row['coordinates'],
                'error_message': row['error_message'],
                'lineage': lineage,
                'geo_mentions': geo_mentions,
            }

            # Analyst classification (separate namespace from smart
            # `categories` above - Api/services/analyst_categories.py). Reuses
            # the existing service exactly as the search-result decoration
            # path does (AnalystCategoryService.attach_categories_to_results),
            # so the file-detail view and search results never disagree.
            try:
                details['analyst_categories'] = (
                    AnalystCategoryService.categories_for_files([file_id]).get(file_id, [])
                )
            except Exception as e:
                logger.warning(f"Error loading analyst categories for file_id={file_id}: {e}")
                details['analyst_categories'] = []

            # Per-assignment detail (who/when/originating query) for the
            # Analyst Classification section (FR-1.5 traceability) - the
            # category list above only carries id/name/color.
            try:
                assignments, _ = AnalystCategoryService.list_assignments(file_id=file_id, limit=50)
                details['analyst_assignments'] = assignments
            except Exception as e:
                logger.warning(f"Error loading analyst assignments for file_id={file_id}: {e}")
                details['analyst_assignments'] = []

            return jsonify({'success': True, 'details': details})
        except Exception as e:
            logger.error(f"Error fetching file details: {e}")
            return client_error(e, subsystem='Api.routes.api', success_key='success', status=500)
