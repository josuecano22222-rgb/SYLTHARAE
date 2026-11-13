"""
Notification display formatting - single source of truth for how a
Notification becomes the title/message shown in the UI.
"""

from datetime import date
from core.monitoring.notification_service import Notification, NotificationType


def format_title_message(notification: Notification, _=None) -> tuple[str, str]:
    """Return (title, message) for a notification in the caller's language.

    The optional ``_`` argument is ``flask_babel.gettext`` when called from
    a request context; if absent the raw English strings are used.
    """
    t = notification.type
    meta = notification.metadata or {}

    if t == NotificationType.SIMILAR_FILES:
        file_names = meta.get('file_names', [])
        count = meta.get('duplicate_count', len(file_names))
        names = file_names[:5]
        if len(file_names) > 5:
            names.append(f"... and {len(file_names) - 5} more")
        # Use file_name from metadata (notification no longer has file_name field)
        primary_file = meta.get('file_name') or (file_names[0] if file_names else 'Unknown')
        
        # Check if this is a similarity notification (has similar_count but no duplicate_count)
        if 'similar_count' in meta and 'duplicate_count' not in meta:
            title = f"Similar Files Detected: {primary_file}"
            msg = f"Found {meta['similar_count']} similar file(s) with similarity ≥ {int(meta.get('similarity_threshold', 0.8) * 100)}%"
        else:
            title = f"Duplicate Files Detected: {primary_file}"
            if file_names:
                msg = f"Found {count} duplicate file(s) with the same hash. Files: {', '.join(names)}"
            else:
                msg = f"Found {count} duplicate file(s) with the same hash"
        return title, msg

    if t == NotificationType.FUTURE_DATE:
        event_date = notification.event_date
        date_str = event_date.isoformat() if event_date else "unknown date"
        # Use file_name from metadata (notification no longer has file_name field)
        primary_file = meta.get('file_name') or meta.get('file_names', [''])[0] or 'this file'
        title = f"Future Date Detected: {date_str}"
        msg = f"Date {date_str} found in {primary_file}"
        # Add live countdown
        if event_date:
            from datetime import date as _date
            today = _date.today()
            if event_date >= today:
                days = (event_date - today).days
                if days == 0:
                    msg += " (today)"
                elif days == 1:
                    msg += " (tomorrow)"
                else:
                    msg += f" ({days} days away)"
        return title, msg

    if t == NotificationType.FUTURE_EVENT:
        # Use file_name from metadata (notification no longer has file_name field)
        primary_file = meta.get('file_name') or meta.get('file_names', [''])[0] or 'Unknown'
        title = f"Future Event: {primary_file}"
        msg = meta.get('message', 'A future event was detected.')
        return title, msg

    # Generic types: the stored text is what the writer chose to say, so it is
    # shown verbatim. The label is a fallback for a row that carries none -
    # replacing a real title with "Information" hid the message that was sent.
    if t == NotificationType.PROCESSING_COMPLETE:
        return (notification.title or "Processing Complete",
                notification.message or meta.get('message', 'File processing has completed.'))

    if t == NotificationType.BATCH_COMPLETE:
        # The counts are computed when the batch finishes and carried in
        # metadata; that sentence is richer than the row's stored message.
        return (notification.title or "Batch Processing Complete",
                meta.get('message') or notification.message
                or 'Batch processing has completed.')

    if t == NotificationType.ERROR:
        return (notification.title or "Error",
                notification.message or meta.get('message', 'An error occurred.'))

    if t == NotificationType.WARNING:
        return (notification.title or "Warning",
                notification.message or meta.get('message', 'A warning was issued.'))

    if t == NotificationType.INFO:
        return (notification.title or "Information",
                notification.message or meta.get('message', 'An informational message.'))

    if t == NotificationType.SCENARIO_OUTCOME:
        title = meta.get('title', 'Scenario Outcome')
        msg = meta.get('message', 'A scenario produced a new outcome.')
        return title, msg

    if t == NotificationType.SCENARIO_STATUS:
        title = meta.get('title', 'Scenario Status')
        msg = meta.get('message', 'A scenario status changed.')
        return title, msg

    # Fallback for any future types
    return notification.title, notification.message


def path_names(file_ids) -> dict:
    """``{file_id: (file_name, file_path)}`` for the referenced paths.

    ``alerts`` stores only ``file_id``; the name and path belong to ``paths``
    and are read with one join-equivalent query for the whole page rather than
    one query per row. Rows whose path is gone simply have no entry.
    """
    ids = sorted({int(i) for i in file_ids if i is not None})
    if not ids:
        return {}
    try:
        from Api.utils import execute_query

        rows = execute_query(
            "SELECT p.id, p.file_name, p.file_path FROM paths p"
            " WHERE p.id = ANY(%s)",
            (ids,),
            fetch="all",
        ) or []
    except Exception:  # pragma: no cover - display must never fail the list
        return {}
    return {r[0]: (r[1], r[2]) for r in rows}


def display_payload(notification: Notification, _=None, names=None) -> dict:
    """The complete JSON payload the UI expects for a notification.

    ``names`` is the result of :func:`path_names`; the metadata copy is a
    fallback for rows written before the read joined ``paths``.
    """
    title, message = format_title_message(notification, _)
    file_name, file_path = (names or {}).get(
        notification.file_id,
        (notification.metadata.get('file_name'),
         notification.metadata.get('file_path')))
    return {
        'id': notification.id,
        'type': notification.type.value,
        'priority': notification.priority.value,
        'title': title,
        'message': message,
        'file_id': notification.file_id,
        'file_name': file_name,
        'file_path': file_path,
        'event_date': notification.event_date.isoformat() if notification.event_date else None,
        'metadata': notification.metadata,
        'created_at': notification.created_at.isoformat() if notification.created_at else None,
        'read': notification.read,
        'dismissed': notification.dismissed,
        'recipient_user_id': notification.recipient_user_id,
        'scenario_id': notification.scenario_id,
        'scenario_evaluation_id': notification.scenario_evaluation_id,
        'addressed': notification.recipient_user_id is not None,
    }