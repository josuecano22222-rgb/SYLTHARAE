from .best_repo import BaseRepository
from datetime import date
from ..queries.side_queries import SideQueries

class SidesRepository(BaseRepository):


    def insert_info_sides(self, name, importance, date_creation=None):
        """Insert a new side and return its ID"""
        # AUDIT-DATE-01: default resolved per call, not once at import time.
        date_creation = date_creation or date.today()
        return self.execute(
            SideQueries.insert_side(),
            (name, importance, date_creation),
            fetchone=True
        )

    def update_info_sides(self, side_id, name, importance):
        """Update side info"""
        return self.execute(
            SideQueries.update_side(),
            (name, importance, side_id)
        )

    def select_info_sides(self):
        """Get all sides (id, name)"""
        return self.execute(
            SideQueries.get_all(),
            None,
            fetchall=True
        )

    def get_by_id(self, side_id):
        """Get side by ID"""
        return self.execute(
            SideQueries.get_by_id(),
            (side_id,),
            fetchone=True
        )

    def get_by_name(self, name):
        """Get side by name"""
        return self.execute(
            SideQueries.get_by_name(),
            (name,),
            fetchone=True
        )

    def list_sides(self, search=None, limit=50):
        """List sides with optional search"""
        search_param = f"%{search}%" if search else None
        return self.execute(
            SideQueries.list_sides(),
            (search, search_param, limit),
            fetchall=True
        )

    def delete_side(self, side_id):
        """Delete a side"""
        return self.execute(
            SideQueries.delete_side(),
            (side_id,)
        )

    def check_side_usage(self, side_id):
        """Check if side is used by any files"""
        row = self.execute(
            SideQueries.check_side_usage(),
            (side_id,),
            fetchone=True
        )
        return row[0] if row else 0

    def get_or_create_side(self, name, importance, date_creation=None):
        """Get existing side or create new one"""
        # AUDIT-DATE-01: default resolved per call, not once at import time.
        date_creation = date_creation or date.today()
        return self.execute(
            SideQueries.get_or_create_side(),
            (name, importance, date_creation),
            fetchone=True
        )