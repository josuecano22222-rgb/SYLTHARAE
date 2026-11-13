from .best_repo import BaseRepository
from ..queries.keyword_hash_queries import KeywordHashQueries

class KeywordsHashsRepository(BaseRepository):


    def bulk_insert_keywords_hashs(self, hash_id, keyword_id_and_counts):#{id:count}

        if not keyword_id_and_counts:
            return        
        values = [(hash_id, keyword_id, count) for keyword_id, count in keyword_id_and_counts.items()]
        placeholders = ",".join(["(%s,%s,%s)"] * len(values))
        query = KeywordHashQueries.insert_keyword_hash(placeholders)

        flat_values = [item for sublist in values for item in sublist]
        self.execute(query, flat_values)

    def insert_keywords_hashs(self, hash_id, keyword_id, word_count):
        
        query = KeywordHashQueries.insert_keyword_hash_one()
        params = (hash_id, keyword_id, word_count)

        return self.execute(query, params, True)
