"""
ThreadManager - Individual thread management
Provides full lifecycle control for threads with priority, monitoring, and communication.
"""
import logging
import threading
import time
import queue
import traceback
import inspect
from dataclasses import dataclass
from typing import Dict, List, Callable, Any, Optional
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class ThreadPriority(Enum):
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4


class ThreadState(Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"
    COMPLETED = "completed"


@dataclass
class ThreadMetrics:
    thread_id: str
    name: str
    state: ThreadState
    priority: ThreadPriority
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_msg: Optional[str] = None
    execution_count: int = 0
    memory_usage_mb: float = 0.0


class ManagedThread:
    """Thread wrapper with control capabilities"""
    
    def __init__(self, thread_id: str, name: str, target: Callable, 
                 args=(), kwargs=None, priority=ThreadPriority.NORMAL):
        self.thread_id = thread_id
        self.name = name
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.priority = priority
        
        # Control mechanisms
        self.pause_event = threading.Event()
        self.stop_event = threading.Event()
        self.pause_event.set()
        
        # Communication
        self.inbox = queue.Queue()
        self.outbox = queue.Queue()
        
        # Metrics
        self.metrics = ThreadMetrics(
            thread_id=thread_id,
            name=name,
            state=ThreadState.CREATED,
            priority=priority,
            created_at=datetime.now()
        )
        
        self.thread = threading.Thread(
            target=self._controlled_run,
            name=name,
            daemon=True
        )
    
    def _controlled_run(self):
        """Execute target with controls"""
        try:
            self.metrics.state = ThreadState.RUNNING
            self.metrics.started_at = datetime.now()
            
            # Try passing control methods to target, fallback to simple call
            sig = inspect.signature(self.target)
            param_names = set(sig.parameters.keys())
            
            # Build kwargs with only the parameters the function accepts
            control_kwargs = {}
            if 'check_pause' in param_names:
                control_kwargs['check_pause'] = self.check_pause
            if 'check_stop' in param_names:
                control_kwargs['check_stop'] = self.check_stop
            if 'send_message' in param_names:
                control_kwargs['send_message'] = self.send_message
            if 'receive_message' in param_names:
                control_kwargs['receive_message'] = self.receive_message
            
            # Merge with existing kwargs
            merged_kwargs = {**self.kwargs, **control_kwargs}
            
            # Call the function
            self.target(*self.args, **merged_kwargs)
            
            self.metrics.state = ThreadState.COMPLETED
            self.metrics.execution_count += 1
            
        except Exception as e:
            self.metrics.state = ThreadState.ERROR
            self.metrics.error_msg = str(e)
            traceback.print_exc()
        finally:
            self.metrics.completed_at = datetime.now()
    
    def check_pause(self):
        """Pause point - call in target function"""
        self.pause_event.wait()
    
    def check_stop(self) -> bool:
        """Stop check - call in target function"""
        return self.stop_event.is_set()
    
    def send_message(self, msg: Any):
        """Send message from thread"""
        self.outbox.put(msg)
    
    def receive_message(self, timeout: float = 0.1) -> Optional[Any]:
        """Receive message in thread"""
        try:
            return self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def start(self):
        self.thread.start()
    
    def pause(self):
        self.metrics.state = ThreadState.PAUSED
        self.pause_event.clear()
    
    def resume(self):
        self.metrics.state = ThreadState.RUNNING
        self.pause_event.set()
    
    def stop(self, timeout: float = 5.0):
        self.stop_event.set()
        self.pause_event.set()
        self.thread.join(timeout=timeout)
        self.metrics.state = ThreadState.STOPPED
    
    def join(self, timeout: Optional[float] = None):
        """Wait for thread to complete"""
        return self.thread.join(timeout=timeout)
    
    def is_alive(self) -> bool:
        return self.thread.is_alive()
    
    @property
    def ident(self):
        """Get thread identifier (delegates to underlying thread)"""
        return self.thread.ident if hasattr(self.thread, 'ident') else None


class ThreadManager:
    """Manages multiple threads with full lifecycle control"""
    
    def __init__(self):
        self.threads: Dict[str, ManagedThread] = {}
        self.lock = threading.RLock()
        self.next_id = 0
        self.monitor_active = False
        self.monitor_thread = None
    
    def create_thread(self, name: str, target: Callable, 
                     args=(), kwargs=None, 
                     priority=ThreadPriority.NORMAL,
                     auto_start=True) -> str:
        """Create and optionally start a thread"""
        with self.lock:
            thread_id = f"thread_{self.next_id}"
            self.next_id += 1
            
            managed = ManagedThread(thread_id, name, target, args, kwargs, priority)
            self.threads[thread_id] = managed
            
            if auto_start:
                managed.start()
            
            # Per-thread submission is a hot path (one call per processed file),
            # so it is logged at debug level.  Unconditional stdout writes here
            # interleaved with the reader's carriage-return progress line and
            # garbled the console output during large ingestions.
            logger.debug(f"Created thread: {thread_id} ({name})")
            return thread_id
    
    def start_thread(self, thread_id: str):
        """Start a created thread"""
        with self.lock:
            if thread_id in self.threads:
                self.threads[thread_id].start()
    
    def send_to_thread(self, thread_id: str, message: Any):
        """Send message to thread"""
        with self.lock:
            if thread_id in self.threads:
                self.threads[thread_id].inbox.put(message)
    
    def receive_from_thread(self, thread_id: str, timeout: float = 0.1) -> Optional[Any]:
        """Receive a message from a thread's outbox.

        The manager lock is held only long enough to resolve the thread's
        outbox; the blocking ``get`` happens outside it.  Holding the
        process-wide manager lock across a blocking queue read serialised
        *every* manager operation (thread creation, message receipt, state
        reads) behind each individual wait - with one message wait per file that
        put a hard ceiling on ingestion throughput no matter how many workers
        were configured.
        """
        with self.lock:
            entry = self.threads.get(thread_id)
            outbox = entry.outbox if entry is not None else None
        if outbox is None:
            return None
        try:
            return outbox.get(timeout=timeout)
        except queue.Empty:
            return None

    def receive_from_thread_nowait(self, thread_id: str) -> Optional[Any]:
        """Non-blocking receive: ``None`` when the outbox has nothing ready."""
        with self.lock:
            entry = self.threads.get(thread_id)
            outbox = entry.outbox if entry is not None else None
        if outbox is None:
            return None
        try:
            return outbox.get_nowait()
        except queue.Empty:
            return None
    
    def pause_thread(self, thread_id: str):
        """Pause thread execution"""
        with self.lock:
            if thread_id in self.threads:
                self.threads[thread_id].pause()
    
    def resume_thread(self, thread_id: str):
        """Resume paused thread"""
        with self.lock:
            if thread_id in self.threads:
                self.threads[thread_id].resume()
    
    def stop_thread(self, thread_id: str, timeout: float = 5.0):
        """Stop specific thread"""
        with self.lock:
            if thread_id in self.threads:
                self.threads[thread_id].stop(timeout)
    
    def stop_all(self, timeout: float = 5.0):
        """Stop all threads"""
        with self.lock:
            for thread in self.threads.values():
                thread.stop(timeout)
    
    def get_errors(self) -> List[ThreadMetrics]:
        """Get all threads with errors"""
        with self.lock:
            return [t.metrics for t in self.threads.values() 
                   if t.metrics.state == ThreadState.ERROR]
    
    def restart_thread(self, thread_id: str):
        """Restart a failed thread"""
        with self.lock:
            if thread_id in self.threads:
                old = self.threads[thread_id]
                new_thread = ManagedThread(
                    thread_id, old.name, old.target, 
                    old.args, old.kwargs, old.priority
                )
                self.threads[thread_id] = new_thread
                new_thread.start()
    
    def start_monitoring(self, interval: float = 2.0):
        """Start thread monitoring"""
        if self.monitor_active:
            return
        
        self.monitor_active = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True
        )
        self.monitor_thread.start()
    
    def _monitor_loop(self, interval: float):
        """Monitor thread health"""
        while self.monitor_active:
            with self.lock:
                for tid, thread in list(self.threads.items()):
                    if thread.metrics.state == ThreadState.RUNNING and not thread.is_alive():
                        thread.metrics.state = ThreadState.ERROR
                        thread.metrics.error_msg = "Thread died unexpectedly"
                        # Warning from the monitor thread: logging keeps the
                        # line from being spliced into the progress output of a
                        # running ingestion.
                        logger.warning(f"Thread {tid} died unexpectedly")
            
            time.sleep(interval)
    
    def stop_monitoring(self):
        """Stop monitoring"""
        self.monitor_active = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=3.0)
    
    def get_by_priority(self, priority: ThreadPriority) -> List[str]:
        """Get threads by priority"""
        with self.lock:
            return [tid for tid, t in self.threads.items() 
                   if t.priority == priority]
    
    def set_priority(self, thread_id: str, priority: ThreadPriority):
        """Change thread priority"""
        with self.lock:
            if thread_id in self.threads:
                self.threads[thread_id].priority = priority
                self.threads[thread_id].metrics.priority = priority
    
    def cleanup_completed(self):
        """Remove completed threads"""
        with self.lock:
            to_remove = [tid for tid, t in self.threads.items()
                        if t.metrics.state in (ThreadState.COMPLETED, ThreadState.STOPPED)
                        and not t.is_alive()]
            
            for tid in to_remove:
                del self.threads[tid]
            
            if to_remove:
                logger.debug(f"Cleaned up {len(to_remove)} threads")
    
    def discard_finished_thread(self, thread_id: str) -> bool:
        """Remove a *finished* thread's registry entry.

        The registry keeps one entry per created thread, with two queues, its
        metrics and a reference to the worker's arguments.  Nothing pruned it,
        so a long ingestion retained one dead thread object per processed file -
        measured at hundreds of live thread objects and tens of megabytes for a
        1000-file run, and unbounded in the millions.  Removal is refused while
        the underlying thread is alive, so a caller can never lose track of
        running work; the caller is expected to have collected the outbox
        first, which is exactly what the reader's collector does.
        """
        with self.lock:
            entry = self.threads.get(thread_id)
            if entry is None:
                return False
            try:
                if entry.is_alive():
                    return False
            except Exception:
                pass
            del self.threads[thread_id]
            return True

    def get_statistics(self) -> Dict:
        """Get thread statistics"""
        with self.lock:
            stats = {
                "total": len(self.threads),
                "running": sum(1 for t in self.threads.values() 
                             if t.metrics.state == ThreadState.RUNNING),
                "paused": sum(1 for t in self.threads.values() 
                            if t.metrics.state == ThreadState.PAUSED),
                "stopped": sum(1 for t in self.threads.values() 
                             if t.metrics.state == ThreadState.STOPPED),
                "completed": sum(1 for t in self.threads.values() 
                               if t.metrics.state == ThreadState.COMPLETED),
                "errors": sum(1 for t in self.threads.values() 
                            if t.metrics.state == ThreadState.ERROR),
                "by_priority": {}
            }
            
            for priority in ThreadPriority:
                stats["by_priority"][priority.name] = sum(
                    1 for t in self.threads.values() if t.priority == priority
                )
            
            return stats
    
    def get_thread_info(self, thread_id: str) -> Optional[ThreadMetrics]:
        """Get thread metrics"""
        with self.lock:
            if thread_id in self.threads:
                return self.threads[thread_id].metrics
        return None
    
    def shutdown(self):
        """Shutdown manager"""
        self.stop_monitoring()
        self.stop_all()
        print("[ThreadMgr] Shutdown complete")
