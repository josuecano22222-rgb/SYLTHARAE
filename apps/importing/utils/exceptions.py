"""
Custom Exceptions
Domain-specific exceptions for better error handling
"""


class DomainImportException(Exception):
    """Base exception for domain import operations"""
    pass


class DataLoadException(DomainImportException):
    """Exception raised when data loading fails"""
    pass


class DataParseException(DomainImportException):
    """Exception raised when data parsing fails"""
    pass


class ValidationException(DomainImportException):
    """Exception raised when validation fails"""
    pass


class DatabaseException(DomainImportException):
    """Exception raised when database operations fail"""
    pass


class ConnectionPoolException(DatabaseException):
    """Exception raised when connection pool operations fail"""
    pass


class RepositoryException(DatabaseException):
    """Exception raised when repository operations fail"""
    pass


class ProcessingException(DomainImportException):
    """Exception raised when term processing fails"""
    pass


class ConfigurationException(DomainImportException):
    """Exception raised when configuration is invalid"""
    pass
