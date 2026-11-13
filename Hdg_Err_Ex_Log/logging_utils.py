"""
Logging utilities - Independent action recorder
No dependencies on other project modules.
"""

import os
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional


class ActionRecorder:
    """
    Records command-line actions instantly to a log file.
    Thread-safe singleton pattern for global access.
    Independent module with no external dependencies.
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(ActionRecorder, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self.enabled = True
        self.log_file = None
        self.log_dir = Path("logs")
        self.log_dir.mkdir(exist_ok=True)
        self.file_lock = threading.Lock()
        self._initialized = True
    
    def start_recording(self, log_filename: Optional[str] = None) -> Path:
        """
        Start recording actions to a log file.
        
        Args:
            log_filename: Optional custom log filename
            
        Returns:
            Path to log file
        """
        if not log_filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_filename = f"action_log_{timestamp}.txt"
        
        self.log_file = self.log_dir / log_filename
        
        # Write header
        with open(self.log_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write(f"ACTION LOG - Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n\n")
        
        self.enabled = True
        self.record_action("SYSTEM", "Recording started", {"log_file": str(self.log_file)})
        return self.log_file
    
    def stop_recording(self):
        """Stop recording actions"""
        if self.enabled and self.log_file:
            self.record_action("SYSTEM", "Recording stopped", {})
            self.enabled = False
    
    def record_action(self, action_type: str, description: str, 
                     details: Optional[Dict[str, Any]] = None, level: str = "INFO"):
        """
        Record an action instantly.
        
        Args:
            action_type: Type of action (USER_INPUT, FUNCTION_CALL, FILE_OP, ERROR, etc.)
            description: Brief description of the action
            details: Dictionary with additional details
            level: Log level (INFO, WARNING, ERROR, DEBUG)
        """
        if not self.enabled or not self.log_file:
            return
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        log_entry = {
            "timestamp": timestamp,
            "level": level,
            "type": action_type,
            "description": description,
            "details": details or {}
        }
        
        # Format for human-readable log
        log_line = f"[{timestamp}] [{level}] [{action_type}] {description}"
        if details:
            # Format details nicely
            details_str = json.dumps(details, indent=2, default=str)
            log_line += f"\n  Details: {details_str}"
        log_line += "\n" + "-" * 80 + "\n"
        
        # Write instantly (thread-safe)
        with self.file_lock:
            try:
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(log_line)
                    f.flush()  # Ensure immediate write
            except Exception:
                # Silently fail if logging fails to avoid breaking the main app
                pass


# Global singleton instance
recorder = ActionRecorder()


def record_command_line_action(action_type: str, description: str, 
                              details: Optional[Dict[str, Any]] = None, level: str = "INFO"):
    """
    Record a command-line action.
    
    Args:
        action_type: Type of action
        description: Description of the action
        details: Optional details dictionary
        level: Log level
    """
    recorder.record_action(action_type, description, details, level)


def start_action_recording(log_filename: Optional[str] = None) -> Path:
    """
    Start action recording.
    
    Args:
        log_filename: Optional custom log filename
        
    Returns:
        Path to log file
    """
    recorder.start_recording(log_filename)
    return recorder.log_file


def stop_action_recording():
    """Stop action recording"""
    recorder.stop_recording()


def is_recording_enabled() -> bool:
    """
    Check if recording is enabled.
    
    Returns:
        True if recording is enabled
    """
    return recorder.enabled and recorder.log_file is not None


def get_log_file_path() -> Optional[Path]:
    """
    Get current log file path.
    
    Returns:
        Path to log file or None if recording is not enabled
    """
    if recorder.enabled and recorder.log_file:
        return recorder.log_file
    return None

