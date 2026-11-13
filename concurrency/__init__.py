"""
Concurrency Management Library - Independent modules for thread, process, async, and pool management
All modules are standalone and can be used independently in any project.

This library provides four core managers:
- ThreadManager: For thread-based concurrency
- ProcessManager: For process-based concurrency
- AsyncManager: For async/await concurrency
- MultiprocessingManager: For worker pool concurrency

Plus a ConcurrencyHub for centralized access to all managers.

Usage:
    from concurrency import ThreadManager, hub
    
    # Use individual manager
    thread_mgr = ThreadManager()
    
    # Or use hub
    hub.thread_mgr.create_thread(...)
"""

from .thread_manager import (
    ThreadManager,
    ManagedThread,
    ThreadPriority,
    ThreadState,
    ThreadMetrics
)

from .process_manager import (
    ProcessManager,
    ManagedProcess,
    ProcessPriority,
    ProcessState,
    ProcessMetrics
)

from .async_manager import (
    AsyncManager,
    ManagedTask,
    TaskPriority,
    TaskState,
    TaskMetrics
)

from .pool_manager import (
    MultiprocessingManager,
    ManagedPool,
    PoolPriority,
    PoolState,
    PoolMetrics,
    TaskResult
)

from .hub import ConcurrencyHub, hub

__all__ = [
    # Thread management
    'ThreadManager',
    'ManagedThread',
    'ThreadPriority',
    'ThreadState',
    'ThreadMetrics',
    # Process management
    'ProcessManager',
    'ManagedProcess',
    'ProcessPriority',
    'ProcessState',
    'ProcessMetrics',
    # Async management
    'AsyncManager',
    'ManagedTask',
    'TaskPriority',
    'TaskState',
    'TaskMetrics',
    # Pool management
    'MultiprocessingManager',
    'ManagedPool',
    'PoolPriority',
    'PoolState',
    'PoolMetrics',
    'TaskResult',
    # Hub (centralized access)
    'ConcurrencyHub',
    'hub',
]
