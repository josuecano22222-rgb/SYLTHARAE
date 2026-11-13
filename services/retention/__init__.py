"""Retention (step 21): explicit, audited pruning of append-only growth.

* :mod:`services.retention.model` - the policy table (what may be pruned,
  how old, never younger, and what 0 means) and pure validation;
* :mod:`services.retention.service` - counting, batched set-based deletes,
  tombstone-free whole-row removal, per-area audit, and the ``retention``
  job body the scheduler and the Run-now button both go through.

Nothing is deleted silently: every applied area writes one
``retention.applied`` audit row with the days, the deleted count and the
batch count, and an area whose days setting is 0 is kept forever - stated,
never assumed.
"""

from services.retention.model import (  # noqa: F401
    AREAS, KEEP_FOREVER, POLICIES, RetentionError, effective_days, policy_for,
    validate_days,
)
