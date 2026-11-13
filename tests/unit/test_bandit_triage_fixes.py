"""Regression tests for the defects found while triaging the bandit gate
(docs/SECURITY.md, "Bandit triage of the intelligence layer").

Each test pins one fix so it cannot silently come back:

* validators anchored with ``$`` accepted a trailing newline;
* ``NotificationService._set_flag`` guarded the column it writes into SQL
  with ``assert``, which ``python -O`` removes;
* the horizon wrote the reference date into the SQL text instead of binding it;
* ``SearchService`` defined six methods twice (a broken splice): the first
  copies were dead code that no test exercised.
"""
import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Validators: a trailing newline is not a valid identifier / date / id
# ---------------------------------------------------------------------------

def test_scenario_identifiers_reject_a_trailing_newline():
    from services.monitoring import scenario_model

    assert scenario_model._identifier("high_risk", "case") == "high_risk"
    with pytest.raises(scenario_model.ScenarioDefinitionError):
        scenario_model._identifier("high_risk\n", "case")


@pytest.mark.parametrize("module, name, good", [
    ("core.analytics.kinds", "_ID", "keyness"),
    ("core.criteria.model", "_ISO_DATE", "2026-01-31"),
    ("core.security.audit_query", "_ID", "42"),
    ("services.detection.signal_query", "_METHOD_RE", "gazetteer.exact"),
    ("services.detection.signal_query", "_PLACE_KEY_RE", "wd:Q1234"),
    ("services.detection.signal_query", "_POSITIVE_INT_RE", "7"),
    ("services.monitoring.scenario_model", "_ID_RE", "case_a"),
])
def test_anchored_validators_match_the_whole_value(module, name, good):
    import importlib

    pattern = getattr(importlib.import_module(module), name)
    assert pattern.match(good)
    assert not pattern.match(good + "\n"), f"{module}.{name} accepts a trailing newline"


# ---------------------------------------------------------------------------
# Notification flag column: an explicit check, not an assert
# ---------------------------------------------------------------------------

def test_set_flag_rejects_an_unknown_column_without_relying_on_assert():
    from core.monitoring.notification_service import NotificationService

    service = NotificationService.__new__(NotificationService)  # no DB load
    with pytest.raises(ValueError):
        service._set_flag(1, "read = TRUE, title", None)

    source = (ROOT / "core/monitoring/notification_service.py").read_text(encoding="utf-8")
    fn = source[source.index("def _set_flag"):source.index("def ", source.index("def _set_flag") + 5)]
    assert "assert column" not in fn


# ---------------------------------------------------------------------------
# Horizon: the reference date is a bound parameter
# ---------------------------------------------------------------------------

def test_horizon_binds_the_reference_date():
    from services.detection import signal_query

    bucket = signal_query._bucket_sql()
    assert "ref.r" in bucket and "%(r)s" not in bucket
    assert signal_query._REF_SQL.count("%s") == 1
    source = (ROOT / "services/detection/signal_query.py").read_text(encoding="utf-8")
    assert "DATE '{" not in source, "a date value is written into the SQL text"


def test_horizon_refuses_a_non_date_reference():
    from core.criteria.model import Criteria
    from core.criteria.compiler import AccessScope
    from services.detection import signal_query

    class NoDbCursor:
        def execute(self, *a, **k):  # pragma: no cover - must not be reached
            raise AssertionError("query executed with an invalid reference date")

    from werkzeug.datastructures import MultiDict

    f = signal_query.parse_filter(MultiDict())
    with pytest.raises(signal_query.SignalQueryError):
        signal_query.horizon(NoDbCursor(), f, Criteria(), AccessScope(),
                             "2026-01-01' OR '1'='1")


# ---------------------------------------------------------------------------
# No class in the search layer defines a method twice
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "Api/services/search_service.py",
    "core/criteria/sql.py",
    "core/criteria/compiler.py",
])
def test_no_method_is_defined_twice(path):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    for scope in [tree] + [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        names = [n.name for n in scope.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert not duplicates, f"{path}: defined more than once: {duplicates}"


def test_search_uses_the_shared_analyst_category_predicate():
    source = (ROOT / "Api/services/search_service.py").read_text(encoding="utf-8")
    assert "afc_placeholders" not in source
    assert re.search(r"_filter_predicates\(\s*analyst_category_ids=", source)
