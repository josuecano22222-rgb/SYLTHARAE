"""What each lifecycle status *means*, in one place.

`ACTIVE` and `DEPRECATED` are not labels, they are promises the product makes:
whether an interface is navigable, whether it is switchable, how it is marked,
and what a reader is told. Before this module the answer lived wherever the
question was asked - navigation, settings, the interface API and the generated
documentation each had their own idea, which is how "deprecated" comes to mean
four different things in one application.

The policy is a value, so it can be generated into documentation and asserted
by tests:

    >>> policy(InterfaceStatus.DEPRECATED).navigable
    True
    >>> policy(InterfaceStatus.DEPRECATED).badge
    'Deprecated'
    >>> policy(InterfaceStatus.RETIRED).navigable
    False

`EXPERIMENTAL` is navigable but only while its feature flag is on; that flag
test stays in the state service (`InterfaceState`), because a policy is a
description and the flag is stored state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .model import InterfaceStatus


@dataclass(frozen=True)
class StatusPolicy:
    """How a lifecycle status behaves, everywhere it is observed."""

    status: InterfaceStatus
    #: Shown in navigation (subject to the flag test for EXPERIMENTAL).
    navigable: bool
    #: Listed in the interface manager with a working switch.
    switchable: bool
    #: Short marker for the interface, or None when it needs no marking.
    badge: Optional[str]
    #: One sentence a reader is shown - navigation tooltip, settings row, API.
    note: str
    #: Whether this status may appear in the generated reference as a product
    #: surface (a retired interface is documented as retired, not as a page).
    documented_as_surface: bool

    def __str__(self) -> str:  # pragma: no cover - convenience for debugging
        return f"{self.status}"


ACTIVE = StatusPolicy(
    status=InterfaceStatus.ACTIVE,
    navigable=True,
    switchable=True,
    badge=None,
    # No note: an active interface needs no status remark. The navigation
    # tooltip already carries the interface's own description, and appending
    # a blanket sentence to every active entry read as filler, not as
    # information. (Non-empty notes are still required for statuses that
    # actually need saying something: EXPERIMENTAL, DEPRECATED, RETIRED.)
    note="",
    documented_as_surface=True,
)

EXPERIMENTAL = StatusPolicy(
    status=InterfaceStatus.EXPERIMENTAL,
    navigable=True,          # navigable once its feature flag is on
    switchable=True,
    badge="Experimental",
    note="Not finished. Shown only while its feature flag is enabled.",
    documented_as_surface=True,
)

DEPRECATED = StatusPolicy(
    status=InterfaceStatus.DEPRECATED,
    navigable=True,
    switchable=True,
    badge="Deprecated",
    note="Still works and is still supported, but it is scheduled to be "
         "replaced. Use it only where the replacement is not yet available.",
    documented_as_surface=True,
)

RETIRED = StatusPolicy(
    status=InterfaceStatus.RETIRED,
    navigable=False,
    switchable=False,
    badge="Retired",
    note="No longer part of the product. A stored setting for it is kept and "
         "ignored rather than deleted.",
    documented_as_surface=False,
)

_POLICIES: Dict[InterfaceStatus, StatusPolicy] = {
    policy.status: policy for policy in (ACTIVE, EXPERIMENTAL, DEPRECATED, RETIRED)
}


def policy(status) -> StatusPolicy:
    """The policy for a status (accepts the enum or its name)."""
    if isinstance(status, str):
        try:
            status = InterfaceStatus(status)
        except ValueError:  # pragma: no cover - guarded by validation
            raise KeyError(f"unknown interface status {status!r}") from None
    return _POLICIES[status]


def all_policies() -> Dict[str, StatusPolicy]:
    """Every policy, keyed by status name - for documentation and the API."""
    return {str(status): policy(status) for status in InterfaceStatus}
