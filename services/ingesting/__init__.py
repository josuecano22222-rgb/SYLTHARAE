"""Ingestion service: files/folders into the analysis database.

Wraps the existing processing engine (``pipeline.integrated_reader.
IntegratedFileReader``) so the web frontend, the API and the CLI
compatibility layer all drive the exact same engine.
"""
from services.ingesting.service import IngestionService, IngestionRequest
from services.ingesting.options import IngestionOptions

__all__ = ["IngestionService", "IngestionRequest", "IngestionOptions"]
