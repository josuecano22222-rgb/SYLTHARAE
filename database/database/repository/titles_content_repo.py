from .best_repo import BaseRepository
import zlib
from ..queries.title_queries import TitleQueries
from ..queries.word_queries import WordQueries
from core.serialization import pack_int_list, unpack_int_list



class TitlesContentRepository(BaseRepository):
    """
    Repository for title content operations.
    
    Note: Title content is stored as compressed, pickled lists of word IDs (integers) 
    from the words table, similar to regular content. Title content consists of 
    numbers (word IDs) from the words table.
    """

    def insert_titles_content(self, ids, hash_id, title_status = "Main"):
        """
        Insert title content as a list of word IDs from the words table.
        
        Args:
            ids: List of word IDs (integers) from the words table
            hash_id: Path ID to associate title with
            title_status: Title status (default: "Main")
        
        Returns:
            Title content record ID
        
        Note: Title content consists of numbers (word IDs) from the words table.
        The word IDs are pickled and compressed before storage.
        """
        packed = pack_int_list(ids)
        compressed = zlib.compress(packed)
        try:
            # The frozen schema has no parent column on titles_content, so a
            # parent cannot be stored: the parameter is gone rather than
            # silently ignored (it used to be accepted and discarded).
            last_id = self.execute(
                TitleQueries.insert_title(),
                (compressed, title_status, hash_id),
                True)
            return last_id
        except Exception as e:
            # Re-raise exception instead of just printing - let transaction handler deal with it
            raise

    def Get_titles_content_by_hash(self, hash_id):
        """
        Get title content by path and convert word IDs to text.
        
        Args:
            hash_id: Path ID to get title content for
        
        Returns:
            Space-separated string of title words
        
        Note: Title content consists of numbers (word IDs) from the words table.
        This method retrieves the word IDs and converts them to text.
        """
        row = self.execute(TitleQueries.select_by_hash(), (hash_id,), True)
        if not row:
            return ""
        
        compressed = row[0]
        packed = zlib.decompress(compressed)
        ids = unpack_int_list(packed)

        # Get all words as {id: word} dictionary
        word_rows = self.execute(WordQueries.get_all(), None, False, True)  # fetchall=True
        dictionary = dict(word_rows) if word_rows else {}  # {id:word,id:word}
        return " ".join(dictionary.get(i, f"[ID:{i}]") for i in ids)
    
    def get_title_word_ids(self, hash_id, title_status="Main"):
        """
        Get title content as word IDs (numbers from words table).
        
        Args:
            hash_id: Path ID to get title content for
            title_status: Title status (default: "Main")
        
        Returns:
            List of word IDs (integers) from the words table
        
        Note: Title content consists of numbers (word IDs) from the words table.
        This method returns the IDs directly without converting to text.
        """
        row = self.execute(TitleQueries.select_by_hash(), (hash_id,), fetchone=True)
        if not row:
            return []
        
        compressed = row[0]
        packed = zlib.decompress(compressed)
        ids = unpack_int_list(packed)
        return ids
        
    
    def check_title_exists(self, hash_id, title_status):
        """Check if a title with given status already exists for a path"""
        row = self.execute(
            TitleQueries.check_title_exists(),
            (hash_id, title_status),
            fetchone=True
        )
        return row[0] if row else None
    