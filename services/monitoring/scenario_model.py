"""Scenario definitions: strict parsing, canonical form, fingerprint.

A scenario classifies each content of its **population** into **outcomes**
by **cases** over named **conditions**. It adds no filter language: document
conditions are the canonical ``Criteria`` (core/criteria), signal conditions
the Signal Explorer's ``SignalFilter``, both parsed by their own parsers
(through rule_model, shared with monitoring rules).

Canonical JSON::

    {"criteria": {...},                 # the population (optional: every
                                        # content the owner may read)
     "conditions": {
        "dated_soon": {"signals": {"signal_types": ["date_reference"]},
                       "min_confidence": "medium",
                       "event_window_days": {"from": 0, "to": 30},
                       "min_count": 1},
        "in_source_3": {"criteria": {"sources": [3]}}},
     "cases": [
        {"id": "escalate", "when": {"all": ["dated_soon"], "none": ["in_source_3"]},
         "outcome": "review", "priority": 20}],
     "outcomes": {"review": {"label": "Needs review", "actions": ["notify"]},
                  "none":   {"label": "No action", "actions": []}},
     "default_outcome": "none",
     "strategy": "first_match",
     "notify_existing": false}

Semantics (evaluated set-based in SQL, services/monitoring/scenario_engine):

``conditions``      A signal condition holds for a content when at least
                    ``min_count`` (default 1) of its signals match; a signal
                    whose confidence was not recorded never passes a
                    ``min_confidence``; undated signals never match an event
                    window. A document condition holds when the content has a
                    file occurrence matching the criteria (under the owner's
                    access scope).
``when``            ``all``: every listed condition holds; ``any``: at least
                    one does; ``none``: none does - **absence is explicit**.
                    A case needs at least one listed condition.
``strategy``        ``first_match``: the first matching case in list order
                    decides. ``highest_priority``: the matching case with the
                    highest ``priority`` decides (priorities must be unique,
                    so the choice is never a tie-break). ``all_matching``:
                    every matching case contributes its outcome.
``default_outcome`` Mandatory: the outcome of a content no case matches. It
                    may not carry actions - the default applies to most of
                    the corpus, and notifying on it would notify on nothing
                    in particular.
``priority``        (case) orders cases for ``highest_priority`` only. It is
                    **not** the notification priority, which is derived from
                    the evidence (core/monitoring/priority.py), as for rules.
``actions``         ``notify``: when a content *enters* the outcome, notify
                    the owner. Every outcome is recorded whatever its actions.
``notify_existing`` ``false``: outcomes found when a version starts are the
                    baseline, recorded but not notified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.criteria.model import Criteria, CriteriaError, from_dict, sha256_hex
from services.detection.signal_query import SignalFilter
from services.monitoring.rule_model import (CONFIDENCE_LEVELS, MAX_WINDOW_DAYS,
                                            RuleDefinitionError, _choice, _int, _object,
                                            parse_signal_filter, validate_name)

SCENARIO_SCHEMA_VERSION = 1
STRATEGIES = ("first_match", "all_matching", "highest_priority")
ACTIONS = ("notify",)
MAX_CONDITIONS = 20
MAX_CASES = 30
MAX_OUTCOMES = 20
MAX_MIN_COUNT = 10_000
MAX_CASE_PRIORITY = 1000
MAX_LABEL_LENGTH = 120
#: Identifiers of conditions, cases and outcomes: they appear in stored
#: results and notifications, so they are plain and stable.
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")

_DEFINITION_KEYS = {"schema_version", "criteria", "conditions", "cases", "outcomes",
                    "default_outcome", "strategy", "notify_existing"}

__all__ = ["ScenarioDefinition", "ScenarioDefinitionError", "parse_definition",
           "definition_from_stored", "validate_name", "STRATEGIES"]


class ScenarioDefinitionError(RuleDefinitionError):
    """A scenario definition is invalid (400)."""


def _identifier(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ScenarioDefinitionError(
            f"{what} {value!r} must be lower-case letters, digits and _ (starting with a"
            " letter, at most 40 characters)")
    return value


def _criteria(raw: Any, where: str) -> Criteria:
    if raw is None:
        return Criteria()
    if not isinstance(raw, dict):
        raise ScenarioDefinitionError(f"{where} must be an object")
    try:
        return from_dict(raw)
    except CriteriaError as exc:
        raise ScenarioDefinitionError(f"{where}: {exc}")


def _criteria_is_empty(criteria: Criteria) -> bool:
    neutral = {"unit", "sort", "schema_version"}
    mine = {k: v for k, v in criteria.to_dict().items() if k not in neutral}
    empty = {k: v for k, v in Criteria().to_dict().items() if k not in neutral}
    return mine == empty


@dataclass(frozen=True)
class Condition:
    name: str
    kind: str                                   # "signal" | "document"
    signals: SignalFilter = field(default_factory=SignalFilter)
    min_confidence: Optional[str] = None
    event_window_days: Optional[Tuple[int, int]] = None
    min_count: int = 1
    criteria: Criteria = field(default_factory=Criteria)

    def canonical(self) -> Dict[str, Any]:
        if self.kind == "document":
            return {"criteria": self.criteria.to_dict()}
        return {"signals": self.signals.canonical(), "min_confidence": self.min_confidence,
                "event_window_days": ({"from": self.event_window_days[0],
                                       "to": self.event_window_days[1]}
                                      if self.event_window_days else None),
                "min_count": self.min_count}


@dataclass(frozen=True)
class Case:
    id: str
    all: Tuple[str, ...]
    any: Tuple[str, ...]
    none: Tuple[str, ...]
    outcome: str
    priority: Optional[int]

    def canonical(self) -> Dict[str, Any]:
        return {"id": self.id, "when": {"all": list(self.all), "any": list(self.any),
                                        "none": list(self.none)},
                "outcome": self.outcome, "priority": self.priority}

    @property
    def conditions(self) -> Tuple[str, ...]:
        return self.all + self.any + self.none


@dataclass(frozen=True)
class Outcome:
    id: str
    label: str
    actions: Tuple[str, ...]

    def canonical(self) -> Dict[str, Any]:
        return {"label": self.label, "actions": list(self.actions)}


@dataclass(frozen=True)
class ScenarioDefinition:
    criteria: Criteria
    conditions: Tuple[Condition, ...]
    cases: Tuple[Case, ...]
    outcomes: Tuple[Outcome, ...]
    default_outcome: str
    strategy: str
    notify_existing: bool = False

    def canonical(self) -> Dict[str, Any]:
        return {
            "schema_version": SCENARIO_SCHEMA_VERSION,
            "criteria": self.criteria.to_dict(),
            "conditions": {c.name: c.canonical() for c in self.conditions},
            # Case order is semantic (first_match); it is kept.
            "cases": [c.canonical() for c in self.cases],
            "outcomes": {o.id: o.canonical() for o in self.outcomes},
            "default_outcome": self.default_outcome,
            "strategy": self.strategy,
            "notify_existing": self.notify_existing,
        }

    def fingerprint(self) -> str:
        return sha256_hex(self.canonical())

    def condition(self, name: str) -> Condition:
        return next(c for c in self.conditions if c.name == name)

    def outcome(self, oid: str) -> Outcome:
        return next(o for o in self.outcomes if o.id == oid)

    def ordered_cases(self) -> List[Case]:
        """Cases in decision order: list order, or by descending priority."""
        if self.strategy == "highest_priority":
            return sorted(self.cases, key=lambda c: -c.priority)
        return list(self.cases)

    def notify_outcomes(self) -> List[str]:
        return [o.id for o in self.outcomes if "notify" in o.actions]

    def warnings(self) -> List[str]:
        """Things that are valid but probably not intended (dry-run report)."""
        out = []
        used = {n for c in self.cases for n in c.conditions}
        for c in self.conditions:
            if c.name not in used:
                out.append(f"condition '{c.name}' is not used by any case")
        reached = {c.outcome for c in self.cases} | {self.default_outcome}
        for o in self.outcomes:
            if o.id not in reached:
                out.append(f"outcome '{o.id}' is not produced by any case")
        if not self.notify_outcomes():
            out.append("no outcome has the notify action: outcomes are recorded only")
        return out


def _condition(name: str, raw: Any) -> Condition:
    if not isinstance(raw, dict):
        raise ScenarioDefinitionError(f"condition '{name}' must be an object")
    if "criteria" in raw:
        _object(raw, f"condition '{name}'", ("criteria",))
        criteria = _criteria(raw["criteria"], f"condition '{name}' criteria")
        if _criteria_is_empty(criteria):
            raise ScenarioDefinitionError(f"condition '{name}' has empty criteria")
        return Condition(name=name, kind="document", criteria=criteria)
    raw = _object(raw, f"condition '{name}'",
                  ("signals", "min_confidence", "event_window_days", "min_count"))
    try:
        signals = parse_signal_filter(raw.get("signals"))
    except RuleDefinitionError as exc:
        raise ScenarioDefinitionError(f"condition '{name}' {exc}")
    min_conf = raw.get("min_confidence")
    if min_conf is not None:
        _choice(min_conf, f"condition '{name}' min_confidence", CONFIDENCE_LEVELS)
    window = None
    if raw.get("event_window_days") is not None:
        w = _object(raw["event_window_days"], f"condition '{name}' event_window_days",
                    ("from", "to"))
        if "from" not in w or "to" not in w:
            raise ScenarioDefinitionError(f"condition '{name}' event_window_days needs"
                                          " both from and to")
        lo = _int(w["from"], "event_window_days.from", -MAX_WINDOW_DAYS, MAX_WINDOW_DAYS)
        hi = _int(w["to"], "event_window_days.to", -MAX_WINDOW_DAYS, MAX_WINDOW_DAYS)
        if lo > hi:
            raise ScenarioDefinitionError(f"condition '{name}' event_window_days.from must"
                                          " not be after .to")
        window = (lo, hi)
        if signals.signal_types and not (
                set(signals.signal_types) & {"date_reference", "relative_reference"}):
            raise ScenarioDefinitionError(f"condition '{name}': event_window_days needs"
                                          " dated signals")
    min_count = _int(raw.get("min_count", 1), f"condition '{name}' min_count", 1,
                     MAX_MIN_COUNT)
    if signals == SignalFilter() and min_conf is None and window is None:
        raise ScenarioDefinitionError(
            f"condition '{name}' needs a signal filter, min_confidence, event_window_days"
            " or criteria (\"any signal at all\" is not a condition)")
    return Condition(name=name, kind="signal", signals=signals, min_confidence=min_conf,
                     event_window_days=window, min_count=min_count)


def _name_list(raw: Any, where: str, known: Dict[str, Condition]) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ScenarioDefinitionError(f"{where} must be a list of condition names")
    names = []
    for n in raw:
        if not isinstance(n, str) or n not in known:
            raise ScenarioDefinitionError(f"{where} refers to an undefined condition {n!r}")
        if n in names:
            raise ScenarioDefinitionError(f"{where} lists condition '{n}' twice")
        names.append(n)
    return tuple(names)


def parse_definition(payload: Any) -> ScenarioDefinition:
    """Strictly parse a definition (unknown keys are refused).

    Always raises ``ScenarioDefinitionError``: the helpers shared with
    rule_model raise its base class, which callers catching the scenario
    error would otherwise let escape as an unhandled exception."""
    try:
        return _parse_definition(payload)
    except ScenarioDefinitionError:
        raise
    except RuleDefinitionError as exc:
        raise ScenarioDefinitionError(str(exc)) from exc


def _parse_definition(payload: Any) -> ScenarioDefinition:
    payload = _object(payload if payload is not None else {}, "definition", _DEFINITION_KEYS)
    version = payload.get("schema_version", SCENARIO_SCHEMA_VERSION)
    if version != SCENARIO_SCHEMA_VERSION:
        raise ScenarioDefinitionError(f"unsupported scenario schema_version {version!r}")
    criteria = _criteria(payload.get("criteria"), "criteria")

    raw_conditions = payload.get("conditions")
    if not isinstance(raw_conditions, dict) or not raw_conditions:
        raise ScenarioDefinitionError("conditions must be a non-empty object")
    if len(raw_conditions) > MAX_CONDITIONS:
        raise ScenarioDefinitionError(f"at most {MAX_CONDITIONS} conditions")
    conditions = {}
    for name in sorted(raw_conditions):
        _identifier(name, "condition name")
        conditions[name] = _condition(name, raw_conditions[name])

    raw_outcomes = payload.get("outcomes")
    if not isinstance(raw_outcomes, dict) or not raw_outcomes:
        raise ScenarioDefinitionError("outcomes must be a non-empty object")
    if len(raw_outcomes) > MAX_OUTCOMES:
        raise ScenarioDefinitionError(f"at most {MAX_OUTCOMES} outcomes")
    outcomes = {}
    for oid in sorted(raw_outcomes):
        _identifier(oid, "outcome id")
        raw = _object(raw_outcomes[oid], f"outcome '{oid}'", ("label", "actions"))
        label = raw.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ScenarioDefinitionError(f"outcome '{oid}' needs a label")
        label = " ".join(label.split())
        if len(label) > MAX_LABEL_LENGTH:
            raise ScenarioDefinitionError(f"outcome '{oid}' label is limited to"
                                          f" {MAX_LABEL_LENGTH} characters")
        actions = raw.get("actions", [])
        if not isinstance(actions, list):
            raise ScenarioDefinitionError(f"outcome '{oid}' actions must be a list")
        for a in actions:
            _choice(a, f"outcome '{oid}' action", ACTIONS)
        outcomes[oid] = Outcome(id=oid, label=label, actions=tuple(sorted(set(actions))))

    default = payload.get("default_outcome")
    if default is None:
        raise ScenarioDefinitionError("default_outcome is required: every content needs an"
                                      " outcome when no case matches")
    if default not in outcomes:
        raise ScenarioDefinitionError(f"default_outcome {default!r} is not a defined outcome")
    if outcomes[default].actions:
        raise ScenarioDefinitionError("the default outcome cannot have actions")

    strategy = _choice(payload.get("strategy"), "strategy", STRATEGIES) \
        if payload.get("strategy") is not None else None
    if strategy is None:
        raise ScenarioDefinitionError(f"strategy is required ({', '.join(STRATEGIES)})")

    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ScenarioDefinitionError("cases must be a non-empty list")
    if len(raw_cases) > MAX_CASES:
        raise ScenarioDefinitionError(f"at most {MAX_CASES} cases")
    cases: List[Case] = []
    for i, raw in enumerate(raw_cases):
        raw = _object(raw, f"case {i + 1}", ("id", "when", "outcome", "priority"))
        cid = _identifier(raw.get("id"), f"case {i + 1} id")
        if any(c.id == cid for c in cases):
            raise ScenarioDefinitionError(f"case id '{cid}' is used twice")
        when = _object(raw.get("when"), f"case '{cid}' when", ("all", "any", "none"))
        all_ = _name_list(when.get("all"), f"case '{cid}' when.all", conditions)
        any_ = _name_list(when.get("any"), f"case '{cid}' when.any", conditions)
        none = _name_list(when.get("none"), f"case '{cid}' when.none", conditions)
        if not (all_ or any_ or none):
            raise ScenarioDefinitionError(f"case '{cid}' needs at least one condition")
        overlap = (set(all_) | set(any_)) & set(none)
        if overlap:
            raise ScenarioDefinitionError(
                f"case '{cid}' requires and excludes {', '.join(sorted(overlap))}:"
                " it can never match")
        outcome = raw.get("outcome")
        if outcome not in outcomes:
            raise ScenarioDefinitionError(f"case '{cid}' outcome {outcome!r} is not defined")
        priority = raw.get("priority")
        if strategy == "highest_priority":
            if priority is None:
                raise ScenarioDefinitionError(f"case '{cid}' needs a priority for the"
                                              " highest_priority strategy")
            priority = _int(priority, f"case '{cid}' priority", 1, MAX_CASE_PRIORITY)
        elif priority is not None:
            raise ScenarioDefinitionError(f"case '{cid}': priority only applies to the"
                                          " highest_priority strategy")
        cases.append(Case(id=cid, all=all_, any=any_, none=none, outcome=outcome,
                          priority=priority))
    if strategy == "highest_priority":
        seen: Dict[int, str] = {}
        for c in cases:
            if c.priority in seen:
                raise ScenarioDefinitionError(
                    f"cases '{seen[c.priority]}' and '{c.id}' have the same priority"
                    f" {c.priority}: the decision would depend on list order")
            seen[c.priority] = c.id

    notify_existing = payload.get("notify_existing", False)
    if not isinstance(notify_existing, bool):
        raise ScenarioDefinitionError("notify_existing must be true or false")

    return ScenarioDefinition(
        criteria=criteria, conditions=tuple(conditions[n] for n in sorted(conditions)),
        cases=tuple(cases), outcomes=tuple(outcomes[o] for o in sorted(outcomes)),
        default_outcome=default, strategy=strategy, notify_existing=notify_existing)


def definition_from_stored(stored: Dict[str, Any]) -> ScenarioDefinition:
    return parse_definition(stored)
