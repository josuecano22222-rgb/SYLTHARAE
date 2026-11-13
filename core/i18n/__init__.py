"""Shared UI translation helpers."""

from __future__ import annotations


def translate_ui(message: str) -> str:
    """Translate a registry or catalog English msgid in the current locale."""
    if not message:
        return ""
    try:
        from flask_babel import gettext as _
    except Exception:
        return message
    try:
        return _(message)
    except Exception:
        return message


__all__ = ["translate_ui"]
