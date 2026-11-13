"""Validate the *shape* of a JSON body once, so the view can trust it.

Every handler in this project reads its input as ``data.get("name", "")`` and
then calls string methods on it. That is fine for the client the UI sends and
fatal for anyone else: a list, a dict, a number or a string with a NUL byte in
it reached ``.strip()``, ``strptime`` or the database and the client got a 500
with a correlation id (measured: ``tools/audit/probe_malformed.py``, 36
remaining routes after the non-object-body guard).

The helpers below are the small vocabulary the handlers need. Each one either
returns a value the rest of the handler can use without checking the type
again, or raises :class:`PayloadError` carrying the field name and a sentence a
client can act on.

Two rules they encode, both learned from the sweep:

* a text field is a ``str`` - not "something with ``.strip``";
* a text field never contains ``\\x00``. PostgreSQL cannot store it in a
  ``varchar`` and psycopg2 raises ``ValueError`` while the INSERT is being
  built, which is a 500 for a body the client could have fixed.

Nothing here decides *policy* (whether a name may be empty, how long it may be);
that stays with the handler, which passes ``required``/``max_length`` from the
column it is about to write.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

__all__ = [
    "PayloadError",
    "int_field",
    "list_field",
    "number_field",
    "text_field",
    "validate",
]


class PayloadError(ValueError):
    """A client-fixable problem with the request body.

    ``field`` is the key that failed, so a handler can point at it; the message
    is safe to return unchanged (it names the field and the expectation, never
    an internal value or an exception class).
    """

    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.field = field
        self.message = message

    def to_dict(self) -> dict:
        payload = {"error": self.message, "code": "VALIDATION_FAILED"}
        if self.field:
            payload["field"] = self.field
        return payload


def _checked_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise PayloadError(
            f"{field} must be a string, not {type(value).__name__}", field)
    if "\x00" in value:
        raise PayloadError(f"{field} must not contain NUL characters", field)
    return value


def text_field(data: Mapping[str, Any], key: str, *, default: str = "",
               required: bool = False, max_length: Optional[int] = None,
               trim: bool = False) -> str:
    """A ``str`` for *key* (``default`` when absent or ``None``)."""
    value = data.get(key, default)
    if value is None:
        value = default
    text = _checked_text(value, key)
    if trim:
        text = text.strip()
    if required and not text.strip():
        raise PayloadError(f"{key} is required", key)
    if max_length is not None and len(text) > max_length:
        raise PayloadError(
            f"{key} must be at most {max_length} characters (got {len(text)})", key)
    return text


def number_field(data: Mapping[str, Any], key: str, *, default: Any = None,
                 minimum: Optional[float] = None,
                 maximum: Optional[float] = None) -> Any:
    """A number for *key*; text that parses is accepted, booleans are not."""
    if key not in data or data[key] is None:
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise PayloadError(f"{key} must be a number", key)
    try:
        number = float(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        raise PayloadError(f"{key} must be a number", key) from None
    if minimum is not None and number < minimum:
        raise PayloadError(f"{key} must be at least {minimum}", key)
    if maximum is not None and number > maximum:
        raise PayloadError(f"{key} must be at most {maximum}", key)
    return number


def int_field(data: Mapping[str, Any], key: str, *, default: Any = None,
              minimum: Optional[int] = None, maximum: Optional[int] = None) -> Any:
    """An integer for *key*; ``"12"`` is accepted, ``12.5`` and ``True`` are not."""
    if key not in data or data[key] is None:
        return default
    value = data[key]
    if isinstance(value, bool):
        raise PayloadError(f"{key} must be an integer", key)
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        number = int(value.strip())
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    else:
        raise PayloadError(f"{key} must be an integer", key)
    if minimum is not None and number < minimum:
        raise PayloadError(f"{key} must be at least {minimum}", key)
    if maximum is not None and number > maximum:
        raise PayloadError(f"{key} must be at most {maximum}", key)
    return number


def list_field(data: Mapping[str, Any], key: str, *, default: Any = None,
               item_type: Optional[type] = None,
               max_length: Optional[int] = None) -> Any:
    """A ``list`` for *key*, optionally typed and bounded."""
    if key not in data or data[key] is None:
        return default
    value = data[key]
    if not isinstance(value, list):
        raise PayloadError(f"{key} must be a list", key)
    if max_length is not None and len(value) > max_length:
        raise PayloadError(f"{key} must contain at most {max_length} items", key)
    if item_type is not None:
        for item in value:
            if isinstance(item, bool) or not isinstance(item, item_type):
                raise PayloadError(
                    f"{key} must contain only {item_type.__name__} values", key)
    return value


def validate(data: Any, builders: Iterable) -> dict:
    """Run ``(key, builder)`` pairs over *data* and return the results.

    Raises the first :class:`PayloadError`; handlers that want every problem at
    once can catch it and call the builders themselves.
    """
    if not isinstance(data, Mapping):
        raise PayloadError("Request body must be a JSON object")
    results = {}
    for key, builder in builders:
        results[key] = builder(data)
    return results
