"""
Database operations module - provides operation classes for categories and keywords.
"""

from database.services.contents_db_service import ContentDBService
from core.serialization import pack_int_list


class CategoryOperations:
    """Operations for category management"""
    
    def __init__(self, db_service=None):
        """Initialize category operations"""
        self.db_service = db_service or ContentDBService()
        self.repo = self.db_service.categorys_repo
    
    def create_category(self, category_word: str):
        """Create a category"""
        return self.db_service.create_category(category_word)
    
    def link_word_to_category(self, word: str, category_word: str):
        """Link word to category"""
        return self.db_service.link_word_to_category(word, category_word)
    
    def list_categories(self, limit=50):
        """List categories"""
        return self.repo.list_categories(limit)
    
    def search_categories(self, search_term=None, limit=50):
        """Search categories"""
        return self.repo.search_categories(search_term, limit)
    
    def process_term(self, term: str, category_id: int):
        """
        Process a term (word or phrase) and add it to the category
        
        Args:
            term: Term to process (single word or multi-word phrase)
            category_id: Category ID to associate with
        
        Returns:
            Dict with keys: success (bool), type ('word' or 'keyword'), id (int), message (str)
        """
        try:
            words = term.strip().lower().split()
            
            if len(words) == 1:
                # Single word - add to words_categorys table
                word = words[0]
                word_id = self.db_service.words_repo.insert_by_word(word)
                
                if not word_id:
                    return {
                        'success': False,
                        'type': 'word',
                        'id': None,
                        'message': f'Failed to insert word "{word}"'
                    }
                
                # Link word to category
                self.db_service.words_categorys_repo.insert_word_category(word_id, category_id)
                
                return {
                    'success': True,
                    'type': 'word',
                    'id': word_id,
                    'message': f'Successfully added word "{word}" to category'
                }
            
            elif len(words) >= 2:
                # Multi-word phrase - add to keywords table
                # Get or insert word IDs for each word
                word_ids = []
                for word in words:
                    word_id = self.db_service.words_repo.insert_by_word(word)
                    if not word_id:
                        return {
                            'success': False,
                            'type': 'keyword',
                            'id': None,
                            'message': f'Failed to insert word "{word}" for phrase "{term}"'
                        }
                    word_ids.append(word_id)
                
                # Create keyword blob
                keyword_blob = pack_int_list(word_ids)
                
                # Check if keyword already exists
                existing_keyword_id = self.db_service.keywords_repo.keyword_exists(word_ids, category_id)
                if existing_keyword_id:
                    return {
                        'success': False,
                        'type': 'keyword',
                        'id': existing_keyword_id,
                        'message': f'Keyword phrase "{term}" already exists in this category'
                    }
                
                # Insert keyword
                keyword_result = self.db_service.keywords_repo.insert_keywords(category_id, word_ids)
                
                if keyword_result:
                    # insert_keywords returns result from execute(query, params, True)
                    # which should be a tuple like (keyword_id,) when using RETURNING
                    if isinstance(keyword_result, tuple):
                        keyword_id = keyword_result[0] if len(keyword_result) > 0 else None
                    else:
                        keyword_id = keyword_result
                    
                    if keyword_id:
                        return {
                            'success': True,
                            'type': 'keyword',
                            'id': keyword_id,
                            'message': f'Successfully added keyword phrase "{term}" to category'
                        }
                
                return {
                    'success': False,
                    'type': 'keyword',
                    'id': None,
                    'message': f'Failed to insert keyword phrase "{term}"'
                }
            else:
                return {
                    'success': False,
                    'type': 'unknown',
                    'id': None,
                    'message': f'Invalid term: "{term}"'
                }
                
        except Exception as e:
            return {
                'success': False,
                'type': 'error',
                'id': None,
                'message': f'Error processing term "{term}": {str(e)}'
            }


class KeywordOperations:
    """Operations for keyword management"""
    
    def __init__(self, db_service=None):
        """Initialize keyword operations"""
        self.db_service = db_service or ContentDBService()
        self.repo = self.db_service.keywords_repo
    
    def create_keyword(self, keyword_phrase: str, category_word: str):
        """Create a keyword"""
        return self.db_service.create_keyword(keyword_phrase, category_word)
    
    def get_all_keywords(self):
        """Get all keywords"""
        return self.db_service.get_all_keywords()
    
    def get_keywords_as_words(self):
        """Get keywords as words"""
        return self.db_service.get_keywords_as_words()
    
    def get_keyword_id_by_blob(self, keyword_blob: bytes, category_id: int):
        """
        Get keyword ID by keyword blob and category ID
        
        Args:
            keyword_blob: Pickled keyword blob (bytes)
            category_id: Category ID
        
        Returns:
            Keyword ID if exists, None otherwise
        """
        from database.database.queries.keyword_queries import KeywordQueries
        row = self.repo.execute(
            KeywordQueries.check_keyword_exists(),
            (keyword_blob, category_id),
            fetchone=True
        )
        return row[0] if row else None

    def insert_keyword_path_relationships(self, hash_id: int, keyword_counts) -> int:
        """Persist ``{keyword_id: count}`` keyword matches for a document.

        Keyword links are content-derived data and live on the canonical
        content row (``keywords_hashs``, keyed by ``hash_id`` since m0011), so
        every occurrence/path of the same bytes shares them.  The bulk insert
        goes through the keywords_hashs repository - the same writer the
        ingestion and association-refresh paths use.

        Args:
            hash_id: The canonical content (``hashs.id``) the matches belong to.
            keyword_counts: Mapping of keyword_id to occurrence count.

        Returns:
            Number of keyword relationships written (0 for an empty mapping).

        Raises:
            Exception: Propagated from the repository so callers can log the
                real cause (previously the update-associations route passed
                this mapping to ``process_keywords_for_content``, which expects
                a word-id sequence, and every file with matches failed with
                ``unhashable type: 'slice'``).
        """
        if not keyword_counts:
            return 0
        self.db_service.keywords_hashs_repo.bulk_insert_keywords_hashs(
            hash_id, dict(keyword_counts)
        )
        return len(keyword_counts)

    #: Explicit name matching the m0011 content-identity model.
    insert_keyword_hash_relationships = insert_keyword_path_relationships


class WordOperations:
    """Operations for word management"""
    
    def __init__(self, db_service=None):
        """Initialize word operations"""
        self.db_service = db_service or ContentDBService()
        self.repo = self.db_service.words_repo
    
    def insert_word(self, word: str):
        """Insert a word"""
        return self.repo.insert_by_word(word)
    
    def get_word_id(self, word: str):
        """Get word ID by word"""
        result = self.repo.get_by_word(word)
        if result:
            return result[0] if isinstance(result, tuple) else result
        return None


def get_category_operations(db_service=None):
    """Get category operations instance"""
    return CategoryOperations(db_service)


def get_keyword_operations(db_service=None):
    """Get keyword operations instance"""
    return KeywordOperations(db_service)


def get_word_operations(db_service=None):
    """Get word operations instance"""
    return WordOperations(db_service)


__all__ = [
    'CategoryOperations',
    'KeywordOperations',
    'WordOperations',
    'get_category_operations',
    'get_keyword_operations',
    'get_word_operations',
]

