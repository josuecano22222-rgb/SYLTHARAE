"""
MultiprocessingManager - Worker pool management
Provides full lifecycle control for multiprocessing pools with priority, monitoring, and task distribution.

"""
import multiprocessing as mp
from multiprocessing import Pool
import time
import traceback
from dataclasses import dataclass
from typing import Dict, List, Callable, Any, Optional, Tuple
from enum import Enum
from datetime import datetime
from queue import PriorityQueue
import psutil
import sys
from pathlib import Path

# Set up path BEFORE importing core.path_utils to avoid import conflicts
# This ensures we import from the correct location (core.path_utils, not managers.read.core.path_utils)
try:
    # Calculate project root manually first
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent  # managers/concurrency/ -> project root
    project_root_str = str(project_root)
    
    # Add to path if not already there
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
except Exception:
    # If path setup fails, try to continue anyway
    pass

# Now we can safely import from core.path_utils
try:
    from core.path_utils import setup_path
    # Call setup_path to ensure it's set up properly
    setup_path(__file__, levels_up=2)
except ImportError:
    # If import fails, we've already set up the path manually above
    # Define a fallback setup_path function
    def setup_path(file_path=None, levels_up=0):
        """Fallback setup_path if import fails"""
        try:
            if file_path is None:
                file_path = __file__
            current_file = Path(file_path).resolve()
            project_root = current_file.parent
            for _ in range(levels_up):
                project_root = project_root.parent
            project_root_str = str(project_root)
            if project_root_str not in sys.path:
                sys.path.insert(0, project_root_str)
            return project_root
        except Exception:
            return Path.cwd()
except Exception:
    # If setup_path call fails, continue anyway
    pass


# Standalone monitoring function (must be at module level for pickling)
def _pool_monitor_standalone(monitor_active, pool_data, interval, monitor_queue):
    """Standalone pool monitoring"""
    try:
        import sys
        from pathlib import Path
        project_root = Path(__file__).parent.parent.parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
    except Exception:
        pass
    
    while monitor_active.value == 1:
        for pool_id, info in pool_data.items():
            pending = info.get('pending', 0)
            worker_count = info.get('worker_count', 1)
            
            if pending > worker_count * 2:
                msg = f"[Monitor] Pool {pool_id} has {pending} pending tasks"
                print(msg)
                try:
                    monitor_queue.put({'type': 'alert', 'pool_id': pool_id, 'pending': pending})
                except:
                    pass
        
        time.sleep(interval)


class PoolPriority(Enum):
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4


class PoolState(Enum):
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class PoolMetrics:
    pool_id: str
    name: str
    state: PoolState
    worker_count: int
    created_at: datetime
    tasks_submitted: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_memory_mb: float = 0.0


@dataclass
class TaskResult:
    task_id: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    duration_seconds: float = 0.0


class ManagedPool:
    """Worker pool wrapper with monitoring"""
    
    def __init__(self, pool_id: str, name: str, worker_count: int,
                 priority=PoolPriority.NORMAL):
        self.pool_id = pool_id
        self.name = name
        self.worker_count = worker_count
        self.priority = priority
        
        # Create pool
        self.pool = Pool(processes=worker_count)
        
        # Task tracking
        self.pending_tasks: Dict[str, mp.pool.AsyncResult] = {}
        self.results: Dict[str, TaskResult] = {}
        
        # Metrics
        self.metrics = PoolMetrics(
            pool_id=pool_id,
            name=name,
            state=PoolState.RUNNING,
            worker_count=worker_count,
            created_at=datetime.now()
        )
    
    def submit_task(self, task_id: str, func: Callable, args=(), kwargs=None) -> str:
        """Submit task to pool"""
        start_time = time.time()
        
        try:
            async_result = self.pool.apply_async(func, args, kwargs or {})
            self.pending_tasks[task_id] = async_result
            self.metrics.tasks_submitted += 1
            return task_id
        except Exception as e:
            self.metrics.tasks_failed += 1
            self.results[task_id] = TaskResult(
                task_id=task_id,
                success=False,
                error=str(e),
                duration_seconds=time.time() - start_time
            )
            raise
    
    def get_result(self, task_id: str, timeout: float = 0.1) -> Optional[TaskResult]:
        """Get task result if ready"""
        if task_id in self.results:
            return self.results[task_id]
        
        if task_id in self.pending_tasks:
            async_result = self.pending_tasks[task_id]
            
            if async_result.ready():
                try:
                    result = async_result.get(timeout=timeout)
                    self.metrics.tasks_completed += 1
                    
                    task_result = TaskResult(
                        task_id=task_id,
                        success=True,
                        result=result
                    )
                    self.results[task_id] = task_result
                    del self.pending_tasks[task_id]
                    
                    return task_result
                    
                except Exception as e:
                    self.metrics.tasks_failed += 1
                    
                    task_result = TaskResult(
                        task_id=task_id,
                        success=False,
                        error=str(e)
                    )
                    self.results[task_id] = task_result
                    del self.pending_tasks[task_id]
                    
                    return task_result
        
        return None
    
    def wait_all(self, timeout: float = 30.0):
        """Wait for all tasks to complete"""
        start = time.time()
        
        while self.pending_tasks and (time.time() - start) < timeout:
            for task_id in list(self.pending_tasks.keys()):
                self.get_result(task_id, timeout=0.1)
            time.sleep(0.1)
    
    def close(self):
        """Close pool (no more tasks)"""
        self.pool.close()
    
    def terminate(self):
        """Terminate pool immediately"""
        self.pool.terminate()
        self.metrics.state = PoolState.STOPPED
    
    def join(self, timeout: float = 5.0):
        """Wait for workers to finish"""
        self.pool.join()


class MultiprocessingManager:
    """Manages multiple worker pools with task distribution"""
    
    def __init__(self):
        self.pools: Dict[str, ManagedPool] = {}
        self.next_pool_id = 0
        self.next_task_id = 0
        self.monitor_process = None
        self.monitor_active = mp.Value('i', 0)
        self.manager = mp.Manager()
    
    # 1) LAUNCHING
    def create_pool(self, name: str, worker_count: int,
                   priority=PoolPriority.NORMAL) -> str:
        """Create a worker pool"""
        pool_id = f"pool_{self.next_pool_id}"
        self.next_pool_id += 1
        
        managed_pool = ManagedPool(pool_id, name, worker_count, priority)
        self.pools[pool_id] = managed_pool
        
        print(f"[MultiProcMgr] Created pool: {pool_id} ({name}) with {worker_count} workers")
        return pool_id
    
    def submit_task(self, pool_id: str, func: Callable, 
                   args=(), kwargs=None, priority: int = 5) -> str:
        """Submit task to specific pool"""
        if pool_id not in self.pools:
            raise ValueError(f"Pool {pool_id} not found")
        
        task_id = f"task_{self.next_task_id}"
        self.next_task_id += 1
        
        self.pools[pool_id].submit_task(task_id, func, args, kwargs)
        return task_id
    
    def submit_to_any(self, func: Callable, args=(), kwargs=None) -> Tuple[str, str]:
        """Submit task to any available pool"""
        if not self.pools:
            raise ValueError("No pools available")
        
        # Choose pool with fewest pending tasks
        pool_id = min(self.pools.keys(),
                     key=lambda pid: len(self.pools[pid].pending_tasks))
        
        task_id = self.submit_task(pool_id, func, args, kwargs)
        return pool_id, task_id
    
    # 2) COMMUNICATION
    def get_result(self, pool_id: str, task_id: str, 
                  timeout: float = 0.1) -> Optional[TaskResult]:
        """Get task result"""
        if pool_id in self.pools:
            return self.pools[pool_id].get_result(task_id, timeout)
        return None
    
    def wait_for_task(self, pool_id: str, task_id: str, 
                     timeout: float = 30.0) -> Optional[TaskResult]:
        """Wait for specific task to complete"""
        start = time.time()
        
        while (time.time() - start) < timeout:
            result = self.get_result(pool_id, task_id, timeout=0.1)
            if result:
                return result
            time.sleep(0.1)
        
        return None
    
    def wait_all_tasks(self, pool_id: str, timeout: float = 30.0):
        """Wait for all tasks in pool"""
        if pool_id in self.pools:
            self.pools[pool_id].wait_all(timeout)
    
    # 3) STOPPING
    def close_pool(self, pool_id: str):
        """Close pool (no more tasks)"""
        if pool_id in self.pools:
            self.pools[pool_id].close()
    
    def terminate_pool(self, pool_id: str):
        """Terminate pool immediately"""
        if pool_id in self.pools:
            self.pools[pool_id].terminate()
    
    def stop_all_pools(self):
        """Stop all pools"""
        for pool in self.pools.values():
            pool.close()
            pool.join(timeout=5.0)
            pool.terminate()
    
    # 4) ERROR MANAGEMENT
    def get_failed_tasks(self, pool_id: str) -> List[TaskResult]:
        """Get failed tasks from pool"""
        if pool_id in self.pools:
            return [r for r in self.pools[pool_id].results.values()
                   if not r.success]
        return []
    
    def get_all_errors(self) -> Dict[str, List[TaskResult]]:
        """Get all failed tasks from all pools"""
        errors = {}
        for pool_id, pool in self.pools.items():
            failed = self.get_failed_tasks(pool_id)
            if failed:
                errors[pool_id] = failed
        return errors
    
    def retry_task(self, pool_id: str, task_id: str, func: Callable,
                  args=(), kwargs=None) -> str:
        """Retry a failed task"""
        if pool_id in self.pools:
            # Remove old result
            pool = self.pools[pool_id]
            if task_id in pool.results:
                del pool.results[task_id]
            
            # Resubmit
            return pool.submit_task(task_id, func, args, kwargs)
        
        raise ValueError(f"Pool {pool_id} not found")
    
    # 5) MONITORING
    def start_monitoring(self, interval: float = 2.0):
        """Start pool monitoring"""
        if self.monitor_active.value == 1:
            return
        
        self.monitor_active.value = 1
        
        # Create regular dict for monitoring (not Manager.dict to avoid RemoteError)
        pool_data = {}
        for pool_id, pool in self.pools.items():
            pool_data[pool_id] = {
                'name': pool.name,
                'worker_count': pool.worker_count,
                'pending': len(pool.pending_tasks)
            }
        
        # Use Queue for thread-safe communication
        self.monitor_queue = mp.Queue()
        
        self.monitor_process = mp.Process(
            target=_pool_monitor_standalone,
            args=(self.monitor_active, pool_data, interval, self.monitor_queue),
            daemon=True
        )
        self.monitor_process.start()
    
    def stop_monitoring(self):
        """Stop monitoring"""
        self.monitor_active.value = 0
        if self.monitor_process and self.monitor_process.is_alive():
            self.monitor_process.join(timeout=3.0)
            if self.monitor_process.is_alive():
                self.monitor_process.terminate()
    
    # 6) PRIORITIES
    def get_by_priority(self, priority: PoolPriority) -> List[str]:
        """Get pools by priority"""
        return [pid for pid, p in self.pools.items()
                if p.priority == priority]
    
    def set_priority(self, pool_id: str, priority: PoolPriority):
        """Change pool priority"""
        if pool_id in self.pools:
            self.pools[pool_id].priority = priority
            self.pools[pool_id].metrics.priority = priority
    
    # 7) MEMORY MANAGEMENT
    def cleanup_results(self, pool_id: str):
        """Clear completed task results"""
        if pool_id in self.pools:
            pool = self.pools[pool_id]
            pool.results.clear()
            print(f"[MultiProcMgr] Cleaned results from pool {pool_id}")
    
    def cleanup_all_results(self):
        """Clear all completed task results"""
        for pool_id in self.pools.keys():
            self.cleanup_results(pool_id)
    
    def get_statistics(self) -> Dict:
        """Get pool statistics"""
        stats = {
            "total_pools": len(self.pools),
            "total_workers": sum(p.worker_count for p in self.pools.values()),
            "total_submitted": sum(p.metrics.tasks_submitted for p in self.pools.values()),
            "total_completed": sum(p.metrics.tasks_completed for p in self.pools.values()),
            "total_failed": sum(p.metrics.tasks_failed for p in self.pools.values()),
            "total_pending": sum(len(p.pending_tasks) for p in self.pools.values()),
            "total_memory_mb": sum(p.metrics.total_memory_mb for p in self.pools.values()),
            "pools": {}
        }
        
        for pool_id, pool in self.pools.items():
            stats["pools"][pool_id] = {
                "name": pool.name,
                "workers": pool.worker_count,
                "submitted": pool.metrics.tasks_submitted,
                "completed": pool.metrics.tasks_completed,
                "failed": pool.metrics.tasks_failed,
                "pending": len(pool.pending_tasks),
                "memory_mb": pool.metrics.total_memory_mb
            }
        
        return stats
    
    def get_pool_info(self, pool_id: str) -> Optional[PoolMetrics]:
        """Get pool metrics"""
        if pool_id in self.pools:
            return self.pools[pool_id].metrics
        return None
    
    def shutdown(self):
        """Shutdown manager"""
        self.stop_monitoring()
        self.stop_all_pools()
        print("[MultiProcMgr] Shutdown complete")