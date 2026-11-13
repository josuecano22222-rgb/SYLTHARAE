"""
Content Processors Module
Optimized unified extraction system for the entire project.
"""

from .content_processor import ContentProcessor, get_content_processor

# Global content processor instance (singleton, cached patterns)
content_processor = get_content_processor()

__all__ = ['ContentProcessor', 'get_content_processor', 'content_processor']