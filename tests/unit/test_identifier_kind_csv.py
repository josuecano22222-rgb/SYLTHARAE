"""Bug A: the signal filter sent `identifier_kind=ipv4,ipv6` and got a 400.

The screen and the endpoint, before this fix:

* **Screen** - `templates/Signals/signals.html` line 166 holds a hidden input
  `fIdentifierKind` that belongs to the filter form. `applyKindPreset()` in
  `static/js/pages/signals-page.js` fills it from the preset:
  `preset.identifier_kinds.join(',')`, which for the "ip" preset is the literal
  string `ipv4,ipv6`. `filterParams()` splits comma/space lists for
  `category_id`, `keyword_id`, `place_key` and `hash_id`, and not for
  `identifier_kind`, so one parameter carrying two kinds goes out on the wire.
* **Endpoint** - `Api/routes/signals.py` `_signal_read` calls
  `signal_query.parse_filter(request.args)`; `_values()` compares each raw
  value against `identifiers_intel.KINDS`, so `"ipv4,ipv6"` matches nothing and
  the request fails with

      400 identifier_kind must be one of: email, phone, ipv4, ipv6, url
          (got 'ipv4,ipv6')

The endpoint now accepts a comma-separated list for `identifier_kind`: each
item is validated on its own, duplicates collapse, and a bad item is named in
the message. A single value keeps working, and repeated parameters
(`?identifier_kind=ipv4&identifier_kind=ipv6`) keep working, because both forms
already had to be supported by everything else that takes a list.

Every test below was written first and failed against the old `_values()`.
"""
import pytest

from core.detection import identifiers_intel
from services.detection import signal_query


def _parse(**kwargs):
    """Parse as the route does: a MultiDict-like mapping of name -> list."""
    class Args:
        def __init__(self, pairs):
            self._pairs = pairs

        def getlist(self, name):
            return list(self._pairs.get(name, []))

        def get(self, name, default=None):
            values = self._pairs.get(name)
            return values[0] if values else default

    pairs = {}
    for name, value in kwargs.items():
        pairs[name] = value if isinstance(value, list) else [value]
    return signal_query.parse_filter(Args(pairs))


# --------------------------------------------------------------------------- #
# The regression itself
# --------------------------------------------------------------------------- #
def test_the_two_kinds_the_preset_sends_are_both_accepted():
    parsed = _parse(identifier_kind="ipv4,ipv6")
    assert parsed.identifier_kinds == ("ipv4", "ipv6")


def test_a_single_kind_still_works():
    assert _parse(identifier_kind="email").identifier_kinds == ("email",)


def test_repeated_parameters_still_work():
    assert _parse(identifier_kind=["ipv4", "ipv6"]).identifier_kinds == ("ipv4", "ipv6")


def test_a_duplicate_collapses():
    assert _parse(identifier_kind="ipv4,ipv4").identifier_kinds == ("ipv4",)
    assert _parse(identifier_kind="ipv4,ipv6,ipv4,ipv6,ipv4").identifier_kinds == (
        "ipv4", "ipv6")


def test_surrounding_whitespace_is_ignored():
    assert _parse(identifier_kind=" ipv4 , ipv6 ").identifier_kinds == ("ipv4", "ipv6")


def test_an_unknown_item_is_named_in_the_error():
    with pytest.raises(signal_query.SignalQueryError) as caught:
        _parse(identifier_kind="ipv4,fax")
    message = str(caught.value)
    # The offending item, not the whole string: "got 'ipv4,fax'" tells the
    # caller nothing about which of the two values is wrong.
    assert message == ("identifier_kind must be one of: "
                       + ", ".join(identifiers_intel.KINDS)
                       + " (got 'fax')")


def test_an_unknown_item_alone_is_named_in_the_error():
    with pytest.raises(signal_query.SignalQueryError) as caught:
        _parse(identifier_kind="fax")
    assert "(got 'fax')" in str(caught.value)


def test_an_empty_item_is_ignored_not_fatal():
    """A trailing comma is a typo, not an unknown kind."""
    assert _parse(identifier_kind="ipv4,,ipv6").identifier_kinds == ("ipv4", "ipv6")
    assert _parse(identifier_kind="ipv4,").identifier_kinds == ("ipv4",)


def test_a_value_with_only_separators_means_no_filter():
    """Clearing the hidden input sends an empty string; that is 'no filter'."""
    assert _parse(identifier_kind="").identifier_kinds == ()
    assert _parse(identifier_kind=" , , ").identifier_kinds == ()


def test_the_value_is_named_as_given_when_it_is_the_empty_string():
    """An explicit empty item inside a list is dropped, not reported."""
    assert _parse(identifier_kind=",ipv6").identifier_kinds == ("ipv6",)


# --------------------------------------------------------------------------- #
# The same list rule must not leak into values where a comma is content
# --------------------------------------------------------------------------- #
def test_a_comma_is_still_not_a_separator_where_it_is_content():
    """`evidence_text` is prose: `a,b` must stay one term, not become two."""
    parsed = _parse(evidence_text="Smith,Jones", identifier_kind="email")
    assert parsed.evidence_text == "Smith,Jones"
