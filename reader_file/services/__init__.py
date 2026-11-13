"""
Reader Services Module
Aligned with database design principles

This module provides service classes for file reading operations,
following the same design pattern as the database layer.
"""

from .file_reader_service import FileReaderService, get_file_reader_service
from .file_router_service import FileRouterService, get_file_router_service

__all__ = [
    'FileReaderService',
    'get_file_reader_service',
    'FileRouterService',
    'get_file_router_service',
]
