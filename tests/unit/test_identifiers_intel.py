"""Unit tests for structured identifier detection.

Every signal from this detector carries the single ``signal_type``
``"identifier"``; which kind of identifier it is travels in
``evidence["kind"]``. That is the contract the explorer's identifier presets
filter on, so the tests below key on the kind, not on ``signal_type``.
"""

import pytest

from core.detection import identifiers_intel as ii


def _by_kind(text):
    """Signals keyed by evidence kind, the way the explorer reads them."""
    signals = ii.detect(text).signals
    return {s.evidence["kind"]: s for s in signals}


def test_every_signal_carries_the_identifier_signal_type():
    """The design these tests key on, pinned so it cannot drift quietly."""
    text = "Contact alice@example.org. Server 192.168.0.1. See https://example.org."
    signals = ii.detect(text).signals
    assert signals, "nothing detected; the rest of the file would pass vacuously"
    assert {s.signal_type for s in signals} == {ii.SIGNAL_IDENTIFIER}
    assert set(ii.SIGNAL_TYPES) == {"identifier"}


def test_detects_email_phone_ipv4_and_links_to_sentence():
    text = "Contact alice@example.org or +1 (415) 555-0199. Server 192.168.0.1."
    by_kind = _by_kind(text)
    assert by_kind["email"].value == "alice@example.org"
    assert by_kind["email"].surface == "alice@example.org"
    assert "alice@example.org" in (by_kind["email"].sentence or "")
    assert by_kind["ipv4"].value == "192.168.0.1"
    assert by_kind["phone"].value.replace("+", "").endswith("4155550199") or "4155550199" in by_kind["phone"].value


def test_email_not_double_counted_as_ipv4_fragment():
    text = "Write to user@mail.co.uk today."
    by_kind = _by_kind(text)
    assert set(by_kind) == {"email"}
    assert by_kind["email"].surface == "user@mail.co.uk"


@pytest.mark.parametrize("text, kind", [
    ("Contact alice@example.org today.", "email"),
    ("Call +1 (415) 555-0199 now.", "phone"),
    ("Server 192.168.0.1 is down.", "ipv4"),
    ("Reachable at 2001:db8::1 only.", "ipv6"),
    ("See https://example.org/page.", "url"),
])
def test_each_kind_is_detected_and_labelled(text, kind):
    """All five kinds are reachable and each is labelled correctly."""
    by_kind = _by_kind(text)
    assert kind in by_kind, f"{kind} not detected in {text!r}"
    assert by_kind[kind].evidence["kind"] == kind


def test_empty_and_deterministic():
    assert ii.detect("").signals == ()
    a = [s.to_dict() for s in ii.detect("Call 0033 1 42 86 82 00.").signals]
    b = [s.to_dict() for s in ii.detect("Call 0033 1 42 86 82 00.").signals]
    assert a == b
