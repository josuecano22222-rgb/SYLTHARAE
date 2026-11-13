"""
ProcessManager - Individual process management
Provides full lifecycle control for processes with priority, monitoring, and communication.
"""
import multiprocessing as mp
import time
import traceback
from dataclasses import dataclass
from typing import Dict, List, Callable, Any, Optional
from enum import Enum
from datetime import datetime
import psutil
import os


# Standalone monitoring function (must be at module level for pickling)
def _monitor_loop_standalone(monitor_active, process_pids, interval, monitor_queue):
    """Standalone monitor process health"""
    try:
        import sys
        from pathlib import Path
        project_root = Path(__file__).parent.parent.parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
    except Exception as e:
        # Log path setup error but continue - this is non-critical
        import logging
        logging.getLogger(__name__).debug(f"Could not set up project path in monitor process: {e}")
    
    while monitor_active.value == 1:
        for pid, info in process_pids.items():
            try:
                proc = psutil.Process(info['pid'])
                if not proc.is_running():
                    msg = f"[Monitor] Process {pid} died unexpectedly!"
                    print(msg)
                    try:
                        monitor_queue.put({'type': 'alert', 'message': msg}, timeout=1.0)
                    except Exception as queue_error:
                        # Queue operation failed - log but continue monitoring
                        import logging
                        logging.getLogger(__name__).warning(
                            f"Could not send process death alert to queue: {queue_error}"
                        )
                
                # Check for high resource usage
                mem_mb = proc.memory_info().rss / 1024 / 1024
                if mem_mb > 500:
                    msg = f"[Monitor] High memory usage: {pid} ({mem_mb:.1f}MB)"
                    print(msg)
                    try:
                        monitor_queue.put({'type': 'alert', 'message': msg}, timeout=1.0)
                    except Exception as queue_error:
                        # Queue operation failed - log but continue monitoring
                        import logging
                        logging.getLogger(__name__).warning(
                            f"Could not send memory alert to queue: {queue_error}"
                        )
            except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError) as proc_error:
                # Process no longer exists or access denied - this is expected in some cases
                import logging
                logging.getLogger(__name__).debug(
                    f"Process monitoring skipped for {pid}: {type(proc_error).__name__}"
                )
        
        time.sleep(interval)


class ProcessPriority(Enum):
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4


class ProcessState(Enum):
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    COMPLETED = "completed"


@dataclass
class ProcessMetrics:
    process_id: str
    name: str
    pid: Optional[int]
    state: ProcessState
    priority: ProcessPriority
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_msg: Optional[str] = None
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    execution_count: int = 0


class ManagedProcess:
    """Process wrapper with control and communication"""
    
    def __init__(self, process_id: str, name: str, target: Callable,
                 args=(), kwargs=None, priority=ProcessPriority.NORMAL):
        self.process_id = process_id
        self.name = name
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.priority = priority
        
        # Communication channels
        self.inbox = mp.Queue()
        self.outbox = mp.Queue()
        self.control_queue = mp.Queue()
        
        # Metrics
        self.metrics = ProcessMetrics(
            process_id=process_id,
            name=name,
            pid=None,
            state=ProcessState.CREATED,
            priority=priority,
            created_at=datetime.now()
        )
        
        self.process = mp.Process(
            target=self._controlled_run,
            name=name,
            daemon=True
        )
    
    def _controlled_run(self):
        """Execute target with controls"""
        try:
            # Pass communication queues to target
            self.target(
                *self.args,
                inbox=self.inbox,
                outbox=self.outbox,
                control_queue=self.control_queue,
                **self.kwargs
            )
        except Exception as e:
            self.outbox.put({"type": "error", "error": str(e)})
            traceback.print_exc()
    
    def start(self):
        """Start process"""
        self.process.start()
        self.metrics.pid = self.process.pid
        self.metrics.state = ProcessState.RUNNING
        self.metrics.started_at = datetime.now()
    
    def stop(self, timeout: float = 5.0):
        """Stop process"""
        if self.process.is_alive():
            self.control_queue.put({"command": "stop"})
            self.process.join(timeout=timeout)
            
            if self.process.is_alive():
                self.process.terminate()
                time.sleep(0.5)
                if self.process.is_alive():
                    self.process.kill()
            
            self.metrics.state = ProcessState.STOPPED
            self.metrics.completed_at = datetime.now()
    
    def is_alive(self) -> bool:
        return self.process.is_alive()
    
    def update_metrics(self):
        """Update process metrics using psutil"""
        if self.process.is_alive() and self.metrics.pid:
            try:
                proc = psutil.Process(self.metrics.pid)
                self.metrics.cpu_percent = proc.cpu_percent()
                self.metrics.memory_mb = proc.memory_info().rss / 1024 / 1024
            except (psutil.NoSuchProcess, psutil.AccessDenied) as proc_error:
                # Process no longer exists or access denied - expected when process terminates
                import logging
                logging.getLogger(__name__).debug(
                    f"Could not update metrics for process {self.metrics.pid}: "
                    f"{type(proc_error).__name__}"
                )


class ProcessManager:
    """Manages multiple processes with full lifecycle control"""
    
    def __init__(self):
        self.processes: Dict[str, ManagedProcess] = {}
        self.next_id = 0
        self.monitor_process = None
        self.monitor_active = mp.Value('i', 0)
        self.manager = mp.Manager()
        self.shared_dict = self.manager.dict()
    
    # 1) LAUNCHING
    def create_process(self, name: str, target: Callable,
                      args=(), kwargs=None,
                      priority=ProcessPriority.NORMAL,
                      auto_start=True) -> str:
        """Create and optionally start a process"""
        process_id = f"process_{self.next_id}"
        self.next_id += 1
        
        managed = ManagedProcess(process_id, name, target, args, kwargs, priority)
        self.processes[process_id] = managed
        
        if auto_start:
            managed.start()
        
        print(f"[ProcessMgr] Created process: {process_id} ({name})")
        return process_id
    
    def start_process(self, process_id: str):
        """Start a created process"""
        if process_id in self.processes:
            self.processes[process_id].start()
    
    # 2) COMMUNICATION
    def send_to_process(self, process_id: str, message: Any):
        """Send message to process"""
        if process_id in self.processes:
            self.processes[process_id].inbox.put(message)
    
    def receive_from_process(self, process_id: str, timeout: float = 0.1) -> Optional[Any]:
        """Receive message from process"""
        if process_id in self.processes:
            try:
                return self.processes[process_id].outbox.get(timeout=timeout)
            except Exception as queue_error:
                # Queue timeout or other error - expected behavior
                import logging
                logging.getLogger(__name__).debug(
                    f"Could not receive message from process {process_id}: {type(queue_error).__name__}"
                )
                return None
        return None
    
    def broadcast(self, message: Any):
        """Broadcast message to all processes"""
        for proc in self.processes.values():
            proc.inbox.put(message)
    
    # 3) STOPPING
    def stop_process(self, process_id: str, timeout: float = 5.0):
        """Stop specific process"""
        if process_id in self.processes:
            self.processes[process_id].stop(timeout)
    
    def stop_all(self, timeout: float = 5.0):
        """Stop all processes"""
        for proc in self.processes.values():
            proc.stop(timeout)
    
    # 4) ERROR MANAGEMENT
    def get_errors(self) -> List[ProcessMetrics]:
        """Get all processes with errors"""
        errors = []
        for proc in self.processes.values():
            # Check outbox for error messages
            try:
                while not proc.outbox.empty():
                    msg = proc.outbox.get_nowait()
                    if isinstance(msg, dict) and msg.get("type") == "error":
                        proc.metrics.state = ProcessState.ERROR
                        proc.metrics.error_msg = msg.get("error")
            except Exception as queue_error:
                # Queue operation failed - log but continue processing
                import logging
                logging.getLogger(__name__).debug(
                    f"Could not check error messages from process {proc.process_id}: "
                    f"{type(queue_error).__name__}"
                )
            
            if proc.metrics.state == ProcessState.ERROR:
                errors.append(proc.metrics)
        
        return errors
    
    def restart_process(self, process_id: str):
        """Restart a failed process"""
        if process_id in self.processes:
            old = self.processes[process_id]
            old.stop()
            
            new_proc = ManagedProcess(
                process_id, old.name, old.target,
                old.args, old.kwargs, old.priority
            )
            self.processes[process_id] = new_proc
            new_proc.start()
    
    # 5) MONITORING
    def start_monitoring(self, interval: float = 2.0):
        """Start process monitoring"""
        if self.monitor_active.value == 1:
            return
        
        self.monitor_active.value = 1
        
        # Create a regular dict with process PIDs (not Manager.dict to avoid RemoteError)
        process_pids = {}
        for pid, proc in self.processes.items():
            if proc.metrics.pid:
                process_pids[pid] = {
                    'pid': proc.metrics.pid,
                    'name': proc.name
                }
        
        # Use a Queue for thread-safe communication instead of shared dict
        self.monitor_queue = mp.Queue()
        
        self.monitor_process = mp.Process(
            target=_monitor_loop_standalone,
            args=(self.monitor_active, process_pids, interval, self.monitor_queue),
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
    def get_by_priority(self, priority: ProcessPriority) -> List[str]:
        """Get processes by priority"""
        return [pid for pid, p in self.processes.items()
                if p.priority == priority]
    
    def set_priority(self, process_id: str, priority: ProcessPriority):
        """Change process priority"""
        if process_id in self.processes:
            proc = self.processes[process_id]
            proc.priority = priority
            proc.metrics.priority = priority
            
            # Set OS priority if process is running
            if proc.is_alive() and proc.metrics.pid:
                try:
                    p = psutil.Process(proc.metrics.pid)
                    if priority == ProcessPriority.CRITICAL:
                        p.nice(-10)
                    elif priority == ProcessPriority.HIGH:
                        p.nice(-5)
                    elif priority == ProcessPriority.NORMAL:
                        p.nice(0)
                    else:
                        p.nice(10)
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as priority_error:
                    # Process priority change failed - log but continue
                    import logging
                    logging.getLogger(__name__).warning(
                        f"Could not set process priority for {proc.process_id}: "
                        f"{type(priority_error).__name__} - {priority_error}"
                    )
    
    # 7) MEMORY MANAGEMENT
    def cleanup_completed(self):
        """Remove completed processes"""
        to_remove = [pid for pid, p in self.processes.items()
                    if p.metrics.state in (ProcessState.COMPLETED, ProcessState.STOPPED)
                    and not p.is_alive()]
        
        for pid in to_remove:
            del self.processes[pid]
        
        if to_remove:
            print(f"[ProcessMgr] Cleaned up {len(to_remove)} processes")
    
    def get_statistics(self) -> Dict:
        """Get process statistics"""
        stats = {
            "total": len(self.processes),
            "running": sum(1 for p in self.processes.values()
                          if p.metrics.state == ProcessState.RUNNING),
            "stopped": sum(1 for p in self.processes.values()
                          if p.metrics.state == ProcessState.STOPPED),
            "completed": sum(1 for p in self.processes.values()
                            if p.metrics.state == ProcessState.COMPLETED),
            "errors": sum(1 for p in self.processes.values()
                         if p.metrics.state == ProcessState.ERROR),
            "total_memory_mb": sum(p.metrics.memory_mb for p in self.processes.values()),
            "avg_cpu_percent": sum(p.metrics.cpu_percent for p in self.processes.values()) / max(len(self.processes), 1),
            "by_priority": {}
        }
        
        for priority in ProcessPriority:
            stats["by_priority"][priority.name] = sum(
                1 for p in self.processes.values() if p.priority == priority
            )
        
        return stats
    
    def get_process_info(self, process_id: str) -> Optional[ProcessMetrics]:
        """Get process metrics"""
        if process_id in self.processes:
            return self.processes[process_id].metrics
        return None
    
    def shutdown(self):
        """Shutdown manager"""
        self.stop_monitoring()
        self.stop_all()
        print("[ProcessMgr] Shutdown complete")