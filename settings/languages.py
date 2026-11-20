"""Canonical UI language registry.

Single source of truth for every language the interface offers. Any code
that validates, lists, or renders a language selector must derive its
options from SUPPORTED_LANGUAGES so the sidebar switcher, the Settings
page and the API layer always agree.

Adding a language here (plus a complete translation catalog under
``translations/<code>/LC_MESSAGES/``) is all that is required to expose it.

Supported languages: Arabic, English, Hebrew, Persian.
"""

from collections import OrderedDict

#: code -> endonym (shown verbatim in selectors)
SUPPORTED_LANGUAGES = OrderedDict([
    ('en', 'English'),
    ('ar', 'العربية'),   # Arabic (RTL)
    ('he', 'עברית'),     # Hebrew (RTL)
    ('fa', 'فارسی'),     # Persian (RTL)
])

#: Languages written right-to-left (used for dir=rtl handling)
RTL_LANGUAGES = {'ar', 'he', 'fa', 'ur'}


def language_codes():
    """Ordered list of supported language codes."""
    return list(SUPPORTED_LANGUAGES.keys())


def language_name(code, default=None):
    """Endonym for a supported code, else *default*."""
    return SUPPORTED_LANGUAGES.get(code, default)
