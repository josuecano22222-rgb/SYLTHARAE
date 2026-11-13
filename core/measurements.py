"""Measurements, not guesses.

Every number the interface shows about what the system *did* is one of exactly
three things, and the payload has to say which:

``MEASURED``
    Computed from data the system actually recorded (rows it stored, timings
    it wrote down). Reproducible from the database alone.

``ESTIMATED``
    Derived from recorded data by a stated rule - "average per file over 12
    completed jobs", for example. Useful, and honest about being a derivation.

``UNAVAILABLE``
    Nothing was recorded. The interface must say so. It must NOT substitute a
    plausible-looking constant: a hard-coded "98.5% success rate" or "2.3s
    average" reads to an operator as a measurement of their system, and they
    will make decisions on it.

This module exists so that rule is enforced by the type rather than by
discipline. A :class:`Measurement` cannot be constructed in an inconsistent
state - an unavailable measurement cannot carry a value, and a measured or
estimated one cannot be constructed without one - so the only way to display a
number is to have a real basis for it.

Usage::

    from core.measurements import measured, estimated, unavailable

    rate = measured(97.4, unit="%", sample_size=1840, detail="Last 7 days")
    per_file = estimated(1.9, unit="s", sample_size=12,
                         detail="Average per file across 12 completed jobs")
    throughput = unavailable("No job has completed since the last restart")

and in a template (``measurement`` is the ``to_dict()`` form)::

    {{ stats.success_rate.display }}   -> "97.4 %" / "Estimated 1.9 s" / "No measurement available"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

MEASURED = "measured"
ESTIMATED = "estimated"
UNAVAILABLE = "unavailable"

STATES = (MEASURED, ESTIMATED, UNAVAILABLE)

#: The wording the interface uses when there is nothing real to show. Kept in
#: one place so every page says the same thing in the same words.
NO_MEASUREMENT_TEXT = "No measurement available"
INSUFFICIENT_DATA_TEXT = "Insufficient data"
ESTIMATED_TEXT = "Estimated"

_STATE_LABELS = {
    MEASURED: "",
    ESTIMATED: ESTIMATED_TEXT,
    UNAVAILABLE: NO_MEASUREMENT_TEXT,
}


@dataclass(frozen=True)
class Measurement:
    """A number the interface may show, with the basis it was derived from.

    ``detail`` explains the basis in the operator's language ("Last 7 days",
    "Average per file across 12 completed jobs") and is shown next to the
    value, so a figure is never presented without its provenance.

    ``sample_size`` is the number of underlying observations, when that is
    meaningful; it is what turns "Estimated" from a hedge into a statement.
    """

    value: Optional[float]
    state: str
    detail: str = ""
    unit: str = ""
    sample_size: int = 0

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown measurement state {self.state!r}")
        if self.state == UNAVAILABLE and self.value is not None:
            raise ValueError(
                "an unavailable measurement cannot carry a value - that is the "
                "fabricated-metric bug this type exists to prevent"
            )
        if self.state in (MEASURED, ESTIMATED) and self.value is None:
            raise ValueError(f"a {self.state} measurement needs a value")

    # -- presentation -----------------------------------------------------
    @property
    def available(self) -> bool:
        return self.state != UNAVAILABLE

    @property
    def label(self) -> str:
        """The word that must appear beside the value ('' when measured)."""
        return _STATE_LABELS[self.state]

    def display(self, decimals: int = 1, suffix: Optional[str] = None) -> str:
        """The value as text: '97.4 %', 'Estimated 1.9 s', 'No measurement available'."""
        if self.state == UNAVAILABLE:
            return self.detail or NO_MEASUREMENT_TEXT
        unit = self.unit if suffix is None else suffix
        number = f"{self.value:.{decimals}f}".rstrip("0").rstrip(".") if self.value is not None else ""
        text = f"{number} {unit}".strip()
        return f"{self.label} {text}".strip() if self.label else text

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "state": self.state,
            "available": self.available,
            "label": self.label,
            "display": self.display(),
            "detail": self.detail,
            "unit": self.unit,
            "sample_size": self.sample_size,
        }


def measured(value: float, **kwargs: Any) -> Measurement:
    """A figure computed from recorded data."""
    return Measurement(value=float(value), state=MEASURED, **kwargs)


def estimated(value: float, **kwargs: Any) -> Measurement:
    """A figure derived from recorded data by the rule stated in ``detail``."""
    return Measurement(value=float(value), state=ESTIMATED, **kwargs)


def unavailable(reason: str = "", **kwargs: Any) -> Measurement:
    """No basis exists. ``reason`` is shown to the operator."""
    return Measurement(value=None, state=UNAVAILABLE, detail=reason, **kwargs)
