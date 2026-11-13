from .best_repo import BaseRepository
from ..queries.category_queries import CategoryQueries

class CategorysRepository(BaseRepository):

    def select_category_by_id(self, category_id: int):
        """Return the category row (id, name, word_id, parent_id) or None when absent."""
        return self.execute(CategoryQueries.get_by_id(), (category_id,), True)

    def select_categorys_word_id(self):
        rows = self.execute(CategoryQueries.list_all(), None, False, True)
        return {word_id: id for id, word_id in rows}
    
    def select_category_by_word_id(self, word_id):
        row = self.execute(CategoryQueries.get_by_word_id(), (word_id,), True)
        if row:
            return row[0]
        else:
            return None

    def insert_category(self, word_id, parent_id=None):
        result = self.execute(CategoryQueries.insert_category(), (word_id, parent_id), True)
        if result:
            return result[0] if isinstance(result, tuple) else result
        return None
    
    def get_categories_by_file(self, path_id: int):
        """Get categories for a specific file"""
        return self.execute(
            CategoryQueries.get_categories_by_file(),
            (path_id,),
            fetchall=True
        )

    def search_categories(self, search: str | None, limit: int, offset: int,
                          word_ids=None):
        """
        Search categories with optional search term.

        ``word_ids`` are the words whose *translation* matches the same query
        (services/i18n/term_labels.py). Empty or ``None`` means the predicate is
        what it always was.
        """
        ids = list(word_ids) if word_ids else None
        return self.execute(
            CategoryQueries.search_categories(),
            (search, f"%{search}%" if search else None, ids, ids, limit, offset),
            fetchall=True
        )

    def get_categories_with_stats(self, limit: int):
        """Get categories along with file and word counts"""
        return self.execute(
            CategoryQueries.get_categories_with_stats(),
            (limit,),
            fetchall=True
        )
    
    def get_main_categories(self):
        """Get main categories (no parent) with subcategory counts"""
        return self.execute(
            CategoryQueries.get_main_categories(),
            None,
            fetchall=True
        )
    
    def get_subcategories(self, parent_id: int):
        """Get subcategories of a parent category"""
        return self.execute(
            CategoryQueries.get_subcategories(),
            (parent_id,),
            fetchall=True
        )
    
    def get_parent_category(self, category_id: int):
        """Get parent category of a category"""
        return self.execute(
            CategoryQueries.get_parent_category(),
            (category_id,),
            fetchone=True
        )

    def update_category_by_id(self, category_id: int, word_id: int):
        """Update category word by category ID"""
        row = self.execute(
            CategoryQueries.update_category_by_id(),
            (word_id, category_id),
            fetchone=True
        )
        return row[0] if row else None

    def update_category_by_word_id(self, old_word_id: int, new_word_id: int):
        """Update category word by old word_id"""
        row = self.execute(
            CategoryQueries.update_category_by_word_id(),
            (new_word_id, old_word_id),
            fetchone=True
        )
        return row[0] if row else None
    
    def update_parent_id(self, category_id: int, parent_id: int):
        """Update parent_id of a category"""
        row = self.execute(
            CategoryQueries.update_parent_id(),
            (parent_id, category_id),
            fetchone=True
        )
        return row[0] if row else None