"""
Notification API Routes
System-wide notification management endpoints
"""

from flask import request, jsonify
from flask_babel import gettext as _
from datetime import date, datetime, timezone
from core.monitoring.notification_service import (
    get_notification_service,
    NotificationType,
    NotificationPriority,
    notification_from_row,
    ALERT_COLUMNS,
    visibility_clause,
    utc_today,
)
from core.monitoring.notification_display import (
    display_payload, format_title_message, path_names)
from Api.utils import execute_query
import logging
from core.errors import client_error, client_safe_message
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter
from core.security.flask_ext import write_access_required

logger = logging.getLogger(__name__)


def _future_dates_enabled() -> bool:
    """``notifications.future_dates_enabled`` (default on). An unreadable
    setting is logged and treated as the default, as elsewhere."""
    try:
        from settings import get_settings
        return bool(get_settings().get('notifications', 'future_dates_enabled', True))
    except Exception:
        logger.warning("could not read notifications.future_dates_enabled", exc_info=True)
        return True


def _reference_date_arg() -> date:
    raw = request.args.get('reference_date') or (request.get_json(silent=True) or {}).get(
        'reference_date')
    if not raw:
        return datetime.now(timezone.utc).date()
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise ValueError("reference_date must be an ISO date (YYYY-MM-DD)")


def _current_signals(hash_id: int, reference_date: date) -> dict:
    """Stored signals for ``hash_id``; analyses it first when there is no run
    by the current detector version."""
    from Api.utils.utils import get_connection
    from core.criteria.access import scope_for
    from core.security.flask_ext import current_user
    from services.detection import signal_store
    from services.detection.redetection import redetect_one

    user = current_user()
    scope = scope_for(user)

    def read():
        with get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    return signal_store.signals_for(cur, hash_id, reference_date, scope=scope,
                                                    detectors=['temporal'])
            finally:
                conn.rollback()

    body = read()
    if body['run'] is None or not body['run']['current_version']:
        redetect_one(get_connection, hash_id, detectors=['temporal'])
        body = read()
    return body


def _viewer_id():
    """Id of the signed-in user. Every alerts read and write is limited to
    what this user may see: system-wide alerts and alerts addressed to them
    (a monitoring rule's alerts go to its owner only - administrators
    included, nobody reads another user's rule alerts)."""
    from core.security.flask_ext import current_user

    return getattr(current_user(), 'id', None)


def register_notification_routes(app):
    """Register notification routes with the Flask app"""
    
    @app.route('/api/notifications', methods=['GET'])
    def get_notifications():
        """Get notifications with optional filters"""
        try:
            notification_service = get_notification_service()
            
            # 🔔 OPTIMIZATION: Flush pending notifications when accessed (ensures they're persisted)
            pending_count = notification_service.get_pending_count()
            if pending_count > 0:
                notification_service.flush_pending_notifications()
            
            # Get query parameters
            notification_type = request.args.get('type')
            priority = request.args.get('priority')
            unread_only = request.args.get('unread_only', 'false').lower() == 'true'
            limit = request.args.get('limit', 100, type=int)
            
            # Convert string to enum if provided
            type_enum = None
            if notification_type:
                try:
                    type_enum = NotificationType(notification_type)
                except ValueError:
                    return jsonify({
                        'success': False,
                        'error': f'Invalid notification type: {notification_type}'
                    }), 400
            
            priority_enum = None
            if priority:
                try:
                    priority_enum = NotificationPriority(priority)
                except ValueError:
                    return jsonify({
                        'success': False,
                        'error': f'Invalid priority: {priority}'
                    }), 400
            
            # Get notifications
            notifications = notification_service.get_notifications(
                notification_type=type_enum,
                priority=priority_enum,
                unread_only=unread_only,
                limit=limit,
                for_user_id=_viewer_id(),
            )
            
            # Single shared formatter keeps displayed titles/messages
            # accurate and identical across every endpoint (see
            # core/monitoring/notification_display.py).
            names = path_names([n.file_id for n in notifications])
            notifications_data = [display_payload(n, _, names) for n in notifications]

            return jsonify({
                'success': True,
                'notifications': notifications_data,
                'count': len(notifications_data)
            })
        
        except Exception as e:
            logger.error(f"Error getting notifications: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/upcoming', methods=['GET'])
    def get_upcoming_events():
        """Get upcoming events within specified days"""
        try:
            notification_service = get_notification_service()
            days_ahead = request.args.get('days', 30, type=int)
            
            upcoming = notification_service.get_upcoming_events(days_ahead=days_ahead,
                                                                for_user_id=_viewer_id())
            today = utc_today()
            names = path_names([n.file_id for n in upcoming])
            
            events_data = []
            for n in upcoming:
                title, message = format_title_message(n, _)
                events_data.append({
                    'id': n.id,
                    'title': title,
                    'message': message,
                    'file_id': n.file_id,
                    'file_name': names.get(n.file_id, (None, None))[0]
                                  or n.metadata.get('file_name'),
                    'file_path': names.get(n.file_id, (None, None))[1]
                                  or n.metadata.get('file_path'),
                    'event_date': n.event_date.isoformat() if n.event_date else None,
                    'days_until': (n.event_date - today).days if n.event_date else None,
                    'metadata': n.metadata,
                    'created_at': n.created_at.isoformat()
                })
            
            return jsonify({
                'success': True,
                'events': events_data,
                'count': len(events_data)
            })
        
        except Exception as e:
            logger.error(f"Error getting upcoming events: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/<int:notification_id>', methods=['GET'])
    def get_notification(notification_id):
        """Get a single notification by ID"""
        try:
            notification_service = get_notification_service()
            # Direct row lookup - no forced refresh, no linear scan of
            # an in-memory list capped at 10 000 entries.
            viewer = _viewer_id()
            row = execute_query(
                f"SELECT {ALERT_COLUMNS} FROM alerts"  # nosec B608 # ALERT_COLUMNS and visibility_clause() are constants; values are bound parameters
                f" WHERE id = %s AND dismissed = FALSE AND {visibility_clause()}",
                (notification_id, viewer),
                fetch="one"
            )
            notification = notification_from_row(row) if row else None

            if notification is None:
                # Fallback covers pending (temp-id) notifications in memory.
                notification = next(
                    (
                        n for n in notification_service.get_notifications(
                            limit=5000, for_user_id=viewer)
                        if n.id == notification_id
                    ),
                    None,
                )

            if not notification:
                return jsonify({
                    'success': False,
                    'error': 'Notification not found'
                }), 404

            payload = display_payload(notification, _,
                                      path_names([notification.file_id]))

            return jsonify({
                'success': True,
                'notification': payload
            })
        
        except Exception as e:
            logger.error(f"Error getting notification: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/<int:notification_id>/read', methods=['POST'])
    def mark_notification_read(notification_id):
        """Mark notification as read"""
        try:
            notification_service = get_notification_service()
            success = notification_service.mark_as_read(notification_id,
                                                        for_user_id=_viewer_id())
            
            if success:
                return jsonify({
                    'success': True,
                    'message': 'Notification marked as read'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Notification not found'
                }), 404
        
        except Exception as e:
            logger.error(f"Error marking notification as read: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/<int:notification_id>/dismiss', methods=['POST'])
    def dismiss_notification(notification_id):
        """Dismiss a notification"""
        try:
            notification_service = get_notification_service()
            success = notification_service.dismiss_notification(notification_id,
                                                                for_user_id=_viewer_id())
            
            if success:
                return jsonify({
                    'success': True,
                    'message': 'Notification dismissed'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Notification not found'
                }), 404
        
        except Exception as e:
            logger.error(f"Error dismissing notification: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/analyze-file/<int:file_id>', methods=['POST'])
    @write_access_required
    def analyze_file_for_events(file_id):
        """Create FUTURE_DATE notifications for one file from its stored
        temporal signals (content_signals). Content never analysed by the
        current detector version is analysed first (recorded as a
        re-detection run). ``reference_date`` (YYYY-MM-DD, optional) sets the
        clock; the default is today's UTC date and is echoed back."""
        try:
            if not _future_dates_enabled():
                return jsonify({'success': True, 'notifications_created': 0,
                                'notifications': [], 'skipped': 'future_dates_disabled'})
            try:
                reference_date = _reference_date_arg()
            except ValueError as exc:
                return jsonify({'success': False, 'error': str(exc)}), 400

            file_info = execute_query(
                """
                SELECT p.id, p.file_name, p.file_path, hc.hash_id
                FROM paths p JOIN hash_contexts hc ON hc.id = p.context_id
                WHERE p.id = %s
                """,
                (file_id,),
                fetch="one"
            )
            if not file_info:
                return jsonify({'success': False, 'error': 'File not found'}), 404
            file_id_db, file_name, file_path, hash_id = file_info

            body = _current_signals(hash_id, reference_date)
            if body['status'] == 'no_text':
                return jsonify({'success': False,
                                'error': 'File content not available'}), 400
            if body['status'] == 'failed':
                return jsonify({'success': False, 'error': 'Signal detection failed',
                                'signal_run': body['run']}), 422

            already = {row[0] for row in execute_query(
                "SELECT event_date FROM alerts WHERE type = %s AND file_id = %s",
                (NotificationType.FUTURE_DATE.value, file_id_db), fetch="all") or []}
            notification_service = get_notification_service()
            notifications = notification_service.analyze_file_for_future_events(
                file_id=file_id_db, file_name=file_name, file_path=file_path,
                signals=body['signals'], reference_date=reference_date,
                skip_dates=already)

            # Persist before responding so the payload carries real ids.
            notification_service.flush_pending_notifications()

            return jsonify({
                'success': True,
                'reference_date': reference_date.isoformat(),
                'signal_status': body['status'],
                'truncated': body['status'] == 'truncated',
                'notifications_created': len(notifications),
                'notifications': [
                    {
                        'id': n.id,
                        'type': n.type.value,
                        'title': n.title,
                        'event_date': n.event_date.isoformat() if n.event_date else None
                    }
                    for n in notifications
                ]
            })

        except Exception as e:
            logger.error(f"Error analyzing file for events: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)

    @app.route('/api/notifications/refresh', methods=['POST'])
    def refresh_notifications():
        """Refresh notifications from database"""
        try:
            notification_service = get_notification_service()
            count = notification_service.refresh_notifications()

            return jsonify({
                'success': True,
                'message': 'Notifications refreshed successfully',
                'count': count
            })
        except Exception as e:
            logger.error(f"Error refreshing notifications: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    # The sidebar badge reads this on every page load and then every 30 s per
    # open tab (base-page-handler.js): five idle tabs alone reach the 600/hour
    # default, after which the badge - and each page boot - got 429 (live
    # browser smoke through nginx). An interactive read; see
    # INTERACTIVE_READ_LIMIT.
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    @app.route('/api/notifications/stats', methods=['GET'])
    def get_notification_stats():
        """Get notification statistics"""
        try:
            notification_service = get_notification_service()
            
            # Exact SQL aggregates (COUNT/GROUP BY).  No forced refresh,
            # no in-memory scan: totals stay correct at any table size and
            # the 30-second stats poll stops racing concurrent refreshes.
            stats = notification_service.get_stats(for_user_id=_viewer_id())

            return jsonify({
                'success': True,
                'stats': stats
            })
        
        except Exception as e:
            logger.error(f"Error getting notification stats: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/scan', methods=['POST'])
    @write_access_required
    def scan_for_notifications():
        """Scan for duplicate files and files with future dates, then create notifications"""
        try:
            from core.monitoring.notification_service import (
                get_notification_service,
                NotificationType,
                NotificationPriority
            )

            notification_service = get_notification_service()
            # Collected as (kind, notification, extra); the JSON response is
            # built AFTER the flush so it reports the real database ids, not
            # the temporary negative queue ids.
            created_items = []
            
            # 1. Find duplicate files (same hash, different paths)
            logger.info("Scanning for duplicate files...")
            # First, find hashes with multiple files (group by hash only)
            duplicate_query = """
                SELECT 
                    h.hash,
                    COUNT(DISTINCT p.id) as file_count,
                    MIN(p.id) as primary_file_id
                FROM hashs h
                JOIN hash_contexts hc ON hc.hash_id = h.id
                JOIN paths p ON p.context_id = hc.id
                GROUP BY h.hash
                HAVING COUNT(DISTINCT p.id) > 1
                ORDER BY file_count DESC
                LIMIT 1000
            """
            
            duplicate_results = execute_query(duplicate_query, None, fetch="all")

            # One query loads every hash key that already has a duplicate
            # notification - including DISMISSED ones, so a dismissed
            # duplicate is not resurrected on the next scan.  Set membership
            # replaces the previous per-hash existence probe (N+1 queries).
            existing_hash_rows = execute_query(
                """
                SELECT metadata->>'hash'
                FROM alerts
                WHERE type = %s
                  AND metadata->>'hash' IS NOT NULL
                """,
                (NotificationType.SIMILAR_FILES.value,),
                fetch="all"
            ) or []
            existing_hashes = {r[0] for r in existing_hash_rows}

            duplicate_count = 0
            for row in duplicate_results or []:
                hash_value, file_count, primary_file_id = row
                
                # Get all files with this hash
                files_query = """
                    SELECT p.id, p.file_name, p.file_path
                    FROM paths p
                    INNER JOIN hash_contexts hc ON p.context_id = hc.id JOIN hashs h ON hc.hash_id = h.id
                    WHERE h.hash = %s
                    ORDER BY p.id ASC
                """
                files_result = execute_query(files_query, (hash_value,), fetch="all")
                
                if not files_result or len(files_result) < 2:
                    continue
                
                # Extract file information
                file_ids = [f[0] for f in files_result]
                file_names = [f[1] or 'Unknown' for f in files_result]
                file_paths = [f[2] or '' for f in files_result]
                
                # Use the first file as the primary reference
                primary_file_name = file_names[0] if file_names else 'Unknown'
                primary_file_path = file_paths[0] if file_paths else ''
                
                # Skip hashes that already produced a notification (in any
                # state - dismissed notifications stay dismissed)
                if hash_value not in existing_hashes:
                    from core.monitoring.notification_service import Notification
                    
                    # Create a readable message with file names
                    file_names_display = file_names[:5]
                    if len(file_names) > 5:
                        file_names_display.append(f'... and {len(file_names) - 5} more')
                    
                    notification = Notification(
                        id=None,
                        type=NotificationType.SIMILAR_FILES,
                        priority=NotificationPriority.MEDIUM,
                        title=f"Duplicate Files Detected: {primary_file_name}",
                        message=f"Found {file_count} duplicate file(s) with the same hash. Files: {', '.join(file_names_display)}",
                        file_id=primary_file_id,
                        event_date=None,
                        metadata={
                            'hash': hash_value,
                            'duplicate_count': file_count,
                            'file_ids': file_ids,
                            'file_names': file_names,
                            'file_paths': file_paths,
                            'file_name': primary_file_name,
                            'file_path': primary_file_path
                        },
                        created_at=datetime.now()
                    )
                    notification = notification_service._save_notification(notification)
                    created_items.append(('duplicate', notification, None))
                    existing_hashes.add(hash_value)
                    duplicate_count += 1
            
            # 2. Find files with future dates - from their stored temporal signals.
            # Reads content_signals (Phase 1 detector) - no text is re-parsed
            # here and no date is estimated from the wall clock.
            from core.detection.temporal_intel import (
                DETECTOR_NAME, DETECTOR_VERSION, SIGNAL_DATE, SIGNAL_RELATIVE,
            )

            dated = [SIGNAL_DATE, SIGNAL_RELATIVE]

            today = datetime.now(timezone.utc).date()
            future_date_count = 0
            processed_files = 0
            contents_without_current_signals = None
            if _future_dates_enabled():
                logger.info("Scanning stored signals for future dates...")
                future_rows = execute_query(
                    """
                    SELECT p.id, p.file_name, p.file_path, s.signal_type,
                           s.date_from, s.date_to, s.surface, s.value, s.language,
                           s.calendar, s.resolution, s.char_start, s.char_end,
                           s.text_orientation, s.evidence, s.detector_ver,
                           s.method, s.confidence, s.confidence_basis, s.evidence_sentence,
                           (SELECT array_agg(DISTINCT f.date_from ORDER BY f.date_from)
                              FROM content_signals f
                             WHERE f.hash_id = hc.hash_id AND f.signal_type = ANY(%s)
                               AND f.date_from > %s) AS future_dates
                    FROM paths p
                    JOIN hash_contexts hc ON hc.id = p.context_id
                    JOIN LATERAL (
                        SELECT cs.* FROM content_signals cs
                        WHERE cs.hash_id = hc.hash_id AND cs.signal_type = ANY(%s)
                          AND cs.date_from > %s
                        ORDER BY cs.date_from, cs.char_start, cs.id
                        LIMIT 1
                    ) s ON TRUE
                    WHERE p.file_status = 'Read'
                    ORDER BY p.id DESC
                    LIMIT 5000
                    """,
                    (dated, today, dated, today),
                    fetch="all"
                ) or []
                counts = execute_query(
                    """
                    SELECT
                      count(*) FILTER (WHERE r.hash_id IS NOT NULL),
                      count(DISTINCT hc.hash_id) FILTER (WHERE r.hash_id IS NULL)
                    FROM paths p
                    JOIN hash_contexts hc ON hc.id = p.context_id
                    LEFT JOIN content_signal_runs r
                      ON r.hash_id = hc.hash_id AND r.detector = %s
                     AND r.detector_ver = %s AND r.status <> 'failed'
                    WHERE p.file_status = 'Read'
                    """,
                    (DETECTOR_NAME, DETECTOR_VERSION),
                    fetch="one"
                )
                processed_files, contents_without_current_signals = counts

                candidate_ids = [row[0] for row in future_rows]
                existing_future_pairs = set()
                if candidate_ids:
                    for pair_row in execute_query(
                        """
                        SELECT file_id, event_date
                        FROM alerts
                        WHERE type = %s
                          AND file_id = ANY(%s)
                        """,
                        (NotificationType.FUTURE_DATE.value, candidate_ids),
                        fetch="all"
                    ) or []:
                        existing_future_pairs.add((pair_row[0], pair_row[1]))

                for row in future_rows:
                    (file_id, file_name, file_path, signal_type, date_from, date_to, surface, value,
                     language, calendar, resolution, char_start, char_end,
                     text_orientation, evidence, detector_ver, method, confidence,
                     confidence_basis, evidence_sentence, future_dates) = row
                    # Skip (file, date) pairs that already produced a
                    # notification (in any state, dismissed included).
                    if (file_id, date_from) in existing_future_pairs:
                        continue
                    notification = notification_service.create_future_date_notification(
                        file_id=file_id, file_name=file_name, file_path=file_path,
                        signal={
                            'signal_type': signal_type, 'date_from': date_from.isoformat(),
                            'date_to': date_to.isoformat(), 'surface': surface,
                            'value': value, 'language': language, 'calendar': calendar,
                            'resolution': resolution, 'char_start': char_start,
                            'char_end': char_end, 'text_orientation': text_orientation,
                            'evidence': evidence or {}, 'detector_ver': detector_ver,
                            'method': method, 'confidence': confidence,
                            'confidence_basis': confidence_basis,
                            'sentence': evidence_sentence,
                        },
                        reference_date=today)
                    # Store file_name and file_path in metadata for display
                    notification.metadata['file_name'] = file_name
                    notification.metadata['file_path'] = file_path
                    notification.metadata['future_dates'] = [
                        d.isoformat() for d in (future_dates or [])]
                    created_items.append(('future_date', notification, date_from.isoformat()))
                    existing_future_pairs.add((file_id, date_from))
                    future_date_count += 1

            # Persist, then build the response with real ids.
            notification_service.flush_pending_notifications()

            # Refresh notifications cache to ensure new notifications are available
            notification_service.refresh_notifications()

            notifications_created = []
            for kind, created_notification, extra in created_items:
                entry = {
                    'type': kind,
                    'id': created_notification.id,
                    'title': created_notification.title
                }
                if extra is not None:
                    entry['date'] = extra
                notifications_created.append(entry)

            logger.info(f"Scan completed: {duplicate_count} duplicates, {future_date_count} future dates, {processed_files} files processed")
            
            return jsonify({
                'success': True,
                'message': f'Scan completed. Created {len(notifications_created)} new notification(s)',
                'duplicates_found': duplicate_count,
                'future_dates_found': future_date_count,
                'files_processed': processed_files,
                # Content with no current-version signal run was NOT evaluated
                # for future dates (None = future-date scanning is disabled).
                # Run POST /api/signals/redetect to analyse it.
                'contents_without_current_signals': contents_without_current_signals,
                'reference_date': today.isoformat(),
                'notifications_created': notifications_created
            })
        
        except Exception as e:
            logger.error(f"Error scanning for notifications: {e}", exc_info=True)
            return jsonify({
                'success': False, 
                'error': client_safe_message(e, subsystem='Api.routes.notifications'),
                'message': f'Error during scan: {str(e)}'
            }), 500

