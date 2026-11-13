"""Accuracy contract for the shared notification display formatter.

The four API routes used to carry four independent copies of the
title/message translation logic.  Scan-created duplicate notifications
(``metadata.duplicate_count``) were rendered by the similarity branch and
surfaced as "Found 0 similar file(s) with similarity >= 80%" - the UI showed
a message that matched neither the stored row nor reality.  These tests lock
the shared formatter's behaviour: every number shown must come from live
metadata/dates, never from stale creation-time snapshots or fabricated
defaults.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.monitoring.notification_display import (  # noqa: E402
    display_payload,
    format_title_message,
)
from core.monitoring.notification_service import (  # noqa: E402
    Notification,
    NotificationPriority,
    NotificationType,
)


def gettext_stub(text, **kwargs):
    """Identity translation with % interpolation (flask_babel-compatible)."""
    return text % kwargs if kwargs else text


def make_notification(**overrides) -> Notification:
    defaults = dict(
        id=1,
        type=NotificationType.INFO,
        priority=NotificationPriority.MEDIUM,
        title="Stored title",
        message="Stored message",
        file_id=10,
        event_date=None,
        metadata={'file_name': 'report.docx', 'file_path': '/data/report.docx'},
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        read=False,
        dismissed=False,
    )
    # Remove file_name and file_path from overrides if present (Notification no longer has these fields)
    overrides.pop('file_name', None)
    overrides.pop('file_path', None)
    defaults.update(overrides)
    return Notification(**defaults)


def test_duplicate_notification_renders_as_duplicate_with_real_counts():
    """Scan duplicates must show duplicate_count + file names, not '0 similar'."""
    n = make_notification(
        type=NotificationType.SIMILAR_FILES,
        title="Duplicate Files Detected: a.docx",
        message="Found 3 duplicate file(s) with the same hash. Files: a.docx, b.docx, c.docx",
        file_name="a.docx",
        metadata={
            "hash": "abc123",
            "duplicate_count": 3,
            "file_ids": [1, 2, 3],
            "file_names": ["a.docx", "b.docx", "c.docx"],
            "file_paths": ["/x/a.docx", "/x/b.docx", "/x/c.docx"],
        },
    )
    title, message = format_title_message(n, gettext_stub)

    assert title == "Duplicate Files Detected: a.docx"
    assert "Found 3 duplicate file(s) with the same hash" in message
    assert "a.docx, b.docx, c.docx" in message
    # The old translation branch leaked similarity wording into duplicates:
    assert "similar" not in message.lower()
    assert "≥" not in message and "80%" not in message


def test_duplicate_notification_caps_long_file_lists():
    names = [f"f{i}.txt" for i in range(9)]
    n = make_notification(
        type=NotificationType.SIMILAR_FILES,
        metadata={"hash": "h", "duplicate_count": 9, "file_names": names, "file_name": names[0]},
    )
    _, message = format_title_message(n, gettext_stub)
    assert "Found 9 duplicate file(s)" in message
    assert "f0.txt, f1.txt, f2.txt, f3.txt, f4.txt" in message
    assert "... and 4 more" in message


def test_duplicate_without_file_names_keeps_count_only():
    n = make_notification(
        type=NotificationType.SIMILAR_FILES,
        file_name="a.docx",
        metadata={"hash": "h", "duplicate_count": 2},
    )
    _, message = format_title_message(n, gettext_stub)
    assert message == "Found 2 duplicate file(s) with the same hash"


def test_similarity_notification_still_renders_threshold_message():
    n = make_notification(
        type=NotificationType.SIMILAR_FILES,
        metadata={"similar_count": 4, "similarity_threshold": 0.75, "file_name": "b.docx"},
    )
    title, message = format_title_message(n, gettext_stub)
    assert title == "Similar Files Detected: b.docx"
    assert message == "Found 4 similar file(s) with similarity ≥ 75%"


def test_future_date_days_until_is_recomputed_from_event_date():
    """metadata.days_until is frozen at creation; the display must be live."""
    n = make_notification(
        type=NotificationType.FUTURE_DATE,
        event_date=date.today() + timedelta(days=5),
        metadata={"days_until": 999, "context": "meeting on ..."},
    )
    title, message = format_title_message(n, gettext_stub)
    assert title == f"Future Date Detected: {(date.today() + timedelta(days=5)).isoformat()}"
    assert "(5 days away)" in message
    assert "999" not in message


def test_future_date_in_the_past_drops_the_countdown_phrase():
    n = make_notification(
        type=NotificationType.FUTURE_DATE,
        event_date=date.today() - timedelta(days=2),
        metadata={"days_until": 10},
    )
    _, message = format_title_message(n, gettext_stub)
    assert "days away" not in message


def test_info_notifications_pass_through_stored_text_verbatim():
    n = make_notification(
        type=NotificationType.INFO,
        title="Ingest finished",
        message="14 of 15 files stored",
        metadata={"message": "14 of 15 files stored"},
    )
    title, message = format_title_message(n, gettext_stub)
    # The stored text is what the UI shows; the label is only a fallback.
    assert title == "Ingest finished"
    assert message == "14 of 15 files stored"


def test_warning_title_normalised_only_when_missing():
    n = make_notification(type=NotificationType.WARNING, title="Watch out", message="details", metadata={"message": "details"})
    title, message = format_title_message(n, gettext_stub)
    # A stored title is shown as written; the label only fills a blank one.
    assert title == "Watch out"
    assert message == "details"

    blank = make_notification(type=NotificationType.WARNING, title="", message="")
    title, _ = format_title_message(blank, gettext_stub)
    assert title == "Warning"


def test_batch_complete_count_comes_from_metadata():
    n = make_notification(
        type=NotificationType.BATCH_COMPLETE,
        title="Batch Processing Complete",
        metadata={"message": "Batch processing completed: 42 file(s) processed"},
    )
    _, message = format_title_message(n, gettext_stub)
    assert message == "Batch processing completed: 42 file(s) processed"


def test_display_payload_carries_every_field():
    n = make_notification(event_date=date(2030, 5, 1))
    payload = display_payload(n, gettext_stub)
    assert set(payload) == {
        "id", "type", "priority", "title", "message", "file_id", "file_name",
        "file_path", "event_date", "metadata", "created_at", "read", "dismissed",
        "addressed", "recipient_user_id", "scenario_id", "scenario_evaluation_id",
    }
    assert payload["event_date"] == "2030-05-01"
    # A system-wide notification: addressed to nobody in particular, no rule.
    assert payload["addressed"] is False
    assert payload["scenario_id"] is None
    assert payload["type"] == "info"



