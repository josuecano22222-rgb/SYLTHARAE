"""The guard rails an automatic apply needs before it deletes anything.

The workbook is authoritative, which is a good rule and a dangerous one: a
truncated download, a half-saved file and a workbook whose sheets were emptied
are all "authoritative" too, and a startup apply has nobody to ask. This module
is the answer to "how much of the taxonomy may one sync remove without a human?"

It measures *removals* - unlinked terms, deleted keywords, categories the
workbook no longer mentions and categories a plan would delete - against what the
database currently holds, and refuses above a fraction. The fraction is
``SYLTHARAE_TAXONOMY_SHRINK_MAX`` (0..1, default 0.25): a sync that would remove
more than a quarter of what is stored is refused with the numbers in the message,
and the operator raises the limit deliberately if that is what they meant.

Refusing is the right failure for a boot-time step. A confirmation prompt cannot
be answered at 3am by a supervisor, and "applied, look at the log" is how a
taxonomy disappears.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

#: The fraction of the stored taxonomy one sync may remove without a decision.
LIMIT_ENV = "SYLTHARAE_TAXONOMY_SHRINK_MAX"
DEFAULT_MAX_SHRINK = 0.25

#: Always allowed, whatever the fraction. A percentage alone is nonsense on a
#: small database: one stored membership removed is "100%", and a guard that
#: refuses every edit to a new taxonomy is a guard people switch off. The floor
#: makes the guard bite where the loss is real - a large curated taxonomy - and
#: stay out of the way while one is being built.
MIN_ABSOLUTE = 25


def max_shrink(default: float = DEFAULT_MAX_SHRINK) -> float:
    """The configured limit, or the default when it is absent or unusable."""
    raw = (os.environ.get(LIMIT_ENV) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not 0.0 <= value <= 1.0:
        return default
    return value


def _removals(plan: Any) -> int:
    """How many stored rows this plan would take away.

    A category that will be pruned appears in both ``missing_categories`` and
    ``delete_categories``; counting both said "10 of 6 stored rows", which is
    how a guard earns a reputation for being wrong. Categories are counted once,
    by name, and a category the workbook merely stopped mentioning counts too:
    that is the shape a truncated file has, and it is exactly the case this
    guard exists for.
    """
    categories = {str(name).casefold()
                  for name in (getattr(plan, "missing_categories", ()) or ())}
    categories |= {str(name).casefold()
                   for name in (getattr(plan, "delete_categories", ()) or ())}
    return (len(getattr(plan, "unlink_terms", ()) or ())
            + len(getattr(plan, "delete_keywords", ()) or ())
            + len(categories))


def removed_threshold(base: int, limit: float) -> int:
    """How many removals the fraction allows, before the absolute floor."""
    return int(base * limit)


def _additions(plan: Any) -> int:
    """Rows this plan would add: the other half of "does the taxonomy shrink?"."""
    total = 0
    for field in ("link_terms", "create_keywords"):
        total += len(getattr(plan, field, ()) or ())
    return total


def check(plan: Any, stored: Dict[str, int],
          limit: Optional[float] = None) -> Dict[str, Any]:
    """``{ok, removed, added, after, stored, fraction, limit, reason}``.

    ``stored`` is ``taxonomy_sync.snapshot(conn)``: table counts.

    The measure is the **size the taxonomy would have afterwards**, not the
    number of removals. A first sync that replaces a small hand-seeded database
    with the full workbook removes every seeded row and adds thousands; judging
    it by removals alone refused a run that grows the taxonomy by two orders of
    magnitude. A truncated workbook removes almost everything and adds nothing,
    which is the case this guard exists for - and it still fails.
    """
    limit = max_shrink() if limit is None else limit
    removed = _removals(plan)
    added = _additions(plan)
    base = int(stored.get("words_categorys") or 0) + int(stored.get("keywords") or 0)
    after = max(0, base - removed + added)
    fraction = 0.0 if base <= 0 else min(1.0, (base - after) / base)
    if base <= 0:
        return {"ok": True, "removed": removed, "stored": 0, "fraction": 0.0,
                "limit": limit, "allowance": removed,
                "reason": "the database stores no taxonomy yet, so nothing can "
                          "be removed"}
    allowance = max(removed_threshold(base, limit), MIN_ABSOLUTE)
    if base - after <= allowance:
        return {"ok": True, "removed": removed, "added": added, "after": after,
                "stored": base, "fraction": round(fraction, 4), "limit": limit,
                "allowance": allowance,
                "reason": (f"the taxonomy would go from {base} to {after} stored "
                           f"rows ({removed} removed, {added} added), which is "
                           f"within the {limit:.0%} limit and its "
                           f"{MIN_ABSOLUTE}-row floor")}
    return {
        "ok": False, "removed": removed, "added": added, "after": after,
        "stored": base, "fraction": round(fraction, 4), "limit": limit,
        "allowance": allowance,
        "reason": (f"this sync would take the taxonomy from {base} to {after} "
                   f"stored rows ({removed} removed, {added} added), a "
                   f"{fraction:.0%} shrink, above the {limit:.0%} limit and its "
                   f"{MIN_ABSOLUTE}-row floor; the workbook is probably "
                   f"truncated, half-saved or missing sheets. Nothing was "
                   f"written. Raise {LIMIT_ENV} deliberately if the removal is "
                   f"intended"),
    }
