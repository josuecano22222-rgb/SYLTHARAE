import time
import uuid
import logging
from typing import Dict, Optional, List, Callable
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field

from concurrency import ThreadManager, ThreadPriority

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Task status enumeration"""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskProgress:
    """Task progress information"""
    task_id: str
    status: TaskStatus
    current: int = 0
    total: int = 0
    label: str = ""
    message: str = ""
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    logs: List[Dict[str, str]] = field(default_factory=list)
    # PROGRESS: dynamically discovered nested work (archive members, email
    # attachments, embedded Office/OpenDocument objects, recursive descendants).
    # ``total`` is the *discovered* total and grows while the task runs;
    # ``initial_total`` is the top-level count the task started with.
    nested: int = 0
    initial_total: int = 0
    detail: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        # ``total`` grows as containers are opened, so clamp: a task can never
        # be further along than the work discovered so far.
        percent = (self.current / self.total * 100) if self.total > 0 else 0.0
        return {
            'task_id': self.task_id,
            'status': self.status.value,
            'current': self.current,
            'total': self.total,
            'label': self.label,
            'message': self.message,
            'error': self.error,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'progress_percent': min(100.0, max(0.0, percent)),
            'nested': self.nested,
            'initial_total': self.initial_total,
            'total_is_dynamic': self.total != self.initial_total,
            'detail': dict(self.detail),
            'logs': self.logs[-50:]  # Last 50 log entries
        }


class FileProcessingTaskManager:
    """
    Manages background file processing tasks with progress tracking.
    Allows multiple tasks to run concurrently without blocking the web interface.
    """
    
    def __init__(self, max_concurrent_tasks: int = 8):
        """
        Initialize task manager.
        
        Args:
            max_concurrent_tasks: Maximum number of concurrent processing tasks
        """
        self.max_concurrent_tasks = max_concurrent_tasks
        self._tasks: Dict[str, TaskProgress] = {}
        
        self._thread_manager = ThreadManager()
        self._thread_manager.start_monitoring(interval=2.0)
        
        # Thread-safe locks (still needed for task management)
        import threading
        # AUDIT (CONC-02): ``_task_lock`` is acquired recursively in six
        # places - ``_update_task_error``, ``pause_task``, ``resume_task`` and
        # three terminal branches of ``_process_task`` all call
        # ``_add_task_log`` (which locks again) while already holding it. With
        # a plain Lock the first task to reach any terminal state
        # self-deadlocked and permanently wedged ``_task_lock``, so every
        # later /upload/* request (create_task, get_task_progress, pause,
        # resume, cancel) blocked forever and leaked a worker thread.
        #
        # LOCK ORDER (mandatory everywhere): ``_pause_lock`` -> ``_task_lock``.
        # ``wait_if_paused`` runs inside every processing loop and takes them
        # in that order; taking them the other way round deadlocks against it
        # (ABBA), which RLock alone cannot fix.
        self._task_lock = threading.RLock()
        self._active_thread_ids: Dict[str, str] = {}  # Map task_id -> thread_id
        self._thread_lock = threading.Lock()
        # Pause control: track paused tasks and their pause events
        self._paused_tasks: Dict[str, threading.Event] = {}
        self._pause_lock = threading.Lock()
        
    def create_task(
        self,
        file_path: str,
        source_id: int,
        side_id: int,
        task_name: Optional[str] = None
    ) -> str:
        """
        Create a new processing task and start it in the background.
        
        Args:
            file_path: Path to file or directory to process
            source_id: Source ID for storage
            side_id: Side ID for storage
            task_name: Optional task name for identification
        
        Returns:
            Task ID for tracking progress
        """
        task_id = str(uuid.uuid4())
        
        # Check if we can start a new task
        with self._thread_lock:
            stats = self._thread_manager.get_statistics()
            active_count = stats.get('running', 0)
            if active_count >= self.max_concurrent_tasks:
                raise RuntimeError(f"Maximum concurrent tasks ({self.max_concurrent_tasks}) reached")
        
        # Create task progress tracker
        progress = TaskProgress(
            task_id=task_id,
            status=TaskStatus.PENDING,
            label="Initializing...",
            message=f"Preparing to process: {file_path}",
            started_at=datetime.now()
        )
        
        with self._task_lock:
            self._tasks[task_id] = progress
        
        thread_name = f"task_{task_id[:8]}_{task_name or 'processing'}"
        thread_id = self._thread_manager.create_thread(
            name=thread_name,
            target=self._process_task,
            args=(task_id, file_path, source_id, side_id, task_name),
            priority=ThreadPriority.NORMAL,
            auto_start=True
        )
        
        with self._thread_lock:
            self._active_thread_ids[task_id] = thread_id
        
        logger.info(f"Created processing task {task_id} for {file_path} (thread: {thread_id})")
        
        return task_id
    
    def get_task_progress(self, task_id: str) -> Optional[TaskProgress]:
        """
        Get progress for a task.
        
        Args:
            task_id: Task ID
        
        Returns:
            TaskProgress object or None if task not found
        """
        with self._task_lock:
            return self._tasks.get(task_id)
    
    def get_all_tasks(self) -> List[TaskProgress]:
        """Get all tasks"""
        with self._task_lock:
            return list(self._tasks.values())
    
    def pause_task(self, task_id: str) -> bool:
        """
        Pause a running task.
        
        Args:
            task_id: Task ID
        
        Returns:
            True if task was paused, False if not found or cannot be paused
        """
        # AUDIT (CONC-02): lock order _pause_lock -> _task_lock (see the
        # constructor note). Logging moved outside both critical sections.
        with self._pause_lock:
            with self._task_lock:
                if task_id not in self._tasks:
                    return False

                progress = self._tasks[task_id]
                if progress.status not in (TaskStatus.RUNNING, TaskStatus.PENDING):
                    return False

                # Set status to paused
                progress.status = TaskStatus.PAUSED
                progress.message = "Task paused by user"

            # Create or get pause event (held under _pause_lock only)
            if task_id not in self._paused_tasks:
                self._paused_tasks[task_id] = threading.Event()
            # Set the event to signal pause
            self._paused_tasks[task_id].set()

        self._add_task_log(task_id, 'info', 'Task paused by user')
        logger.info(f"Task {task_id} paused")
        return True
    
    def resume_task(self, task_id: str) -> bool:
        """
        Resume a paused task.
        
        Args:
            task_id: Task ID
        
        Returns:
            True if task was resumed, False if not found or cannot be resumed
        """
        # AUDIT (CONC-02): lock order _pause_lock -> _task_lock; logging
        # outside both critical sections.
        with self._pause_lock:
            with self._task_lock:
                if task_id not in self._tasks:
                    return False

                progress = self._tasks[task_id]
                if progress.status != TaskStatus.PAUSED:
                    return False

                # Set status back to running
                progress.status = TaskStatus.RUNNING
                progress.message = "Task resumed by user"

            # Clear the pause event to allow processing to continue
            if task_id in self._paused_tasks:
                self._paused_tasks[task_id].clear()

        self._add_task_log(task_id, 'info', 'Task resumed by user')
        logger.info(f"Task {task_id} resumed")
        return True
    
    def is_task_paused(self, task_id: str) -> bool:
        """
        Check if a task is currently paused.
        
        Args:
            task_id: Task ID
        
        Returns:
            True if task is paused, False otherwise
        """
        with self._pause_lock:
            if task_id not in self._paused_tasks:
                return False
            return self._paused_tasks[task_id].is_set()
    
    def wait_if_paused(self, task_id: str, timeout: float = 0.1) -> bool:
        """
        Wait if task is paused. This should be called periodically in processing loops.
        
        Args:
            task_id: Task ID
            timeout: Timeout in seconds to check pause status
        
        Returns:
            True if task should continue (not paused or resumed), False if cancelled
        """
        with self._task_lock:
            if task_id not in self._tasks:
                return False

            progress = self._tasks[task_id]
            if progress.status == TaskStatus.CANCELLED:
                return False

        # AUDIT (CONC-04): ``_pause_lock`` must NEVER be held while waiting.
        #
        # The original code entered the ``while event.is_set():`` loop *inside*
        # ``with self._pause_lock:``, so a paused task held the lock for the
        # entire duration of the pause. ``resume_task`` must acquire that same
        # lock to ``clear()`` the event, and ``cancel_task`` needs it to clear
        # the event as well - so both blocked forever on any paused task while
        # the waiting thread blocked on an event only they could clear. A
        # textbook deadlock, and one no amount of re-entrancy fixes: it is a
        # classic "wait while holding the lock a rescuer needs" bug.
        #
        # The fix is to take the lock only for the dictionary lookup, and do
        # all waiting outside it. The event object itself is stable once
        # fetched (``pause_task`` creates it once and only ever ``set()``s or
        # ``clear()``s it; it is only *removed* from the dict after the task
        # reaches a terminal state), so holding a reference across the wait is
        # safe.
        event = self._get_pause_event(task_id)
        if event is None or not event.is_set():
            return True

        while event.is_set():
            # Check for cancellation between waits
            with self._task_lock:
                if task_id in self._tasks:
                    if self._tasks[task_id].status == TaskStatus.CANCELLED:
                        return False
                else:
                    return False

            # Wait without holding any lock, so resume/cancel can proceed.
            event.wait(timeout=timeout)

            # Re-fetch: resume or cancel may have replaced/cleared the event.
            current = self._get_pause_event(task_id)
            if current is None:
                return True
            event = current

        return True

    def _get_pause_event(self, task_id: str):
        """Fetch the pause Event for a task under ``_pause_lock``.

        Helper for :meth:`wait_if_paused` so the lock is held only for the
        dictionary lookup and never across a blocking wait.
        """
        with self._pause_lock:
            return self._paused_tasks.get(task_id)
    
    def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a running task.
        
        Args:
            task_id: Task ID
        
        Returns:
            True if task was cancelled, False if not found or already completed
        """
        # AUDIT (CONC-02): lock order _pause_lock -> _task_lock; previously
        # inverted and racing wait_if_paused, which takes them the other way.
        with self._pause_lock:
            with self._task_lock:
                if task_id not in self._tasks:
                    return False

                progress = self._tasks[task_id]
                if progress.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                    return False

                progress.status = TaskStatus.CANCELLED
                progress.message = "Task cancelled by user"
                progress.completed_at = datetime.now()

            # Clear pause event if exists to allow cancellation to proceed
            if task_id in self._paused_tasks:
                self._paused_tasks[task_id].clear()

        return True
    
    def _process_task(
        self,
        task_id: str,
        file_path: str,
        source_id: int,
        side_id: int,
        task_name: Optional[str]
    ):
        """
        Process a file in the background.
        This runs in a separate thread and updates progress as it goes.
        """
        try:
            # Update status to running
            with self._task_lock:
                if task_id in self._tasks:
                    self._tasks[task_id].status = TaskStatus.RUNNING
                    self._tasks[task_id].label = "Starting processing..."
                    self._tasks[task_id].message = f"Processing: {file_path}"
            
            # Import here to avoid circular dependencies
            import os
            import sys
            from pathlib import Path
            
            # Add project root to path
            project_root = str(Path(__file__).parent.parent.parent.parent)
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            
            from pipeline.integrated_reader import IntegratedFileReader  
            from core.validation.validation import PathValidator, ValidationError
            from Hdg_Err_Ex_Log import handle_error, ErrorCategory, ErrorSeverity, ErrorContext
            
            # Validate and normalize path
            try:
                normalized_path = file_path.strip().strip('"\'')
                normalized_path = os.path.expanduser(normalized_path)
                normalized_path = os.path.abspath(normalized_path)
                
                if os.path.isfile(normalized_path):
                    validated_path = PathValidator.validate_file_path(normalized_path)
                    is_directory = False
                elif os.path.isdir(normalized_path):
                    validated_path = PathValidator.validate_directory_path(normalized_path)
                    is_directory = True
                else:
                    raise ValidationError(f"Path does not exist: {normalized_path}")
            except ValidationError as e:
                self._update_task_error(task_id, f"Validation error: {str(e)}")
                return
            
            try:



                # AUDIT (ING-01): the request carries source/side *ids*, but
                # this code looked them up by name. The lookup never matched,
                # so ``source_name``/``side_name`` stayed unbound and the
                # reader constructor below raised
                # ``UnboundLocalError: cannot access local variable
                # 'source_name'`` - every server-path ingestion task failed
                # with "Failed to initialize IntegratedFileReader".
                from database import get_source_by_id, get_side_by_id

                source_name = None
                side_name = None

                source_info = get_source_by_id(source_id)
                if source_info:
                    source_name = source_info.get('name')

                side_info = get_side_by_id(side_id)
                if side_info:
                    side_name = side_info.get('name')

                if not source_name or not side_name:
                    raise ValueError(
                        f"Unknown source/side for ids {source_id}/{side_id}"
                    )
            except Exception as e:
                logger.warning(f"Could not get source/side names: {e}, using defaults")
                self._update_task_error(
                    task_id,
                    f"Invalid source/side: {e}",
                )
                return
            
            # Initialize reader with storage enabled
            reader = None
            try:
                # AUDIT (ING-02): ``monitor_interval`` is not a parameter of
                # IntegratedFileReader.__init__ (see pipeline/integrated_reader.py),
                # so every task died here with a TypeError. Removed - the
                # remaining kwargs match the real signature.
                reader = IntegratedFileReader(
                    max_workers=self.max_concurrent_tasks,
                    enable_monitoring=True,
                    use_priority=True,
                    enable_storage=True,
                    storage_source=source_name,
                    storage_side=side_name
                )
                # AUDIT (ING-03): ``reader.initialize()`` removed -
                # IntegratedFileReader has no such method, so every task
                # failed with AttributeError here. Storage is already wired up
                # by ``__init__``, which calls ``_init_storage()`` itself.
            except Exception as e:
                logger.error(f"Failed to initialize IntegratedFileReader: {e}", exc_info=True)
                self._update_task_error(task_id, f"Failed to initialize reader: {str(e)}")
                return
            
            # Track progress
            file_count = [0]
            last_stats_update = [time.time()]
            STATS_UPDATE_INTERVAL = 1.0  # Update stats every second
            
            def update_progress_from_stats():
                """Publish the engine's live progress into the task record.

                PROGRESS: this used to read ``get_statistics()`` and then gate
                the whole update behind ``if total > 0``. For a single-file task
                ``total`` stayed 0 for the entire run (only ``process_folder``
                ever set it), so *no* intermediate update was ever published and
                the bar jumped straight from nothing to the final
                ``current=1, total=1``. It also knew nothing about nested
                objects, which live in the same shared ledger now.
                """
                try:
                    live = reader.get_live_progress()
                    total = live.get('total_files', 0) or 0
                    done = live.get('files_done', 0) or 0
                    completed = live.get('files_completed', 0) or 0
                    failed = live.get('files_failed', 0) or 0
                    skipped = live.get('files_skipped', 0) or 0
                    unsupported = live.get('files_unsupported', 0) or 0
                    retryable = live.get('files_retryable', 0) or 0
                    locked = live.get('files_locked', 0) or 0
                    cancelled = live.get('files_cancelled', 0) or 0
                    in_progress = live.get('in_progress', 0) or 0
                    nested = live.get('files_nested', 0) or 0
                    initial = live.get('files_initial', 0) or 0
                    pending = live.get('files_pending', 0) or 0
                    percent = live.get('percent', 0) or 0

                    # Publish even at total == 0 so the UI shows a live task
                    # instead of nothing at all while discovery is running.
                    progress_label = f"Processing: {done}/{total} files ({percent:.0f}%)"
                    if nested:
                        progress_label += f" | {nested} nested (of {initial} top-level)"
                    if failed:
                        progress_label += f" | Failed: {failed}"
                    if in_progress:
                        progress_label += f" | In Progress: {in_progress}"

                    detail_message = (
                        f"Completed: {completed}, Failed: {failed}, "
                        f"Skipped: {skipped}, Unsupported: {unsupported}, "
                        f"Retryable: {retryable}, Locked: {locked}, "
                        f"Cancelled: {cancelled}, Running: {in_progress}, "
                        f"Queued: {pending}"
                    )
                    phase = live.get('current_phase')
                    current_file = live.get('current_file')
                    if phase:
                        detail_message = f"{phase} | {detail_message}"
                    if current_file:
                        detail_message += f" | {os.path.basename(str(current_file))}"

                    self._update_task_progress(
                        task_id,
                        current=done,
                        total=max(total, done),
                        label=progress_label,
                        message=detail_message,
                        nested=nested,
                        initial_total=initial or total,
                        detail={
                            'completed': completed,
                            'failed': failed,
                            'skipped': skipped,
                            'unsupported': unsupported,
                            'retryable': retryable,
                            'locked': locked,
                            'cancelled': cancelled,
                            'in_progress': in_progress,
                            'pending': pending,
                            'percent': int(percent),
                            'containers_opened': live.get('containers_opened', 0) or 0,
                        }
                    )

                    # Add log entry periodically
                    current_time = time.time()
                    if current_time - last_stats_update[0] >= STATS_UPDATE_INTERVAL:
                        self._add_task_log(
                            task_id,
                            'info',
                            f"Progress: {done}/{total} files ({percent:.0f}%) | {detail_message}"
                        )
                        last_stats_update[0] = current_time
                except Exception as e:
                    logger.debug(f"Error updating progress from stats: {e}")
            
            # Process with IntegratedFileReader
            with ErrorContext(
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={"file_path": validated_path, "source_id": source_id, "side_id": side_id}
            ):
                try:
                    monitoring_active = [True]
                    
                    def progress_monitor():
                        """Monitor progress and update task status"""
                        while monitoring_active[0]:
                            if not self.wait_if_paused(task_id, timeout=1.0):
                                break
                            
                            # Check if task is cancelled
                            with self._task_lock:
                                if task_id in self._tasks:
                                    if self._tasks[task_id].status == TaskStatus.CANCELLED:
                                        break
                            
                            # Update progress from reader stats
                            update_progress_from_stats()
                            
                            # Check if reader is complete
                            try:
                                if reader.is_complete():
                                    break
                            except Exception as e:
                                # Log but continue - reader may not have is_complete method
                                logger.debug(f"Error checking reader completion: {e}")
                                pass
                            
                            time.sleep(0.5)  # Check every 0.5 seconds
                    
                    monitor_thread_id = self._thread_manager.create_thread(
                        name=f"monitor_{task_id[:8]}",
                        target=progress_monitor,
                        priority=ThreadPriority.LOW,
                        auto_start=True
                    )
                    
                    # Process file or folder
                    results = []
                    if is_directory:
                        results = reader.process_folder(validated_path)
                    else:
                        result = reader.process_single_file(validated_path)
                        results = [result] if result else []
                    
                    monitoring_active[0] = False
                    try:
                        self._thread_manager.stop_thread(monitor_thread_id, timeout=2.0)
                    except Exception as e:
                        logger.debug(f"Error stopping monitor thread: {e}")
                    
                    # Final progress update.
                    # AUDIT (ING-04) + PROGRESS: the ledger is authoritative for
                    # both the folder and the single-file path, and it includes
                    # nested objects (archive members, attachments, embedded
                    # objects) that ``get_statistics()`` never counted. Fall
                    # back to the real result count only if the ledger somehow
                    # reported nothing.
                    live = reader.get_live_progress()
                    final_completed = live.get('files_done') or len([r for r in results if r])
                    final_total = live.get('total_files') or len(results)
                    final_failed = (live.get('files_failed') or 0) + (live.get('files_retryable') or 0)
                    
                    # Check for cancellation
                    with self._task_lock:
                        if task_id in self._tasks:
                            if self._tasks[task_id].status == TaskStatus.CANCELLED:
                                self._add_task_log(task_id, 'info', 'Processing cancelled')
                                return
                    
                    # Log final results
                    if results:
                        for result in results:
                            if result:
                                file_name = result.get('Metadata', {}).get('name', 'Unknown')
                                file_path = result.get('Metadata', {}).get('path', '')
                                file_type = result.get('Metadata', {}).get('type', 'Unknown')
                                file_size = result.get('Metadata', {}).get('size_bytes', 0)
                                has_error = bool(result.get('Content', {}).get('error'))
                                
                                log_lines = []
                                log_lines.append(f"File: {file_name}")
                                log_lines.append(f"Path: {file_path}")
                                log_lines.append(f"Type: {file_type}")
                                log_lines.append(f"Size: {file_size:,} bytes")
                                log_lines.append(f"Status: {'Successful' if not has_error else 'Failed'}")
                                log_lines.append(f"Processing Status: {'Completed' if not has_error else 'Failed'}")
                                log_lines.append(f"Storage Status: Stored")
                                
                                self._add_task_log(task_id, 'info' if not has_error else 'error', "\n".join(log_lines))
                    
                except KeyboardInterrupt:
                    # Task was cancelled
                    with self._task_lock:
                        if task_id in self._tasks:
                            if self._tasks[task_id].status == TaskStatus.CANCELLED:
                                self._add_task_log(task_id, 'info', 'Processing cancelled')
                                return
                finally:
                    # Cleanup reader
                    # AUDIT (ING-05): IntegratedFileReader has no ``shutdown()``
                    # - every task logged "Error during reader cleanup". The
                    # context-manager protocol is the supported teardown path
                    # (joins threads, closes the pool, closes the DB hub and
                    # finalizes the checkpoint), so use ``__exit__`` instead.
                    if reader:
                        try:
                            reader.__exit__(None, None, None)
                        except Exception as cleanup_error:
                            logger.warning(f"Error during reader cleanup: {cleanup_error}")
            
            # 🔔 FINAL NOTIFICATION FLUSH: Ensure all notifications are persisted
            try:
                from core.monitoring.notification_service import get_notification_service
                notification_service = get_notification_service()
                pending_count = notification_service.get_pending_count()
                if pending_count > 0:
                    flushed = notification_service.flush_pending_notifications()
                    if flushed > 0:
                        self._add_task_log(task_id, 'info', f"Flushed {flushed} notification(s) to database")
                        logger.info(f"Flushed {flushed} notification(s) after processing completion")
            except Exception as e:
                logger.warning(f"Could not flush notifications after processing: {e}")
            
            # Get final statistics
            final_nested = 0
            live = {}
            try:
                if reader:
                    # PROGRESS: single source of truth for the closing figures.
                    live = reader.get_live_progress()
                    final_completed = live.get('files_done') or len([r for r in results if r])
                    final_total = live.get('total_files') or len(results)
                    final_failed = (live.get('files_failed') or 0) + (live.get('files_retryable') or 0)
                    final_nested = live.get('files_nested') or 0
                    final_skipped = live.get('files_skipped') or 0
                    final_unsupported = live.get('files_unsupported') or 0
                else:
                    final_completed = len(results) if results else 0
                    final_total = final_completed
                    final_failed = 0
                    final_skipped = 0
                    final_unsupported = 0
            except Exception:
                final_completed = len(results) if results else 0
                final_total = final_completed
                final_failed = 0
                final_skipped = 0
                final_unsupported = 0

            # Mark as completed
            completion_message = f"Successfully processed {final_completed} file(s)"
            if final_nested:
                completion_message += f", including {final_nested} nested object(s)"
            if final_failed > 0:
                completion_message += f" ({final_failed} failed)"
            if final_skipped > 0:
                completion_message += f" ({final_skipped} skipped)"
            if final_unsupported > 0:
                completion_message += f" ({final_unsupported} unsupported)"

            self._update_task_progress(
                task_id,
                current=final_completed,
                total=final_total if final_total > 0 else final_completed,
                label="Completed",
                message=completion_message,
                nested=final_nested,
                initial_total=(live.get('files_initial') if reader else None),
                detail={
                    'completed': final_completed - final_failed,
                    'failed': final_failed,
                    'skipped': final_skipped,
                    'unsupported': final_unsupported,
                    'nested': final_nested,
                    'percent': 100,
                }
            )
            
            with self._task_lock:
                if task_id in self._tasks:
                    self._tasks[task_id].status = TaskStatus.COMPLETED
                    self._tasks[task_id].completed_at = datetime.now()
                    self._add_task_log(task_id, 'success', f"Processing completed successfully: {final_completed} files processed")
            
            logger.info(f"Task {task_id} completed successfully: {final_completed} files processed")
            
        except Exception as e:
            logger.error(f"Error processing task {task_id}: {e}", exc_info=True)
            self._update_task_error(task_id, f"Processing error: {str(e)}")
        finally:
            # Clean up thread reference and pause event
            with self._thread_lock:
                thread_id = self._active_thread_ids.pop(task_id, None)
                if thread_id:
                    try:
                        self._thread_manager.stop_thread(thread_id, timeout=5.0)
                    except Exception as e:
                        logger.debug(f"Error stopping thread {thread_id}: {e}")
            with self._pause_lock:
                self._paused_tasks.pop(task_id, None)
    
    def _update_task_progress(
        self,
        task_id: str,
        current: int,
        total: int,
        label: str = "",
        message: str = "",
        nested: Optional[int] = None,
        initial_total: Optional[int] = None,
        detail: Optional[Dict[str, int]] = None
    ):
        """Update task progress.

        ``total`` is the dynamically discovered total; it is only ever raised
        here so a late, smaller snapshot cannot make the bar appear to go
        backwards while nested discovery is still settling.
        """
        with self._task_lock:
            if task_id in self._tasks:
                progress = self._tasks[task_id]
                progress.current = max(0, int(current or 0))
                progress.total = max(int(total or 0), progress.total)
                if nested is not None:
                    progress.nested = max(0, int(nested))
                if initial_total is not None:
                    progress.initial_total = max(0, int(initial_total))
                if detail:
                    progress.detail = {k: int(v or 0) for k, v in detail.items()}
                if label:
                    progress.label = label
                if message:
                    progress.message = message
    
    def _update_task_error(self, task_id: str, error: str):
        """Update task with error"""
        with self._task_lock:
            if task_id in self._tasks:
                progress = self._tasks[task_id]
                progress.status = TaskStatus.FAILED
                progress.error = error
                progress.message = f"Error: {error}"
                progress.completed_at = datetime.now()
                self._add_task_log(task_id, 'error', error)
    
    def _add_task_log(self, task_id: str, log_type: str, message: str):
        """Add log entry to task"""
        with self._task_lock:
            if task_id in self._tasks:
                self._tasks[task_id].logs.append({
                    'type': log_type,
                    'message': message,
                    'timestamp': datetime.now().isoformat()
                })
                # Keep only last 1000 log entries
                if len(self._tasks[task_id].logs) > 1000:
                    self._tasks[task_id].logs = self._tasks[task_id].logs[-1000:]
    
    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """
        Clean up old completed/failed tasks.
        
        Args:
            max_age_hours: Maximum age in hours for tasks to keep
        """
        cutoff_time = datetime.now().timestamp() - (max_age_hours * 3600)
        
        with self._task_lock:
            tasks_to_remove = []
            for task_id, progress in self._tasks.items():
                if progress.completed_at:
                    task_time = progress.completed_at.timestamp()
                    if task_time < cutoff_time:
                        tasks_to_remove.append(task_id)
            
            for task_id in tasks_to_remove:
                del self._tasks[task_id]
                logger.debug(f"Cleaned up old task: {task_id}")
            
            if tasks_to_remove:
                logger.info(f"Cleaned up {len(tasks_to_remove)} old task(s)")


# Global task manager instance
_task_manager: Optional[FileProcessingTaskManager] = None
import threading
_manager_lock = threading.Lock()


def get_task_manager() -> FileProcessingTaskManager:
    """Get or create global task manager instance"""
    global _task_manager
    
    with _manager_lock:
        if _task_manager is None:
            _task_manager = FileProcessingTaskManager(max_concurrent_tasks=5)
        
        return _task_manager

