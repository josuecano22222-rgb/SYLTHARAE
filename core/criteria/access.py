"""The caller's :class:`AccessScope` - one place to compute it.

The schema has no per-source ACL: every authenticated role may read every
source (docs/implementation/CRITERIA_SPINE.md), so the scope is currently
unrestricted. It is still built here and passed down to every read path, so
a future ACL is applied once, in SQL, before retrieval - not re-derived by
each route.
"""

from __future__ import annotations

from core.criteria.compiler import AccessScope


def scope_for(user) -> AccessScope:
    return AccessScope.unrestricted(user_id=getattr(user, "id", None),
                                    role=getattr(user, "role", None))
