"""
Concurrency Hub - Optional centralized access to all concurrency managers
Can be used for convenience, but all managers can also be used independently.
"""
from .thread_manager import ThreadManager
from .async_manager import AsyncManager
from .process_manager import ProcessManager
from .pool_manager import MultiprocessingManager


class ConcurrencyHub:
    """
    Optional hub for centralized access to all concurrency managers.
    All managers can be used independently without this hub.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
            cls._instance._thread_mgr = None
            cls._instance._async_mgr = None
            cls._instance._process_mgr = None
            cls._instance._pool_mgr = None
            cls._instance._monitoring_interval = 2.0  # Default value
        return cls._instance
    
    def set_monitoring_interval(self, interval: float = 2.0):
        """
        Set monitoring interval for all managers.
        
        Args:
            interval: Monitoring interval in seconds (default: 2.0)
        """
        self._monitoring_interval = interval
    
    
    @property
    def thread_mgr(self):
        """Lazy initialization of ThreadManager"""
        if self._thread_mgr is None:
            self._thread_mgr = ThreadManager()
            self._thread_mgr.start_monitoring(interval=self._monitoring_interval)
            print("[Hub] ThreadManager initialized")
        return self._thread_mgr
    
    @property
    def async_mgr(self):
        """Lazy initialization of AsyncManager"""
        if self._async_mgr is None:
            self._async_mgr = AsyncManager()
            self._async_mgr.initialize()
            print("[Hub] AsyncManager initialized")
        return self._async_mgr
    
    @property
    def process_mgr(self):
        """Lazy initialization of ProcessManager"""
        if self._process_mgr is None:
            self._process_mgr = ProcessManager()
            self._process_mgr.start_monitoring(interval=self._monitoring_interval)
            print("[Hub] ProcessManager initialized")
        return self._process_mgr
    
    @property
    def pool_mgr(self):
        """Lazy initialization of MultiprocessingManager"""
        if self._pool_mgr is None:
            self._pool_mgr = MultiprocessingManager()
            self._pool_mgr.start_monitoring(interval=self._monitoring_interval)
            print("[Hub] MultiprocessingManager initialized")
        return self._pool_mgr
    
    def is_initialized(self, manager_type: str = 'all') -> bool:
        """Check if managers are initialized"""
        if manager_type == 'all':
            return all([
                self._thread_mgr is not None,
                self._async_mgr is not None,
                self._process_mgr is not None,
                self._pool_mgr is not None
            ])
        elif manager_type == 'thread':
            return self._thread_mgr is not None
        elif manager_type == 'async':
            return self._async_mgr is not None
        elif manager_type == 'process':
            return self._process_mgr is not None
        elif manager_type == 'pool':
            return self._pool_mgr is not None
        return False
    
    def shutdown_all(self):
        """Clean shutdown of all initialized managers"""
        print("\n[Hub] Starting shutdown sequence...")
        
        # Shutdown in reverse order of dependency
        if self._async_mgr is not None:
            print("[Hub] Shutting down AsyncManager...")
            self._async_mgr.shutdown()
        
        if self._pool_mgr is not None:
            print("[Hub] Shutting down MultiprocessingManager...")
            self._pool_mgr.shutdown()
        
        if self._process_mgr is not None:
            print("[Hub] Shutting down ProcessManager...")
            self._process_mgr.shutdown()
        
        if self._thread_mgr is not None:
            print("[Hub] Shutting down ThreadManager...")
            self._thread_mgr.shutdown()
        
        print("[Hub] All managers shut down successfully")


# Optional global instance (can be imported or managers used directly)
hub = ConcurrencyHub()
