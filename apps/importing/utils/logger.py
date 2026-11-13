"""
Logging Configuration
Centralized logging setup for the application
"""

import logging
import sys
from .constants import LOG_FORMAT, LOG_DATE_FORMAT


class LoggerFactory:
    """Factory for creating configured loggers"""
    
    _configured = False
    
    @classmethod
    def configure(cls, level=logging.INFO):
        """Configure logging for the application"""
        if cls._configured:
            return
        
        logging.basicConfig(
            level=level,
            format=LOG_FORMAT,
            datefmt=LOG_DATE_FORMAT,
            handlers=[
                logging.StreamHandler(sys.stdout)
            ]
        )
        cls._configured = True
    
    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        """Get a logger for a module"""
        if not cls._configured:
            cls.configure()
        return logging.getLogger(name)


def get_logger(name: str) -> logging.Logger:
    """Convenience function to get a logger"""
    return LoggerFactory.get_logger(name)
