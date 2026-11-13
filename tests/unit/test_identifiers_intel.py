"""Unit tests for structured identifier detection."""

from core.detection import identifiers_intel as ii


def test_detects_email_phone_ipv4_and_links_to_sentence():
    text = "Contact alice@example.org or +1 (415) 555-0199. Server 192.168.0.1."
    result = ii.detect(text)
    by_type = {s.signal_type: s for s in result.signals}
    assert by_type["email"].value == "alice@example.org"
    assert by_type["email"].surface == "alice@example.org"
    assert "alice@example.org" in (by_type["email"].sentence or "")
    assert by_type["ipv4"].value == "192.168.0.1"
    assert by_type["phone"].value.replace("+", "").endswith("4155550199") or "4155550199" in by_type["phone"].value


def test_email_not_double_counted_as_ipv4_fragment():
    text = "Write to user@mail.co.uk today."
    types = {s.signal_type for s in ii.detect(text).signals}
    assert types == {"email"}


def test_empty_and_deterministic():
    assert ii.detect("").signals == ()
    a = [s.to_dict() for s in ii.detect("Call 0033 1 42 86 82 00.").signals]
    b = [s.to_dict() for s in ii.detect("Call 0033 1 42 86 82 00.").signals]
    assert a == b
