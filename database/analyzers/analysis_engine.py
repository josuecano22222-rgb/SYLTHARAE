"""
Content Analysis Engine - analyzes content using database queries.
"""

import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AnalysisConfig:
    """Configuration for content analysis"""
    max_results: int = 100
    min_relevance: float = 0.0
    enable_caching: bool = True


class ContentAnalysisEngine:
    """
    Content analysis engine for analyzing document content.
    Uses database queries to extract insights from stored content.
    """
    
    def __init__(self, db_hub=None, config: Optional[AnalysisConfig] = None):
        """
        Initialize content analysis engine.
        
        Args:
            db_hub: DatabaseHub instance (optional)
            config: AnalysisConfig instance (optional)
        """
        self.db_hub = db_hub
        self.config = config or AnalysisConfig()
        self.logger = logger
    
    def analyze_content(self, path_id: int) -> Dict[str, Any]:
        """
        Analyze content for a given path ID.
        
        Args:
            path_id: Path ID to analyze
            
        Returns:
            Dictionary with analysis results
        """
        try:
            from database.services.contents_db_service import ContentDBService
            db_service = ContentDBService()
            
            # Get content as text
            content = db_service.get_content_as_text(path_id)
            
            # Get content as array with words and tags
            content_array = db_service.get_content_as_array(path_id)
            
            # Basic analysis
            word_count = len(content_array) if content_array else 0
            unique_words = len(set(item.get('word_id') for item in content_array)) if content_array else 0
            
            # Get categories
            categories = {}
            if content_array:
                for item in content_array:
                    word_id = item.get('word_id')
                    tags = item.get('tags', [])
                    for tag in tags:
                        if tag not in categories:
                            categories[tag] = 0
                        categories[tag] += 1
            
            return {
                'path_id': path_id,
                'word_count': word_count,
                'unique_words': unique_words,
                'categories': categories,
                'content_preview': content[:200] if content else '',
            }
        except Exception as e:
            self.logger.error(f"Error analyzing content for path_id {path_id}: {e}")
            return {
                'path_id': path_id,
                'error': str(e),
                'word_count': 0,
                'unique_words': 0,
                'categories': {},
            }
    
    def analyze_keywords(self, path_id: int) -> List[Dict[str, Any]]:
        """
        Analyze keywords in content.
        
        Args:
            path_id: Path ID to analyze
            
        Returns:
            List of keyword analysis results
        """
        try:
            from database.services.contents_db_service import ContentDBService
            db_service = ContentDBService()
            
            # Get keywords for this path
            keywords_dict = db_service.get_all_keywords()
            # This is a simplified implementation
            # Full implementation would check which keywords appear in the content
            
            return []
        except Exception as e:
            self.logger.error(f"Error analyzing keywords for path_id {path_id}: {e}")
            return []
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get analysis statistics.
        
        Returns:
            Dictionary with statistics
        """
        try:
            from database.services.contents_db_service import ContentDBService
            db_service = ContentDBService()
            
            # Get basic statistics
            all_sources = db_service.get_all_sources()
            all_sides = db_service.get_all_sides()
            
            return {
                'total_sources': len(all_sources) if all_sources else 0,
                'total_sides': len(all_sides) if all_sides else 0,
            }
        except Exception as e:
            self.logger.error(f"Error getting statistics: {e}")
            return {
                'total_sources': 0,
                'total_sides': 0,
            }

