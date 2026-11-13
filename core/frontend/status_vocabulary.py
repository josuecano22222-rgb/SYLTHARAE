"""The application's status vocabulary, and how each status is presented.

An application status is a fact ("COMPLETED_WITH_WARNINGS"); a presentation
state is a decision about how to show it ("warning"). Keeping them apart stops
two things from happening at once:

* twenty semantic badge variants, one per status word anybody ever coined; and
* three different colours for the same status, because three pages each
  decided for themselves.

This module is the single owner of that mapping. `status_badge` renders the
presentation state; the job pages and the JavaScript that draws a status chip
read the same table through the global the application injects, so there is
one answer to "what colour is a paused job" rather than one per consumer.

The mapping is deliberately small. A status this table has never heard of is
not silently given a meaning: it is presented as neutral, with the text the
caller supplied, and marked `known=False` so a reader can tell that the
application said something the interface does not have an opinion about.
"""

from __future__ import annotations

import json
from typing import Dict, List, NamedTuple, Optional

#: The presentation states a caller may render. Deliberately few: these are
#: the answers the interface has, not a word for every status.
PRESENTATION_STATES = (
    "success",     # it worked, it is on, it is well
    "warning",     # attention needed; not broken
    "danger",      # it failed, or it is destructive
    "info",        # neutral-but-notable, in progress
    "progress",    # work is happening now
    "neutral",     # no opinion - the default for anything unrecognised
    "muted",       # set aside: off, archived, cancelled, retired
)


class StatusPresentation(NamedTuple):
    """What to show for one application status."""

    status: str
    state: str
    label: str
    known: bool

    def to_dict(self) -> Dict[str, object]:
        return {"status": self.status, "state": self.state,
                "label": self.label, "known": self.known}


def normalise(status: object) -> str:
    """The key a status is looked up by.

    The application is inconsistent about case and separators - `RUNNING` from
    the job engine, `running` from the task manager, `completed with warnings`
    from a report - and all three are the same status.
    """
    text = str(status or "").strip()
    for separator in (" ", "-", "."):
        text = text.replace(separator, "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text.upper()


#: Application status -> (presentation state, label).
#:
#: Everything the application currently emits is here: the eight job states
#: from `services/jobs/job_state.py`, the task states from `Api/task_manager`,
#: the interface lifecycle from `core/interfaces/lifecycle.py`, and the record
#: states the content pages show.
VOCABULARY: Dict[str, tuple] = {
    # --- work in progress ------------------------------------------------
    "QUEUED": ("neutral", "Queued"),
    "PENDING": ("neutral", "Pending"),
    "RUNNING": ("progress", "Running"),
    "IN_PROGRESS": ("progress", "Running"),
    "PROCESSING": ("progress", "Processing"),
    "PAUSED": ("warning", "Paused"),
    "CANCELLING": ("warning", "Cancelling"),
    "CANCELLED": ("muted", "Cancelled"),
    "CANCELED": ("muted", "Cancelled"),
    # --- outcomes --------------------------------------------------------
    "COMPLETED": ("success", "Completed"),
    "SUCCESS": ("success", "Success"),
    "SUCCEEDED": ("success", "Succeeded"),
    "DONE": ("success", "Done"),
    "COMPLETED_WITH_WARNINGS": ("warning", "Completed with warnings"),
    "FAILED": ("danger", "Failed"),
    "ERROR": ("danger", "Error"),
    "FAILURE": ("danger", "Failure"),
    # --- how a thing stands ----------------------------------------------
    "ACTIVE": ("success", "Active"),
    "ENABLED": ("success", "Enabled"),
    "VALID": ("success", "Valid"),
    "INACTIVE": ("muted", "Inactive"),
    "DISABLED": ("muted", "Disabled"),
    "OFF": ("muted", "Off"),
    "UNUSED": ("muted", "Unused"),
    "INVALID": ("danger", "Invalid"),
    "DUPLICATE": ("warning", "Duplicate"),
    "WARNING": ("warning", "Warning"),
    "DRAFT": ("neutral", "Draft"),
    "PUBLISHED": ("success", "Published"),
    "UNKNOWN": ("neutral", "Unknown"),
    # --- not part of the working set -------------------------------------
    "ARCHIVED": ("muted", "Archived"),
    "UNAVAILABLE": ("muted", "Unavailable"),
    "EXPIRED": ("muted", "Expired"),
    "DELETED": ("muted", "Deleted"),
    # --- interface lifecycle (mirrors core/interfaces/lifecycle.py) -------
    "EXPERIMENTAL": ("info", "Experimental"),
    "DEPRECATED": ("warning", "Deprecated"),
    "RETIRED": ("muted", "Retired"),
}


def present(status: object, label: Optional[str] = None) -> StatusPresentation:
    """How to show this status.

    An unrecognised status keeps its own text and is marked `known=False`:
    the interface does not invent a meaning for a status it was never told
    about, and it does not pretend the status was one it knows.
    """
    key = normalise(status)
    if not key:
        return StatusPresentation("", "neutral", label or "", False)
    entry = VOCABULARY.get(key)
    if entry is None:
        # Its own words, spaced for reading, and no borrowed meaning.
        return StatusPresentation(key, "neutral",
                                  label or key.replace("_", " ").title(), False)
    state, default_label = entry
    return StatusPresentation(key, state, label or default_label, True)


def state_for(status: object) -> str:
    return present(status).state


def known_statuses() -> List[str]:
    return sorted(VOCABULARY)


def vocabulary_json() -> str:
    """The vocabulary, for the JavaScript that renders a status chip.

    One owner, two renderers: the badge component draws it server-side and
    `static/js/modules/core/status.js` draws it when a page updates a status
    without reloading. Neither keeps its own copy.
    """
    return json.dumps(
        {status: {"state": state, "label": label}
         for status, (state, label) in VOCABULARY.items()},
        sort_keys=True, separators=(",", ":"))
