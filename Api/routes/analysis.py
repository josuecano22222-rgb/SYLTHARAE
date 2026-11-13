"""
Analysis routes
"""

from flask import render_template, request, jsonify
from Api.utils import execute_query, select_info_sources, select_info_sides
from Api.services.analysis_stats import (
    analysis_measurements,
    unavailable_measurements as unavailable_analysis_measurements,
)

import logging

from core.errors import client_error
from core.security import login_required
import os
from datetime import datetime

logger = logging.getLogger(__name__)


def get_recent_jobs(execute_query, limit: int = 5):
    """Recent jobs, with the outcome of each - the page's history panel.

    Reported as stored: the counts come from the job's own statistics, and a
    job with no timings shows no duration rather than an assumed one.
    """
    try:
        rows = execute_query(
            """
            SELECT job_id, job_type, status, started_at, completed_at,
                   COALESCE((stats->>'files_completed')::int, 0) AS files_completed,
                   COALESCE((stats->>'files_failed')::int, 0) AS files_failed,
                   COALESCE((stats->>'files_discovered')::int, 0) AS files_discovered
            FROM jobs
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (int(limit),),
            fetch="all",
        ) or []
    except Exception as exc:
        logger.warning("Could not load recent jobs for the analysis page: %s", exc)
        return []

    jobs = []
    for row in rows:
        (job_id, job_type, status, started_at, completed_at,
         completed, failed, discovered) = list(row)[:8]
        duration = None
        if started_at and completed_at and completed_at > started_at:
            duration = (completed_at - started_at).total_seconds()
        jobs.append({
            "job_id": job_id,
            "job_type": job_type,
            "status": status,
            "created_at": started_at or completed_at,
            "duration_seconds": duration,
            "files_completed": completed or 0,
            "files_failed": failed or 0,
            "files_discovered": discovered or 0,
            "success_rate": (100.0 * completed / (completed + failed)) if (completed or failed) else None,
        })
    return jobs

def register_analysis_routes(app):
    """Register analysis routes with the Flask app"""
    
    @app.route('/analysis/batch/process', methods=['POST'])
    @login_required
    def analysis_batch_process():
        """Process the selected files through the real ingestion pipeline.

        The batch page queues files that have no content yet (or that failed
        earlier); each one is re-run through the same single-file entry point
        the upload path uses. Under the content-identity model this is
        idempotent: a file whose content is already stored resolves to its
        existing occurrence instead of duplicating anything.
        """
        try:
            data = request.get_json(silent=True) or {}
            file_ids = data.get('file_ids')
            if not isinstance(file_ids, list) or not file_ids:
                return jsonify({'success': False,
                                'error': 'file_ids (non-empty list) is required'}), 400
            try:
                file_ids = [int(fid) for fid in file_ids][:200]
            except (TypeError, ValueError):
                return jsonify({'success': False,
                                'error': 'file_ids must be integers'}), 400

            from pipeline.integrated_reader import IntegratedFileReader

            rows = execute_query("""
                SELECT p.id, p.file_path, p.file_name,
                       COALESCE(s.name, '') AS source_name,
                       COALESCE(si.name, '') AS side_name
                FROM paths p
                JOIN hash_contexts hc ON hc.id = p.context_id
                LEFT JOIN sources s ON s.id = hc.source_id
                LEFT JOIN sides si ON si.id = hc.side_id
                WHERE p.id = ANY(%s)
            """, (file_ids,), fetch="all") or []
            by_id = {row[0]: row for row in rows}

            results = []
            succeeded = 0
            with IntegratedFileReader(max_workers=1, enable_storage=True) as reader:
                for fid in file_ids:
                    row = by_id.get(fid)
                    if not row:
                        results.append({'file_id': fid, 'status': 'error',
                                        'error': 'File not found'})
                        continue
                    _, file_path, file_name, source_name, side_name = list(row)[:5]
                    try:
                        reader.storage_source = source_name
                        reader.storage_side = side_name
                        outcome = reader.process_single_file(file_path) or {}
                        path_id = outcome.get('database_path_id')
                        ok = bool(path_id)
                        results.append({
                            'file_id': fid,
                            'path_id': path_id,
                            'status': 'success' if ok else 'error',
                            'duplicate': bool(outcome.get('duplicate')),
                        })
                        if ok:
                            succeeded += 1
                    except Exception as exc:
                        logger.error("Batch analysis: processing %s (%s) failed: %s",
                                     fid, file_name, exc, exc_info=True)
                        results.append({'file_id': fid, 'status': 'error',
                                        'error': 'Processing failed'})

            return jsonify({
                'success': True,
                'results': results,
                'processed': len(results),
                'succeeded': succeeded,
                'failed': len(results) - succeeded,
            })
        except Exception as e:
            logger.error(f"Error in batch analysis processing: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.analysis',
                                success_key='success', status=500)
    
    @app.route('/analysis/batch')
    def analysis_batch():
        """Batch Analysis page"""
        try:
            # Get unanalyzed files (files without content)
            unanalyzed = execute_query("""
                SELECT p.id, p.file_name, p.file_size, p.file_type,
                       COALESCE(s.name, 'Unknown') as source_name,
                       COALESCE(si.name, 'Unknown') as side_name
                FROM paths p
                LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
                LEFT JOIN sources s ON hc.source_id = s.id
                LEFT JOIN sides si ON hc.side_id = si.id
                LEFT JOIN contents c ON c.hash_id = hc.hash_id
                WHERE c.id IS NULL
                ORDER BY p.date_creation DESC
                LIMIT 100
            """) or []
            
            # Get failed files (files with error_message or files that failed to save)
            failed_files = execute_query("""
                SELECT p.id, p.file_name, p.file_size, p.file_type,
                       COALESCE(s.name, 'Unknown') as source_name,
                       COALESCE(si.name, 'Unknown') as side_name,
                       p.error_message, p.file_path, p.file_status
                FROM paths p
                LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
                LEFT JOIN sources s ON hc.source_id = s.id
                LEFT JOIN sides si ON hc.side_id = si.id
                WHERE (p.error_message IS NOT NULL AND p.error_message != '')
                   OR (p.file_status = 'Unread' AND p.date_creation < CURRENT_DATE - INTERVAL '1 day')
                ORDER BY p.date_creation DESC
                LIMIT 100
            """) or []
            
            # Get sources and sides as dictionaries
            sources = select_info_sources() or {}
            sides = select_info_sides() or {}
            
            # Get recent jobs for the history panel. The panel used to be three
            # hard-coded examples ("Batch #5 - 98.5% success"); showing invented
            # runs beside real ones is indistinguishable from real history, so
            # it now lists the jobs the system actually recorded.
            recent_jobs = get_recent_jobs(execute_query, limit=5)
            
            # Processing figures. Every one of these is a Measurement: a value
            # with a recorded basis, or an explicit "no measurement available".
            # Nothing here is ever a stand-in constant.
            queue_size = len(unanalyzed)
            stats = analysis_measurements(execute_query, queue_size=queue_size)
            
            return render_template('Analysis/analysis_batch.html',
                                 stats=stats,
                                 success_rate=stats['success_rate'],
                                 avg_processing_time=stats['avg_processing_time'],
                                 estimated_time=stats['estimated_time'],
                                 queue_size=queue_size,
                                 recent_jobs=recent_jobs,
                                 unanalyzed=unanalyzed,
                                 failed_files=failed_files,
                                 sources=sources,
                                 sides=sides)
        except Exception as e:
            # The page still renders, but with measurements that say they are
            # unavailable - never with invented values that look like a reading.
            logger.error(f"Error loading batch analysis page: {e}", exc_info=True)
            fallback = unavailable_analysis_measurements(
                "The processing figures could not be read for this request"
            )
            return render_template('Analysis/analysis_batch.html',
                                 stats=fallback,
                                 success_rate=fallback['success_rate'],
                                 avg_processing_time=fallback['avg_processing_time'],
                                 estimated_time=fallback['estimated_time'],
                                 queue_size=0,
                                 recent_jobs=[],
                                 unanalyzed=[],
                                 failed_files=[],
                                 sources={},
                                 sides={})
    
    @app.route('/api/analysis/retry/<int:file_id>', methods=['POST'])
    def retry_file_analysis(file_id):
        """Retry reading and saving a failed file"""
        try:
            # Get file information from database
            file_info = execute_query("""
                SELECT p.file_path, p.file_name, hc.source_id, hc.side_id,
                       s.name as source_name, si.name as side_name
                FROM paths p
                LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
                LEFT JOIN sources s ON hc.source_id = s.id
                LEFT JOIN sides si ON hc.side_id = si.id
                WHERE p.id = %s
            """, (file_id,), fetch="one")
            
            if not file_info:
                return jsonify({
                    'success': False,
                    'error': 'File not found'
                }), 404
            
            file_path = file_info[0]
            source_name = file_info[4] or 'Unknown'
            side_name = file_info[5] or 'Unknown'
            
            # Check if file exists
            if not os.path.exists(file_path):
                return jsonify({
                    'success': False,
                    'error': f'File not found on disk: {file_path}'
                }), 404
            
            # Process file using IntegratedFileReader
            from pipeline.integrated_reader import IntegratedFileReader
            from settings import get_processing_config
            
            processing_cfg = get_processing_config()
            
            with IntegratedFileReader(
                max_workers=processing_cfg.max_workers,
                enable_monitoring=processing_cfg.enable_monitoring,
                enable_storage=True,
                storage_source=source_name,
                storage_side=side_name
            ) as reader:
                result = reader.process_single_file(file_path)
                
                # Get storage statistics
                storage_stats = reader.get_storage_statistics() if reader.enable_storage else None
                
                if result:
                    stored = storage_stats and storage_stats.get('completed', 0) > 0
                    
                    # Clear error message if successful
                    if stored:
                        execute_query(
                            "UPDATE paths SET error_message = NULL WHERE id = %s",
                            (file_id,),
                            fetch=False
                        )
                    
                    return jsonify({
                        'success': True,
                        'stored': stored,
                        'message': 'File processed successfully' if stored else 'File processed but not stored'
                    })
                else:
                    return jsonify({
                        'success': False,
                        'error': 'Failed to process file'
                    }), 500
                    
        except Exception as e:
            logger.error(f"Error retrying SYLTHARAE for file_id {file_id}: {e}", exc_info=True)
            # Provide user-friendly error message
            error_message = str(e)
            if 'not found' in error_message.lower():
                user_message = f"File {file_id} was not found. It may have been deleted or moved."
            elif 'permission' in error_message.lower():
                user_message = f"Permission denied accessing file {file_id}. Please check file permissions."
            elif 'corrupted' in error_message.lower() or 'invalid' in error_message.lower():
                user_message = f"File {file_id} appears to be corrupted or in an unsupported format."
            else:
                user_message = f"An error occurred while processing file {file_id}. Please try again or contact support."
            
            # The reader gets the sentence the classification selected; the
            # exception text is logged with a correlation id instead of being
            # returned whenever debug logging happens to be on.
            return client_error(e, subsystem="analysis",
                                public_message=user_message,
                                success_key="success")
    
