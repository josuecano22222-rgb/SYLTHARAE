from .best_repo import BaseRepository
from datetime import date
from typing import Optional, Dict, Any
import json
from ..queries.alert_queries import AlertQueries


class AlertsRepository(BaseRepository):
    """Repository for alerts operations"""

    def insert_alert(
        self,
        type: str,
        priority: str,
        title: str,
        message: str,
        file_id: Optional[int] = None,
        file_name: Optional[str] = None,
        file_path: Optional[str] = None,
        event_date: Optional[date] = None,
        metadata: Optional[Dict[str, Any]] = None,
        read: bool = False,
        dismissed: bool = False
    ):
        """Insert a new alert and return its ID.

        ``alerts`` has no name/path columns: the file is referenced by
        ``file_id`` (the path id). Display names supplied by a caller are kept
        in ``metadata`` so a row written before the join existed still renders,
        while readers derive them from ``paths``.
        """
        payload = dict(metadata or {})
        if file_name is not None:
            payload.setdefault("file_name", file_name)
        if file_path is not None:
            payload.setdefault("file_path", file_path)
        metadata_json = json.dumps(payload) if payload else None

        params = (
            type, priority, title, message, file_id,
            event_date, metadata_json, read, dismissed
        )
        
        return self.execute(
            AlertQueries.insert_alert(),
            params,
            fetchone=True
        )

    def update_alert(
        self,
        alert_id: int,
        type: Optional[str] = None,
        priority: Optional[str] = None,
        title: Optional[str] = None,
        message: Optional[str] = None,
        file_id: Optional[int] = None,
        file_name: Optional[str] = None,
        file_path: Optional[str] = None,
        event_date: Optional[date] = None,
        metadata: Optional[Dict[str, Any]] = None,
        read: Optional[bool] = None,
        dismissed: Optional[bool] = None
    ):
        """Update alert - first get existing values, then update"""
        existing = self.get_by_id(alert_id)
        if not existing:
            return None
        
        # Use existing values if not provided
        type = type or existing[1]
        priority = priority or existing[2]
        title = title or existing[3]
        message = message or existing[4]
        file_id = file_id if file_id is not None else existing[5]
        file_name = file_name or existing[6]
        file_path = file_path or existing[7]
        event_date = event_date or existing[8]
        metadata = metadata if metadata is not None else (json.loads(existing[9]) if existing[9] else None)
        read = read if read is not None else existing[10]
        dismissed = dismissed if dismissed is not None else existing[11]
        
        payload = dict(metadata or {})
        if file_name is not None:
            payload.setdefault("file_name", file_name)
        if file_path is not None:
            payload.setdefault("file_path", file_path)
        metadata_json = json.dumps(payload) if payload else None

        params = (
            type, priority, title, message, file_id,
            event_date, metadata_json, read, dismissed, alert_id
        )
        
        return self.execute(
            AlertQueries.update_alert(),
            params
        )

    def select_all_alerts(self):
        """Get all alerts"""
        return self.execute(
            AlertQueries.get_all(),
            None,
            fetchall=True
        )

    def get_by_id(self, alert_id):
        """Get alert by ID"""
        row = self.execute(
            AlertQueries.get_by_id(),
            (alert_id,),
            fetchone=True
        )
        if row and row[9]:  # metadata field
            # Convert JSON string back to dict
            row_list = list(row)
            row_list[9] = json.loads(row[9])
            return tuple(row_list)
        return row

    def delete_alert(self, alert_id):
        """Delete an alert"""
        return self.execute(
            AlertQueries.delete_alert(),
            (alert_id,)
        )

    def mark_as_read(self, alert_id):
        """Mark alert as read"""
        return self.execute(
            AlertQueries.mark_as_read(),
            (alert_id,)
        )

    def mark_as_dismissed(self, alert_id):
        """Mark alert as dismissed"""
        return self.execute(
            AlertQueries.mark_as_dismissed(),
            (alert_id,)
        )

    def get_unread_alerts(self, limit=50):
        """Get unread alerts"""
        return self.execute(
            AlertQueries.get_unread_alerts(),
            (limit,),
            fetchall=True
        )

    def get_active_alerts(self, limit=50):
        """Get active (not dismissed) alerts"""
        return self.execute(
            AlertQueries.get_active_alerts(),
            (limit,),
            fetchall=True
        )

    def get_by_file_id(self, file_id):
        """Get alerts by file ID"""
        rows = self.execute(
            AlertQueries.get_by_file_id(),
            (file_id,),
            fetchall=True
        )
        # Convert metadata JSON strings back to dicts
        result = []
        for row in rows:
            row_list = list(row)
            if row_list[9]:  # metadata field
                row_list[9] = json.loads(row_list[9])
            result.append(tuple(row_list))
        return result

    def get_by_type(self, alert_type, limit=50):
        """Get alerts by type"""
        return self.execute(
            AlertQueries.get_by_type(),
            (alert_type, limit),
            fetchall=True
        )

    def get_by_priority(self, priority, limit=50):
        """Get alerts by priority"""
        return self.execute(
            AlertQueries.get_by_priority(),
            (priority, limit),
            fetchall=True
        )

    def search_alerts(
        self,
        alert_type: Optional[str] = None,
        priority: Optional[str] = None,
        read: Optional[bool] = None,
        dismissed: Optional[bool] = None,
        search_text: Optional[str] = None,
        limit: int = 50
    ):
        """Search alerts with multiple filters"""
        search_param = f"%{search_text}%" if search_text else None
        
        params = (
            alert_type, alert_type,
            priority, priority,
            read, read,
            dismissed, dismissed,
            search_text, search_param, search_param,
            limit
        )
        
        rows = self.execute(
            AlertQueries.search_alerts(),
            params,
            fetchall=True
        )
        # Convert metadata JSON strings back to dicts
        result = []
        for row in rows:
            row_list = list(row)
            if row_list[9]:  # metadata field
                row_list[9] = json.loads(row_list[9])
            result.append(tuple(row_list))
        return result

    def count_unread(self):
        """Count unread alerts"""
        row = self.execute(
            AlertQueries.count_unread(),
            None,
            fetchone=True
        )
        return row[0] if row else 0

    def count_active(self):
        """Count active (not dismissed) alerts"""
        row = self.execute(
            AlertQueries.count_active(),
            None,
            fetchone=True
        )
        return row[0] if row else 0
