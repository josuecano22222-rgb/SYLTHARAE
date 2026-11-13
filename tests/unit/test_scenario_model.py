"""Step 12: scenario definition parsing, canonical form and fingerprint."""

from __future__ import annotations

import copy

import pytest

from services.monitoring.scenario_model import (MAX_CASES, ScenarioDefinitionError,
                                                definition_from_stored, parse_definition)


def base(**over):
    d = {
        "criteria": {"sources": [1]},
        "conditions": {
            "soon": {"signals": {"signal_types": ["date_reference"]},
                     "event_window_days": {"from": 0, "to": 10}, "min_confidence": "medium"},
            "in_b": {"criteria": {"sources": [2]}},
        },
        "cases": [
            {"id": "c1", "when": {"all": ["soon"], "none": ["in_b"]}, "outcome": "review"},
            {"id": "c2", "when": {"any": ["in_b"]}, "outcome": "watch"},
        ],
        "outcomes": {"review": {"label": "Review", "actions": ["notify"]},
                     "watch": {"label": "Watch", "actions": []},
                     "none": {"label": "Nothing", "actions": []}},
        "default_outcome": "none",
        "strategy": "first_match",
    }
    d.update(over)
    return d


def refused(d, fragment):
    with pytest.raises(ScenarioDefinitionError) as info:
        parse_definition(d)
    assert fragment in str(info.value), str(info.value)


def test_a_valid_definition_round_trips_through_its_canonical_form():
    parsed = parse_definition(base())
    canon = parsed.canonical()
    assert canon["schema_version"] == 1 and canon["notify_existing"] is False
    assert [c["id"] for c in canon["cases"]] == ["c1", "c2"]
    again = definition_from_stored(canon)
    assert again.canonical() == canon and again.fingerprint() == parsed.fingerprint()
    assert len(parsed.fingerprint()) == 64


def test_fingerprint_ignores_key_order_but_not_case_order():
    d = base()
    shuffled = dict(reversed(list(d.items())))
    shuffled["conditions"] = dict(reversed(list(d["conditions"].items())))
    shuffled["outcomes"] = dict(reversed(list(d["outcomes"].items())))
    assert parse_definition(shuffled).fingerprint() == parse_definition(d).fingerprint()
    reordered = base(cases=list(reversed(base()["cases"])))
    # first_match: case order is semantic.
    assert parse_definition(reordered).fingerprint() != parse_definition(d).fingerprint()


def test_every_semantic_field_changes_the_fingerprint():
    fp = parse_definition(base()).fingerprint()
    variants = [
        base(strategy="all_matching"),
        base(notify_existing=True),
        base(default_outcome="watch"),
        base(criteria={"sources": [3]}),
    ]
    d = base()
    d["conditions"]["soon"]["min_count"] = 2
    variants.append(d)
    d = base()
    d["outcomes"]["watch"]["actions"] = ["notify"]
    variants.append(d)
    assert len({parse_definition(v).fingerprint() for v in variants} | {fp}) == len(variants) + 1


def test_the_default_outcome_is_mandatory_and_silent():
    d = base()
    del d["default_outcome"]
    refused(d, "default_outcome is required")
    refused(base(default_outcome="nope"), "not a defined outcome")
    refused(base(default_outcome="review"), "default outcome cannot have actions")


@pytest.mark.parametrize("strategy", ["first_match", "all_matching", "highest_priority"])
def test_the_three_strategies_are_accepted(strategy):
    d = base(strategy=strategy)
    if strategy == "highest_priority":
        d["cases"][0]["priority"] = 20
        d["cases"][1]["priority"] = 10
    parsed = parse_definition(d)
    assert parsed.strategy == strategy
    if strategy == "highest_priority":
        assert [c.id for c in parsed.ordered_cases()] == ["c1", "c2"]


def test_strategy_rules():
    d = base()
    del d["strategy"]
    refused(d, "strategy is required")
    refused(base(strategy="random"), "strategy")
    refused(base(strategy="highest_priority"), "needs a priority")
    d = base(strategy="highest_priority")
    d["cases"][0]["priority"] = d["cases"][1]["priority"] = 5
    refused(d, "same priority")                         # never a hidden tie-break
    d = base()
    d["cases"][0]["priority"] = 5
    refused(d, "priority only applies")


def test_cases_are_checked_against_conditions_and_outcomes():
    d = base()
    d["cases"][0]["when"] = {"all": ["ghost"]}
    refused(d, "undefined condition 'ghost'")
    d = base()
    d["cases"][0]["when"] = {"all": ["soon"], "none": ["soon"]}
    refused(d, "can never match")
    d = base()
    d["cases"][0]["when"] = {}
    refused(d, "at least one condition")
    d = base()
    d["cases"][0]["when"] = {"all": ["soon", "soon"]}
    refused(d, "twice")
    d = base()
    d["cases"][1]["id"] = "c1"
    refused(d, "used twice")
    d = base()
    d["cases"][0]["outcome"] = "ghost"
    refused(d, "is not defined")
    refused(base(cases=[]), "cases must be a non-empty list")
    many = [{"id": f"c{i}", "when": {"all": ["soon"]}, "outcome": "review"}
            for i in range(MAX_CASES + 1)]
    refused(base(cases=many), f"at most {MAX_CASES} cases")


def test_conditions_are_strict():
    refused(base(conditions={}), "conditions must be a non-empty object")
    d = base()
    d["conditions"]["soon"] = {"min_count": 1}
    refused(d, "is not a condition")                    # "any signal" is not a condition
    d = base()
    d["conditions"]["soon"]["event_window_days"] = {"from": 5, "to": 1}
    refused(d, "must not be after")
    d = base()
    d["conditions"]["soon"]["event_window_days"] = {"from": 0}
    refused(d, "both from and to")
    d = base()
    d["conditions"]["soon"]["signals"] = {"signal_types": ["orientation"]}
    refused(d, "needs dated signals")
    d = base()
    d["conditions"]["soon"]["min_confidence"] = "certain"
    refused(d, "min_confidence")
    d = base()
    d["conditions"]["in_b"] = {"criteria": {}}
    refused(d, "empty criteria")
    d = base()
    d["conditions"]["soon"]["surprise"] = 1
    refused(d, "surprise")


def test_unknown_keys_versions_and_actions_are_refused():
    refused(base(surprise=True), "surprise")
    refused(base(schema_version=2), "unsupported scenario schema_version")
    refused(base(notify_existing="yes"), "notify_existing")
    d = base()
    d["outcomes"]["watch"]["actions"] = ["email"]
    refused(d, "action must be one of: notify")
    d = base()
    d["outcomes"]["watch"]["label"] = ""
    refused(d, "needs a label")


def test_warnings_flag_the_valid_but_unintended():
    d = copy.deepcopy(base())
    d["conditions"]["unused"] = {"criteria": {"sources": [9]}}
    d["outcomes"]["orphan"] = {"label": "Orphan", "actions": []}
    d["outcomes"]["review"]["actions"] = []
    warnings = parse_definition(d).warnings()
    assert "condition 'unused' is not used by any case" in warnings
    assert "outcome 'orphan' is not produced by any case" in warnings
    assert any("no outcome has the notify action" in w for w in warnings)
    assert parse_definition(base()).warnings() == []


def test_errors_from_the_shared_rule_helpers_surface_as_scenario_errors():
    # Regression: _object/_choice/_int (rule_model) raise the base class;
    # callers that catch ScenarioDefinitionError must still see them.
    for d in (base(surprise=1), base(strategy="random")):
        with pytest.raises(ScenarioDefinitionError):
            parse_definition(d)
