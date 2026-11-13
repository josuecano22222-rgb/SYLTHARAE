"""
Resource Coordinator - Prevents system freezes by managing resource allocation
across multiple application instances (CLI and Web).

This module:
- Detects running application instances
- Calculates safe worker counts based on system resources
- Limits database connections per instance
- Monitors CPU/memory to prevent overload
"""

import os
import sys
import multiprocessing
import threading
import time
import json
from pathlib import Path
from typing import Optional, Dict
from dataclasses import dataclass
import logging

# Try to import psutil, but handle gracefully if not available
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    # Create a minimal fallback
    class _FakePsutil:
        @staticmethod
        def cpu_count():
            return multiprocessing.cpu_count()
        
        @staticmethod
        def virtual_memory():
            class _Memory:
                total = 8 * 1024**3  # Assume 8GB default
                available = 4 * 1024**3
                percent = 50.0
            return _Memory()
        
        @staticmethod
        def cpu_percent(interval=None):
            return 50.0  # Assume moderate load
    
    psutil = _FakePsutil()

logger = logging.getLogger(__name__)


@dataclass
class ResourceLimits:
    """Resource limits for an application instance"""
    max_workers: int
    db_pool_max: int
    cpu_limit_percent: float = 80.0
    memory_limit_percent: float = 80.0


#: Memory pressure is the only signal that can crash the process, so it is the
#: one that throttles work. CPU near 100 % is what a saturation-capable,
#: I/O-bound pipeline *looks like* when it is healthy - the previous monitor
#: treated "CPU > 85 %" as overload, shrank the worker pool while the run was
#: busy (a 2-core host was reduced from 8 workers to 2 within seconds, then
#: logged "System overload detected" for the rest of the run), and pushed whole
#: sub-trees through the sequential fallback. These are the only two numbers
#: that decide throttling now.
MEMORY_EMERGENCY_PERCENT = 92.0
MEMORY_PRESSURE_PERCENT = 85.0

#: Worker ceiling for a single instance, before the per-instance split. The
#: pipeline is I/O-bound (hashing, DB, extraction), so oversubscribing cores is
#: the normal way to keep them busy; the old ceiling of 4 could never reach the
#: 8 or 16 workers configured in settings on a larger host.
MAX_WORKERS_PER_INSTANCE = 16
#: Memory assumed per worker when deciding how many fit (measured RSS of the
#: ingest pipeline per in-flight worker is well under this).
WORKER_MEMORY_BUDGET_GB = 0.35
#: Cores left for the OS, the database and the gateway.
RESERVED_CORES = 2


class ResourceCoordinator:
    """
    Coordinates resource usage across multiple application instances.
    Prevents system freezes by intelligently allocating resources.
    """
    
    _instance = None
    _lock = threading.Lock()

    #: CPU load is sampled without blocking.  ``psutil.cpu_percent(interval=X)``
    #: sleeps for X seconds *in the calling thread*; the ingest loops call
    #: ``should_yield()`` every 1000 items, so a blocking sample added ~100 ms
    #: per call inside the storage transaction (measured: ~10 s of pure sleep
    #: for a single 50k-word document).  ``interval=None`` returns the usage
    #: since the previous call without sleeping; the value is cached for a
    #: short window so hot loops do not even pay for the syscall.
    _CPU_SAMPLE_TTL = 0.5
    _cpu_sample = None
    _cpu_sample_time = 0.0
    _cpu_sample_lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        with self._lock:
            if self._initialized:
                return
            
            # Get project root for lock file
            try:
                from core.path_utils import setup_path
                project_root = setup_path()
            except:
                project_root = Path.cwd()
            
            self.lock_file = project_root / '.app_instance.lock'
            self.instance_id = f"{os.getpid()}_{int(time.time())}"
            self.instance_type = self._detect_instance_type()
            
            # Prime the non-blocking CPU sampler (first delta has no window).
            try:
                psutil.cpu_percent(interval=None)
            except Exception:
                pass

            # System resources
            self.cpu_count = multiprocessing.cpu_count()
            try:
                self.total_memory_gb = psutil.virtual_memory().total / (1024**3)
            except Exception:
                # Fallback if psutil fails
                self.total_memory_gb = 8.0  # Assume 8GB default
            
            # Resource limits
            self._update_resource_limits()
            
            # Register this instance
            self._register_instance()
            
            # Start monitoring thread
            self._monitoring = False
            self._monitor_thread = None
            self._start_monitoring()
            
            # Cleanup on exit
            import atexit
            atexit.register(self._cleanup)
            
            self._initialized = True
            
            logger.info(f"Resource Coordinator initialized for {self.instance_type}")
            logger.info(f"  Instance ID: {self.instance_id}")
            logger.info(f"  CPU cores: {self.cpu_count}")
            logger.info(f"  Total memory: {self.total_memory_gb:.1f} GB")
            logger.info(f"  Allocated workers: {self.resource_limits.max_workers}")
            logger.info(f"  Allocated DB pool: {self.resource_limits.db_pool_max}")
    
    def _detect_instance_type(self) -> str:
        """Detect if this is CLI or Web instance"""
        script_name = Path(sys.argv[0]).name.lower()
        if 'run_cli' in script_name or 'cli' in script_name:
            return 'cli'
        elif 'run_web' in script_name or 'web' in script_name or 'flask' in script_name:
            return 'web'
        else:
            return 'unknown'
    
    def _get_running_instances(self) -> Dict[str, Dict]:
        """Get all running application instances from lock file"""
        instances = {}
        
        if not self.lock_file.exists():
            return instances
        
        try:
            with open(self.lock_file, 'r') as f:
                data = json.load(f)
                # Filter out stale instances (older than 5 minutes)
                current_time = time.time()
                for instance_id, info in data.items():
                    if current_time - info.get('last_update', 0) < 300:  # 5 minutes
                        instances[instance_id] = info
        except Exception as e:
            logger.debug(f"Error reading lock file: {e}")
        
        return instances
    
    def _register_instance(self):
        """Register this instance in the lock file"""
        instances = self._get_running_instances()
        
        instances[self.instance_id] = {
            'pid': os.getpid(),
            'type': self.instance_type,
            'started_at': time.time(),
            'last_update': time.time(),
            'workers': self.resource_limits.max_workers,
            'db_pool': self.resource_limits.db_pool_max
        }
        
        try:
            with open(self.lock_file, 'w') as f:
                json.dump(instances, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not write lock file: {e}")
    
    def _update_instance(self):
        """Update this instance's timestamp in lock file"""
        instances = self._get_running_instances()
        
        if self.instance_id in instances:
            instances[self.instance_id]['last_update'] = time.time()
            instances[self.instance_id]['workers'] = self.resource_limits.max_workers
            instances[self.instance_id]['db_pool'] = self.resource_limits.db_pool_max
            
            try:
                with open(self.lock_file, 'w') as f:
                    json.dump(instances, f, indent=2)
            except Exception as e:
                logger.debug(f"Could not update lock file: {e}")
    
    def _update_resource_limits(self):
        """Calculate safe resource limits based on running instances"""
        instances = self._get_running_instances()
        
        # Count instances by type
        cli_count = sum(1 for inst in instances.values() if inst.get('type') == 'cli')
        web_count = sum(1 for inst in instances.values() if inst.get('type') == 'web')
        total_instances = len(instances)
        
        # If this is a new instance, don't count it yet
        if self.instance_id not in instances:
            if self.instance_type == 'cli':
                cli_count += 1
            elif self.instance_type == 'web':
                web_count += 1
            total_instances += 1
        
        # Calculate safe worker count.
        #
        # Two independent caps: cores (oversubscribed on purpose, the work is
        # I/O-bound) and memory (never oversubscribed - that is what crashes).
        max_workers = self._instance_budget(total_instances)

        # A recalculation must not undo a memory-pressure reduction: callers
        # ask for worker counts at any moment, and raising the pool back while
        # memory is short is the fatal case the monitor exists to prevent.
        current = getattr(self, "resource_limits", None)
        if (getattr(self, "_system_overload", False) and current is not None
                and current.max_workers < max_workers):
            max_workers = current.max_workers
        
        # Calculate database pool size
        # Need enough connections for concurrent workers + buffer
        # Each worker may need 2-3 connections (transaction + nested operations + bulk inserts)
        # Formula: (max_workers * 3) + buffer for other operations
        base_pool_size = (max_workers * 3) + 6  # 3 connections per worker + 6 buffer
        
        # Split among instances if multiple instances running
        if total_instances == 1:
            db_pool_max = min(base_pool_size, 25)  # Allow up to 25 for single instance
        elif total_instances == 2:
            db_pool_max = min(base_pool_size // 2, 20)  # Split but allow up to 20 per instance
        else:
            # For multiple instances, be more generous - allow at least 15 per instance
            # This prevents connection exhaustion during heavy operations
            db_pool_max = max(15, min(base_pool_size // total_instances, 20))
        
        # Ensure minimum of 10 connections (needed for concurrent processing with bulk operations)
        # Increased from 6 to 10 to handle concurrent web requests better
        db_pool_max = max(10, db_pool_max)
        
        self.resource_limits = ResourceLimits(
            max_workers=max_workers,
            db_pool_max=db_pool_max
        )
        
        logger.debug(f"Resource limits updated: {max_workers} workers, {db_pool_max} DB connections")
        logger.debug(f"  Running instances: CLI={cli_count}, Web={web_count}, Total={total_instances}")
    
    def _start_monitoring(self):
        """Start background monitoring thread"""
        if self._monitoring:
            return
        
        self._monitoring = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="ResourceMonitor"
        )
        self._monitor_thread.start()
    
    def _monitor_loop(self):
        """Watch memory pressure and the configured worker ceiling.

        What this loop does *not* do any more: shrink the worker pool because
        the CPU is busy. That behaviour turned a healthy saturated run into a
        starved one and, through the reader's overload check, into sequential
        processing for every extracted sub-tree. What it does: react to memory
        pressure, which is a genuinely fatal condition, and restore the
        configured worker count once the pressure passes.
        """
        self._system_overload = False
        self._last_cpu_percent = 0.0
        self._last_memory_percent = 0.0

        while self._monitoring:
            try:
                self._update_instance()

                try:
                    cpu_percent = self.get_cpu_percent()
                    memory_percent = psutil.virtual_memory().percent

                    self._last_cpu_percent = cpu_percent
                    self._last_memory_percent = memory_percent
                except Exception as e:
                    logger.debug(f"Error checking system resources: {e}")
                    continue

                action = self._evaluate_pressure(cpu_percent, memory_percent)

                if action == "reduced":
                    logger.info(
                        "Reduced workers to %d (memory pressure %.1f%%)",
                        self.resource_limits.max_workers, memory_percent,
                    )
                elif action == "restored":
                    logger.info(
                        "Restored workers to %d (CPU=%.1f%%, Memory=%.1f%%)",
                        self.resource_limits.max_workers, cpu_percent, memory_percent,
                    )

                time.sleep(5)

            except Exception as e:
                logger.debug(f"Error in monitoring loop: {e}")
                time.sleep(5)

    def _count_instances(self) -> int:
        """How many instances are running, this one included.

        The registration file does not list an instance until it writes itself
        there, so a coordinator that has not registered yet must count itself.
        """
        instances = self._get_running_instances()
        total = len(instances)
        if self.instance_id not in instances:
            total += 1
        return max(1, total)

    def _instance_budget(self, total_instances: Optional[int] = None) -> int:
        """This instance's worker budget: the ceiling, minus the worker a web
        instance keeps for request work.

        The one definition used by both the limit recalculation and the
        monitor's restore. They used to differ (restore ignored the web
        reservation), so every recalculation lowered the pool and the next
        monitor cycle logged a spurious "Restored workers" raising it again.
        """
        budget = self._worker_ceiling(total_instances)
        if self.instance_type == 'web':
            budget -= 1
        return max(1, budget)

    def _worker_ceiling(self, total_instances: Optional[int] = None) -> int:
        """How many workers this instance may use, from cores *and* memory.

        Cores set the concurrency the pipeline can usefully keep busy; memory
        sets the hard limit (a worker that cannot allocate dies mid-file and
        loses its work). Neither is guessed from the momentary CPU reading.

        ``total_instances=None`` means "ask the registration file". Callers that
        have just counted the instances themselves may pass the number they
        computed; the monitor must not, or it would restore workers against a
        single-instance ceiling while two instances are competing for the host
        (the restore path used to do exactly that).
        """
        cores = max(1, self.cpu_count - RESERVED_CORES)
        # The pipeline is I/O-bound: 2x cores is the useful ceiling when memory
        # allows, because workers block on disk, hashing and the database.
        cpu_bound = max(1, min(MAX_WORKERS_PER_INSTANCE, cores * 2))
        try:
            memory_bound = int(self.total_memory_gb / WORKER_MEMORY_BUDGET_GB)
        except Exception:
            memory_bound = cpu_bound
        memory_bound = max(1, memory_bound)
        ceiling = max(1, min(cpu_bound, memory_bound, MAX_WORKERS_PER_INSTANCE))
        if total_instances is None:
            total_instances = self._count_instances()
        if total_instances > 1:
            ceiling = max(1, ceiling // total_instances)
        return ceiling

    def _evaluate_pressure(self, cpu_percent: float,
                           memory_percent: float) -> str:
        """Apply one resource sample to the worker budget.

        The policy in one place, so it is testable without a live monitor
        thread and so the two mistakes this project already made cannot come
        back silently:

        * CPU saturation (``cpu_percent`` high) is *not* overload. This is an
          I/O-bound pipeline; busy cores are what a healthy run looks like.
          Treating it as overload shrank the pool mid-run and pushed whole
          extracted sub-trees onto the sequential path.
        * memory pressure *is* overload, and it is fatal if ignored: a worker
          that cannot allocate dies mid-file and loses its work.

        Returns ``"reduced"`` when the budget was lowered, ``"restored"`` when
        it was raised back toward the ceiling, ``"none"`` otherwise. The
        ``_system_overload`` flag (memory only) is kept in step for callers.
        """
        was_overloaded = self._system_overload
        overloaded = memory_percent > MEMORY_PRESSURE_PERCENT
        self._system_overload = overloaded

        if overloaded:
            if not was_overloaded:
                logger.warning(
                    "Memory pressure detected: CPU=%.1f%%, Memory=%.1f%% "
                    "(memory is the throttle signal; CPU saturation alone is "
                    "not overloaded for an I/O-bound pipeline)",
                    cpu_percent, memory_percent,
                )
            if self.resource_limits.max_workers > 1:
                reduction = 2 if memory_percent > MEMORY_EMERGENCY_PERCENT else 1
                self.resource_limits.max_workers = max(
                    1, self.resource_limits.max_workers - reduction
                )
                return "reduced"
            return "none"

        action = "none"
        if was_overloaded:
            action = "restored"
        # Restore toward the ceiling as soon as pressure is gone, at the
        # configured rate rather than "one worker per cycle".
        ceiling = self._instance_budget()
        if self.resource_limits.max_workers < ceiling:
            step = max(1, ceiling // 4)
            self.resource_limits.max_workers = min(
                ceiling, self.resource_limits.max_workers + step
            )
            action = "restored"
        return action

    def _cleanup(self):
        """Clean up instance registration"""
        try:
            instances = self._get_running_instances()
            if self.instance_id in instances:
                del instances[self.instance_id]
                
                if instances:
                    with open(self.lock_file, 'w') as f:
                        json.dump(instances, f, indent=2)
                else:
                    # No more instances, remove lock file
                    if self.lock_file.exists():
                        self.lock_file.unlink()
        except Exception as e:
            logger.debug(f"Error during cleanup: {e}")
        
        self._monitoring = False
    
    def get_safe_worker_count(self, requested: Optional[int] = None) -> int:
        """
        Get safe worker count for this instance.
        
        Args:
            requested: Requested worker count (from settings)
        
        Returns:
            Safe worker count that won't overload the system
        """
        # Update limits based on current instances
        self._update_resource_limits()
        
        if requested is None:
            return self.resource_limits.max_workers
        
        # Use the minimum of requested and calculated safe limit
        return min(requested, self.resource_limits.max_workers)
    
    def get_safe_db_pool_size(self, requested: Optional[int] = None) -> int:
        """
        Get safe database pool size for this instance.
        
        Args:
            requested: Requested pool size (from settings)
        
        Returns:
            Safe pool size that won't exhaust database connections
        """
        # Update limits based on current instances
        self._update_resource_limits()
        
        if requested is None:
            return self.resource_limits.db_pool_max
        
        # Use the minimum of requested and calculated safe limit
        return min(requested, self.resource_limits.db_pool_max)
    
    def is_system_overloaded(self) -> bool:
        """
        Check if system is currently overloaded.
        
        Returns:
            True while *memory* is under pressure (above
            ``MEMORY_PRESSURE_PERCENT``). CPU saturation is not overload: this
            pipeline is I/O-bound, so busy cores mean the run is healthy.
        """
        return self._system_overload
    
    def get_cpu_percent(self) -> float:
        """Current CPU load percentage, sampled without blocking.

        Uses the delta-based ``psutil.cpu_percent(interval=None)`` and caches
        the result for :attr:`_CPU_SAMPLE_TTL`, so callers in hot loops pay
        (almost) nothing.  Never raises: a monitoring failure must not break
        the caller's work.
        """
        now = time.monotonic()
        sample = ResourceCoordinator._cpu_sample
        if sample is not None and (now - ResourceCoordinator._cpu_sample_time) < self._CPU_SAMPLE_TTL:
            return sample

        try:
            value = float(psutil.cpu_percent(interval=None))
        except Exception:
            return sample if sample is not None else 0.0

        # psutil returns 0.0 when no measurable window has elapsed since the
        # previous sample; keep the last real reading in that case.
        if value == 0.0 and sample is not None:
            value = sample

        with ResourceCoordinator._cpu_sample_lock:
            ResourceCoordinator._cpu_sample = value
            ResourceCoordinator._cpu_sample_time = now
        return value

    def should_yield(self, aggressive: bool = False) -> bool:
        """
        Check if current operation should yield to prevent system overload.
        
        Args:
            aggressive: If True, use more aggressive thresholds for yielding
        
        Returns:
            True if operation should yield/pause
        """
        try:
            cpu_percent = self.get_cpu_percent()
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            
            # Use aggressive thresholds if requested, or if system is already overloaded
            cpu_threshold = 75.0 if aggressive or self._system_overload else 90.0
            memory_threshold = 75.0 if aggressive or self._system_overload else 90.0
            
            return cpu_percent > cpu_threshold or memory_percent > memory_threshold
        except Exception:
            return False
    
    def get_yield_duration(self) -> float:
        """
        Get recommended yield duration based on current system load.
        
        Returns:
            Seconds to sleep/yield (0.0 to 0.1)
        """
        try:
            cpu_percent = self.get_cpu_percent()

            if cpu_percent > 95:
                return 0.1  # 100ms for severe overload
            elif cpu_percent > 85:
                return 0.05  # 50ms for high load
            elif cpu_percent > 75:
                return 0.01  # 10ms for moderate load
            else:
                return 0.0  # No yield needed
        except Exception:
            return 0.01  # Default small yield if monitoring fails
    
    def get_resource_status(self) -> Dict:
        """Get current resource status"""
        instances = self._get_running_instances()
        try:
            cpu_percent = self.get_cpu_percent()
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_available_gb = memory.available / (1024**3)
        except Exception:
            cpu_percent = 0.0
            memory_percent = 0.0
            memory_available_gb = 0.0
        
        return {
            'instance_id': self.instance_id,
            'instance_type': self.instance_type,
            'cpu_count': self.cpu_count,
            'cpu_percent': cpu_percent,
            'memory_total_gb': self.total_memory_gb,
            'memory_percent': memory_percent,
            'memory_available_gb': memory_available_gb,
            'max_workers': self.resource_limits.max_workers,
            'db_pool_max': self.resource_limits.db_pool_max,
            'system_overload': getattr(self, '_system_overload', False),
            'running_instances': {
                'total': len(instances),
                'cli': sum(1 for inst in instances.values() if inst.get('type') == 'cli'),
                'web': sum(1 for inst in instances.values() if inst.get('type') == 'web')
            }
        }


# Global instance
_coordinator: Optional[ResourceCoordinator] = None
_coordinator_lock = threading.Lock()


def get_resource_coordinator() -> ResourceCoordinator:
    """Get the global resource coordinator instance"""
    global _coordinator
    if _coordinator is None:
        with _coordinator_lock:
            if _coordinator is None:
                _coordinator = ResourceCoordinator()
    return _coordinator


def get_safe_worker_count(requested: Optional[int] = None) -> int:
    """
    Get safe worker count for current instance.
    
    Args:
        requested: Requested worker count from settings
    
    Returns:
        Safe worker count
    """
    coordinator = get_resource_coordinator()
    return coordinator.get_safe_worker_count(requested)


def get_safe_db_pool_size(requested: Optional[int] = None) -> int:
    """
    Get safe database pool size for current instance.
    
    Args:
        requested: Requested pool size from settings
    
    Returns:
        Safe pool size
    """
    coordinator = get_resource_coordinator()
    return coordinator.get_safe_db_pool_size(requested)


def get_resource_status() -> Dict:
    """Get current resource status"""
    coordinator = get_resource_coordinator()
    return coordinator.get_resource_status()


def is_system_overloaded() -> bool:
    """
    Check if system is currently overloaded.
    
    Returns:
        True if system is overloaded (CPU > 85% or Memory > 85%)
    """
    coordinator = get_resource_coordinator()
    return coordinator.is_system_overloaded()

def should_yield(aggressive: bool = False) -> bool:
    """
    Check if current operation should yield to prevent system overload.
    
    Args:
        aggressive: If True, use more aggressive thresholds for yielding
    
    Returns:
        True if operation should yield/pause
    """
    coordinator = get_resource_coordinator()
    return coordinator.should_yield(aggressive)


def get_yield_duration() -> float:
    """
    Get recommended yield duration based on current system load.
    
    Returns:
        Seconds to sleep/yield (0.0 to 0.1)
    """
    coordinator = get_resource_coordinator()
    return coordinator.get_yield_duration()

