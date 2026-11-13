"""
Notification Service
System-wide notification management for alerts, similar files, and future events
"""

import logging
from datetime import datetime, date, timedelta, timezone
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum
import json
import threading


logger = logging.getLogger(__name__)

# Signal types that can carry a resolved date (a relative expression only
# when resolved against the document's own date). From the detector, so the
# notification layer never hard-codes its vocabulary.
from core.detection.temporal_intel import SIGNAL_DATE, SIGNAL_RELATIVE  # noqa: E402

_DATED_SIGNAL_TYPES = (SIGNAL_DATE, SIGNAL_RELATIVE)


class NotificationType(Enum):
    """Types of notifications"""
    SIMILAR_FILES = "similar_files"
    FUTURE_DATE = "future_date"
    FUTURE_EVENT = "future_event"
    PROCESSING_COMPLETE = "processing_complete"
    BATCH_COMPLETE = "batch_complete"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    # Scenarios (services/monitoring/scenario_engine.py): addressed likewise.
    SCENARIO_OUTCOME = "scenario_outcome"
    SCENARIO_STATUS = "scenario_status"


#: Types that must always be addressed to one user.
ADDRESSED_TYPES = frozenset({NotificationType.SCENARIO_OUTCOME,
                             NotificationType.SCENARIO_STATUS})


class NotificationPriority(str, Enum):
    """Notification priority levels.

    Notification priority is derived, never chosen (core/monitoring/priority.py).
    ``critical`` is produced by the signal rule for a high-confidence, imminent
    date and is storable in ``alerts.priority``; the scenario summary rule
    stops at ``high`` because ``scenario_outcomes.priority`` does not admit it.

    A ``str`` mixin, not a bare ``Enum``: the stored vocabulary *is* these
    strings (``alerts.priority``, ``scenario_outcomes.priority``), so a value
    read back from the database must compare equal to the one this module
    derives. With a bare ``Enum`` ``NotificationPriority.HIGH == 'high'`` is
    False, and the SQL/derivation cross-check in test_scenario_engine_pg
    fails on the type rather than on the rule. ``.value`` is unchanged, as is
    ``str()`` on 3.11 - writing code that needs the text still asks for
    ``.value``.
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Notification:
    """Represents a system notification"""
    id: Optional[int]
    type: NotificationType
    priority: NotificationPriority
    title: str
    message: str
    file_id: Optional[int]
    event_date: Optional[date]
    metadata: Dict[str, Any]
    created_at: datetime
    read: bool = False
    dismissed: bool = False
    #: None = system-wide (every user sees it); otherwise the only user who does.
    recipient_user_id: Optional[int] = None
    #: The scenario that produced it, if any.
    scenario_id: Optional[int] = None
    scenario_evaluation_id: Optional[int] = None
    #: The file is referenced by ``file_id`` only (the path id). Name and
    #: path are read by joining ``paths`` - they are not columns of
    #: ``alerts`` and not attributes of this object.


#: Column order shared by every ``SELECT ... FROM alerts`` in this service.
ALERT_COLUMNS = (
    "id, type, priority, title, message, file_id, event_date, "
    "metadata, created_at, read, dismissed, recipient_user_id, "
    "scenario_id, scenario_evaluation_id"
)


def visibility_clause(alias: str = "") -> str:
    """SQL condition: the alerts a user may see - system-wide ones and those
    addressed to them. Takes one parameter, the user id; ``None`` (no
    user) leaves only system-wide alerts, because ``= NULL`` is never true."""
    col = f"{alias}." if alias else ""
    return f"({col}recipient_user_id IS NULL OR {col}recipient_user_id = %s)"


def visible_to(notification: "Notification", user_id: Optional[int]) -> bool:
    return (notification.recipient_user_id is None
            or (user_id is not None and notification.recipient_user_id == user_id))


def utc_today() -> date:
    """\"Today\" for notifications: the UTC date, the same clock the signal
    routes and rule evaluations use (the server's local date could differ
    from both)."""
    return datetime.now(timezone.utc).date()


def _naive_utc(stamp: Optional[datetime]) -> Optional[datetime]:
    """``alerts.created_at`` is a TIMESTAMP (no zone): store UTC wall time.

    The objects carry timezone-aware datetimes, so sending them unchanged
    makes PostgreSQL drop the offset and the value that comes back no longer
    equals the one that went in - which is how a batch flush lost the mapping
    between rows and notifications.
    """
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        return stamp
    return stamp.astimezone(timezone.utc).replace(tzinfo=None)


def insert_alerts(cur, notifications: List["Notification"]) -> List[int]:
    """Insert notifications through the caller's cursor, in the caller's
    transaction, and return their ids in order.

    Used where a notification must commit or roll back together with other
    writes (a scenario evaluation records which alert delivered which match).
    It bypasses the in-memory queue on purpose; call
    ``get_notification_service().refresh_notifications()`` after the commit
    if system-wide alerts were written.
    """
    ids: List[int] = []
    for n in notifications:
        if n.type in ADDRESSED_TYPES and n.recipient_user_id is None:
            raise ValueError("scenario notifications must be addressed to a user")
        meta = n.metadata
        scenario_eval = (meta.get("scenario_evaluation_id")
                         if n.scenario_id is not None else None)
        cur.execute(
            "INSERT INTO alerts (type, priority, title, message, file_id, event_date,"
            " metadata, created_at, read, dismissed, recipient_user_id, scenario_id,"
            " scenario_evaluation_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING id",
            (n.type.value, n.priority.value, n.title, n.message, n.file_id,
             n.event_date, json.dumps(meta, ensure_ascii=False),
             _naive_utc(n.created_at), n.read, n.dismissed, n.recipient_user_id,
             n.scenario_id, scenario_eval))
        row = cur.fetchone()
        n.id = row["id"] if isinstance(row, dict) else row[0]
        ids.append(n.id)
    return ids

def notification_from_row(row) -> Notification:
    """Convert an ``alerts`` row (``ALERT_COLUMNS`` order) to a Notification."""
    metadata: Dict[str, Any] = {}
    raw_metadata = row[7]
    if isinstance(raw_metadata, dict):
        metadata = dict(raw_metadata)
    elif isinstance(raw_metadata, str):
        try:
            metadata = json.loads(raw_metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    return Notification(
        id=row[0],
        type=NotificationType(row[1]),
        priority=NotificationPriority(row[2]),
        title=row[3],
        message=row[4],
        file_id=row[5],
        event_date=row[6] if row[6] else None,
        metadata=metadata,
        created_at=row[8],
        read=bool(row[9]),
        dismissed=bool(row[10]),
        recipient_user_id=row[11] if len(row) > 11 else None,
        scenario_id=row[12] if len(row) > 12 else None,
        scenario_evaluation_id=row[13] if len(row) > 13 else None,
    )



class NotificationService:
    """
    Centralized notification service for system-wide alerts.

    Features:
    - Similar files notifications
    - Future date alerts
    - Future event alerts
    - Notification persistence
    - Notification filtering and management
    """

    def __init__(self):
        self.logger = logger
        self._notifications: List[Notification] = []
        #  OPTIMIZATION: Pending notifications queue for batch writes (no DB queries during processing)
        self._pending_notifications: List[Notification] = []
        # Guards BOTH the in-memory list and the pending queue.  The list is
        # only ever swapped as a whole under this lock (never clear()+append),
        # so concurrent refreshes can no longer interleave into duplicate
        # entries ("10 loaded" for a table with 5 rows).
        self._lock = threading.RLock()
        self._next_temp_id = -1  # Temporary IDs for pending notifications (negative to avoid conflicts)
        self._load_notifications()

    def _fetch_notifications(self, limit: int = 1000) -> List[Notification]:
        """Read notifications from the database into a fresh list."""
        # Use execute_query from Api.utils (best practice - single source of truth)
        from Api.utils import execute_query

        rows = execute_query(
            f"""
            SELECT {ALERT_COLUMNS}
            FROM alerts
            WHERE dismissed = FALSE AND recipient_user_id IS NULL
            ORDER BY created_at DESC
            LIMIT %s
            """,  # nosec B608 # ALERT_COLUMNS is a constant; the limit is a bound parameter
            (int(limit),),
            fetch="all"
        )

        fetched: List[Notification] = []
        for row in rows or []:
            try:
                fetched.append(notification_from_row(row))
            except Exception as e:
                self.logger.warning(f"Error loading notification: {e}")
        return fetched

    def _fetch_addressed(self, user_id: int, notification_type, priority, unread_only: bool,
                         limit: int) -> List[Notification]:
        """Alerts addressed to ``user_id``, read from the database each time.
        They are not cached: one user's volume must never push another
        user's alerts out of a shared list."""
        from Api.utils import execute_query

        where = ["dismissed = FALSE", "recipient_user_id = %s"]
        params: list = [user_id]
        if notification_type:
            where.append("type = %s")
            params.append(notification_type.value)
        if priority:
            where.append("priority = %s")
            params.append(priority.value)
        if unread_only:
            where.append("read = FALSE")
        params.append(int(limit))
        rows = execute_query(
            f"SELECT {ALERT_COLUMNS} FROM alerts WHERE {' AND '.join(where)}"  # nosec B608 # ALERT_COLUMNS is a constant, the WHERE items are fixed fragments; values are bound parameters
            " ORDER BY created_at DESC, id DESC LIMIT %s", tuple(params), fetch="all")
        return [
            notification_from_row(row) for row in rows or []
        ]

    def _load_notifications(self) -> None:
        with self._lock:
            self._notifications = self._fetch_notifications()

    def get_notifications(self, notification_type=None, priority=None,
                          unread_only: bool = False, limit: int = 100,
                          for_user_id: Optional[int] = None) -> List[Notification]:
        """Get notifications for ``for_user_id``.

        System-wide alerts + alerts addressed to that user, merged and sorted.
        """
        if for_user_id is not None:
            addressed = self._fetch_addressed(for_user_id, notification_type,
                                              priority, unread_only, limit)
        else:
            addressed = []

        with self._lock:
            system_wide = [n for n in self._notifications
                           if n.recipient_user_id is None]
            if notification_type:
                system_wide = [n for n in system_wide if n.type == notification_type]
            if priority:
                system_wide = [n for n in system_wide if n.priority == priority]
            if unread_only:
                system_wide = [n for n in system_wide if not n.read]
            # Addressed notifications are already filtered by the SQL query.
            all_notifications = system_wide + addressed
            all_notifications.sort(key=lambda n: n.created_at, reverse=True)
            return all_notifications[:limit]

    def get_pending_count(self) -> int:
        with self._lock:
            return len(self._pending_notifications)

    def _save_notification(self, notification: Notification) -> Notification:
        """Queue ``notification`` for the next flush and give it a temporary id.

        Nothing is written here: ``flush_pending_notifications`` performs one
        multi-row INSERT for the whole batch, which is what keeps a burst of
        notifications to a single round trip and lets every object learn its
        real id from the same statement. A negative id means "not persisted
        yet"; callers that need the row immediately call flush.
        """
        with self._lock:
            if notification not in self._pending_notifications:
                notification.id = self._next_temp_id
                self._next_temp_id -= 1
                self._pending_notifications.append(notification)
            if notification not in self._notifications:
                self._notifications.insert(0, notification)
        return notification

    #: Column order of the batch insert; ``created_at`` (index 7) is returned
    #: so the rows can be matched back to the objects that produced them.
    _FLUSH_COLUMNS = (
        "type", "priority", "title", "message", "file_id", "event_date",
        "metadata", "created_at", "read", "dismissed", "recipient_user_id",
        "scenario_id", "scenario_evaluation_id",
    )
    _CREATED_AT_INDEX = _FLUSH_COLUMNS.index("created_at")

    def flush_pending_notifications(self) -> int:
        """Write every queued notification in one statement; return the count.

        One multi-row INSERT keeps a batch atomic (a single round trip) and
        lets the real ids be mapped back to the objects that produced them.
        If the write fails nothing is lost: the batch stays queued and the
        next flush retries it.
        """
        from Api.utils import execute_query
        from psycopg2.extras import Json

        with self._lock:
            pending = list(self._pending_notifications)
            if not pending:
                return 0
            # Identical timestamps would make the returned rows ambiguous, so
            # a batch that shares one moment is spread by a microsecond each.
            used = set()
            for notice in pending:
                stamp = _naive_utc(notice.created_at)
                while stamp in used:
                    stamp = stamp + timedelta(microseconds=1)
                used.add(stamp)
                notice.created_at = stamp

        rows = []
        for notice in pending:
            scenario_eval = (notice.metadata.get("scenario_evaluation_id")
                             if notice.scenario_id is not None else None)
            rows.append((
                notice.type.value, notice.priority.value, notice.title,
                notice.message, notice.file_id, notice.event_date,
                Json(notice.metadata), _naive_utc(notice.created_at), notice.read,
                notice.dismissed, notice.recipient_user_id, notice.scenario_id,
                scenario_eval,
            ))

        columns = ", ".join(self._FLUSH_COLUMNS)
        placeholders = ", ".join(
            "(" + ", ".join(["%s"] * len(self._FLUSH_COLUMNS)) + ")" for _ in rows
        )
        params = [value for row in rows for value in row]
        try:
            returned = execute_query(
                f"INSERT INTO alerts ({columns}) VALUES {placeholders}"
                " RETURNING id, created_at",
                tuple(params),
                fetch="all",
            ) or []
        except Exception as exc:
            logger.error("Error flushing notifications: %s", exc)
            with self._lock:
                for notice in pending:
                    if notice not in self._pending_notifications:
                        self._pending_notifications.append(notice)
            return 0

        by_created = {}
        for row in returned:
            if isinstance(row, dict):
                by_created[row["created_at"]] = row["id"]
            else:
                by_created[row[1]] = row[0]

        written = 0
        with self._lock:
            for notice in pending:
                real_id = by_created.get(notice.created_at)
                if real_id is None:
                    continue
                notice.id = real_id
                for known in self._notifications:
                    if known is notice or known.id == notice.id:
                        known.id = real_id
                written += 1
            # Only rows the database confirmed leave the queue.
            self._pending_notifications = [
                n for n in self._pending_notifications if n.id is None or n.id < 0
            ]
        return written

    def _set_flag(self, notification_id: int, column: str,
                  for_user_id: Optional[int] = None) -> bool:
        """Internal: set read/dismissed flag on a notification ``for_user_id`` may see.
        Returns False when no such notification exists or the user cannot see it."""
        # An explicit check, not ``assert``: the column name is written into
        # the SQL below, and asserts are removed under ``python -O``.
        if column not in ("read", "dismissed"):
            raise ValueError(f"unsupported alert flag column: {column!r}")
        # Pending notifications (negative temporary ids) are not in the
        # database yet; they are always system-wide.
        if notification_id < 0:
            with self._lock:
                for notification in self._notifications:
                    if notification.id == notification_id and visible_to(notification,
                                                                         for_user_id):
                        setattr(notification, column, True)
                        return True
            return False
        from Api.utils import execute_query

        updated_row = execute_query(
            f"UPDATE alerts SET {column} = TRUE WHERE id = %s AND {visibility_clause()}"  # nosec B608 # column is checked against ('read', 'dismissed') above; visibility_clause() is constant; values bound
            " RETURNING id",
            (notification_id, for_user_id),
            fetch="one",
        )
        if not updated_row:
            return False
        with self._lock:
            for notification in self._notifications:
                if notification.id == notification_id:
                    setattr(notification, column, True)
        return True

    def mark_as_read(self, notification_id: int,
                     for_user_id: Optional[int] = None) -> bool:
        """Mark a notification ``for_user_id`` may see as read. Returns False
        when there is no such notification. Database errors propagate."""
        return self._set_flag(notification_id, "read", for_user_id)

    def dismiss_notification(self, notification_id: int,
                             for_user_id: Optional[int] = None) -> bool:
        """Dismiss a notification ``for_user_id`` may see (see mark_as_read)."""
        return self._set_flag(notification_id, "dismissed", for_user_id)

    def refresh_notifications(self) -> int:
        """
        Reload notifications from database (useful after external changes).

        Returns:
            Number of notifications now held in memory.

        The swap is atomic: the fresh list is built OUTSIDE the lock and
        swapped in as a whole, with still-pending (not yet flushed)
        notifications merged back in.  The old clear()+append() sequence let
        two concurrent refreshes interleave and double every row in memory
        ("10 loaded" while the table held 5).
        """
        try:
            fetched = self._fetch_notifications()
        except Exception as e:
            self.logger.error(f"Error refreshing notifications: {e}")
            fetched = None

        if fetched is None:
            return 0

        with self._lock:
            self._notifications = fetched + list(self._pending_notifications)
            loaded = len(self._notifications)
        self.logger.info(f"Refreshed notifications: {loaded} loaded")
        return loaded

    def get_stats(self, for_user_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Exact notification statistics.

        COUNT/GROUP BY queries run in SQL, so totals are correct for any
        table size and memory stays O(1) - unlike the previous implementation
        which forced a full refresh and counted an in-memory list capped at
        LIMIT 1000.  Pending (unflushed) notifications are overlaid so brand
        new alerts are counted immediately.
        """
        today = utc_today()
        visible = visibility_clause()
        stats: Dict[str, Any] = {
            'total': 0,
            'unread': 0,
            'by_type': {},
            'by_priority': {},
            'upcoming_events': 0,
        }

        try:
            from Api.utils import execute_query

            row = execute_query(
                """
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE NOT read) AS unread
                FROM alerts
                WHERE dismissed = FALSE AND """ + visible,  # nosec B608 # visibility_clause() is a constant condition; the user id is a bound parameter
                (for_user_id,),
                fetch="one"
            )
            if row:
                stats['total'] = int(row[0])
                stats['unread'] = int(row[1])

            for type_row in execute_query(
                """
                SELECT type, COUNT(*)
                FROM alerts
                WHERE dismissed = FALSE AND """ + visible + " GROUP BY type",  # nosec B608 # visibility_clause() is a constant condition; the user id is a bound parameter
                (for_user_id,),
                fetch="all"
            ) or []:
                stats['by_type'][type_row[0]] = int(type_row[1])

            for priority_row in execute_query(
                """
                SELECT priority, COUNT(*)
                FROM alerts
                WHERE dismissed = FALSE AND """ + visible + " GROUP BY priority",  # nosec B608 # visibility_clause() is a constant condition; the user id is a bound parameter
                (for_user_id,),
                fetch="all"
            ) or []:
                stats['by_priority'][priority_row[0]] = int(priority_row[1])

            upcoming_row = execute_query(
                """
                SELECT COUNT(*)
                FROM alerts
                WHERE type = %s
                  AND dismissed = FALSE
                  AND event_date BETWEEN %s AND %s
                  AND """ + visible,  # nosec B608 # visibility_clause() is a constant condition; values are bound parameters
                (NotificationType.FUTURE_DATE.value, today, today + timedelta(days=30),
                 for_user_id),
                fetch="one"
            )
            if upcoming_row:
                stats['upcoming_events'] = int(upcoming_row[0])
        except Exception as e:
            self.logger.warning(
                f"SQL stats unavailable, falling back to in-memory counts "
                f"(list capped at {1000}): {e}"
            )
            for n in self.get_notifications(limit=1000, for_user_id=for_user_id):
                stats['total'] += 1
                if not n.read:
                    stats['unread'] += 1
                stats['by_type'][n.type.value] = stats['by_type'].get(n.type.value, 0) + 1
                stats['by_priority'][n.priority.value] = stats['by_priority'].get(n.priority.value, 0) + 1
            stats['upcoming_events'] = len(self.get_upcoming_events(days_ahead=30,
                                                                    for_user_id=for_user_id))
            return stats

        # Overlay notifications that are still queued (not in the DB yet).
        with self._lock:
            pending = [n for n in self._pending_notifications if not n.dismissed]
        for n in pending:
            stats['total'] += 1
            if not n.read:
                stats['unread'] += 1
            stats['by_type'][n.type.value] = stats['by_type'].get(n.type.value, 0) + 1
            stats['by_priority'][n.priority.value] = stats['by_priority'].get(n.priority.value, 0) + 1
            if (
                n.type == NotificationType.FUTURE_DATE
                and n.event_date
                and today <= n.event_date <= today + timedelta(days=30)
            ):
                stats['upcoming_events'] += 1

        return stats

    def get_upcoming_events(self, days_ahead: int = 30,
                            for_user_id: Optional[int] = None) -> List[Notification]:
        """Upcoming future-date events ``for_user_id`` may see, within
        ``days_ahead`` UTC days (SQL + pending overlay)."""
        today = utc_today()
        end_date = today + timedelta(days=days_ahead)

        try:
            from Api.utils import execute_query
            rows = execute_query(
                f"""
                SELECT {ALERT_COLUMNS}
                FROM alerts
                WHERE type = %s
                  AND dismissed = FALSE
                  AND event_date BETWEEN %s AND %s
                  AND {visibility_clause()}
                ORDER BY event_date ASC, id ASC
                LIMIT 1000
                """,  # nosec B608 # ALERT_COLUMNS and visibility_clause() are constants; values are bound parameters
                (NotificationType.FUTURE_DATE.value, today, end_date, for_user_id),
                fetch="all"
            )
            upcoming = [notification_from_row(row) for row in rows or []]
        except Exception as e:
            self.logger.warning(f"SQL upcoming query unavailable, using in-memory list: {e}")
            notifications = self.get_notifications(
                notification_type=NotificationType.FUTURE_DATE,
                unread_only=False,
                for_user_id=for_user_id,
            )
            upcoming = [
                n for n in notifications
                if n.event_date and today <= n.event_date <= end_date
            ]

        # Merge queued (unflushed) future-date notifications.
        with self._lock:
            pending = [
                n for n in self._pending_notifications
                if not n.dismissed
                and n.type == NotificationType.FUTURE_DATE
                and n.event_date
                and today <= n.event_date <= end_date
            ]
        known_ids = {n.id for n in upcoming}
        upcoming.extend(n for n in pending if n.id not in known_ids)

        return sorted(upcoming, key=lambda x: x.event_date or date.max)

    def create_future_date_notification(self, file_id: int, file_name: str,
                                        file_path: str, signal: Dict[str, Any],
                                        reference_date: date) -> Notification:
        """Create a FUTURE_DATE notification from a temporal signal."""
        from core.monitoring.priority import priority_from_signal
        prio = priority_from_signal(signal, reference_date)
        event_date = signal.get('date_from')
        if isinstance(event_date, str):
            event_date = date.fromisoformat(event_date)
        # Extract fields for backward compatibility with tests
        evidence_sentence = signal.get('sentence')
        confidence = signal.get('confidence')
        method = signal.get('method')
        calendar = signal.get('calendar')
        language = signal.get('language')
        detector_ver = signal.get('detector_ver')
        confidence_basis = signal.get('confidence_basis')
        days_until = (event_date - reference_date).days if event_date else None
        notification = Notification(
            id=self._next_temp_id,
            type=NotificationType.FUTURE_DATE,
            priority=prio,
            title=f"Future Date: {signal.get('surface', 'Unknown')}",
            message=f"Date {event_date} found in {file_name}",
            file_id=file_id,
            event_date=event_date,
            metadata={
                'signal': signal,
                'reference_date': reference_date.isoformat(),
                'file_name': file_name,
                'file_path': file_path,
                'evidence_sentence': evidence_sentence,
                'confidence': confidence,
                'confidence_basis': confidence_basis,
                'method': method,
                'calendar': calendar,
                'language': language,
                'detector_ver': detector_ver,
                'days_until': days_until,
            },
            created_at=datetime.now(timezone.utc),
        )
        self._next_temp_id -= 1
        with self._lock:
            self._pending_notifications.append(notification)
        return notification

    def analyze_file_for_future_events(self, file_id: int, file_name: str,
                                       file_path: str, signals: List[Dict[str, Any]],
                                       reference_date: date,
                                       skip_dates: set) -> List[Notification]:
        """Analyze a file's signals and create FUTURE_DATE notifications
        for dates beyond ``reference_date`` that are not in ``skip_dates``."""
        from core.monitoring.priority import priority_from_signal
        created = []
        for sig in signals:
            dt = sig.get('date_from')
            if isinstance(dt, str):
                dt = date.fromisoformat(dt)
            if dt and dt > reference_date and dt not in skip_dates:
                prio = priority_from_signal(sig, reference_date)
                evidence_sentence = sig.get('sentence')
                confidence = sig.get('confidence')
                method = sig.get('method')
                calendar = sig.get('calendar')
                language = sig.get('language')
                detector_ver = sig.get('detector_ver')
                confidence_basis = sig.get('confidence_basis')
                days_until = (dt - reference_date).days
                notification = Notification(
                    id=self._next_temp_id,
                    type=NotificationType.FUTURE_DATE,
                    priority=prio,
                    title=f"Future Date: {sig.get('surface', 'Unknown')}",
                    message=f"Date {dt} found in {file_name}",
                    file_id=file_id,
                    event_date=dt,
                    metadata={
                        'signal': sig,
                        'reference_date': reference_date.isoformat(),
                        'file_name': file_name,
                        'file_path': file_path,
                        'evidence_sentence': evidence_sentence,
                        'confidence': confidence,
                        'confidence_basis': confidence_basis,
                        'method': method,
                        'calendar': calendar,
                        'language': language,
                        'detector_ver': detector_ver,
                        'days_until': days_until,
                    },
                    created_at=datetime.now(timezone.utc),
                )
                self._next_temp_id -= 1
                with self._lock:
                    self._pending_notifications.append(notification)
                created.append(notification)
                skip_dates.add(dt)
        return created


def get_notification_service() -> NotificationService:
    """Get singleton instance of NotificationService"""
    if not hasattr(get_notification_service, '_instance'):
        get_notification_service._instance = NotificationService()
    return get_notification_service._instance