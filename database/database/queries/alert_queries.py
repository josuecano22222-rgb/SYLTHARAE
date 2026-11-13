"""SQL queries for the alerts table.

``alerts`` (the frozen schema) stores the file reference as ``file_id`` - the
path id - and carries no file name or path of its own. Every read therefore
joins ``paths`` so a caller still receives the name and the path in one row,
and every write stores only what the table has. The row shape stays the one
the repository documents: id, type, priority, title, message, file_id,
file_name, file_path, event_date, metadata, created_at, read, dismissed,
recipient_user_id.
"""
from .base_queries import BaseQueries


#: One SELECT list for every read, so the positional row shape never varies.
_ALERT_SELECT = """
            SELECT a.id, a.type, a.priority, a.title, a.message, a.file_id,
                   p.file_name, p.file_path, a.event_date, a.metadata,
                   a.created_at, a.read, a.dismissed, a.recipient_user_id
            FROM alerts a
            LEFT JOIN paths p ON p.id = a.file_id
"""


class AlertQueries(BaseQueries):
    """SQL queries related to alerts"""

    @staticmethod
    def get_by_id() -> str:
        """Get alert by ID"""
        return _ALERT_SELECT + """
            WHERE a.id = %s
        """

    @staticmethod
    def get_all() -> str:
        """Get all alerts"""
        return _ALERT_SELECT + """
            ORDER BY a.created_at DESC
        """

    @staticmethod
    def insert_alert() -> str:
        """Insert new alert (display names live in metadata, see the repo)"""
        return """
            INSERT INTO alerts (type, priority, title, message, file_id,
                              event_date, metadata, read, dismissed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """

    @staticmethod
    def update_alert() -> str:
        """Update alert"""
        return """
            UPDATE alerts
            SET type = %s, priority = %s, title = %s, message = %s,
                file_id = %s, event_date = %s,
                metadata = %s, read = %s, dismissed = %s
            WHERE id = %s
        """

    @staticmethod
    def delete_alert() -> str:
        """Delete alert"""
        return "DELETE FROM alerts WHERE id = %s"

    @staticmethod
    def mark_as_read() -> str:
        """Mark alert as read"""
        return "UPDATE alerts SET read = TRUE WHERE id = %s"

    @staticmethod
    def mark_as_dismissed() -> str:
        """Mark alert as dismissed"""
        return "UPDATE alerts SET dismissed = TRUE WHERE id = %s"

    @staticmethod
    def get_unread_alerts() -> str:
        """Get unread alerts"""
        return _ALERT_SELECT + """
            WHERE a.read = FALSE
            ORDER BY a.created_at DESC
            LIMIT %s
        """

    @staticmethod
    def get_active_alerts() -> str:
        """Get active (not dismissed) alerts"""
        return _ALERT_SELECT + """
            WHERE a.dismissed = FALSE
            ORDER BY a.created_at DESC
            LIMIT %s
        """

    @staticmethod
    def get_by_file_id() -> str:
        """Get alerts by file ID"""
        return _ALERT_SELECT + """
            WHERE a.file_id = %s
            ORDER BY a.created_at DESC
        """

    @staticmethod
    def get_by_type() -> str:
        """Get alerts by type"""
        return _ALERT_SELECT + """
            WHERE a.type = %s
            ORDER BY a.created_at DESC
            LIMIT %s
        """

    @staticmethod
    def get_by_priority() -> str:
        """Get alerts by priority"""
        return _ALERT_SELECT + """
            WHERE a.priority = %s
            ORDER BY a.created_at DESC
            LIMIT %s
        """

    @staticmethod
    def search_alerts() -> str:
        """Search alerts with multiple filters"""
        return _ALERT_SELECT + """
            WHERE (%s IS NULL OR a.type = %s)
              AND (%s IS NULL OR a.priority = %s)
              AND (%s IS NULL OR a.read = %s)
              AND (%s IS NULL OR a.dismissed = %s)
              AND (%s IS NULL OR a.title ILIKE %s OR a.message ILIKE %s
                   OR p.file_name ILIKE %s)
            ORDER BY a.created_at DESC
            LIMIT %s
        """

    @staticmethod
    def count_unread() -> str:
        """Count unread alerts"""
        return "SELECT COUNT(*) FROM alerts WHERE read = FALSE"

    @staticmethod
    def count_active() -> str:
        """Count active (not dismissed) alerts"""
        return "SELECT COUNT(*) FROM alerts WHERE dismissed = FALSE"
