"""The identifier detector's current contract, pinned without editing the old test.

``tests/unit/test_identifiers_intel.py`` is on the do-not-edit list. Two of its
tests index signals by ``s.signal_type`` expecting ``email`` / ``phone`` /
``ipv4``:

    by_type = {s.signal_type: s for s in result.signals}
    assert by_type["email"].value == "alice@example.org"

``core/detection/identifiers_intel.py`` deliberately does not work that way any
more, and says so at SIGNAL_IDENTIFIER:

    content_signals.signal_type has one value for identifiers ("identifier"),
    and the kind travels in the evidence JSON, which is what the explorer's
    identifier presets filter on.

So the two old assertions can only pass by reverting that decision. Until the
owner decides, this file pins the behaviour the detector actually promises, so
the regression risk of the old test's intent is still covered: an address is
still detected as an email, and it is still not double counted as an IPv4
fragment.
"""

from __future__ import annotations

from core.detection import identifiers_intel as ii


def _by_kind(result):
    by_kind = {}
    for signal in result.signals:
        by_kind.setdefault(signal.evidence["kind"], []).append(signal)
    return by_kind


def test_email_phone_and_ipv4_are_still_detected_under_one_signal_type():
    result = ii.detect("Contact alice@example.org or +1 (415) 555-0199. Server 192.168.0.1.")
    assert {signal.signal_type for signal in result.signals} == {ii.SIGNAL_IDENTIFIER}

    by_kind = _by_kind(result)
    assert by_kind["email"][0].value == "alice@example.org"
    assert by_kind["email"][0].surface == "alice@example.org"
    assert "alice@example.org" in (by_kind["email"][0].sentence or "")
    assert by_kind["ipv4"][0].value == "192.168.0.1"
    assert "4155550199" in by_kind["phone"][0].value.replace("+", "")


def test_an_email_is_not_also_counted_as_an_ipv4_fragment():
    result = ii.detect("Write to user@mail.co.uk today.")
    kinds = [signal.evidence["kind"] for signal in result.signals]
    assert kinds == ["email"]
    assert len(result.signals) == 1
