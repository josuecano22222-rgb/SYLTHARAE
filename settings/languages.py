"""Canonical UI language registry.

Single source of truth for every language the interface offers. Any code
that validates, lists, or renders a language selector must derive its
options from SUPPORTED_LANGUAGES so the sidebar switcher, the Settings
page and the API layer always agree.

Adding a language here (plus a complete translation catalog under
``translations/<code>/LC_MESSAGES/``) is all that is required to expose it.

Supported languages: Arabic, Croatian, English, Hebrew, Persian.
"""

from collections import OrderedDict

#: code -> endonym (shown verbatim in selectors)
SUPPORTED_LANGUAGES = OrderedDict([
    ('en', 'English'),
    ('ar', 'العربية'),   # Arabic (RTL)
    ('he', 'עברית'),     # Hebrew (RTL)
    ('fa', 'فارسی'),     # Persian (RTL)
    # AUDIT-I18N-03: translations/hr/LC_MESSAGES held 3301 translated strings
    # and a correct three-form plural rule while Croatian was missing here, so
    # the catalog could never be reached: the switcher, the Settings page, the
    # API validation and the setup wizard all derive their options from this
    # registry. Either the catalog ships or it does not - a locale nobody can
    # select is wasted translation work.
    ('hr', 'Hrvatski'),  # Croatian
])

#: Languages written right-to-left (used for dir=rtl handling)
RTL_LANGUAGES = {'ar', 'he', 'fa', 'ur'}


def language_codes():
    """Ordered list of supported language codes."""
    return list(SUPPORTED_LANGUAGES.keys())


def language_name(code, default=None):
    """Endonym for a supported code, else *default*."""
    return SUPPORTED_LANGUAGES.get(code, default)
