"""
Concurrency Monitoring Routes
Provides real-time monitoring of the four concurrency managers:
- ThreadManager
- ProcessManager
- AsyncManager
- MultiprocessingManager
"""

from flask import Blueprint, render_template, jsonify, request
import logging
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = str(Path(__file__).parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from concurrency import hub
from core.errors import client_error, client_safe_message

logger = logging.getLogger(__name__)

# Create blueprint
concurrency_bp = Blueprint('concurrency', __name__, url_prefix='/concurrency')


#: The two managers that need ``multiprocessing.Manager()`` cannot exist without
#: it; the thread and async managers do not need one at all. When the start
#: fails on a host (see ``concurrency.manager_start``) the honest report is a
#: thread-only one, not an error stub: the figures below are real, the two
#: missing cards say why, and an operator can still see what the application is
#: doing. The reason is a fixed sentence - never the exception text - because it
#: is rendered in the dashboard.
MANAGER_UNAVAILABLE_CODE = "multiprocessing_unavailable"
MANAGER_UNAVAILABLE_REASON = (
    "The multiprocessing manager could not be started on this host, so process "
    "and worker-pool figures are not available. Thread and async figures are "
    "live, and ingestion is unaffected: it runs on the thread pool."
)


def _unavailable() -> dict:
    """The card payload for a manager this host cannot build."""
    return {
        "status": "unavailable",
        "mode": "threads-only",
        "reason_code": MANAGER_UNAVAILABLE_CODE,
        "reason": MANAGER_UNAVAILABLE_REASON,
        "active_processes": 0,
        "total_processes": 0,
    }


def _worker_capacity() -> dict:
    """What this host can actually run, without starting a manager.

    The resource coordinator is what the ingestion path asks for a safe worker
    count, so reporting its answer is the same number an operator would see in a
    run - it is measured, not guessed.
    """
    capacity = {}
    try:
        from core.resource_coordinator import get_safe_worker_count

        capacity["recommended_workers"] = get_safe_worker_count(4)
    except Exception:  # the coordinator is optional
        pass
    try:
        import os

        capacity["cpu_count"] = os.cpu_count()
    except Exception:
        pass
    return capacity


@concurrency_bp.route('/')
def dashboard():
    """Concurrency monitoring dashboard page"""
    return render_template('concurrency/dashboard.html')


@concurrency_bp.route('/api/metrics')
def get_metrics():
    """Get metrics from all four concurrency managers"""
    try:
        metrics = {
            'thread_manager': {},
            'process_manager': {},
            'async_manager': {},
            'pool_manager': {}
        }
        unavailable = []
        
        # Thread Manager metrics
        try:
            thread_mgr = hub.thread_mgr
            stats = thread_mgr.get_statistics()
            metrics['thread_manager'] = {
                'active_threads': stats.get('running', 0),
                'total_threads': stats.get('total', 0),
                'paused': stats.get('paused', 0),
                'stopped': stats.get('stopped', 0),
                'completed': stats.get('completed', 0),
                'errors': stats.get('errors', 0),
                'by_priority': stats.get('by_priority', {}),
                'status': 'active' if hub.is_initialized('thread') else 'inactive'
            }
        except Exception as e:
            logger.warning(f"Error getting thread manager metrics: {e}")
            metrics['thread_manager'] = {'status': 'error', 'error': client_safe_message(e, subsystem='Api.routes.concurrency')}
        
        # Process Manager metrics
        try:
            process_mgr = hub.process_mgr
            stats = process_mgr.get_statistics()
            metrics['process_manager'] = {
                'active_processes': stats.get('running', 0),
                'total_processes': stats.get('total', 0),
                'stopped': stats.get('stopped', 0),
                'completed': stats.get('completed', 0),
                'errors': stats.get('errors', 0),
                'total_memory_mb': stats.get('total_memory_mb', 0.0),
                'avg_cpu_percent': stats.get('avg_cpu_percent', 0.0),
                'by_priority': stats.get('by_priority', {}),
                'status': 'active' if hub.is_initialized('process') else 'inactive'
            }
        except Exception as e:
            logger.warning("Process manager metrics unavailable: %s", e)
            metrics['process_manager'] = _unavailable()
            unavailable.append('process_manager')
        
        # Async Manager metrics
        try:
            async_mgr = hub.async_mgr
            stats = async_mgr.get_statistics()
            metrics['async_manager'] = {
                'active_tasks': stats.get('running', 0),
                'total_tasks': stats.get('total', 0),
                'paused': stats.get('paused', 0),
                'stopped': stats.get('stopped', 0),
                'completed': stats.get('completed', 0),
                'errors': stats.get('errors', 0),
                'total_runtime': stats.get('total_runtime', 0.0),
                'by_priority': stats.get('by_priority', {}),
                'status': 'active' if hub.is_initialized('async') else 'inactive'
            }
        except Exception as e:
            logger.warning(f"Error getting async manager metrics: {e}")
            metrics['async_manager'] = {'status': 'error', 'error': client_safe_message(e, subsystem='Api.routes.concurrency')}
        
        # Pool Manager (MultiprocessingManager) metrics
        try:
            pool_mgr = hub.pool_mgr
            stats = pool_mgr.get_statistics()
            metrics['pool_manager'] = {
                'total_pools': stats.get('total_pools', 0),
                'total_workers': stats.get('total_workers', 0),
                'total_submitted': stats.get('total_submitted', 0),
                'total_completed': stats.get('total_completed', 0),
                'total_failed': stats.get('total_failed', 0),
                'total_pending': stats.get('total_pending', 0),
                'total_memory_mb': stats.get('total_memory_mb', 0.0),
                'pools': stats.get('pools', {}),
                'status': 'active' if hub.is_initialized('pool') else 'inactive'
            }
        except Exception as e:
            logger.warning("Pool manager metrics unavailable: %s", e)
            metrics['pool_manager'] = _unavailable()
            unavailable.append('pool_manager')
        
        degraded = bool(unavailable)
        if degraded:
            # The measured numbers an operator needs when the managers are gone.
            metrics['worker_capacity'] = _worker_capacity()
        return jsonify({
            'success': True,
            'mode': 'threads-only' if degraded else 'full',
            'degraded': degraded,
            'unavailable': sorted(unavailable),
            'metrics': metrics,
            'timestamp': str(datetime.now().isoformat())
        })
    except Exception as e:
        logger.error(f"Error getting concurrency metrics: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.concurrency', success_key='success', status=500)


@concurrency_bp.route('/api/threads')
def get_threads():
    """Get detailed thread information"""
    try:
        thread_mgr = hub.thread_mgr
        
        thread_list = []
        # Access threads via the threads dictionary
        for thread_id, managed_thread in thread_mgr.threads.items():
            metrics = managed_thread.metrics
            thread_list.append({
                'id': thread_id,
                'name': metrics.name,
                'state': metrics.state.value,
                'priority': metrics.priority.name,
                'created_at': metrics.created_at.isoformat() if metrics.created_at else None,
                'started_at': metrics.started_at.isoformat() if metrics.started_at else None,
                'completed_at': metrics.completed_at.isoformat() if metrics.completed_at else None,
                'error_msg': metrics.error_msg,
                'execution_count': metrics.execution_count,
                'memory_usage_mb': metrics.memory_usage_mb
            })
        
        return jsonify({
            'success': True,
            'threads': thread_list,
            'count': len(thread_list)
        })
    except Exception as e:
        logger.error(f"Error getting threads: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.concurrency', success_key='success', status=500)


@concurrency_bp.route('/api/processes')
def get_processes():
    """Get detailed process information"""
    try:
        process_mgr = hub.process_mgr
        
        process_list = []
        # Access processes via the processes dictionary
        for process_id, managed_process in process_mgr.processes.items():
            metrics = managed_process.metrics
            process_list.append({
                'id': process_id,
                'name': metrics.name,
                'state': metrics.state.value,
                'priority': metrics.priority.name,
                'pid': metrics.pid,
                'created_at': metrics.created_at.isoformat() if metrics.created_at else None,
                'started_at': metrics.started_at.isoformat() if metrics.started_at else None,
                'completed_at': metrics.completed_at.isoformat() if metrics.completed_at else None,
                'error_msg': metrics.error_msg,
                'cpu_percent': metrics.cpu_percent,
                'memory_mb': metrics.memory_mb
            })
        
        return jsonify({
            'success': True,
            'processes': process_list,
            'count': len(process_list)
        })
    except Exception as e:
        logger.warning("Process list unavailable: %s", e)
        return jsonify({
            'success': True,
            'available': False,
            'reason_code': MANAGER_UNAVAILABLE_CODE,
            'reason': MANAGER_UNAVAILABLE_REASON,
            'processes': [],
            'count': 0,
        })


@concurrency_bp.route('/api/async-tasks')
def get_async_tasks():
    """Get detailed async task information"""
    try:
        async_mgr = hub.async_mgr
        
        task_list = []
        # Access tasks via the tasks dictionary
        for task_id, managed_task in async_mgr.tasks.items():
            metrics = managed_task.metrics
            task_list.append({
                'id': task_id,
                'name': metrics.name,
                'state': metrics.state.value,
                'priority': metrics.priority.name,
                'created_at': metrics.created_at.isoformat() if metrics.created_at else None,
                'started_at': metrics.started_at.isoformat() if metrics.started_at else None,
                'completed_at': metrics.completed_at.isoformat() if metrics.completed_at else None,
                'error_msg': metrics.error_msg,
                'execution_count': metrics.execution_count,
                'runtime_seconds': metrics.runtime_seconds
            })
        
        return jsonify({
            'success': True,
            'tasks': task_list,
            'count': len(task_list)
        })
    except Exception as e:
        logger.error(f"Error getting async tasks: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.concurrency', success_key='success', status=500)


@concurrency_bp.route('/api/pools')
def get_pools():
    """Get detailed pool information"""
    try:
        pool_mgr = hub.pool_mgr
        
        pool_list = []
        # Access pools via the pools dictionary
        for pool_id, managed_pool in pool_mgr.pools.items():
            metrics = managed_pool.metrics
            pool_list.append({
                'id': pool_id,
                'name': metrics.name,
                'state': metrics.state.value,
                'worker_count': metrics.worker_count,
                'tasks_submitted': metrics.tasks_submitted,
                'tasks_completed': metrics.tasks_completed,
                'tasks_failed': metrics.tasks_failed,
                'pending_tasks': len(managed_pool.pending_tasks),
                'created_at': metrics.created_at.isoformat() if metrics.created_at else None,
                'total_memory_mb': metrics.total_memory_mb
            })
        
        return jsonify({
            'success': True,
            'pools': pool_list,
            'count': len(pool_list)
        })
    except Exception as e:
        logger.warning("Pool list unavailable: %s", e)
        return jsonify({
            'success': True,
            'available': False,
            'reason_code': MANAGER_UNAVAILABLE_CODE,
            'reason': MANAGER_UNAVAILABLE_REASON,
            'pools': [],
            'count': 0,
        })

