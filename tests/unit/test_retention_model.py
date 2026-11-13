"""Step 21 unit tests: the retention policy model.

The policy table is the authority: defaults, bounds, and the meaning of
``0`` (keep forever - an explicit choice, never "not configured"). These
tests run without a database.
"""

from __future__ import annotations

import pytest

from services.retention.model import (AREAS, KEEP_FOREVER, MAX_DAYS,
                                      POLICIES, RetentionError, describe_deletes,
                                      effective_days, policy_for, setting_key,
                                      validate_days)


class TestThePolicyTable:
    def test_every_area_names_its_table_and_age_column(self):
        for area, policy in POLICIES.items():
            assert policy["table"], area
            assert policy["age_column"], area

    def test_the_documented_defaults(self):
        defaults = {area: p["default_days"] for area, p in POLICIES.items()}
        assert defaults == {
            "jobs": 90,
            "scenario_outcomes": KEEP_FOREVER,
            "notifications": KEEP_FOREVER,
            "path_revisions": KEEP_FOREVER,
            "audit_log": KEEP_FOREVER,
        }

    def test_evidence_destroying_areas_default_to_keep_forever(self):
        # Deleting these would destroy evidence; the default must be an
        # explicit keep-forever, not a number somebody picked.
        for area in ("scenario_outcomes", "notifications",
                     "path_revisions", "audit_log"):
            assert POLICIES[area]["default_days"] == KEEP_FOREVER, area

    def test_live_work_has_a_status_guard(self):
        assert "QUEUED" in POLICIES["jobs"]["status_guard"]
        assert "RUNNING" in POLICIES["jobs"]["status_guard"]

    def test_every_area_states_what_it_deletes(self):
        for area in AREAS:
            assert describe_deletes(area), area


class TestValidateDays:
    def test_zero_is_keep_forever(self):
        assert validate_days("jobs", 0) == 0

    def test_the_enabled_range(self):
        assert validate_days("jobs", 1) == 1
        assert validate_days("jobs", MAX_DAYS) == MAX_DAYS

    @pytest.mark.parametrize("bad", [-1, MAX_DAYS + 1, 10_000])
    def test_out_of_bounds_is_refused(self, bad):
        with pytest.raises(RetentionError):
            validate_days("jobs", bad)

    @pytest.mark.parametrize("bad", [True, False, "90", 90.5, None])
    def test_non_integers_are_refused(self, bad):
        with pytest.raises(RetentionError):
            validate_days("jobs", bad)

    def test_unknown_area_is_refused(self):
        with pytest.raises(RetentionError):
            validate_days("everything", 30)
        with pytest.raises(RetentionError):
            policy_for("everything")


class TestEffectiveDays:
    def test_the_default_applies_when_the_setting_is_absent(self):
        assert effective_days("jobs", None) == 90
        assert effective_days("audit_log", None) == KEEP_FOREVER

    def test_a_stored_setting_wins(self):
        assert effective_days("jobs", 7) == 7

    def test_a_hand_edited_file_cannot_smuggle_a_policy(self):
        # Whatever the file says, a non-integer falls back to the default.
        assert effective_days("jobs", "30") == 90
        assert effective_days("jobs", True) == 90


def test_settings_keys_are_stable():
    assert setting_key("jobs") == "retention.jobs_days"
    assert setting_key("scenario_outcomes") == "retention.scenario_outcomes_days"


def test_configured_days_reads_the_stored_setting(monkeypatch):
    """The service layer resolves the effective policy from the settings
    manager; the model re-validates whatever the file contains."""
    from services.retention import service

    monkeypatch.setattr(service, "_settings_get",
                        lambda key, default: {"retention.jobs_days": 7,
                                              "retention.audit_log_days": True}
                        .get(key))
    assert service.configured_days("jobs") == 7
    # A hand-edited boolean cannot become a policy: the default stands.
    assert service.configured_days("audit_log") == KEEP_FOREVER


def test_the_settings_declarations_match_the_policy_table():
    """Every policy has a declared setting with the same default and bounds
    (the settings definition validates at the UI edge; the model at the
    service edge - they must not drift)."""
    from settings.settings_models import SETTING_DEFINITIONS

    declared = {k: d for k, d in SETTING_DEFINITIONS.items()
                if k.startswith("retention.")}
    assert {setting_key(a) for a in AREAS} <= set(declared)
    for area in AREAS:
        d = declared[setting_key(area)]
        assert d.default == POLICIES[area]["default_days"], area
        assert d.min_value == 0 and d.max_value == MAX_DAYS, area


def test_retention_is_a_schedulable_administrative_type():
    from services.scheduling.model import REQUIRED_ROLE, SCHEDULE_TYPES

    assert "retention" in SCHEDULE_TYPES
    assert REQUIRED_ROLE["retention"] == "admin"