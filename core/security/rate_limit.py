"""API-01: Shared Flask-Limiter instance.

Created here (not in the app factory) so blueprints can decorate routes with
``@limiter.limit`` at import time without circular imports; the app factory
calls ``limiter.init_app(app)``. Default limits apply to every route; stricter
per-route limits are declared on login, search, import/export and admin
mutations. In multi-process deployments set RATELIMIT_STORAGE_URI (e.g.
redis://...) so counters are shared.

Rate limits are read from environment at init_app time (not import time)
so that .env values are always used.
"""
import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


#: Limit for the read-only endpoints an *interactive* page calls repeatedly:
#: the archives explorer's section pagination, the reader's chunked content
#: loading, the file-details modal's details call, image serving, and the job
#: progress/error polling. These are not abuse patterns - one page load
#: legitimately issues a burst of them (the explorer loads seven sections at
#: once, and jumping to page 20 walks the cursors from page 1), and against
#: the 60/minute default the operator's own browsing was refused: the browser
#: console shows one 429 per section fetch, and the job page could not show
#: why a file failed for the whole duration of a run.
#:
#: Still bounded - 10 requests a second, nothing like an unbounded exemption -
#: and it *replaces* the default limits for those routes rather than adding to
#: them, so the hourly default cannot be hit by ordinary browsing either.
#: Expensive queries (search), mutations and admin actions keep the strict
#: defaults; see the decorators in Api/routes/.
INTERACTIVE_READ_LIMIT = "600 per minute"


def _get_rate_limits():
    """Read rate limits from environment at call time, not import time."""
    per_minute = os.environ.get("RATE_LIMIT_PER_MINUTE", "60")
    per_hour = os.environ.get("RATE_LIMIT_PER_HOUR", "600")
    return [f"{per_hour} per hour", f"{per_minute} per minute"]


limiter = Limiter(
    get_remote_address,
    default_limits=_get_rate_limits(),  # evaluated at import (before .env)
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
)


def refresh_limits():
    """Re-read rate limits from environment.  Called after .env is loaded."""
    try:
        limiter._default_limits = _get_rate_limits()
    except Exception:
        pass
