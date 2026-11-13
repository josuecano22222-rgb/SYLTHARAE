"""Unit: the user's sidebar preferences narrow what the registry allows.

build_navigation keeps its contract with a preference map applied:
hidden entries drop out, an explicit position places an entry ahead of the
declared order inside its domain, visibility itself is still decided by
the registry/state (a preference can never widen it), and an absent or
empty preference map changes nothing. The service's validation runs before
any database access: unknown interface ids, non-boolean hidden values and
bad or duplicated positions are refused (the connection object below is a
sentinel - validation must not touch it).
"""

from __future__ import annotations

import pytest

from core.interfaces import REGISTRY, build_navigation, get_interface
from services.navigation_prefs import NavigationPrefsError, set_prefs

from tests.unit.test_interface_lifecycle import FakeUser, _state


def _user(role="admin"):
    return FakeUser(role)


def _url_for(endpoint, **kw):
    return f"/{endpoint}"


def _ids(groups):
    return [e.interface_id for g in groups for e in g.entries]


def _all_on():
    return _state(**{i.interface_id: True for i in REGISTRY})


def test_without_prefs_the_declared_order_stands():
    plain = _ids(build_navigation(_all_on(), _user(), url_for=_url_for))
    assert plain and plain == _ids(build_navigation(
        _all_on(), _user(), url_for=_url_for, prefs={}))


def test_a_hidden_entry_is_removed_but_others_stand():
    victim = _ids(build_navigation(_all_on(), _user(), url_for=_url_for))[1]
    groups = build_navigation(_all_on(), _user(), url_for=_url_for,
                              prefs={victim: {"hidden": True, "position": None}})
    ids = _ids(groups)
    assert victim not in ids
    assert len(ids) == len(_ids(build_navigation(
        _all_on(), _user(), url_for=_url_for))) - 1


def test_a_position_places_the_entry_first_in_its_domain():
    groups = build_navigation(_all_on(), _user(), url_for=_url_for)
    first = groups[0].entries[0]
    second_of_first = groups[0].entries[1] if len(groups[0].entries) > 1 else None
    if second_of_first is None:
        pytest.skip("the first domain holds a single entry")
    groups = build_navigation(
        _all_on(), _user(), url_for=_url_for,
        prefs={second_of_first.interface_id: {"hidden": False, "position": 1}})
    assert groups[0].entries[0].interface_id == second_of_first.interface_id
    # The previously-first entry follows, it is not lost.
    assert groups[0].entries[1].interface_id == first.interface_id


def test_a_preference_cannot_make_an_invisible_entry_appear():
    state = _state(**{i.interface_id: False for i in REGISTRY})
    some_id = next(i.interface_id for i in REGISTRY)
    groups = build_navigation(state, _user(), url_for=_url_for,
                              prefs={some_id: {"hidden": False, "position": 1}})
    assert _ids(groups) == []


def test_an_active_page_stays_reachable_in_the_model():
    """Hiding is presentation for ONE user; the interface still exists and
    the URL still serves it (authorization is core/security's business)."""
    victim = _ids(build_navigation(_all_on(), _user(), url_for=_url_for))[0]
    assert get_interface(victim) is not None


# ------------------------------------------------------------------ service

def test_set_prefs_refuses_unknown_interface_before_the_database():
    with pytest.raises(NavigationPrefsError, match="unknown interface"):
        set_prefs(object(), 1, [{"interface_id": "not_an_interface",
                                 "hidden": True, "position": None}])


def test_set_prefs_refuses_bad_shapes_before_the_database():
    with pytest.raises(NavigationPrefsError, match="must be a list"):
        set_prefs(object(), 1, {"interface_id": "reports"})
    real = "reports"
    with pytest.raises(NavigationPrefsError, match="hidden must be"):
        set_prefs(object(), 1, [{"interface_id": real, "hidden": "yes",
                                 "position": None}])
    with pytest.raises(NavigationPrefsError, match="position must be"):
        set_prefs(object(), 1, [{"interface_id": real, "hidden": False,
                                 "position": 0}])
    with pytest.raises(NavigationPrefsError, match="position must be"):
        set_prefs(object(), 1, [{"interface_id": real, "hidden": False,
                                 "position": True}])
    with pytest.raises(NavigationPrefsError, match="unknown field"):
        set_prefs(object(), 1, [{"interface_id": real, "hidden": False,
                                 "position": None, "extra": 1}])


def test_set_prefs_refuses_a_duplicated_position_before_the_database():
    real = next(i.interface_id for i in REGISTRY)
    other = next(i.interface_id for i in REGISTRY if i.interface_id != real)
    with pytest.raises(NavigationPrefsError, match="given twice"):
        set_prefs(object(), 1, [
            {"interface_id": real, "hidden": False, "position": 1},
            {"interface_id": other, "hidden": False, "position": 1},
        ])
