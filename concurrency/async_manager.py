"""
AsyncManager - Individual async task management
Provides full lifecycle control for async tasks with priority, monitoring, and communication.
"""
import asyncio
import time
import traceback
from dataclasses import dataclass
from typing import Dict, List, Callable, Any, Optional, Coroutine, Union
from enum import Enum
from datetime import datetime
import psutil
import os


class TaskPriority(Enum):
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4


class TaskState(Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"
    COMPLETED = "completed"


@dataclass
class TaskMetrics:
    task_id: str
    name: str
    state: TaskState
    priority: TaskPriority
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_msg: Optional[str] = None
    execution_count: int = 0
    runtime_seconds: float = 0.0


class ManagedTask:
    """Async task wrapper with control capabilities"""
    
    def __init__(self, task_id: str, name: str, 
                 coro_or_factory: Union[Coroutine, Callable[[], Coroutine]],
                 priority=TaskPriority.NORMAL):
        self.task_id = task_id
        self.name = name
        self.priority = priority
        
        # Store coroutine factory for restart capability
        # If a coroutine is passed directly, wrap it in a factory
        if asyncio.iscoroutine(coro_or_factory):
            # Single-use coroutine - wrap in factory that raises error on restart
            original_coro = coro_or_factory
            def _single_use_factory():
                raise RuntimeError(
                    "Cannot restart task: original coroutine was single-use. "
                    "Pass a coroutine factory (callable) to enable restarts."
                )
            self.coro_factory: Callable[[], Coroutine] = _single_use_factory
            self.coro = original_coro
        else:
            # Coroutine factory provided
            self.coro_factory: Callable[[], Coroutine] = coro_or_factory
            self.coro = self.coro_factory()
        
        # Control
        self.pause_event = asyncio.Event()
        self.stop_event = asyncio.Event()
        self.pause_event.set()
        
        # Communication
        self.inbox = asyncio.Queue()
        self.outbox = asyncio.Queue()
        
        # Metrics
        self.metrics = TaskMetrics(
            task_id=task_id,
            name=name,
            state=TaskState.CREATED,
            priority=priority,
            created_at=datetime.now()
        )
        
        self.task: Optional[asyncio.Task] = None
    
    async def _controlled_run(self):
        """Execute coroutine with controls"""
        try:
            self.metrics.state = TaskState.RUNNING
            self.metrics.started_at = datetime.now()
            
            await self.coro
            
            self.metrics.state = TaskState.COMPLETED
            self.metrics.execution_count += 1
            
        except asyncio.CancelledError:
            self.metrics.state = TaskState.STOPPED
        except Exception as e:
            self.metrics.state = TaskState.ERROR
            self.metrics.error_msg = str(e)
            traceback.print_exc()
        finally:
            self.metrics.completed_at = datetime.now()
            if self.metrics.started_at:
                self.metrics.runtime_seconds = (
                    self.metrics.completed_at - self.metrics.started_at
                ).total_seconds()
    
    async def check_pause(self):
        """Pause point - call in coroutine"""
        await self.pause_event.wait()
    
    def check_stop(self) -> bool:
        """Stop check - call in coroutine"""
        return self.stop_event.is_set()
    
    async def send_message(self, msg: Any):
        """Send message from task"""
        await self.outbox.put(msg)
    
    async def receive_message(self, timeout: float = 0.1) -> Optional[Any]:
        """Receive message in task"""
        try:
            return await asyncio.wait_for(self.inbox.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
    
    def start(self, loop: asyncio.AbstractEventLoop):
        """Start task"""
        self.task = loop.create_task(self._controlled_run())
    
    def pause(self):
        """Pause task"""
        self.metrics.state = TaskState.PAUSED
        self.pause_event.clear()
    
    def resume(self):
        """Resume task"""
        self.metrics.state = TaskState.RUNNING
        self.pause_event.set()
    
    def stop(self):
        """Stop task"""
        self.stop_event.set()
        self.pause_event.set()
        if self.task:
            self.task.cancel()
        self.metrics.state = TaskState.STOPPED
    
    def is_done(self) -> bool:
        """Check if task is done"""
        return self.task.done() if self.task else False


class AsyncManager:
    """Manages async tasks with full lifecycle control"""
    
    def __init__(self):
        self.tasks: Dict[str, ManagedTask] = {}
        self.next_id = 0
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.monitor_task: Optional[asyncio.Task] = None
        self.monitor_active = False
    
    def initialize(self):
        """Initialize event loop"""
        if self.loop is None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
    
    # 1) LAUNCHING
    def create_async_task(self, name: str, 
                         coro_or_factory: Union[Coroutine, Callable[[], Coroutine]],
                         priority=TaskPriority.NORMAL,
                         auto_start=True) -> str:
        """
        Create and optionally start an async task.
        
        This method creates an async coroutine task. For file processing tasks,
        use FileProcessingTaskManager.create_task() instead.
        
        Args:
            name: Task name for identification
            coro_or_factory: Coroutine to execute, or a callable that returns a coroutine.
                           Using a factory (callable) enables task restart functionality.
            priority: Task priority (default: NORMAL)
            auto_start: Whether to start the task immediately (default: True)
            
        Returns:
            Task ID for tracking
            
        Note:
            This method differs from FileProcessingTaskManager.create_task()
            which is specifically for file processing tasks. This is for general
            async coroutine tasks.
            
            To enable restart functionality, pass a coroutine factory (callable):
            ```python
            async def my_task():
                # task code
                pass
            
            # Enable restart
            manager.create_async_task("my_task", lambda: my_task())
            
            # Or with parameters
            manager.create_async_task("my_task", lambda: my_task(param1, param2))
            ```
        """
        if self.loop is None:
            self.initialize()
        
        task_id = f"task_{self.next_id}"
        self.next_id += 1
        
        managed = ManagedTask(task_id, name, coro_or_factory, priority)
        self.tasks[task_id] = managed
        
        if auto_start:
            managed.start(self.loop)
        
        print(f"[AsyncMgr] Created task: {task_id} ({name})")
        return task_id
    
    def start_task(self, task_id: str):
        """Start a created task"""
        if task_id in self.tasks and self.loop:
            self.tasks[task_id].start(self.loop)
    
    # 2) COMMUNICATION
    async def send_to_task(self, task_id: str, message: Any):
        """Send message to task"""
        if task_id in self.tasks:
            await self.tasks[task_id].inbox.put(message)
    
    async def receive_from_task(self, task_id: str, timeout: float = 0.1) -> Optional[Any]:
        """Receive message from task"""
        if task_id in self.tasks:
            try:
                return await asyncio.wait_for(
                    self.tasks[task_id].outbox.get(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                return None
        return None
    
    async def broadcast(self, message: Any):
        """Broadcast message to all tasks"""
        for task in self.tasks.values():
            await task.inbox.put(message)
    
    # 3) STOPPING
    def pause_task(self, task_id: str):
        """Pause task"""
        if task_id in self.tasks:
            self.tasks[task_id].pause()
    
    def resume_task(self, task_id: str):
        """Resume task"""
        if task_id in self.tasks:
            self.tasks[task_id].resume()
    
    def stop_task(self, task_id: str):
        """Stop specific task"""
        if task_id in self.tasks:
            self.tasks[task_id].stop()
    
    def stop_all(self):
        """Stop all tasks"""
        for task in self.tasks.values():
            task.stop()
    
    # 4) ERROR MANAGEMENT
    def get_errors(self) -> List[TaskMetrics]:
        """Get all tasks with errors"""
        return [t.metrics for t in self.tasks.values()
                if t.metrics.state == TaskState.ERROR]
    
    def restart_task(self, task_id: str):
        """
        Restart a failed or stopped task.
        
        This method creates a new coroutine from the stored factory and restarts
        the task. The task must have been created with a coroutine factory
        (callable) to be restartable.
        
        Args:
            task_id: ID of the task to restart
            
        Raises:
            KeyError: If task_id doesn't exist
            RuntimeError: If task was created with a single-use coroutine
        """
        if task_id not in self.tasks:
            raise KeyError(f"Task {task_id} not found")
        
        managed = self.tasks[task_id]
        
        # Check if task is in a restartable state
        if managed.metrics.state not in (TaskState.ERROR, TaskState.STOPPED, TaskState.COMPLETED):
            print(f"[AsyncMgr] Task {task_id} is in state {managed.metrics.state}, "
                  f"cannot restart. Stop the task first.")
            return
        
        # Stop existing task if it's still running
        if managed.task and not managed.task.done():
            managed.stop()
        
        # Create new coroutine from factory
        try:
            new_coro = managed.coro_factory()
        except RuntimeError as e:
            # This is the error from single-use coroutine wrapper
            print(f"[AsyncMgr] Cannot restart task {task_id}: {e}")
            raise RuntimeError(f"Cannot restart task {task_id}: {e}") from e
        
        # Update coroutine reference
        managed.coro = new_coro
        
        # Ensure loop is initialized and set as current
        if self.loop is None:
            self.initialize()
        else:
            # Ensure the loop is set as the current event loop
            try:
                asyncio.set_event_loop(self.loop)
            except RuntimeError:
                # Loop might already be set, which is fine
                pass
        
        # Reset control events (will use current event loop)
        managed.pause_event = asyncio.Event()
        managed.stop_event = asyncio.Event()
        managed.pause_event.set()
        
        # Clear communication queues
        managed.inbox = asyncio.Queue()
        managed.outbox = asyncio.Queue()
        
        # Reset metrics (preserve execution count and total runtime)
        managed.metrics.state = TaskState.CREATED
        managed.metrics.started_at = None
        managed.metrics.completed_at = None
        managed.metrics.error_msg = None
        
        # Start the new task
        managed.start(self.loop)
        print(f"[AsyncMgr] Restarted task: {task_id} ({managed.name})")
    
    # 5) MONITORING
    async def start_monitoring(self, interval: float = 2.0):
        """Start task monitoring"""
        if self.monitor_active:
            return
        
        self.monitor_active = True
        self.monitor_task = asyncio.create_task(self._monitor_loop(interval))
    
    async def _monitor_loop(self, interval: float):
        """Monitor task health"""
        while self.monitor_active:
            for tid, task in list(self.tasks.items()):
                if task.metrics.state == TaskState.RUNNING and task.is_done():
                    if task.task and task.task.exception():
                        task.metrics.state = TaskState.ERROR
                        task.metrics.error_msg = str(task.task.exception())
                        print(f"[Monitor] Task {tid} failed: {task.metrics.error_msg}")
            
            await asyncio.sleep(interval)
    
    def stop_monitoring(self):
        """Stop monitoring"""
        self.monitor_active = False
        if self.monitor_task:
            self.monitor_task.cancel()
    
    # 6) PRIORITIES
    def get_by_priority(self, priority: TaskPriority) -> List[str]:
        """Get tasks by priority"""
        return [tid for tid, t in self.tasks.items()
                if t.priority == priority]
    
    def set_priority(self, task_id: str, priority: TaskPriority):
        """Change task priority"""
        if task_id in self.tasks:
            self.tasks[task_id].priority = priority
            self.tasks[task_id].metrics.priority = priority
    
    # 7) MEMORY MANAGEMENT
    def cleanup_completed(self):
        """Remove completed tasks"""
        to_remove = [tid for tid, t in self.tasks.items()
                    if t.metrics.state in (TaskState.COMPLETED, TaskState.STOPPED)
                    and t.is_done()]
        
        for tid in to_remove:
            del self.tasks[tid]
        
        if to_remove:
            print(f"[AsyncMgr] Cleaned up {len(to_remove)} tasks")
    
    def get_statistics(self) -> Dict:
        """Get task statistics"""
        stats = {
            "total": len(self.tasks),
            "running": sum(1 for t in self.tasks.values()
                          if t.metrics.state == TaskState.RUNNING),
            "paused": sum(1 for t in self.tasks.values()
                         if t.metrics.state == TaskState.PAUSED),
            "stopped": sum(1 for t in self.tasks.values()
                          if t.metrics.state == TaskState.STOPPED),
            "completed": sum(1 for t in self.tasks.values()
                            if t.metrics.state == TaskState.COMPLETED),
            "errors": sum(1 for t in self.tasks.values()
                         if t.metrics.state == TaskState.ERROR),
            "total_runtime": sum(t.metrics.runtime_seconds for t in self.tasks.values()),
            "by_priority": {}
        }
        
        for priority in TaskPriority:
            stats["by_priority"][priority.name] = sum(
                1 for t in self.tasks.values() if t.priority == priority
            )
        
        return stats
    
    def get_task_info(self, task_id: str) -> Optional[TaskMetrics]:
        """Get task metrics"""
        if task_id in self.tasks:
            return self.tasks[task_id].metrics
        return None
    
    def run_until_complete(self, coro: Coroutine):
        """Run coroutine until complete"""
        if self.loop:
            return self.loop.run_until_complete(coro)
    
    def shutdown(self):
        """Shutdown manager"""
        self.stop_monitoring()
        self.stop_all()
        
        if self.loop:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            
            # Give tasks a chance to finish
            # Only run_until_complete if loop is not already running
            if pending:
                try:
                    if self.loop.is_running():
                        # If loop is already running, we can't use run_until_complete
                        # Tasks will be cancelled and cleaned up by the running loop
                        pass
                    else:
                        self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except RuntimeError:
                    # If we can't run the loop (e.g., already running in another context),
                    # just cancel the tasks - they'll be cleaned up
                    pass
        
        print("[AsyncMgr] Shutdown complete")
