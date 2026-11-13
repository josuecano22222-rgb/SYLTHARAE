import pytest

from apps.web.session_cookies import session_cookie_settings


def test_defaults_keep_existing_production_policy():
    assert session_cookie_settings({}) == {
        "SESSION_COOKIE_SECURE": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
    }


def test_development_defaults_to_non_secure_lax():
    assert session_cookie_settings({"FLASK_ENV": "development"}) == {
        "SESSION_COOKIE_SECURE": False,
        "SESSION_COOKIE_SAMESITE": "Lax",
    }


def test_cross_site_embedded_https_cookie_settings():
    assert session_cookie_settings({
        "FLASK_ENV": "development",
        "SESSION_COOKIE_SECURE": "true",
        "SESSION_COOKIE_SAMESITE": "None",
    }) == {
        "SESSION_COOKIE_SECURE": True,
        "SESSION_COOKIE_SAMESITE": "None",
    }


def test_samesite_none_requires_secure_cookie():
    with pytest.raises(ValueError, match="requires SESSION_COOKIE_SECURE=true"):
        session_cookie_settings({"FLASK_ENV": "development", "SESSION_COOKIE_SAMESITE": "None"})


@pytest.mark.parametrize("value", ["maybe", "yes-ish"])
def test_rejects_invalid_secure_flag(value):
    with pytest.raises(ValueError, match="SESSION_COOKIE_SECURE"):
        session_cookie_settings({"SESSION_COOKIE_SECURE": value})


def test_rejects_invalid_samesite_value():
    with pytest.raises(ValueError, match="SESSION_COOKIE_SAMESITE"):
        session_cookie_settings({"SESSION_COOKIE_SAMESITE": "CrossSite"})
