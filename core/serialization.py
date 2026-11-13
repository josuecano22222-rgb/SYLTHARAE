"""Safe serialization for database blobs (DB-08).

Historically the application stored word-id lists, title id lists and
position indexers as **pickle** blobs.  Pickle deserialization of attacker
influenced data is code execution; combined with the (now fixed) backup
import injection this was a realistic escalation chain.

Policy:

* All NEW data is written as UTF-8 JSON (``pack_*`` helpers).
* Reading legacy pickle payloads is permitted only through
  :class:`_RestrictedUnpickler`, which allows only built-in container types,
  ints, strings, floats, bools and None - no globals, no reduction, no code
  execution.  Unknown payloads fail closed.
* The data-repair tool (``scripts/repair_data.py``) rewrites legacy blobs to
  JSON; after repair the pickle path is never exercised.
"""

from __future__ import annotations

import io
import json
import logging
import pickle
from typing import Any, List

logger = logging.getLogger(__name__)

_ALLOWED_GLOBALS: frozenset = frozenset()  # nothing from module scope is permitted


class RestrictedDeserializationError(ValueError):
    """Raised when a legacy blob contains disallowed pickle opcodes."""


class _RestrictedUnpickler(pickle.Unpickler):
    """Unpickler that refuses anything beyond plain built-in data."""

    def find_class(self, module, name):  # noqa: D102
        raise RestrictedDeserializationError(
            f"Forbidden pickle global during legacy read: {module}.{name}"
        )


def _looks_like_pickle(data: bytes) -> bool:
    return bool(data) and data[0] in (0x80, 0x04, 0x02) or (
        bool(data) and data[0] == ord("(")
    )


def pack_int_list(ids: List[int]) -> bytes:
    """Serialize a list of integers as JSON bytes."""
    clean = [int(i) for i in (ids or [])]
    return json.dumps(clean, separators=(",", ":")).encode("utf-8")


def unpack_int_list(data: bytes | bytearray | memoryview | str | None) -> List[int]:
    """Deserialize an int list written by :func:`pack_int_list`.

    Legacy pickle payloads are readable exactly once per deployment lifecycle
    (until repaired) and only through the restricted unpickler.
    """
    if data is None:
        return []
    if isinstance(data, str):
        data = data.encode("utf-8")
    data = bytes(data)
    if not data:
        return []
    if _looks_like_pickle(data):
        try:
            value = _RestrictedUnpickler(io.BytesIO(data)).load()
        except Exception as exc:
            raise RestrictedDeserializationError(
                "Legacy blob could not be safely deserialized"
            ) from exc
        if isinstance(value, list):
            return [int(i) for i in value if isinstance(i, (int, float)) and not isinstance(i, bool)]
        if isinstance(value, (int, float)):
            return [int(value)]
        raise RestrictedDeserializationError("Legacy blob is not an int list")
    try:
        value = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RestrictedDeserializationError("Blob is neither JSON nor legacy data") from exc
    if isinstance(value, list):
        return [int(i) for i in value]
    if isinstance(value, (int, float)):
        return [int(value)]
    return []


def pack_mapping(obj: Any) -> bytes:
    """Serialize a JSON-compatible mapping/list as JSON bytes."""
    return json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8")


def unpack_mapping(data: bytes | bytearray | memoryview | None) -> Any:
    """Deserialize JSON bytes written by :func:`pack_mapping`."""
    if not data:
        return None
    if isinstance(data, str):
        data = data.encode("utf-8")
    data = bytes(data)
    if _looks_like_pickle(data):
        try:
            return _RestrictedUnpickler(io.BytesIO(data)).load()
        except Exception as exc:
            raise RestrictedDeserializationError(
                "Legacy blob could not be safely deserialized"
            ) from exc
    return json.loads(data.decode("utf-8"))
