"""
Input Validation Module
Comprehensive validation for all inputs
"""

import os
from pathlib import Path
from typing import List, Optional, Union
import re


class ValidationError(Exception):
    """Custom exception for validation errors"""
    pass


class PathValidator:
    """Validates file and directory paths"""
    
    @staticmethod
    def validate_file_path(file_path: str, max_size_bytes: Optional[int] = None) -> str:
        """
        Validate and normalize file path.
        
        Args:
            file_path: Path to validate
            max_size_bytes: Optional maximum file size in bytes (default: None, no limit)
                            NOTE: This parameter is deprecated - no file size limits are enforced
            
        Returns:
            Normalized file path string
            
        Raises:
            ValidationError: If validation fails
        """
        if not file_path:
            raise ValidationError("File path cannot be empty")
        
        path = Path(file_path).resolve()
        
        if not path.exists():
            raise ValidationError(f"File does not exist: {file_path}")
        
        if not path.is_file():
            raise ValidationError(f"Path is not a file: {file_path}")
        
        if not os.access(path, os.R_OK):
            raise ValidationError(f"File is not readable: {file_path}")
        
        # File size validation removed - no limitations on file size
        # All files of any size are supported
        
        return str(path)
    
    @staticmethod
    def validate_directory_path(dir_path: str) -> str:
        """Validate and normalize directory path"""
        if not dir_path:
            raise ValidationError("Directory path cannot be empty")
        
        path = Path(dir_path).resolve()
        
        if not path.exists():
            raise ValidationError(f"Directory does not exist: {dir_path}")
        
        if not path.is_dir():
            raise ValidationError(f"Path is not a directory: {dir_path}")
        
        if not os.access(path, os.R_OK):
            raise ValidationError(f"Directory is not readable: {dir_path}")
        
        return str(path)
    
    @staticmethod
    def validate_paths_exist(paths: List[str]) -> List[str]:
        """Validate multiple paths"""
        validated_paths = []
        
        for path in paths:
            try:
                if Path(path).is_file():
                    validated_paths.append(PathValidator.validate_file_path(path))
                elif Path(path).is_dir():
                    validated_paths.append(PathValidator.validate_directory_path(path))
                else:
                    raise ValidationError(f"Path does not exist: {path}")
            except ValidationError as e:
                print(f"Warning: {e}")
                continue
        
        return validated_paths


class DatabaseValidator:
    """Validates database configuration"""
    
    @staticmethod
    def validate_host(host: str) -> str:
        """Validate database host"""
        if not host:
            raise ValidationError("Database host cannot be empty")
        
        # Basic hostname/IP validation
        if not re.match(r'^[a-zA-Z0-9.-]+$', host):
            raise ValidationError("Invalid database host format")
        
        return host
    
    @staticmethod
    def validate_port(port: Union[str, int]) -> int:
        """Validate database port"""
        try:
            port_int = int(port)
            if not (1 <= port_int <= 65535):
                raise ValidationError("Database port must be between 1 and 65535")
            return port_int
        except ValueError:
            raise ValidationError("Database port must be a valid integer")
    
    @staticmethod
    def validate_database_name(db_name: str) -> str:
        """Validate database name"""
        if not db_name:
            raise ValidationError("Database name cannot be empty")
        
        # PostgreSQL database name validation
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', db_name):
            raise ValidationError("Invalid database name format")
        
        return db_name


class ProcessingValidator:
    """Validates processing configuration"""
    
    @staticmethod
    def validate_max_workers(workers: Union[str, int]) -> int:
        """Validate max workers"""
        try:
            workers_int = int(workers)
            if workers_int < 1:
                raise ValidationError("Max workers must be at least 1")
            if workers_int > 100:
                raise ValidationError("Max workers cannot exceed 100")
            return workers_int
        except ValueError:
            raise ValidationError("Max workers must be a valid integer")
    
    @staticmethod
    def validate_chunk_size(size: Union[str, int]) -> int:
        """Validate chunk size"""
        try:
            size_int = int(size)
            if size_int < 1024:
                raise ValidationError("Chunk size must be at least 1KB")
            if size_int > 100 * 1024 * 1024:  # 100MB
                raise ValidationError("Chunk size cannot exceed 100MB")
            return size_int
        except ValueError:
            raise ValidationError("Chunk size must be a valid integer")
    
    @staticmethod
    def validate_max_depth(depth: Union[str, int]) -> int:
        """Validate max depth"""
        try:
            depth_int = int(depth)
            if depth_int < 1:
                raise ValidationError("Max depth must be at least 1")
            if depth_int > 50:
                raise ValidationError("Max depth cannot exceed 50")
            return depth_int
        except ValueError:
            raise ValidationError("Max depth must be a valid integer")


class ConfigValidator:
    """Main configuration validator"""
    
    @staticmethod
    def validate_all(config_dict: dict) -> dict:
        """Validate entire configuration"""
        validated = {}
        
        # Database validation - always include defaults if section missing
        db_config = config_dict.get('database', {})
        validated['database'] = {
            'host': DatabaseValidator.validate_host(db_config.get('host', 'localhost')),
            'user': db_config.get('user', 'postgres'),
            'database': DatabaseValidator.validate_database_name(db_config.get('database', 'analysis')),
            'port': DatabaseValidator.validate_port(db_config.get('port', 5432)),
            'password': db_config.get('password', '')
        }
        
        # Processing validation - always include defaults if section missing
        proc_config = config_dict.get('processing', {})
        validated['processing'] = {
            'max_workers': ProcessingValidator.validate_max_workers(proc_config.get('max_workers', 10)),
            'chunk_size': ProcessingValidator.validate_chunk_size(proc_config.get('chunk_size', 1024 * 1024)),
            'timeout': int(proc_config.get('timeout', 300)),
            'parallel_processing': bool(proc_config.get('parallel_processing', True)),
            'extract_archives': bool(proc_config.get('extract_archives', True)),
            'extract_attachments': bool(proc_config.get('extract_attachments', True)),
            'process_nested': bool(proc_config.get('process_nested', True)),
            'max_depth': ProcessingValidator.validate_max_depth(proc_config.get('max_depth', 10)),
            'calculate_hashes': bool(proc_config.get('calculate_hashes', True)),
            'hash_algorithm': proc_config.get('hash_algorithm', 'sha256')
        }
        
        return validated
