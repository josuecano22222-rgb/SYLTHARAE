from .best_repo import BaseRepository
from ..queries.word_category_queries import WordCategoryQueries

class WordsCategorysRepository(BaseRepository):

    def insert_word_category(self, word_id, category_id):

        exists = self.execute(
            WordCategoryQueries.check_link_exists(),
            (word_id, category_id), True
        )
        if not exists:
            self.execute(
                WordCategoryQueries.link_word_to_category(),
                (word_id, category_id)
            )
        else:
            return None
    
    def get_words_by_category(self, category_id, limit=100):
        return self.execute(WordCategoryQueries.get_words_by_category(), (category_id, limit,), False, True)
    
    def get_categories_by_word_id(self, word_id):
        """Get all categories (tags) for a word"""
        return self.execute(
            WordCategoryQueries.get_categories_by_word_id(),
            (word_id,),
            fetchall=True
        ) 
       
        