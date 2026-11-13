"""Environment-driven Flask session cookie settings."""

from __future__ import annotations

import os
from collections.abc import Mapping

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_VALID_SAMESITE = {"lax": "Lax", "strict": "Strict", "none": "None"}


def session_cookie_settings(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """Return validated Flask session-cookie flags.

    Defaults preserve the existing policy: Secure in production and SameSite
    Lax. Explicit overrides support HTTPS preview/embed environments where the
    browser loads the application as a third-party frame. SameSite=None always
    requires Secure, as enforced by modern browsers.
    """
    env = os.environ if env is None else env
    secure_value = env.get("SESSION_COOKIE_SECURE")
    if secure_value is None or not secure_value.strip():
        secure = env.get("FLASK_ENV", "production").strip().lower() == "production"
    else:
        value = secure_value.strip().lower()
        if value in _TRUE:
            secure = True
        elif value in _FALSE:
            secure = False
        else:
            raise ValueError("SESSION_COOKIE_SECURE must be a boolean value")

    samesite_value = env.get("SESSION_COOKIE_SAMESITE", "Lax").strip().lower()
    try:
        samesite = _VALID_SAMESITE[samesite_value]
    except KeyError as exc:
        raise ValueError(
            "SESSION_COOKIE_SAMESITE must be Lax, Strict, or None"
        ) from exc

    if samesite == "None" and not secure:
        raise ValueError("SESSION_COOKIE_SAMESITE=None requires SESSION_COOKIE_SECURE=true")

    return {"SESSION_COOKIE_SECURE": secure, "SESSION_COOKIE_SAMESITE": samesite}
