from .best_repo import BaseRepository
from datetime import date
from ..queries.source_queries import SourceQueries

class SourcesRepository(BaseRepository):
    
    def insert_info_sources(
        self,
        name,
        country,
        job,
        importance,
        city="",
        description="",
        accounts="",
        note="",
        attachments="",
        ownership="",
        access_status="",
        entry_date=None,
        date_creation=None,
        category_id=None,
    ):
        """Insert a new source and return its ID"""
        # AUDIT-DATE-01: default resolved per call, not once at import time.
        entry_date = entry_date or date.today()
        date_creation = date_creation or date.today()
        params = (
            name,
            country,
            city,
            job,
            description,
            importance,
            attachments,
            ownership,
            accounts,
            note,
            access_status,
            entry_date,
            date_creation,
            category_id,
        )

        # Failures propagate: returning None here made ``create_source`` look
        # successful with ``source_id=None`` and the file then failed later
        # with an unrelated foreign-key error.  Callers already handle the
        # exception (StoragePipeline wraps source creation in try/except).
        return self.execute(
            SourceQueries.insert_source(),
            params,
            fetchone=True
        )

    def update_info_sources(
        self,
        source_id,
        name,
        country,
        job,
        importance,
        city="",
        description="",
        accounts="",
        note="",
        attachments="",
        ownership="",
        access_status="",
        entry_date=None,
        category_id=None,
    ):
        """Update an existing source"""
        if entry_date is None:
            entry_date = date.today()

        params = (
            name,
            country,
            city,
            job,
            description,
            importance,
            accounts,
            note,
            attachments,
            ownership,
            access_status,
            entry_date,
            category_id,
            source_id,
        )

        return self.execute(
            SourceQueries.update_source(),
            params,
            fetchone=True
        )

    def select_info_sources(self):
        """Get all sources (id, name)"""
        return self.execute(
            SourceQueries.get_all(),
            None,
            fetchall=True
        )

    def get_by_id(self, source_id):
        """Get source by ID"""
        return self.execute(
            SourceQueries.get_by_id(),
            (source_id,),
            fetchone=True
        )

    def get_by_name(self, name):
        """Get source by name"""
        return self.execute(
            SourceQueries.get_by_name(),
            (name,),
            fetchone=True
        )

    def list_sources(self, search=None, limit=50):
        """List sources with optional search"""
        search_param = f"%{search}%" if search else None
        return self.execute(
            SourceQueries.list_sources(),
            (search, search_param, limit),
            fetchall=True
        )

    def delete_source(self, source_id):
        """Delete source"""
        return self.execute(
            SourceQueries.delete_source(),
            (source_id,)
        )

    def check_source_usage(self, source_id):
        """Check if source is used by any files"""
        row = self.execute(
            SourceQueries.check_source_usage(),
            (source_id,),
            fetchone=True
        )
        return row[0] if row else 0

    def get_source_with_stats(self, source_id):
        """Get source with document statistics"""
        return self.execute(
            SourceQueries.get_source_with_stats(),
            (source_id,),
            fetchone=True
        )

    def get_or_create_source(
        self,
        name,
        job,
        importance,
        country,
        date_creation=None
    ):
        """Get existing source or create new one"""
        # AUDIT-DATE-01: default resolved per call, not once at import time.
        date_creation = date_creation or date.today()
        return self.execute(
            SourceQueries.get_or_create_source(),
            (name, job, importance, country, date_creation),
            fetchone=True
        )