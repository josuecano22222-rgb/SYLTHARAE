"""Shared utilities for monitoring rule/scenario definitions.

This module provides the common parsing utilities used by both rule and
scenario definitions. The rule-specific definition classes have been removed;
only the shared helpers remain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from core.criteria.model import Criteria, CriteriaError, from_dict
from services.detection.signal_query import SignalFilter, SignalQueryError, parse_filter

#: Confidence levels accepted by signal conditions.
CONFIDENCE_LEVELS = ("low", "medium", "high")

#: Maximum event window days (10 years) - sanity limit on user input.
MAX_WINDOW_DAYS = 3650


class RuleDefinitionError(ValueError):
    """A definition that cannot be accepted as written (HTTP 400)."""


def _int(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuleDefinitionError(f"{name} must be an integer")
    if not lo <= value <= hi:
        raise RuleDefinitionError(f"{name} must be between {lo} and {hi}")
    return value


def _choice(value: Any, name: str, allowed) -> str:
    if value not in allowed:
        raise RuleDefinitionError(f"{name} must be one of: {', '.join(allowed)}")
    return value


def _object(value: Any, name: str, keys) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise RuleDefinitionError(f"{name} must be an object")
    unknown = sorted(set(value) - set(keys))
    if unknown:
        raise RuleDefinitionError(f"unknown {name} field(s): {', '.join(unknown)}")
    return value


# SignalFilter field mapping for parse_signal_filter
_SIGNAL_ARGS = {
    "detectors": "detector", "signal_types": "signal_type", "resolutions": "resolution",
    "confidence": "confidence", "languages": "language", "calendars": "calendar",
    "methods": "method", "orientations": "orientation", "place_keys": "place_key",
    "identifier_kinds": "identifier_kind", "hash_ids": "hash_id",
}
_SIGNAL_SCALARS = {"evidence_text": "evidence_text", "surface_text": "surface_text",
                   "value_text": "value_text", "event_from": "event_from",
                   "event_to": "event_to"}
_SIGNAL_MODES = {"evidence_mode": "evidence_mode", "surface_mode": "surface_mode",
                 "value_mode": "value_mode"}


def parse_signal_filter(payload: Any) -> SignalFilter:
    """A ``SignalFilter`` from its canonical JSON form, through the Explorer's
    own parser (so a rule/scenario accepts exactly what the Explorer accepts)."""
    if payload in (None, {}):
        return SignalFilter()
    payload = _object(payload, "signals",
                      list(_SIGNAL_ARGS) + list(_SIGNAL_SCALARS) + list(_SIGNAL_MODES))
    from werkzeug.datastructures import MultiDict
    args = MultiDict()
    for key, arg in _SIGNAL_ARGS.items():
        values = payload.get(key)
        if values in (None, []):
            continue
        if not isinstance(values, list):
            raise RuleDefinitionError(f"signals.{key} must be a list")
        for v in values:
            if isinstance(v, bool) or not isinstance(v, (str, int)):
                raise RuleDefinitionError(f"signals.{key} values must be strings")
            args.add(arg, str(v))
    for key, arg in _SIGNAL_SCALARS.items():
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise RuleDefinitionError(f"signals.{key} must be a string")
        args.add(arg, value)
    for key, arg in _SIGNAL_MODES.items():
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise RuleDefinitionError(f"signals.{key} must be a string")
        args.add(arg, value)
    try:
        return parse_filter(args)
    except SignalQueryError as exc:
        raise RuleDefinitionError(f"signals: {exc}") from exc


def validate_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise RuleDefinitionError("name is required")
    name = " ".join(name.split())
    if len(name) > 200:
        raise RuleDefinitionError(f"name is limited to 200 characters")
    return name