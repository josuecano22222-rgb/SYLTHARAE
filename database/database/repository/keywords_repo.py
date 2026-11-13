from .best_repo import BaseRepository
from ..queries.keyword_queries import KeywordQueries
from core.serialization import pack_int_list, unpack_int_list
import logging

logger = logging.getLogger(__name__)

class KeywordsRepository(BaseRepository):

    def insert_keywords(self, category_id, list_keywords_ids):
        """Insert keywords with pickled list.

        Failures propagate: swallowing them here made a missing keyword index
        look like a successful ingest (the same silent-failure pattern that
        hid the transaction aborts in production).
        """
        keyword_byte = pack_int_list(list_keywords_ids)
        query = KeywordQueries.insert_keyword()
        params = (keyword_byte, category_id)
        return self.execute(query, params, True)

    def select_all_keywords(self):
        """Get all keywords and unpickle them"""
        rows = self.execute(KeywordQueries.get_all(), None, fetchall=True)
        dic_keyword = {}
        for row in rows:
            if row[1]:  # Check if keyword field is not None
                try:
                    ids_keyword = unpack_int_list(row[1])
                    dic_keyword[row[0]] = ids_keyword
                except:
                    dic_keyword[row[0]] = row[1]  # Fallback to raw data
            else:
                dic_keyword[row[0]] = None
        return dic_keyword
    
    def update_keywords(self, keyword_id, category_id, list_keywords_ids):
        """Update keywords with pickled list"""
        keyword_byte = pack_int_list(list_keywords_ids)
        return self.execute(
            KeywordQueries.update_by_id(), 
            (keyword_byte, category_id, keyword_id), 
            True
        )
    
    def update_keywords_by_category(self, category_id, list_keywords_ids):
        """Update keywords for a specific category"""
        keyword_byte = pack_int_list(list_keywords_ids)
        return self.execute(
            KeywordQueries.update_by_category_id(), 
            (keyword_byte, category_id), 
            True
        )

    def select_by_id(self, id):
        """Get keyword by ID and unpickle"""
        row = self.execute(KeywordQueries.get_by_id(), (id,), fetchone=True)
        if row:
            # Unpickle the keyword field (index 1 in the row)
            if row[1]:  # Check if keyword field is not None
                try:
                    unpickled_keyword = unpack_int_list(row[1])
                    # Return tuple with unpickled data
                    return (unpickled_keyword, row[2], row[3])  # keyword, category_id, date_creation
                except:
                    return row  # Return raw row if unpickling fails
        return None
    
    def get_keywords_with_usage(self, search, limit, offset):
        """Get keywords with usage stats"""
        search_param = f"%{search}%" if search else None
        rows = self.execute(
            KeywordQueries.get_keywords_with_usage(),
            (search, search_param, limit, offset),
            fetchall=True
        )
        
        # Process rows to unpickle keyword field
        processed_rows = []
        for row in rows:
            if row and row[1]:  # row[1] is the keyword field
                try:
                    unpickled_keyword = unpack_int_list(row[1])
                    processed_rows.append((row[0], unpickled_keyword, row[2], row[3], row[4]))
                except:
                    processed_rows.append(row)  # Keep original if unpickling fails
            else:
                processed_rows.append(row)
        
        return processed_rows
    
    def loads_content_keyword(self, rows):
        """Process and unpickle keyword rows"""
        result = []
        if rows:
            for row in rows:
                if row[0]:  # row[0] is the keyword field
                    try:
                        ids_keyword = unpack_int_list(row[0])
                        result.append((ids_keyword, row[1]))
                    except:
                        result.append((row[0], row[1]))  # Fallback to raw data
        return result
    
    def get_keyword_frequencies(self, path_id, limit):
        """Get keyword frequencies for a file"""
        rows = self.execute(
            KeywordQueries.get_keyword_frequencies(),
            (path_id, limit),
            fetchall=True
        )
        return self.loads_content_keyword(rows)
    
    def delete_keyword_paths(self, keyword_id):
        """Delete keyword-path associations"""
        return self.execute(
            KeywordQueries.delete_keyword_paths(),
            (keyword_id,)
        )
    
    def get_keywords_by_file(self, path_id):
        """Get keywords for a file and unpickle them"""
        rows = self.execute(
            KeywordQueries.get_keywords_by_file(),
            (path_id,),
            fetchall=True
        )
        
        # Process rows to unpickle keyword field
        processed_rows = []
        for row in rows:
            if row and row[1]:  # row[1] is the keyword field
                try:
                    unpickled_keyword = unpack_int_list(row[1])
                    processed_rows.append((row[0], unpickled_keyword, row[2], row[3]))
                except:
                    processed_rows.append(row)  # Keep original if unpickling fails
            else:
                processed_rows.append(row)
        
        return processed_rows
    
    def keyword_exists(self, keyword_data, category_id):
        """Check if keyword exists (keyword_data should be the list to pickle)"""
        keyword_byte = pack_int_list(keyword_data)
        row = self.execute(
            KeywordQueries.check_keyword_exists(),
            (keyword_byte, category_id),
            fetchone=True
        )
        return row[0] if row else None

    def get_by_id(self, keyword_id):
        """Get keyword by ID and unpickle"""
        row = self.execute(
            KeywordQueries.get_by_id(),
            (keyword_id,),
            fetchone=True
        )
        if row:
            # Unpickle the keyword field
            if row[1]:  # row[1] is the keyword field
                try:
                    unpickled_keyword = unpack_int_list(row[1])
                    # Return a new tuple with unpickled data
                    return (row[0], unpickled_keyword, row[2], row[3])
                except:
                    return row  # Return raw row if unpickling fails
        return None

    def list_by_category(self, category_id, limit):
        """List keywords by category and unpickle them"""
        rows = self.execute(
            KeywordQueries.list_by_category(),
            (category_id, limit),
            fetchall=True
        )
        
        # Process rows to unpickle keyword field
        processed_rows = []
        for row in rows:
            if row and row[1]:  # row[1] is the keyword field
                try:
                    unpickled_keyword = unpack_int_list(row[1])
                    processed_rows.append((row[0], unpickled_keyword, row[2], row[3]))
                except:
                    processed_rows.append(row)  # Keep original if unpickling fails
            else:
                processed_rows.append(row)
        
        return processed_rows
    
    def list_all(self, limit):
        """List all keywords with pagination and unpickle them"""
        rows = self.execute(
            KeywordQueries.list_all(),
            (limit,),
            fetchall=True
        )
        
        # Process rows to unpickle keyword field
        processed_rows = []
        for row in rows:
            if row and row[1]:  # row[1] is the keyword field
                try:
                    unpickled_keyword = unpack_int_list(row[1])
                    processed_rows.append((row[0], unpickled_keyword, row[2], row[3]))
                except:
                    processed_rows.append(row)  # Keep original if unpickling fails
            else:
                processed_rows.append(row)
        
        return processed_rows
    
    def delete_keyword(self, keyword_id):
        """Delete a keyword"""
        return self.execute(
            KeywordQueries.delete_keyword(),
            (keyword_id,)
        )
    
    def _unpickle_row(self, row):
        """Helper method to unpickle a row's keyword field"""
        if not row:
            return row
        
        try:
            # Assuming keyword is at index 1, adjust if needed
            if len(row) > 1 and row[1]:
                unpickled_keyword = unpack_int_list(row[1])
                # Reconstruct the tuple with unpickled keyword
                return (row[0], unpickled_keyword) + row[2:]
        except:
            pass
        
        return row
    
    def _unpickle_rows(self, rows):
        """Helper method to unpickle multiple rows"""
        if not rows:
            return rows
        
        processed_rows = []
        for row in rows:
            processed_rows.append(self._unpickle_row(row))
        
        return processed_rows